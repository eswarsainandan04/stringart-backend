from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
import bcrypt

from database import get_db
from config import settings

router = APIRouter()


# 1. Incoming JSON request body
class SignupRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


# 2. Signup Endpoint
@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    # Check if email is already registered
    check_query = text("SELECT user_id FROM users WHERE email = :email")
    existing_user = db.execute(check_query, {"email": payload.email}).fetchone()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered.",
        )

    # Hash the password
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(payload.password.encode("utf-8"), salt).decode("utf-8")

    # Insert into users table
    insert_query = text("""
        INSERT INTO users (name, email, password)
        VALUES (:name, :email, :password)
        RETURNING user_id, name, email, created_at
    """)

    result = db.execute(
        insert_query,
        {
            "name": payload.name,
            "email": payload.email,
            "password": hashed_password,
        },
    )

    # Get the inserted record
    new_user = result.mappings().one()
    new_user_id = str(new_user["user_id"])

    # Allocate welcome free credits in credits table
    db.execute(
        text("""
            INSERT INTO credits (user_id, wallet)
            VALUES (:user_id, :wallet)
            ON CONFLICT (user_id) DO NOTHING
        """),
        {
            "user_id": new_user_id,
            "wallet": settings.DEFAULT_FREE_CREDITS,
        }
    )
    db.commit()

    return {
        "message": "User registered successfully",
        "user": {
            "user_id": new_user_id,
            "name": new_user["name"],
            "email": new_user["email"],
            "wallet": settings.DEFAULT_FREE_CREDITS,
            "created_at": new_user["created_at"],
        },
    }


# Router for /api/passwords/verify (invoked after OTP is confirmed in mobile app)
passwords_verify_router = APIRouter()


class VerifySignupRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


@passwords_verify_router.post("/verify", status_code=status.HTTP_201_CREATED)
def verify_and_create_user(payload: VerifySignupRequest, db: Session = Depends(get_db)):
    """
    Creates user account and allocates free credits after OTP verification.
    """
    target_email = payload.email.lower().strip()

    # Check if email is already registered
    check_query = text("SELECT user_id FROM users WHERE LOWER(email) = :email")
    existing_user = db.execute(check_query, {"email": target_email}).fetchone()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered. Please sign in instead.",
        )

    # Hash the password
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(payload.password.encode("utf-8"), salt).decode("utf-8")

    # Insert into users table
    insert_query = text("""
        INSERT INTO users (name, email, password)
        VALUES (:name, :email, :password)
        RETURNING user_id, name, email, created_at
    """)

    result = db.execute(
        insert_query,
        {
            "name": payload.name.strip(),
            "email": target_email,
            "password": hashed_password,
        },
    )

    new_user = result.mappings().one()
    new_user_id = str(new_user["user_id"])

    # Allocate welcome free credits in credits table
    db.execute(
        text("""
            INSERT INTO credits (user_id, wallet)
            VALUES (:user_id, :wallet)
            ON CONFLICT (user_id) DO NOTHING
        """),
        {
            "user_id": new_user_id,
            "wallet": settings.DEFAULT_FREE_CREDITS,
        }
    )
    db.commit()

    # Issue JWT tokens for seamless login
    from services.auth import GenerateTokens
    token_gen = GenerateTokens(new_user_id)
    access_token = token_gen.generate_access_token()
    refresh_token = token_gen.generate_refresh_token()

    return {
        "status": "success",
        "message": "Account created and verified successfully!",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "user_id": new_user_id,
            "name": new_user["name"],
            "email": new_user["email"],
            "wallet": settings.DEFAULT_FREE_CREDITS,
            "created_at": new_user["created_at"],
        },
    }
