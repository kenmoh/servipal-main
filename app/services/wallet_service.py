from fastapi import HTTPException, status
from uuid import UUID
from decimal import Decimal
from datetime import datetime
import uuid

from supabase import AsyncClient
from app.schemas.wallet_schema import (
    WalletBalanceResponse,
    WalletTransactionResponse,
    TopUpRequest,
    PayWithWalletRequest,
    WalletTopUpInitiationResponse,
    CustomerInfo,
    Customization,
    PayWithWalletResponse,
)
from app.config.config import settings
from app.utils.redis_utils import save_pending
from app.config.logging import logger
from app.dependencies.auth import get_customer_contact_info


# ───────────────────────────────────────────────
# Get Wallet Details (balance + escrow)
# ───────────────────────────────────────────────
async def get_wallet_details(
    user_id: UUID, supabase: AsyncClient
) -> WalletBalanceResponse:
    logger.debug("get_wallet_details_requested", user_id=str(user_id))
    wallet = (
        await supabase.table("wallets")
        .select("balance, escrow_balance")
        .eq("user_id", str(user_id))
        .single()
        .execute()
    )

    if not wallet.data:
        logger.warning("wallet_not_found", user_id=str(user_id))
        raise HTTPException(404, "Wallet not found")

    balance = (
        float(wallet.data["balance"]) if wallet.data["balance"] is not None else 0.0
    )
    escrow_balance = (
        float(wallet.data["escrow_balance"])
        if wallet.data["escrow_balance"] is not None
        else 0.0
    )

    # Round to 2 decimal places (money standard)
    balance = Decimal(round(balance, 2))
    escrow_balance = Decimal(round(escrow_balance, 2))

    # Fetch transactions (limit to recent 20 for performance)
    tx_resp = (
        await supabase.table("transactions")
        .select("*")
        .or_(f"from_user_id.eq.{user_id},to_user_id.eq.{user_id}")
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )

    transactions = []
    for tx in tx_resp.data:
        transactions.append(
            WalletTransactionResponse(
                tx_ref=tx["tx_ref"],
                amount=tx["amount"] if tx["amount"] else Decimal(0.0),
                transaction_type=tx["transaction_type"],
                status=tx["status"],
                payment_method=tx["payment_method"],
                created_at=tx["created_at"],
                from_user_id=tx["from_user_id"],
                to_user_id=tx["to_user_id"],
                order_id=tx["order_id"],
            )
        )

    return WalletBalanceResponse(
        balance=balance, escrow_balance=escrow_balance, transactions=transactions
    )


# ───────────────────────────────────────────────
# Top-up Wallet (via Flutterwave or other)
# ───────────────────────────────────────────────

MAX_WALLET_LIMIT = Decimal("50000.00")


