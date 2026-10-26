from fastapi import APIRouter, Depends
from api.endpoints import health, signup, login, profile, arts, speech, home, payments, reset
from api.deps import get_current_user

api_router = APIRouter()

# 1. PUBLIC / EXPLICITLY SECURED ROUTES
api_router.include_router(health.router, prefix="/system")
api_router.include_router(signup.router, prefix="/auth", tags=["Auth"])
api_router.include_router(login.router, prefix="/auth", tags=["Auth"])
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])
api_router.include_router(reset.router, prefix="/passwords", tags=["Passwords & OTP"])
api_router.include_router(signup.passwords_verify_router, prefix="/passwords", tags=["Passwords & OTP"])

# 2. PROTECTED ROUTES (Requires Verified JWT Access Token)
protected_router = APIRouter(dependencies=[Depends(get_current_user)])
protected_router.include_router(home.router, prefix="/home", tags=["Home"])
protected_router.include_router(profile.router, prefix="/profile", tags=["Profile"])
protected_router.include_router(arts.router, prefix="/generation", tags=["Generation"])
protected_router.include_router(arts.my_arts_router)
protected_router.include_router(speech.router, prefix="/speech", tags=["Voice Speech"])

api_router.include_router(protected_router)
