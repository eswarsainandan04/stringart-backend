import uuid
import razorpay
from typing import Optional, Dict, Any, Tuple
from urllib.parse import quote
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException
from config import settings, Settings


def calculate_plan_pricing(plan: str, entered_credits: Optional[int] = None) -> Tuple[int, float]:
    """Calculates allocated credits and purchase amount based on selected plan and .env constants."""
    plan_key = plan.lower().strip()
    current_settings = Settings()
    rate = current_settings.RUPEES_PER_ART

    if plan_key == "silver":
        credits = current_settings.SILVER_CREDITS
        amount = float(credits * rate)
    elif plan_key == "gold":
        credits = current_settings.GOLD_CREDITS
        amount = float(credits * rate)
    elif plan_key == "platinum":
        credits = current_settings.PLATINUM_CREDITS
        amount = float(credits * rate)
    elif plan_key == "custom":
        if not entered_credits or entered_credits <= 0:
            raise HTTPException(
                status_code=400,
                detail="For custom plan, 'entered_credits' must be a positive integer greater than 0."
            )
        credits = int(entered_credits)
        amount = float(credits * rate)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plan '{plan}'. Allowed plans: silver, gold, platinum, custom."
        )

    return credits, amount


def launch_payment(
    db: Session,
    user_id: str,
    plan: str,
    entered_credits: Optional[int] = None
) -> Dict[str, Any]:
    """
    Initiates Razorpay payment order:
    1. Validates user existence.
    2. Calculates amount & credits based on plan.
    3. Creates Razorpay Order via Official API.
    4. Inserts pending record into PostgreSQL 'payments' table.
    5. Returns Order ID and Key ID for Mobile App checkout.
    """
    # 1. Verify user exists
    user_check = db.execute(
        text("SELECT user_id FROM users WHERE user_id = :user_id"),
        {"user_id": user_id}
    ).fetchone()

    if not user_check:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' does not exist.")

    # 2. Calculate pricing
    credits, purchase_amount = calculate_plan_pricing(plan, entered_credits)

    # 3. Generate unique payment UUID
    payment_id = uuid.uuid4()
    amount_in_paise = int(round(purchase_amount * 100))
    clean_receipt = str(payment_id).replace("-", "")[:30]

    # 4. Create Razorpay Order
    order_id = ""
    try:
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        razorpay_order = client.order.create({
            "amount": amount_in_paise,
            "currency": "INR",
            "receipt": f"rcpt_{clean_receipt[:20]}",
            "notes": {
                "user_id": str(user_id),
                "plan": plan.lower().strip(),
                "credits": str(credits),
            }
        })
        order_id = razorpay_order.get("id")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Razorpay order: {str(e)}"
        )

    # 5. Insert pending order into payments table
    db.execute(
        text("""
            INSERT INTO payments (payment_id, user_id, plan, purchase_amount, status, order_id)
            VALUES (:payment_id, :user_id, :plan, :purchase_amount, 'pending', :order_id)
        """),
        {
            "payment_id": payment_id,
            "user_id": user_id,
            "plan": plan.lower().strip(),
            "purchase_amount": purchase_amount,
            "order_id": order_id,
        }
    )
    db.commit()

    return {
        "payment_id": str(payment_id),
        "user_id": str(user_id),
        "plan": plan.lower().strip(),
        "credits": credits,
        "purchase_amount": purchase_amount,
        "order_id": order_id,
        "key_id": settings.RAZORPAY_KEY_ID,
        "currency": "INR"
    }


