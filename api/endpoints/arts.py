import os
import uuid
import shutil
import base64
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Depends, Form, File, UploadFile, HTTPException, status, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

import io
from PIL import Image

from database import get_db
from api.deps import get_current_user
from config import settings
from services.storage import storage, build_base_url
from services.remove_background import process_circular_subject
from services.string_generation import StringGeneration
from services.script_generation import generate_manual_script_pdf
from services.speech_generation import generate_and_save_speech

router = APIRouter()
my_arts_router = APIRouter(tags=["My Arts"])

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


class CreateArtRequest(BaseModel):
    art_name: str = Field(..., min_length=1, max_length=200, description="Name of the string art project")


@router.post("/create", status_code=status.HTTP_201_CREATED)
def create_art(
    payload: CreateArtRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = current_user["user_id"]
    cleaned_name = payload.art_name.strip()

    if not cleaned_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Art name cannot be empty.",
        )

    # Check if art_name already exists in database table arts
    existing_art = db.execute(
        text("""
            SELECT 1 FROM arts 
            WHERE user_id = :user_id 
              AND LOWER(TRIM(art_name)) = LOWER(:art_name)
        """),
        {"user_id": user_id, "art_name": cleaned_name}
    ).fetchone()

    if existing_art:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This Name is already existed",
        )

    # Generate art_{UUID} as requested
    art_id = f"art_{uuid.uuid4()}"

    query = text("""
        INSERT INTO arts (
            art_id, 
            user_id, 
            art_name, 
            lines, 
            thread, 
            image_url, 
            created_at, 
            updated_at
        ) 
        VALUES (
            :art_id, 
            :user_id, 
            :art_name, 
            NULL, 
            NULL, 
            NULL, 
            CURRENT_TIMESTAMP, 
            CURRENT_TIMESTAMP
        )
        RETURNING 
            art_id, 
            user_id, 
            art_name, 
            lines, 
            thread, 
            image_url, 
            created_at, 
            updated_at;
    """)

    result = db.execute(
        query,
        {
            "art_id": art_id,
            "user_id": user_id,
            "art_name": cleaned_name,
        },
    ).mappings().first()

    db.commit()

    if not result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create art project in database.",
        )

    created_at_val = result["created_at"]
    created_at_str = created_at_val.isoformat() if created_at_val else None

    return {
        "art_id": result["art_id"],
        "user_id": str(result["user_id"]),
        "art_name": result["art_name"],
        "lines": result["lines"],
        "thread": result["thread"],
        "image_url": result["image_url"],
        "created_at": created_at_str,
    }


