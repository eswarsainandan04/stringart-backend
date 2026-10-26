from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from api.deps import get_current_user
from services import amout_transactions
from config import Settings

router = APIRouter(tags=["Payments"])


class PlanOptionResponse(BaseModel):
    id: str
    name: str
    badge: Optional[str] = None
    credits: int
    price: float
    popular: Optional[bool] = False
    accentColor: str


class PricingPlansResponse(BaseModel):
    rupees_per_art: int
    default_free_credits: int
    silver_credits: int
    gold_credits: int
    platinum_credits: int
    plans: List[PlanOptionResponse]


@router.get("/plans", response_model=PricingPlansResponse)
def get_payment_plans():
    """
    Returns dynamic pricing and plan configuration from backend .env:
    RUPEES_PER_ART, DEFAULT_FREE_CREDITS, SILVER_CREDITS, GOLD_CREDITS, PLATINUM_CREDITS.
    """
    current_settings = Settings()
    rate = current_settings.RUPEES_PER_ART
    return {
        "rupees_per_art": rate,
        "default_free_credits": current_settings.DEFAULT_FREE_CREDITS,
        "silver_credits": current_settings.SILVER_CREDITS,
        "gold_credits": current_settings.GOLD_CREDITS,
        "platinum_credits": current_settings.PLATINUM_CREDITS,
        "plans": [
            {
                "id": "silver",
                "name": "Silver Pack",
                "badge": "STARTER",
                "credits": current_settings.SILVER_CREDITS,
                "price": float(current_settings.SILVER_CREDITS * rate),
                "popular": False,
                "accentColor": "#94A3B8",
            },
            {
                "id": "gold",
                "name": "Gold Pack",
                "badge": "MOST POPULAR",
                "credits": current_settings.GOLD_CREDITS,
                "price": float(current_settings.GOLD_CREDITS * rate),
                "popular": True,
                "accentColor": "#E0AA32",
            },
            {
                "id": "platinum",
                "name": "Platinum Pack",
                "badge": "BEST VALUE",
                "credits": current_settings.PLATINUM_CREDITS,
                "price": float(current_settings.PLATINUM_CREDITS * rate),
                "popular": False,
                "accentColor": "#6366F1",
            },
        ],
    }


class PaymentLaunchRequest(BaseModel):
    user_id: Optional[UUID] = Field(None, description="User ID (defaults to authenticated user)")
    plan: str = Field(..., description="silver, gold, platinum, or custom")
    entered_credits: Optional[int] = Field(None, description="Number of credits if plan is custom")


class PaymentLaunchResponse(BaseModel):
    payment_id: str
    user_id: str
    plan: str
    credits: int
    purchase_amount: float
    order_id: str
    key_id: str
    currency: str = "INR"
    upi_url: Optional[str] = None


class PaymentVerifyRequest(BaseModel):
    payment_id: UUID
    status: str = Field(..., description="SUCCESS or FAILURE / CANCELLED")
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
    transaction_ref: Optional[str] = Field(None, description="Bank Transaction Reference / UTR / Auth Code")


class PaymentVerifyResponse(BaseModel):
    status: str
    payment_id: str
    credits_added: Optional[int] = None
    payment_method: Optional[str] = None
    transaction_ref: Optional[str] = None
    wallet: Optional[int] = None
    message: Optional[str] = None


class WalletBalanceResponse(BaseModel):
    user_id: str
    wallet: int


@router.post("/launch", response_model=PaymentLaunchResponse)
def launch_payment(
    request: PaymentLaunchRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Creates a Razorpay order and returns order_id and key_id for mobile checkout.
    🔒 Requires JWT Authentication.
    """
    user_id = str(current_user["user_id"])
    result = amout_transactions.launch_payment(
        db=db,
        user_id=user_id,
        plan=request.plan,
        entered_credits=request.entered_credits,
    )
    return result


@router.post("/verify", response_model=PaymentVerifyResponse)
def verify_payment(
    request: PaymentVerifyRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Verifies payment completion:
    - 🔒 Requires JWT Authentication.
    - If status == 'SUCCESS': verifies Razorpay HMAC signature and credits user wallet.
    - If status != 'SUCCESS': marks payment failed.
    """
    result = amout_transactions.verify_payment(
        db=db,
        payment_id=str(request.payment_id),
        status=request.status,
        razorpay_order_id=request.razorpay_order_id,
        razorpay_payment_id=request.razorpay_payment_id,
        razorpay_signature=request.razorpay_signature,
        transaction_ref=request.transaction_ref,
        user_id=str(current_user["user_id"]),
    )
    return result


@router.get("/balance", response_model=WalletBalanceResponse)
def get_authenticated_wallet_balance(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Fetches the authenticated user's wallet credit balance.
    🔒 Requires JWT Authentication.
    """
    user_id = str(current_user["user_id"])
    balance = amout_transactions.get_user_wallet(db, user_id)
    return {
        "user_id": user_id,
        "wallet": balance
    }


