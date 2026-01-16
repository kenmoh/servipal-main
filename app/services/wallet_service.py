from fastapi import HTTPException, status
from uuid import UUID
from decimal import Decimal
from typing import List, Optional
from datetime import datetime

from supabase import AsyncClient
from app.schemas.wallet_schema import (
    WalletBalanceResponse,
    WalletTransactionResponse,
    TopUpRequest,
    PayWithWalletRequest,
    WalletTopUpInitiationResponse,
    CustomerInfo,
    Customization,
    PayWithWalletResponse
)
from app.config.config import settings
from app.utils.redis_utils import save_pending
from app.config.logging import logger

# ───────────────────────────────────────────────
# Get Wallet Details (balance + escrow)
# ───────────────────────────────────────────────
async def get_wallet_details(user_id: UUID, supabase: AsyncClient) -> WalletBalanceResponse:
    wallet = await supabase.table("wallets")\
        .select("balance, escrow_balance")\
        .eq("user_id", str(user_id))\
        .single()\
        .execute()

    if not wallet.data:
        raise HTTPException(404, "Wallet not found")

    balance = float(wallet.data["balance"]) if wallet.data["balance"] is not None else 0.0
    escrow_balance = float(wallet.data["escrow_balance"]) if wallet.data["escrow_balance"] is not None else 0.0

    # Round to 2 decimal places (money standard)
    balance = round(balance, 2)
    escrow_balance = round(escrow_balance, 2)

    # Fetch transactions (limit to recent 20 for performance)
    tx_resp = await supabase.table("transactions")\
        .select("*")\
        .or_(f"from_user_id.eq.{user_id},to_user_id.eq.{user_id}")\
        .order("created_at", desc=True)\
        .limit(20)\
        .execute()

    transactions = []
    for tx in tx_resp.data:
        transactions.append(WalletTransactionResponse(
            tx_ref=tx["tx_ref"],
            amount=round(float(tx["amount"]), 2) if tx["amount"] else 0.0,
            transaction_type=tx["transaction_type"],
            status=tx["status"],
            payment_method=tx["payment_method"],
            details=tx["details"],
            created_at=tx["created_at"],
            from_user_id=tx["from_user_id"],
            to_user_id=tx["to_user_id"],
            order_id=tx["order_id"]
        ))

    return WalletBalanceResponse(
        balance=balance,
        escrow_balance=escrow_balance,
        transactions=transactions
    )


# ───────────────────────────────────────────────
# Top-up Wallet (via Flutterwave or other)
# ───────────────────────────────────────────────

MAX_WALLET_LIMIT = Decimal("50000.00")

async def initiate_wallet_top_up(
    data: TopUpRequest,
    user_id: UUID,
    supabase: AsyncClient
) -> WalletTopUpInitiationResponse:
    """
    Initiate wallet top-up via Flutterwave RN SDK.
    Enforces max wallet balance of ₦50,000.
    """
    try:
        # Minimum amount validation
        if data.amount < Decimal("1000"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Minimum top-up amount is ₦1000")

        # Get current wallet balance
        wallet = await supabase.table("wallets")\
            .select("balance")\
            .eq("user_id", str(user_id))\
            .single()\
            .execute()

        if not wallet.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found")

        current_balance = Decimal(str(wallet.data["balance"]))

        # Check max limit
        new_balance = current_balance + data.amount
        if new_balance > MAX_WALLET_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Top-up would exceed the maximum wallet balance of ₦{MAX_WALLET_LIMIT:,.2f}. "
                       f"Current balance: ₦{current_balance:,.2f}. "
                       f"Maximum you can top up now: ₦{(MAX_WALLET_LIMIT - current_balance):,.2f}"
            )

        # Generate unique tx_ref
        tx_ref = f"TOPUP-{uuid.uuid4().hex[:12].upper()}"

        # Save pending state in Redis
        pending_data = {
            "user_id": str(user_id),
            "amount": float(data.amount),
            "tx_ref": tx_ref,
            "payment_method": data.payment_method,
            "created_at": datetime.utcnow().isoformat()
        }
        await save_pending(f"pending_topup_{tx_ref}", pending_data, expire=1800)

        # Get real customer info
        customer_info = await get_customer_contact_info()

        # Return SDK-ready data
        return WalletTopUpResponse(
            tx_ref=tx_ref,
            amount=float(data.amount),
            public_key=settings.FLUTTERWAVE_PUBLIC_KEY,
            currency="NGN",
            customer=customer_info,
            customization=Customization(
                title="Servipal Wallet Top-up",
                description=f"Top up ₦{data.amount:,.2f} to your wallet"
            )
        )

    except HTTPException as he:
        logger.error("Top-up initiation failed for %s: %s", tx_ref, e, exc_info=True)
        raise he
    except Exception as e:
        logger.error("Top-up initiation failed for %s: %s", tx_ref, e, exc_info=True)
        raise HTTPException(500, f"Top-up initiation failed: {str(e)}")

# ───────────────────────────────────────────────
# Pay with Wallet (deduct from balance)
# ───────────────────────────────────────────────
async def pay_with_wallet(
    user_id: UUID,
    data: PayWithWalletRequest,
    supabase: AsyncClient
) -> dict:
    """
    Deduct amount from user's wallet balance.
    - Checks sufficient balance
    - Atomic via RPC
    - Records transaction
    """
    try:
        # Get current balance
        wallet = await supabase.table("wallets")\
            .select("balance")\
            .eq("user_id", str(user_id))\
            .single()\
            .execute()

        if not wallet.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found")

        current_balance = Decimal(str(wallet.data["balance"]))

        if current_balance < data.amount:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient wallet balance")

        # Deduct from balance (atomic RPC)
        await supabase.rpc("update_wallet_balance", {
            "p_user_id": str(user_id),
            "p_delta": -data.amount,
            "p_field": "balance"
        }).execute()

        # Record transaction
        tx_ref = f"PAY-{uuid.uuid4().hex[:12].upper()}"
        await supabase.table("transactions").insert({
            "tx_ref": tx_ref,
            "amount": float(data.amount),
            "from_user_id": str(user_id),
            "to_user_id": data.to_user_id,
            "order_id": data.order_id,
            "transaction_type": data.transaction_type or "ORDER_PAYMENT",
            "status": "COMPLETED",
            "payment_method": "WALLET",
            "details": data.details or {}
        }).execute()


        return PayWithWalletResponse(
            success=True,
            message="Payment successful from wallet",
            new_balance=float(current_balance - data.amount),
            tx_ref=tx_ref
        )

    except Exception as e:
        logger.error("Wallet payment failed for %s: %s", tx_ref, e, exc_info=True)
        raise HTTPException(500, f"Wallet payment failed: {str(e)}")