@router.post("/image-upload", status_code=status.HTTP_200_OK)
async def upload_art_image(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id_str = str(current_user["user_id"])
    content_type = request.headers.get("content-type", "").lower()

    print("\n" + "=" * 65)
    print(f"[IMAGE UPLOAD REQUEST] User: {user_id_str}")
    print(f"   Content-Type: {content_type}")

    art_id: str | None = None
    contents: bytes | None = None
    filename: str | None = None

    # Support application/json (base64 payload - 100% reliable for React Native Android/iOS)
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON body: {str(e)}",
            )
        art_id = body.get("art_id")
        filename = body.get("filename")
        base64_data = body.get("image_base64") or body.get("image_data") or ""

        if not base64_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing 'image_base64' in JSON body.",
            )

        # Strip optional data URI prefix if present (e.g. data:image/jpeg;base64,...)
        if "," in base64_data:
            base64_data = base64_data.split(",", 1)[1]

        try:
            contents = base64.b64decode(base64_data)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to decode base64 image data: {str(e)}",
            )

    # Support multipart/form-data (FormData payload)
    elif "multipart/form-data" in content_type:
        try:
            form = await request.form()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to parse multipart form data: {str(e)}",
            )
        art_id = form.get("art_id")
        uploaded_file = form.get("file")

        if not uploaded_file:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing 'file' field in multipart form data.",
            )

        filename = getattr(uploaded_file, "filename", None)
        contents = await uploaded_file.read()

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported Content-Type: '{content_type}'. Must be application/json or multipart/form-data.",
        )

    if not art_id or not str(art_id).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'art_id' is required.",
        )
    art_id = str(art_id).strip()

    if not contents or len(contents) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image file content is empty (0 bytes).",
        )

    # Automatically detect real image format from binary magic bytes or filename
    ext = None
    if contents.startswith(b"\xff\xd8\xff"):
        ext = "jpg"
    elif contents.startswith(b"\x89PNG\r\n\x1a\n"):
        ext = "png"
    elif contents[:4] == b"RIFF" and len(contents) >= 12 and contents[8:12] == b"WEBP":
        ext = "webp"
    elif filename and "." in filename:
        raw_ext = filename.split(".")[-1].lower()
        ext = "jpg" if raw_ext == "jpeg" else raw_ext

    if not ext or ext not in ALLOWED_EXTENSIONS:
        ext = "jpg"

    # 2. Check art project ownership in database
    verify_query = text("""
        SELECT art_id, art_name FROM arts
        WHERE art_id = :art_id AND user_id = :user_id
    """)
    art_record = db.execute(verify_query, {
        "art_id": art_id,
        "user_id": user_id_str,
    }).mappings().first()

    if not art_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Art project '{art_id}' not found or does not belong to you.",
        )

    # 3. Filename and Key: images/{user_id}/{art_id}.{ext}
    filename_on_disk = f"{art_id}.{ext}"
    image_key = f"images/{user_id_str}/{filename_on_disk}"
    mime_type = f"image/{'jpeg' if ext == 'jpg' else ext}"

    # 4. Upload bytes to storage (Local Disk or S3 Cloud)
    try:
        full_image_url = storage.upload_bytes(
            key=image_key,
            data=contents,
            content_type=mime_type,
            request=request,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write image file: {str(e)}",
        )

    # 5. Update database record with full URL
    update_query = text("""
        UPDATE arts
        SET image_url = :image_url, updated_at = CURRENT_TIMESTAMP
        WHERE art_id = :art_id AND user_id = :user_id
        RETURNING art_id, art_name, image_url;
    """)

    updated = db.execute(update_query, {
        "image_url": full_image_url,
        "art_id": art_id,
        "user_id": user_id_str,
    }).mappings().first()

    db.commit()

    print(f"[IMAGE STORED] Key: {image_key}")
    print(f"   Size: {len(contents)} bytes")
    print(f"   Full Image URL: {full_image_url}")
    print("=" * 65 + "\n")

    return {
        "art_id": updated["art_id"],
        "art_name": updated["art_name"],
        "image_url": updated["image_url"],
    }





class GenerateArtRequest(BaseModel):
    art_id: str = Field(..., description="Project art ID")
    lines: Optional[int] = Field(3500, description="String art lines count (3500-5000)")
    thread: Optional[str] = Field("0.16 mm", description="Thread thickness")
    pan_x: float = Field(0.0, description="Horizontal drag pan coordinate in UI")
    pan_y: float = Field(0.0, description="Vertical drag pan coordinate in UI")
    scale: float = Field(1.0, description="Pinch zoom scale factor in UI")
    canvas_size: float = Field(340.0, description="Diameter of circular canvas hoop in UI")
    displayed_width: Optional[float] = Field(None, description="Width of image displayed in UI")
    displayed_height: Optional[float] = Field(None, description="Height of image displayed in UI")


