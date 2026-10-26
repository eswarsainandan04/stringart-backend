import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import jwt
from fastapi import HTTPException, status

load_dotenv()

# 1. Configuration & Expiration Constants
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY is missing in your .env file!")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30     # 30 Minutes
REFRESH_TOKEN_EXPIRE_DAYS = 30       # 30 Days


# ==========================================
# 2. GENERATE TOKENS
# ==========================================
class GenerateTokens:
    def __init__(self, user_id: Optional[str] = None):
        self.user_id = str(user_id) if user_id else None

    def generate_access_token(self, user_id: Optional[str] = None) -> str:
        """Generates short-lived Access Token (30 minutes)."""
        uid = str(user_id or self.user_id)
        if not uid:
            raise ValueError("user_id is required to generate access token.")

        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {
            "sub": uid,
            "type": "access",
            "exp": expire,
            "iat": datetime.now(timezone.utc),
        }
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=ALGORITHM)

    def generate_refresh_token(self, user_id: Optional[str] = None) -> str:
        """Generates long-lived Refresh Token (30 days)."""
        uid = str(user_id or self.user_id)
        if not uid:
            raise ValueError("user_id is required to generate refresh token.")

        expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        payload = {
            "sub": uid,
            "type": "refresh",
            "exp": expire,
            "iat": datetime.now(timezone.utc),
        }
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=ALGORITHM)


# ==========================================
# 3. REFRESH LOGIC
# ==========================================
def generate_new_access_token(user_id: Optional[str] = None, refresh_token: str = "") -> str:
    """
    Validates the refresh token and issues a fresh new access token.
    If user_id is omitted, it is securely derived from the token's subject claim.
    If the refresh token is expired, raises an error requiring re-login.
    """
    try:
        payload = jwt.decode(refresh_token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        
        # Verify token type & user identity
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type. Expected a refresh token.",
            )

        token_user_id = payload.get("sub")
        if not token_user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token payload.",
            )

        if user_id and str(token_user_id) != str(user_id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token subject does not match user_id.",
            )

        # Issue brand new access token
        token_gen = GenerateTokens(token_user_id)
        return token_gen.generate_access_token()

    except jwt.ExpiredSignatureError:
        # Node W & X in JWT.md: Refresh token expired -> Force Login
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session ended: Refresh token has expired. Please navigate to login.",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token. Please navigate to login.",
        )


# ==========================================
# 4. VERIFY TOKENS
# ==========================================
class VerifyTokens:
    @staticmethod
    def decode_token(token: str) -> Dict[str, Any]:
        """Decodes token and verifies its cryptographic signature."""
        try:
            return jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired",
            )
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

    @staticmethod
    def verify_token(
        access_token: str, 
        refresh_token: Optional[str] = None, 
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Flow from JWT.md:
        1. Checks access_token.
        2. If access_token is valid -> returns user payload.
        3. If access_token is expired/invalid and refresh_token is provided:
           - Automatically calls generate_new_access_token()
           - If refresh_token also expired -> raises 401 with navigate login instruction.
        """
        try:
            # 1. Try decoding access token normally
            payload = jwt.decode(access_token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
            return {
                "status": "valid",
                "user_id": payload.get("sub"),
                "new_access_token": None,
            }

        except (jwt.ExpiredSignatureError, jwt.PyJWTError):
            # Access token is expired or invalid
            if refresh_token and user_id:
                # Try generating a new access token
                new_token = generate_new_access_token(user_id, refresh_token)
                return {
                    "status": "refreshed",
                    "user_id": user_id,
                    "new_access_token": new_token,
                }

            # If no refresh token provided, reject
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Access token expired. Please refresh token or navigate to login.",
            )
