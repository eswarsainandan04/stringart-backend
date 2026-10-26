from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from api.deps import get_current_user

router = APIRouter()


@router.get("/me", status_code=status.HTTP_200_OK)
def get_user_profile(
    current_user: dict = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    user_id = current_user["user_id"]

    query = text("""
        SELECT u.user_id, u.name, u.email, u.created_at, COALESCE(c.wallet, 0) AS wallet
        FROM users u
        LEFT JOIN credits c ON u.user_id = c.user_id
        WHERE u.user_id = :user_id
    """)
    user = db.execute(query, {"user_id": user_id}).mappings().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Format registration time nicely
    created_at = user["created_at"]
    registration_time_str = (
        created_at.strftime("%d %b %Y, %I:%M %p") 
        if created_at 
        else "Unknown"
    )

    return {
        "user_id": str(user["user_id"]),
        "name": user["name"],
        "email": user["email"],
        "registration_time": registration_time_str,
        "wallet": int(user["wallet"] or 0),
    }