async def initiate_wallet_top_up(
    data: TopUpRequest,
    user_id: UUID,
    supabase: AsyncClient,
) -> WalletTopUpInitiationResponse:
    logger.info(
        "wallet_topup_initiated", user_id=str(user_id), amount=float(data.amount)
    )
    """
    Initiate wallet top-up via Flutterwave RN SDK.
    Enforces max wallet balance of ₦50,000.
    """
    try:
        # Minimum amount validation
        if data.amount < Decimal("1000"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Minimum top-up amount is ₦1000",
            )

        # Get current wallet balance
        wallet = (
            await supabase.table("wallets")
            .select("balance")
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        if not wallet.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found"
            )

        current_balance = Decimal(str(wallet.data["balance"]))

        # Check max limit
        new_balance = current_balance + data.amount
        if new_balance > MAX_WALLET_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Top-up would exceed the maximum wallet balance of ₦{MAX_WALLET_LIMIT:,.2f}. "
                f"Current balance: ₦{current_balance:,.2f}. "
                f"Maximum you can top up now: ₦{(MAX_WALLET_LIMIT - current_balance):,.2f}",
            )

        # Generate unique tx_ref
        tx_ref = f"TOPUP-{uuid.uuid4().hex[:12].upper()}"

        # Save pending state in Redis
        pending_data = {
            "user_id": str(user_id),
            "amount": float(data.amount),
            "tx_ref": tx_ref,
            "payment_method": data.payment_method,
            "created_at": datetime.now().isoformat(),
        }
        await save_pending(f"pending_topup_{tx_ref}", pending_data, expire=1800)

        # Get real customer info
        customer_info = await get_customer_contact_info()

        # Return SDK-ready data
        result = WalletTopUpInitiationResponse(
            tx_ref=tx_ref,
            amount=float(data.amount),
            public_key=settings.FLUTTERWAVE_PUBLIC_KEY,
            currency="NGN",
            customer=CustomerInfo(email=customer_info['email'], name=customer_info['phone']),
            customization=Customization(
                title="Servipal Wallet Top-up",
                description=f"Top up ₦{data.amount:,.2f} to your wallet",
            ),
        )

        logger.info(
            "wallet_topup_initiation_success",
            user_id=str(user_id),
            tx_ref=tx_ref,
            amount=float(data.amount),
        )
        return result

    except HTTPException as he:
        logger.error(
            "wallet_topup_initiation_failed",
            user_id=str(user_id),
            error=str(he.detail),
            exc_info=True,
        )
        raise he
    except Exception as e:
        logger.error(
            "wallet_topup_initiation_error",
            user_id=str(user_id),
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(500, f"Top-up initiation failed: {str(e)}")


# ───────────────────────────────────────────────
# Pay with Wallet (deduct from balance)
# ───────────────────────────────────────────────
async def pay_with_wallet(
    user_id: UUID,
    data: PayWithWalletRequest,
    supabase: AsyncClient
) -> PayWithWalletResponse:
    """
    Deduct amount from user's wallet balance.
    - Checks sufficient balance
    - Atomic via RPC
    - Records trans action
    """
    logger.info(
        "wallet_payment_attempt",
        user_id=str(user_id),
        amount=float(data.amount),
        order_id=str(data.order_id) if data.order_id else None,
    )
    try:
        # Get current balance
        wallet = (
            await supabase.table("wallets")
            .select("balance")
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        if not wallet.data:
            logger.warning("wallet_not_found_for_payment", user_id=str(user_id))
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found"
            )

        old_balance = Decimal(str(wallet.data["balance"]))
        current_balance = old_balance

        if current_balance < data.amount:
            logger.warning(
                "insufficient_wallet_balance",
                user_id=str(user_id),
                balance=float(current_balance),
                requested=float(data.amount),
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient wallet balance",
            )

        # Deduct from balance (atomic RPC)
        await supabase.rpc(
            "update_wallet_balance",
            {"p_user_id": str(user_id), "p_delta": -data.amount, "p_field": "balance"},
        ).execute()

        # Get new balance after deduction
        wallet_after = (
            await supabase.table("wallets")
            .select("balance")
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        new_balance = Decimal(str(wallet_after.data["balance"]))

        # Record transaction
        tx_ref = f"PAY-{uuid.uuid4().hex[:12].upper()}"
        await (
            supabase.table("transactions")
            .insert(
                {
                    "tx_ref": tx_ref,
                    "amount": float(data.amount),
                    "from_user_id": str(user_id),
                    "to_user_id": data.to_user_id,
                    "order_id": data.order_id,
                    "transaction_type": data.transaction_type or "ORDER_PAYMENT",
                    "status": "COMPLETED",
                    "payment_method": "WALLET",
                    "details": data.details or {},
                }
            )
            .execute()
        )


        logger.info(
            "wallet_payment_success",
            user_id=str(user_id),
            tx_ref=tx_ref,
            amount=float(data.amount),
            new_balance=float(new_balance),
        )
        return PayWithWalletResponse(
            success=True,
            message="Payment successful from wallet",
            new_balance=new_balance,
            tx_ref=tx_ref,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "wallet_payment_error", user_id=str(user_id), error=str(e), exc_info=True
        )
        raise HTTPException(500, f"Wallet payment failed: {str(e)}")