@router.post("/generate", status_code=status.HTTP_200_OK)
def generate_circular_string_art(
    payload: GenerateArtRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id_str = str(current_user["user_id"])
    art_id = payload.art_id.strip()

    print("\n" + "=" * 65)
    print(f"[GENERATE ART REQUEST] User: {user_id_str} | Art: {art_id}")
    print(f"   Lines: {payload.lines} | Thread: {payload.thread}")
    print(f"   Pan: ({payload.pan_x:.1f}, {payload.pan_y:.1f}) | Scale: {payload.scale:.2f} | CanvasSize: {payload.canvas_size:.1f}")

    # 0. Check credits balance in credits table before starting pipeline
    credit_row = db.execute(
        text("SELECT wallet FROM credits WHERE user_id = :user_id"),
        {"user_id": user_id_str}
    ).fetchone()

    current_wallet = credit_row[0] if credit_row and credit_row[0] is not None else 0
    if current_wallet <= 0:
        print(f"   [CREDITS REJECTED] User {user_id_str} has {current_wallet} credits. Insufficient credits.")
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient credits. Please recharge your wallet to generate art.",
        )

    # 1. Verify project ownership in DB
    verify_query = text("""
        SELECT art_id, art_name, image_url, lines, thread
        FROM arts
        WHERE art_id = :art_id AND user_id = :user_id
    """)
    art_record = db.execute(verify_query, {
        "art_id": art_id,
        "user_id": user_id_str,
    }).mappings().first()

    if not art_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Art project '{art_id}' not found or does not belong to you.",
        )

    # 2. Locate source image in storage
    user_images_dir = storage.local_dir / "images" / user_id_str
    user_legacy_dir = storage.local_dir / user_id_str

    source_files = []
    if user_images_dir.exists():
        source_files = [
            f for f in user_images_dir.glob(f"{art_id}.*")
            if f.is_file() and f.parent == user_images_dir
        ]

    if not source_files and user_legacy_dir.exists():
        source_files = [
            f for f in user_legacy_dir.glob(f"{art_id}.*")
            if f.is_file() and f.parent == user_legacy_dir
        ]

    source_image_input = None
    if source_files:
        source_image_input = source_files[0]
        print(f"   Source image found: {source_files[0].name} ({source_files[0].stat().st_size} bytes)")
    elif storage.is_s3:
        for ext in ["png", "jpg", "jpeg", "webp"]:
            raw_bytes = storage.read_bytes(f"images/{user_id_str}/{art_id}.{ext}")
            if raw_bytes:
                source_image_input = Image.open(io.BytesIO(raw_bytes))
                break

    if not source_image_input:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No image found for art project '{art_id}'. Please upload an image first.",
        )

    # 3. Process circular subject removal onto light grey composite (using u2net)
    try:
        processed_pil, _ = process_circular_subject(
            image_input=source_image_input,
            pan_x=payload.pan_x,
            pan_y=payload.pan_y,
            scale=payload.scale,
            canvas_size=payload.canvas_size,
            target_size=800,
            bg_color=(237, 237, 237),  # light grey #EDEDED
            model_name="u2net_human_seg",
            generate_svg=False,
        )
    except Exception as e:
        print(f"[ERROR] Background removal failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process image and remove background: {str(e)}",
        )

    # 4. Save background-removed canvas image to storage
    canvas_buf = io.BytesIO()
    processed_pil.save(canvas_buf, format="PNG")
    canvas_key = f"images/{user_id_str}/canvas/{art_id}.png"
    full_canvas_url = storage.upload_bytes(
        key=canvas_key,
        data=canvas_buf.getvalue(),
        content_type="image/png",
        request=request,
    )

    # 5. Generate CNC G-Code using StringGeneration module
    try:
        gcode_content = StringGeneration(
            image=processed_pil,
            lines=payload.lines or 3500,
            thread=payload.thread or "0.16 mm",
            user_id=user_id_str,
            art_id=art_id,
        )
    except Exception as e:
        print(f"[ERROR] String generation / G-Code failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate G-Code pattern: {str(e)}",
        )

    # Upload G-code to storage
    gcode_key = f"gcode/{user_id_str}/{art_id}.nc"
    full_gcode_url = storage.upload_bytes(
        key=gcode_key,
        data=gcode_content.encode("utf-8"),
        content_type="text/plain",
        request=request,
    )

    # 6. Generate Manual Weaving PDF Script: storage/scripts/{user_id}/{art_id}.pdf
    full_script_url = None
    try:
        script_key = f"scripts/{user_id_str}/{art_id}.pdf"
        pdf_path = storage.get_local_path(script_key)
        generate_manual_script_pdf(
            gcode_input=gcode_content,
            user_id=user_id_str,
            art_id=art_id,
            art_name=art_record["art_name"],
            num_pins=240,
            thread_type=payload.thread or "0.16 mm",
            output_path=pdf_path,
        )
        full_script_url = storage.upload_file(
            key=script_key,
            file_path=pdf_path,
            content_type="application/pdf",
            request=request,
        )
    except Exception as e:
        print(f"[WARNING] Could not generate manual script PDF: {e}")

    # 6b. Generate Voice Assistant Speech JSON & save to database voice table
    try:
        generate_and_save_speech(
            db=db,
            user_id=user_id_str,
            art_id=art_id,
            gcode_text=gcode_content,
            num_pins=240,
        )
    except Exception as e:
        db.rollback()
        print(f"[WARNING] Could not generate voice speech: {e}")

    # 7. Update database record with canvas_url, gcode_url, script_url, lines, and thread
    update_query = text("""
        UPDATE arts
        SET image_url = :image_url,
            canvas_url = :canvas_url,
            gcode_url = :gcode_url,
            script_url = :script_url,
            lines = :lines,
            thread = :thread,
            updated_at = CURRENT_TIMESTAMP
        WHERE art_id = :art_id AND user_id = :user_id
        RETURNING art_id, art_name, lines, thread, image_url, canvas_url, gcode_url, script_url;
    """)

    updated = db.execute(update_query, {
        "image_url": full_canvas_url,
        "canvas_url": full_canvas_url,
        "gcode_url": full_gcode_url,
        "script_url": full_script_url,
        "lines": payload.lines,
        "thread": payload.thread,
        "art_id": art_id,
        "user_id": user_id_str,
    }).mappings().first()

    db.commit()

    # 8. Deduct 1 credit from user wallet in credits table after pipeline completes
    deduct_result = db.execute(
        text("""
            UPDATE credits 
            SET wallet = GREATEST(0, wallet - 1),
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = :user_id
            RETURNING wallet
        """),
        {"user_id": user_id_str}
    ).fetchone()
    db.commit()

    remaining_wallet = deduct_result[0] if deduct_result else max(0, current_wallet - 1)
    print(f"   [CREDITS DEDUCTED] 1 credit deducted. Remaining wallet: {remaining_wallet}")

    print(f"[ART GENERATED SUCCESSFULLY]")
    print(f"   Canvas URL: {full_canvas_url}")
    print(f"   G-Code URL: {full_gcode_url}")
    if full_script_url:
        print(f"   Script PDF URL: {full_script_url}")
    print("=" * 65 + "\n")

    return {
        "art_id": updated["art_id"],
        "art_name": updated["art_name"],
        "image_url": updated["image_url"],
        "canvas_image_url": full_canvas_url,
        "lines": updated["lines"],
        "thread": updated["thread"],
        "gcode_url": full_gcode_url,
        "gcode": gcode_content,
        "script_url": full_script_url,
        "wallet": remaining_wallet,
    }


