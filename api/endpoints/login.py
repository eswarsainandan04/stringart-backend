from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import text
import bcrypt

from database import get_db
from services.auth import GenerateTokens

router = APIRouter()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/login", status_code=status.HTTP_200_OK)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    # 1. Find user by email
    query = text("SELECT user_id, name, email, password FROM users WHERE email = :email")
    user = db.execute(query, {"email": payload.email}).mappings().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # 2. Verify password with bcrypt
    password_matches = bcrypt.checkpw(
        payload.password.encode("utf-8"),
        user["password"].encode("utf-8"),
    )

    if not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # 3. Generate BOTH tokens using user_id from database
    token_generator = GenerateTokens(user["user_id"])
    access_token = token_generator.generate_access_token()
    refresh_token = token_generator.generate_refresh_token()

    # 4. Return tokens to user
    return {
        "message": "Login successful",
        "token_type": "bearer",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": {
            "user_id": str(user["user_id"]),
            "name": user["name"],
            "email": user["email"],
        },
    }


class RefreshRequest(BaseModel):
    refresh_token: str
    user_id: Optional[str] = None


@router.post("/refresh", status_code=status.HTTP_200_OK)
def refresh_token(payload: RefreshRequest):
    from services.auth import generate_new_access_token

    print("\n" + "=" * 65)
    print("🔄 [REFRESH ENDPOINT] Received token refresh request:")
    print(f"   User ID: {payload.user_id or '(derived from token)'}")
    print(f"   Refresh Token Preview: {payload.refresh_token[:15]}...")

    try:
        new_access_token = generate_new_access_token(payload.user_id, payload.refresh_token)
        print("✅ [REFRESH SUCCESS] Issued BRAND NEW Access Token!")
        print("=" * 65 + "\n")
        return {
            "token_type": "bearer",
            "access_token": new_access_token,
        }
    except HTTPException as e:
        print(f"❌ [REFRESH REJECTED] {e.detail}")
        print("=" * 65 + "\n")
        raise e

