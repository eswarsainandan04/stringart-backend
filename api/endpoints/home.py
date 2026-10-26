from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from api.deps import get_current_user
from services.storage import storage
from api.endpoints.arts import _format_url

router = APIRouter()


@router.get("/stats", status_code=status.HTTP_200_OK)
def get_home_stats(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns counts for total_arts, pending_arts, and completed_arts
    for the authenticated user.
    """
    user_id_str = str(current_user["user_id"])

    # Query all arts for the current user
    query = text("""
        SELECT 
            art_id, 
            gcode_url
        FROM arts
        WHERE user_id = :user_id;
    """)

    rows = db.execute(query, {"user_id": user_id_str}).mappings().all()

    total_arts = len(rows)
    completed_arts = 0

    for r in rows:
        art_id = str(r["art_id"])
        raw_gcode_url = r.get("gcode_url")
        # Check database column or storage file existence
        if raw_gcode_url or storage.file_exists(f"gcode/{user_id_str}/{art_id}.nc"):
            completed_arts += 1

    pending_arts = max(0, total_arts - completed_arts)

    return {
        "status": "success",
        "total_arts": total_arts,
        "pending_arts": pending_arts,
        "completed_arts": completed_arts,
        "stats": {
            "total_arts": total_arts,
            "pending_arts": pending_arts,
            "completed_arts": completed_arts,
        },
    }


@router.get("/list", status_code=status.HTTP_200_OK)
def get_home_completed_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns the latest 5 completed string arts for the authenticated user,
    ready for Voice Guide and PDF Manual Instructions.
    """
    user_id_str = str(current_user["user_id"])

    # Fetch arts for user, newest first
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

    for r in rows:
        # Only take up to 5 completed arts
        if len(completed_list) >= 5:
            break

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
        if not has_gcode:
            continue

        # Auto-generate script PDF if missing for completed art
        if not raw_script_url:
            if storage.file_exists(f"scripts/{user_id_str}/{art_id}.pdf"):
                raw_script_url = storage.get_url(f"scripts/{user_id_str}/{art_id}.pdf", request)
            else:
                try:
                    from services.script_generation import generate_manual_script_pdf
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

        display_img = formatted_canvas_url or formatted_image_url

        # Read G-code content for client animation & voice guidance
        gcode_content: Optional[str] = storage.read_text(f"gcode/{user_id_str}/{art_id}.nc")

        created_at_val = r.get("created_at")
        created_at_str = (
            created_at_val.isoformat() if hasattr(created_at_val, "isoformat") else str(created_at_val)
        ) if created_at_val else None

        updated_at_val = r.get("updated_at")
        updated_at_str = (
            updated_at_val.isoformat() if hasattr(updated_at_val, "isoformat") else str(updated_at_val)
        ) if updated_at_val else None

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
            "has_gcode": True,
            "is_completed": True,
            "status": "completed",
            "created_at": created_at_str,
            "updated_at": updated_at_str,
        }

        completed_list.append(art_data)

    return {
        "status": "success",
        "arts": completed_list,
        "count": len(completed_list),
    }
