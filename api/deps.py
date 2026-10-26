from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

from services.auth import JWT_SECRET_KEY, ALGORITHM

# HTTPBearer automatically extracts "Bearer <token>" from Authorization header
security = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Dependency that verifies the JWT access token from the Authorization header.
    Prints informative learning logs to the terminal.
    """
    token = credentials.credentials
    token_preview = f"{token[:12]}...{token[-6:]}" if len(token) > 18 else token

    print("\n" + "=" * 65)
    print("[AUTH MIDDLEWARE] Received request with Authorization token:")
    print(f"   Token Preview: {token_preview}")

    try:
        # Decode and verify signature + expiration
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")

        # Ensure it is an access token, not a refresh token
        if token_type != "access":
            print("[AUTH REJECTED] Wrong token type! Expected 'access' token.")
            print("=" * 65 + "\n")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type: Expected access token",
            )

        print("[AUTH ALLOWED] Signature verified & Token is ACTIVE!")
        print(f"[AUTH USER] Authenticated user_id: {user_id}")
        print("=" * 65 + "\n")

        return {"user_id": user_id, "payload": payload}

    except jwt.ExpiredSignatureError:
        print("[AUTH EXPIRED] Access token has expired!")
        print("   -> Frontend should send refresh token to /api/auth/refresh")
        print("=" * 65 + "\n")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token expired",
        )

    except jwt.PyJWTError as e:
        print(f"[AUTH REJECTED] Token invalid or tampered! Error: {e}")
        print("=" * 65 + "\n")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