class ResetArtRequest(BaseModel):
    art_id: str = Field(..., description="Project art ID to reset")


@router.post("/reset", status_code=status.HTTP_200_OK)
def reset_art_project(
    payload: ResetArtRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Resets the art project:
    1. Deletes generated circular canvas image at storage/images/{user_id}/canvas/{art_id}.*
    2. Deletes generated CNC G-Code at storage/gcode/{user_id}/{art_id}.nc
    3. Restores and loads the original uploaded photo at storage/images/{user_id}/{art_id}.*
    4. Updates database image_url back to the original photo.
    """
    user_id_str = str(current_user["user_id"])
    art_id = payload.art_id.strip()

    print("\n" + "=" * 65)
    print(f"[RESET ART REQUEST] User: {user_id_str} | Art: {art_id}")

    # 1. Verify project ownership in DB
    verify_query = text("""
        SELECT art_id, art_name, image_url
        FROM arts
        WHERE art_id = :art_id AND user_id = :user_id
    """)
    art_record = db.execute(verify_query, {
        "art_id": art_id,
        "user_id": user_id_str,
    }).mappings().first()

    if not art_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Art project '{art_id}' not found or does not belong to you.",
        )

    # 2. Delete generated canvas, gcode, and script PDF via storage
    deleted_canvas = []
    if storage.delete_file(f"images/{user_id_str}/canvas/{art_id}.png"):
        deleted_canvas.append(f"{art_id}.png")
    storage.delete_file(f"images/{user_id_str}/canvas/{art_id}.jpg")

    deleted_gcode = []
    if storage.delete_file(f"gcode/{user_id_str}/{art_id}.nc"):
        deleted_gcode.append(f"{art_id}.nc")

    storage.delete_file(f"scripts/{user_id_str}/{art_id}.pdf")

    # 3. Locate original photo in storage
    original_image_url = None
    orig_files = list(storage.local_dir.glob(f"images/{user_id_str}/{art_id}.*"))
    if not orig_files:
        orig_files = list(storage.local_dir.glob(f"{user_id_str}/{art_id}.*"))
    if orig_files:
        original_image_url = storage.get_url(f"images/{user_id_str}/{orig_files[0].name}", request)
    elif storage.is_s3:
        for ext in ["png", "jpg", "jpeg", "webp"]:
            if storage.file_exists(f"images/{user_id_str}/{art_id}.{ext}"):
                original_image_url = storage.get_url(f"images/{user_id_str}/{art_id}.{ext}", request)
                break

    # 4. Update database (clear canvas_url, gcode_url, script_url)
    update_query = text("""
        UPDATE arts
        SET image_url = :image_url,
            canvas_url = NULL,
            gcode_url = NULL,
            script_url = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE art_id = :art_id AND user_id = :user_id
        RETURNING art_id, art_name, image_url;
    """)
    db.execute(update_query, {
        "image_url": original_image_url,
        "art_id": art_id,
        "user_id": user_id_str,
    })
    db.commit()
    print(f"   [RESTORED ORIGINAL IMAGE URL] {original_image_url}")

    print(f"[RESET COMPLETED SUCCESSFULLY]")
    print("=" * 65 + "\n")

    return {
        "message": "Reset completed successfully.",
        "art_id": art_id,
        "image_url": original_image_url,
        "deleted_canvas_files": deleted_canvas,
        "deleted_gcode_files": deleted_gcode,
    }


class RegenerateArtRequest(BaseModel):
    art_id: str = Field(..., description="Project art ID to regenerate")
    lines: Optional[int] = Field(3500, description="String art lines count (3500-5000)")


@router.post("/regenerate", status_code=status.HTTP_200_OK)
def regenerate_art_project(
    payload: RegenerateArtRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Regenerates the art project G-Code and string art pattern:
    1. Deletes the existing CNC G-Code and manual script PDF.
    2. Retrieves the existing canvas image from storage.
    3. Retrieves modified lines from request payload.
    4. Passes the canvas image and lines to the StringGeneration module.
    5. Saves new CNC G-Code, updates script PDF, and updates database columns.
    """
    user_id_str = str(current_user["user_id"])
    art_id = payload.art_id.strip()

    print("\n" + "=" * 65)
    print(f"[REGENERATE ART REQUEST] User: {user_id_str} | Art: {art_id}")
    print(f"   Lines: {payload.lines}")

    # 1. Verify project ownership in DB
    verify_query = text("""
        SELECT art_id, art_name, image_url, lines, thread
        FROM arts
        WHERE art_id = :art_id AND user_id = :user_id
    """)
    art_record = db.execute(verify_query, {
        "art_id": art_id,
        "user_id": user_id_str,
    }).mappings().first()

    if not art_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Art project '{art_id}' not found or does not belong to you.",
        )

    # 2. Step 1: Delete existing G-Code and script PDF from storage
    deleted_gcode = []
    if storage.delete_file(f"gcode/{user_id_str}/{art_id}.nc"):
        deleted_gcode.append(f"{art_id}.nc")
    storage.delete_file(f"scripts/{user_id_str}/{art_id}.pdf")

    # 3. Step 2: Get the canvas image from storage
    canvas_image_input = None
    canvas_local = storage.get_local_path(f"images/{user_id_str}/canvas/{art_id}.png")
    if canvas_local.is_file():
        canvas_image_input = canvas_local
    elif storage.is_s3:
        raw_canvas = storage.read_bytes(f"images/{user_id_str}/canvas/{art_id}.png")
        if raw_canvas:
            canvas_image_input = Image.open(io.BytesIO(raw_canvas))

    if not canvas_image_input:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No canvas image found for art project '{art_id}'. Please generate the art first.",
        )

    # 4. Step 3: Extract Lines from payload (fallback to existing DB value)
    lines_val = payload.lines if payload.lines is not None else (art_record["lines"] or 3500)
    existing_thread = art_record["thread"] or "0.16 mm"

    # 5. Open canvas image as PIL RGB
    try:
        if isinstance(canvas_image_input, Image.Image):
            canvas_pil = canvas_image_input.convert("RGB")
        else:
            with Image.open(canvas_image_input) as img:
                canvas_pil = img.convert("RGB")
    except Exception as e:
        print(f"[ERROR] Failed to load canvas image: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read canvas image: {str(e)}",
        )

    # 6. Step 4: Generate new G-code
    try:
        gcode_content = StringGeneration(
            image=canvas_pil,
            lines=lines_val,
            thread=existing_thread,
            user_id=user_id_str,
            art_id=art_id,
        )
    except Exception as e:
        print(f"[ERROR] String generation / G-Code failed during regenerate: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to regenerate G-Code pattern: {str(e)}",
        )

    # Upload G-code to storage
    gcode_key = f"gcode/{user_id_str}/{art_id}.nc"
    full_gcode_url = storage.upload_bytes(
        key=gcode_key,
        data=gcode_content.encode("utf-8"),
        content_type="text/plain",
        request=request,
    )
    full_canvas_url = storage.get_url(f"images/{user_id_str}/canvas/{art_id}.png", request)

    # 7. Generate Manual Weaving PDF Script: storage/scripts/{user_id}/{art_id}.pdf
    full_script_url = None
    try:
        script_key = f"scripts/{user_id_str}/{art_id}.pdf"
        pdf_path = storage.get_local_path(script_key)
        generate_manual_script_pdf(
            gcode_input=gcode_content,
            user_id=user_id_str,
            art_id=art_id,
            art_name=art_record["art_name"],
            num_pins=240,
            thread_type=existing_thread,
            output_path=pdf_path,
        )
        full_script_url = storage.upload_file(
            key=script_key,
            file_path=pdf_path,
            content_type="application/pdf",
            request=request,
        )
    except Exception as e:
        print(f"[WARNING] Could not generate manual script PDF: {e}")

    # 7b. Generate Voice Assistant Speech JSON & save to database voice table
    try:
        generate_and_save_speech(
            db=db,
            user_id=user_id_str,
            art_id=art_id,
            gcode_text=gcode_content,
            num_pins=240,
        )
    except Exception as e:
        db.rollback()
        print(f"[WARNING] Could not generate voice speech: {e}")

    # 8. Update database record with new lines, canvas_url, gcode_url, script_url
    update_query = text("""
        UPDATE arts
        SET lines = :lines,
            image_url = :image_url,
            canvas_url = :canvas_url,
            gcode_url = :gcode_url,
            script_url = :script_url,
            updated_at = CURRENT_TIMESTAMP
        WHERE art_id = :art_id AND user_id = :user_id
        RETURNING art_id, art_name, lines, thread, image_url, canvas_url, gcode_url, script_url;
    """)

    updated = db.execute(update_query, {
        "image_url": full_canvas_url,
        "canvas_url": full_canvas_url,
        "gcode_url": full_gcode_url,
        "script_url": full_script_url,
        "lines": lines_val,
        "art_id": art_id,
        "user_id": user_id_str,
    }).mappings().first()

    db.commit()

    print(f"[ART REGENERATED SUCCESSFULLY]")
    print(f"   Canvas URL: {full_canvas_url}")
    print(f"   G-Code URL: {full_gcode_url}")
    if full_script_url:
        print(f"   Script PDF URL: {full_script_url}")
    print(f"   Lines: {lines_val}")
    print("=" * 65 + "\n")

    return {
        "art_id": updated["art_id"],
        "art_name": updated["art_name"],
        "image_url": updated["image_url"],
        "canvas_image_url": full_canvas_url,
        "lines": updated["lines"],
        "thread": updated["thread"],
        "gcode_url": full_gcode_url,
        "gcode": gcode_content,
        "script_url": full_script_url,
    }


