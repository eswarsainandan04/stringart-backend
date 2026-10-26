"""String Art Voice Assistant Speech Generator.

Decodes CNC G-Code (.nc files) into structured, step-by-step spoken instructions
for real-time voice-guided weaving in the mobile application.
Persists the speech JSON into the `voice` database table with bookmark tracking.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from services.script_generation import decode_gcode_to_steps
from services.storage import storage

logger = logging.getLogger("speech_generation")


def num_to_words(n: int) -> str:
    """Converts integers (1 to 9999) into natural spoken English words.
    Prevents mobile TTS engines from spelling digits individually (e.g. 'one nine oh' for 190).
    """
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
            "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
            "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    if n == 0:
        return "zero"
    if n < 20:
        return ones[n]
    if n < 100:
        return tens[n // 10] + (" " + ones[n % 10] if n % 10 != 0 else "")
    if n < 1000:
        rem = n % 100
        return ones[n // 100] + " hundred" + (" " + num_to_words(rem) if rem != 0 else "")
    if n < 1000000:
        rem = n % 1000
        return num_to_words(n // 1000) + " thousand" + (" " + num_to_words(rem) if rem != 0 else "")
    return str(n)


def generate_speech_json(
    gcode_text: str,
    art_id: str,
    num_pins: int = 240,
) -> Dict[str, Any]:
    """Decodes G-Code into a clean voice speech JSON structure with natural spoken words.

    Rules for speech phrases:
    - Step 1: "Start at pin {from}, go to {to}"
    - Intermediate milestones (every 50 or 100 steps): "Step {step}. {to}"
    - Final step: "Final step! Finish at pin {to}"
    - Regular steps: spoken words (e.g. "one hundred ninety" for 190)
    """
    raw_steps = decode_gcode_to_steps(gcode_text, num_pins=num_pins)
    total_steps = len(raw_steps)

    if total_steps == 0:
        return {
            "art_id": str(art_id),
            "total_steps": 0,
            "total_pins": num_pins,
            "start_pin": 1,
            "steps": [],
        }

    start_pin = raw_steps[0]["from_pin"]
    formatted_steps: List[Dict[str, Any]] = []

    for idx, item in enumerate(raw_steps):
        step_num = item["step"]
        p_from = item["from_pin"]
        p_to = item["to_pin"]

        p_from_words = num_to_words(p_from)
        p_to_words = num_to_words(p_to)
        step_words = num_to_words(step_num)

        is_first = (idx == 0)
        is_last = (idx == total_steps - 1)
        is_milestone = (step_num % 50 == 0 or step_num % 100 == 0) or is_last

        if is_first:
            speech = f"Start at pin {p_from_words}, go to {p_to_words}"
        elif is_last:
            speech = f"Final step! Finish at pin {p_to_words}"
        elif is_milestone:
            speech = f"Step {step_words}. {p_to_words}"
        else:
            speech = p_to_words

        record: Dict[str, Any] = {
            "step": step_num,
            "from": p_from,
            "to": p_to,
            "speech": speech,
        }

        if is_milestone:
            record["milestone"] = True

        formatted_steps.append(record)

    return {
        "art_id": str(art_id),
        "total_steps": total_steps,
        "total_pins": num_pins,
        "start_pin": start_pin,
        "steps": formatted_steps,
    }


def save_speech_to_db(
    db: Session,
    user_id: str,
    art_id: str,
    speech_data: Dict[str, Any],
    default_interval: int = 2,
) -> Dict[str, Any]:
    """Upserts the speech JSON into the `voice` database table and sets bookmark to 0."""
    speech_json_str = json.dumps(speech_data)

    upsert_sql = text("""
        INSERT INTO voice (user_id, art_id, speech, bookmark, "interval", created_at, updated_at)
        VALUES (:user_id, :art_id, CAST(:speech AS jsonb), 0, :interval, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, art_id)
        DO UPDATE SET
            speech = EXCLUDED.speech,
            bookmark = 0,
            "interval" = EXCLUDED."interval",
            updated_at = CURRENT_TIMESTAMP
        RETURNING voice_id, user_id, art_id, bookmark, "interval", created_at, updated_at;
    """)

    try:
        result = db.execute(
            upsert_sql,
            {
                "user_id": str(user_id),
                "art_id": str(art_id),
                "speech": speech_json_str,
                "interval": default_interval,
            },
        ).mappings().first()

        db.commit()
        return dict(result) if result else {}
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving speech to database: {e}")
        raise e


def generate_and_save_speech(
    db: Session,
    user_id: str,
    art_id: str,
    gcode_text: Optional[str] = None,
    num_pins: int = 240,
    default_interval: int = 2,
) -> Dict[str, Any]:
    """Decodes G-Code, builds the voice speech JSON, and saves it in the database `voice` table.

    If `gcode_text` is not supplied, it queries `gcode_url` from the `arts` table and fetches it.
    """
    clean_art_id = str(art_id)
    user_id_str = str(user_id)

    # Resolve G-Code text if not explicitly provided
    if not gcode_text:
        query_sql = text("SELECT gcode_url FROM arts WHERE art_id = :art_id AND user_id = :user_id;")
        row = db.execute(query_sql, {"art_id": clean_art_id, "user_id": user_id_str}).mappings().first()
        if not row or not row.get("gcode_url"):
            logger.warning(f"No gcode_url found in database for art {clean_art_id}")
            return {}

        gcode_url = row["gcode_url"]
        try:
            gcode_text = storage.read_text(gcode_url)
        except Exception as e:
            logger.error(f"Failed to read G-code from storage {gcode_url}: {e}")
            return {}

    speech_data = generate_speech_json(gcode_text, art_id=clean_art_id, num_pins=num_pins)
    save_speech_to_db(db, user_id=user_id_str, art_id=clean_art_id, speech_data=speech_data, default_interval=default_interval)
    logger.info(f"Generated & saved voice speech for art {clean_art_id} ({speech_data.get('total_steps')} steps)")
    return speech_data
