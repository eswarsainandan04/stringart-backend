"""Voice Assistant API Endpoints.

Handles:
- GET /api/speech/play (and alias /pay): Fetches speech sequence records, current bookmark, and interval.
- POST /api/speech/action: Updates bookmark and interval settings in the database.
"""

from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from api.deps import get_current_user
from services.speech_generation import generate_and_save_speech

router = APIRouter()


class SpeechActionRequest(BaseModel):
    art_id: str = Field(..., description="Target string art ID")
    bookmark: Optional[int] = Field(None, ge=0, description="Current step index or 0 for restart")
    interval: Optional[float] = Field(None, ge=0.1, le=60.0, description="Interval in seconds between spoken steps (0.1s to 60.0s)")


def _get_or_create_voice(art_id: str, user_id_str: str, db: Session) -> Dict[str, Any]:
    """Retrieves existing voice record from database or generates it on demand."""
    clean_art_id = art_id.strip()

    select_sql = text("""
        SELECT voice_id, user_id, art_id, speech, bookmark, "interval", created_at, updated_at
        FROM voice
        WHERE art_id = :art_id AND user_id = :user_id;
    """)
    row = db.execute(select_sql, {"art_id": clean_art_id, "user_id": user_id_str}).mappings().first()

    if not row:
        # Check if art belongs to user
        art_sql = text("SELECT art_id, gcode_url FROM arts WHERE art_id = :art_id AND user_id = :user_id;")
        art_row = db.execute(art_sql, {"art_id": clean_art_id, "user_id": user_id_str}).mappings().first()
        if not art_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Art project '{clean_art_id}' not found for this user.",
            )

        # On-demand generation for arts that don't have voice records yet
        generated = generate_and_save_speech(db=db, user_id=user_id_str, art_id=clean_art_id)
        if not generated:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not generate voice speech for project '{clean_art_id}'. G-Code may be missing.",
            )

        # Query newly inserted voice record
        row = db.execute(select_sql, {"art_id": clean_art_id, "user_id": user_id_str}).mappings().first()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve generated voice record.",
            )

    speech_content = row["speech"] or {}
    total_steps = speech_content.get("total_steps", 0) if isinstance(speech_content, dict) else 0

    return {
        "voice_id": str(row["voice_id"]),
        "art_id": row["art_id"],
        "speech": speech_content,
        "bookmark": row["bookmark"] or 0,
        "interval": float(row["interval"]) if row["interval"] is not None else 2.0,
        "total_steps": total_steps,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


@router.get("/play", status_code=status.HTTP_200_OK)
def get_speech_play(
    art_id: str = Query(..., description="String art ID"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetches the full speech sequence JSON, bookmark position, and interval time."""
    user_id_str = str(current_user["user_id"])
    return _get_or_create_voice(art_id=art_id, user_id_str=user_id_str, db=db)


# Alias supporting /pay as requested in specification
@router.get("/pay", status_code=status.HTTP_200_OK, include_in_schema=False)
def get_speech_pay_alias(
    art_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alias for /play."""
    user_id_str = str(current_user["user_id"])
    return _get_or_create_voice(art_id=art_id, user_id_str=user_id_str, db=db)


@router.post("/action", status_code=status.HTTP_200_OK)
def update_speech_action(
    payload: SpeechActionRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Updates the voice bookmark (current step position) and interval time."""
    user_id_str = str(current_user["user_id"])
    clean_art_id = payload.art_id.strip()

    # Ensure record exists first
    _get_or_create_voice(art_id=clean_art_id, user_id_str=user_id_str, db=db)

    update_sql = text("""
        UPDATE voice
        SET bookmark = COALESCE(:bookmark, bookmark),
            "interval" = COALESCE(:interval, "interval"),
            updated_at = CURRENT_TIMESTAMP
        WHERE art_id = :art_id AND user_id = :user_id
        RETURNING voice_id, art_id, bookmark, "interval", updated_at;
    """)

    updated = db.execute(
        update_sql,
        {
            "art_id": clean_art_id,
            "user_id": user_id_str,
            "bookmark": payload.bookmark,
            "interval": payload.interval,
        },
    ).mappings().first()

    db.commit()

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice record for art '{clean_art_id}' could not be updated.",
        )

    return {
        "status": "success",
        "art_id": updated["art_id"],
        "bookmark": updated["bookmark"],
        "interval": float(updated["interval"]) if updated["interval"] is not None else 2.0,
        "updated_at": updated["updated_at"].isoformat() if updated["updated_at"] else None,
    }