# =========================================================================
# 5. MY ARTS & DELETE ENDPOINTS (Merged from art_view.py & arts_delete.py)
# =========================================================================

def _format_url(url_val: Optional[str], request: Request) -> Optional[str]:
    """Ensures storage URLs resolve with valid SigV4 signatures or current request host."""
    if not url_val:
        return None
    # If in S3 mode and URL lacks active SigV4 signature, generate fresh presigned URL
    if storage.is_s3:
        if "X-Amz-Signature" not in url_val:
            clean_key = storage.extract_key(url_val)
            return storage.get_url(clean_key, request)
        return url_val

    # Local mode
    if url_val.startswith(("http://", "https://")):
        return url_val
    base_url = build_base_url(request)
    clean_val = "/" + url_val.lstrip("/")
    return f"{base_url}{clean_val}"


@my_arts_router.get("/my-arts", status_code=status.HTTP_200_OK)
def get_my_arts(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Fetch all user arts partitioned into Completed and Pending tabs.
    Directly queries database columns (image_url, canvas_url, gcode_url, script_url).
    """
    user_id_str = str(current_user["user_id"])

    # Query all asset URLs directly from arts database table
    query = text("""
        SELECT 
            art_id, 
            user_id, 
            art_name, 
            lines, 
            thread, 
            image_url, 
            canvas_url,
            gcode_url,
            script_url,
            created_at, 
            updated_at
        FROM arts
        WHERE user_id = :user_id
        ORDER BY created_at DESC;
    """)

    rows = db.execute(query, {"user_id": user_id_str}).mappings().all()

    completed_list: List[Dict[str, Any]] = []
    pending_list: List[Dict[str, Any]] = []

    for r in rows:
        art_id = str(r["art_id"])

        raw_image_url = r.get("image_url")
        raw_canvas_url = r.get("canvas_url")
        raw_gcode_url = r.get("gcode_url")
        raw_script_url = r.get("script_url")

        # Fallback check for legacy arts before database column migration
        if not raw_gcode_url and storage.file_exists(f"gcode/{user_id_str}/{art_id}.nc"):
            raw_gcode_url = storage.get_url(f"gcode/{user_id_str}/{art_id}.nc", request)
            raw_canvas_url = raw_canvas_url or storage.get_url(f"images/{user_id_str}/canvas/{art_id}.png", request)
            db.execute(text("""
                UPDATE arts 
                SET gcode_url = :g, canvas_url = :c 
                WHERE art_id = :aid
            """), {"g": raw_gcode_url, "c": raw_canvas_url, "aid": art_id})
            db.commit()

        has_gcode = bool(raw_gcode_url)

        # Fallback: auto-generate script PDF if missing for completed art
        if has_gcode and not raw_script_url:
            if storage.file_exists(f"scripts/{user_id_str}/{art_id}.pdf"):
                raw_script_url = storage.get_url(f"scripts/{user_id_str}/{art_id}.pdf", request)
            else:
                try:
                    gcode_file = storage.get_local_path(f"gcode/{user_id_str}/{art_id}.nc")
                    pdf_file = storage.get_local_path(f"scripts/{user_id_str}/{art_id}.pdf")
                    if gcode_file.is_file():
                        generate_manual_script_pdf(gcode_file, pdf_file, art_name=r.get("art_name") or "String Art")
                        raw_script_url = storage.get_url(f"scripts/{user_id_str}/{art_id}.pdf", request)
                except Exception as e:
                    print(f"[WARNING] Could not auto-generate missing script PDF: {e}")

            if raw_script_url:
                db.execute(text("""
                    UPDATE arts 
                    SET script_url = :s 
                    WHERE art_id = :aid
                """), {"s": raw_script_url, "aid": art_id})
                db.commit()

        # Format full URLs for client consumption
        formatted_image_url = _format_url(raw_image_url, request)
        formatted_canvas_url = _format_url(raw_canvas_url, request)
        formatted_gcode_url = _format_url(raw_gcode_url, request)
        formatted_script_url = _format_url(raw_script_url, request)

        # Display image: Completed arts prioritize canvas preview, pending arts show original
        display_img = formatted_canvas_url if (has_gcode and formatted_canvas_url) else formatted_image_url

        # Read G-code content for completed arts using storage service
        gcode_content: Optional[str] = None
        if has_gcode:
            gcode_content = storage.read_text(f"gcode/{user_id_str}/{art_id}.nc")

        created_at_val = r.get("created_at")
        if created_at_val:
            created_at_str = created_at_val.isoformat() if hasattr(created_at_val, "isoformat") else str(created_at_val)
        else:
            created_at_str = None

        art_data = {
            "art_id": art_id,
            "user_id": user_id_str,
            "art_name": r.get("art_name") or "Untitled",
            "lines": r.get("lines") or 3500,
            "thread": r.get("thread") or "0.16 mm",
            "image_url": display_img,
            "canvas_image_url": formatted_canvas_url,
            "gcode_url": formatted_gcode_url,
            "gcode": gcode_content,
            "script_url": formatted_script_url,
            "has_gcode": has_gcode,
            "is_completed": has_gcode,
            "status": "completed" if has_gcode else "pending",
            "created_at": created_at_str,
        }

        if has_gcode:
            completed_list.append(art_data)
        else:
            pending_list.append(art_data)

    return {
        "status": "success",
        "completed": completed_list,
        "pending": pending_list,
    }


class DeleteArtRequest(BaseModel):
    art_id: Optional[str] = None


@my_arts_router.delete("/delete-art", status_code=status.HTTP_200_OK)
async def delete_art(
    request: Request,
    art_id: Optional[str] = Query(None, description="Project art ID to delete"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Deletes an art project by art_id:
    1. Removes database record: DELETE FROM arts WHERE art_id=:art_id AND user_id=:user_id
    2. Deletes uploaded image, canvas preview, CNC G-code, and script PDF via StorageService (Local or S3)
    """
    user_id_str = str(current_user["user_id"])

    # Resolve target_art_id from query parameter or raw json body fallback
    target_art_id: Optional[str] = art_id
    if not target_art_id:
        try:
            body = await request.json()
            if isinstance(body, dict):
                target_art_id = body.get("art_id")
        except Exception:
            pass

    if not target_art_id or not str(target_art_id).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'art_id' parameter is required for deletion.",
        )

    clean_art_id = str(target_art_id).strip()

    print("\n" + "=" * 65)
    print(f"[DELETE ART REQUEST] User: {user_id_str} | Art ID: {clean_art_id}")

    # 1. Verify existence and ownership in database
    verify_query = text("""
        SELECT art_id, art_name, user_id
        FROM arts
        WHERE art_id = :art_id AND user_id = :user_id;
    """)
    art_row = db.execute(verify_query, {
        "art_id": clean_art_id,
        "user_id": user_id_str,
    }).mappings().first()

    if not art_row:
        print(f"   [ERROR] Art '{clean_art_id}' not found for user {user_id_str}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Art project '{clean_art_id}' not found or does not belong to you.",
        )

    art_name = art_row.get("art_name") or clean_art_id

    # 2. Delete all associated files from storage (Local Disk or S3 Cloud)
    deleted_files = storage.delete_art_files(user_id_str, clean_art_id)
    print(f"   [STORAGE DELETED] Files removed: {deleted_files}")

    # 3. Delete database record from arts table
    delete_query = text("""
        DELETE FROM arts
        WHERE art_id = :art_id AND user_id = :user_id;
    """)
    db.execute(delete_query, {
        "art_id": clean_art_id,
        "user_id": user_id_str,
    })
    db.commit()

    print(f"   [DB DELETED] Record '{clean_art_id}' removed from arts table.")
    print(f"[DELETE ART COMPLETED SUCCESSFULLY]")
    print("=" * 65 + "\n")

    return {
        "success": True,
        "message": f"Artwork '{art_name}' deleted successfully.",
        "art_id": clean_art_id,
        "deleted_files": deleted_files,
    }




