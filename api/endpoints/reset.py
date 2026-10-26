from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
import bcrypt

from database import get_db
from services import otp_verification

router = APIRouter()


class OTPRequest(BaseModel):
    email: EmailStr
    page: str = Field("verification", description="verification or reset")


class PasswordResetRequest(BaseModel):
    email: EmailStr
    new_password: str = Field(..., min_length=6, max_length=128)


@router.post("/otp", status_code=status.HTTP_200_OK)
def send_otp(payload: OTPRequest, db: Session = Depends(get_db)):
    """
    Sends a 6-digit OTP code to the provided email address via Gmail SMTP.
    Returns the OTP and 5-minute expiration to the device memory for local matching.
    """
    target_email = payload.email.lower().strip()
    page_type = payload.page.lower().strip()

    # 1. Check account existence based on flow
    user_check = db.execute(
        text("SELECT user_id FROM users WHERE LOWER(email) = :email"),
        {"email": target_email}
    ).fetchone()

    if page_type == "verification" and user_check:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This email is already registered. Please sign in instead."
        )

    if page_type == "reset" and not user_check:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found with this email address. Please check and try again."
        )

    # 2. Generate 6-digit OTP
    otp_code = otp_verification.generate_otp()

    # 3. Send email via Gmail SMTP
    try:
        otp_verification.send_otp_email(
            to_email=target_email,
            otp=otp_code,
            purpose=page_type
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send email verification: {str(e)}"
        )

    # 4. Return OTP to device memory with 5-minute expiration
    return {
        "status": "success",
        "message": f"Verification code sent to {target_email}",
        "otp": otp_code,
        "expires_in_seconds": 300,
        "email": target_email,
        "page": page_type,
    }


@router.post("/reset", status_code=status.HTTP_200_OK)
def reset_password(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    """
    Updates the user's password in the database after OTP verification.
    """
    target_email = payload.email.lower().strip()

    # 1. Verify user exists
    user = db.execute(
        text("SELECT user_id FROM users WHERE LOWER(email) = :email"),
        {"email": target_email}
    ).fetchone()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found with this email address."
        )

    # 2. Hash new password
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(payload.new_password.encode("utf-8"), salt).decode("utf-8")

    # 3. Update password in database
    db.execute(
        text("UPDATE users SET password = :password WHERE LOWER(email) = :email"),
        {"password": hashed_password, "email": target_email}
    )
    db.commit()

    return {
        "status": "success",
        "message": "Password updated successfully. You can now log in with your new password.",
    }