def verify_payment(
    db: Session,
    payment_id: str,
    status: str,
    razorpay_order_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
    razorpay_signature: Optional[str] = None,
    transaction_ref: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Verifies payment completion:
    - If status == 'SUCCESS': strictly verifies Razorpay HMAC signature and payment_id, marks payments.status='success', credits user wallet.
    - If status != 'SUCCESS': marks payments.status='failed'.
    """
    effective_ref = transaction_ref
    # 1. Query existing payment
    payment = db.execute(
        text("""
            SELECT payment_id, user_id, plan, purchase_amount, status, order_id
            FROM payments 
            WHERE payment_id = :payment_id 
            FOR UPDATE
        """),
        {"payment_id": payment_id}
    ).fetchone()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found.")

    # Ownership check: Ensure the payment belongs to the calling user
    if user_id and str(payment.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="You are not authorized to verify this payment.")

    # Prevent double-processing
    if payment.status == "success":
        wallet_bal = get_user_wallet(db, str(payment.user_id))
        return {
            "status": "success",
            "payment_id": str(payment.payment_id),
            "message": "Payment already verified successfully.",
            "wallet": wallet_bal
        }

    # 2. Process Success
    if status.upper() == "SUCCESS":
        # Production Hardening: Enforce mandatory signature and payment ID for SUCCESS status
        if not razorpay_signature or not razorpay_payment_id:
            raise HTTPException(
                status_code=400,
                detail="Razorpay payment verification requires razorpay_signature and razorpay_payment_id."
            )

        effective_order_id = razorpay_order_id or payment.order_id
        if not effective_order_id:
            raise HTTPException(
                status_code=400,
                detail="Missing order_id for signature verification."
            )

        payment_method = "razorpay"
        try:
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            client.utility.verify_payment_signature({
                "razorpay_order_id": effective_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature
            })

            # Fetch payment details from Razorpay to obtain the exact method (upi, card, netbanking, wallet)
            try:
                rzp_pay = client.payment.fetch(razorpay_payment_id)
                payment_method = rzp_pay.get("method") or "razorpay"
            except Exception as fetch_err:
                print(f"[RAZORPAY NOTICE] Could not fetch payment method: {fetch_err}")
                payment_method = "razorpay"

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Razorpay payment signature verification failed: {str(e)}"
            )

        # Calculate how many credits to allocate based on purchase_amount / rate
        current_settings = Settings()
        rate = current_settings.RUPEES_PER_ART or 1
        credits_to_add = int(float(payment.purchase_amount) / rate)

        # Update payment record with payment_method and transaction_ref
        db.execute(
            text("""
                UPDATE payments 
                SET status = 'success', 
                    order_id = COALESCE(:order_id, order_id),
                    razorpay_payment_id = :razorpay_payment_id,
                    razorpay_signature = :razorpay_signature,
                    payment_method = :payment_method,
                    transaction_ref = :transaction_ref, 
                    verified_at = CURRENT_TIMESTAMP 
                WHERE payment_id = :payment_id
            """),
            {
                "payment_id": payment_id,
                "order_id": razorpay_order_id or payment.order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
                "payment_method": payment_method,
                "transaction_ref": effective_ref or razorpay_payment_id or "RAZORPAY_VERIFIED"
            }
        )

        # Upsert into credits table
        result = db.execute(
            text("""
                INSERT INTO credits (user_id, wallet, updated_at)
                VALUES (:user_id, :credits, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    wallet = credits.wallet + EXCLUDED.wallet, 
                    updated_at = CURRENT_TIMESTAMP
                RETURNING wallet
            """),
            {
                "user_id": str(payment.user_id),
                "credits": credits_to_add
            }
        ).fetchone()

        db.commit()
        new_wallet = result[0] if result else credits_to_add

        return {
            "status": "success",
            "payment_id": str(payment.payment_id),
            "credits_added": credits_to_add,
            "payment_method": payment_method,
            "wallet": new_wallet
        }

    # 3. Process Failure / Cancel
    else:
        db.execute(
            text("""
                UPDATE payments 
                SET status = 'failed', 
                    razorpay_payment_id = :razorpay_payment_id,
                    transaction_ref = :transaction_ref,
                    verified_at = CURRENT_TIMESTAMP 
                WHERE payment_id = :payment_id
            """),
            {
                "payment_id": payment_id,
                "razorpay_payment_id": razorpay_payment_id,
                "transaction_ref": effective_ref or "PAYMENT_CANCELLED"
            }
        )
        db.commit()

        wallet_bal = get_user_wallet(db, str(payment.user_id))
        return {
            "status": "failed",
            "payment_id": str(payment.payment_id),
            "message": "Payment was marked as failed or cancelled.",
            "wallet": wallet_bal
        }


def get_user_wallet(db: Session, user_id: str) -> int:
    """Returns current wallet credits from credits table. Returns 0 if not present."""
    row = db.execute(
        text("SELECT wallet FROM credits WHERE user_id = :user_id"),
        {"user_id": user_id}
    ).fetchone()

    if row is not None:
        return int(row[0])

    return 0
