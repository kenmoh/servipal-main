from fastapi import APIRouter, Request, Header, HTTPException, Depends
from supabase import AsyncClient
from app.services.payment_service import (
    process_successful_delivery_payment,
process_successful_food_payment, process_successful_topup_payment
)
from app.config.config import settings
from app.database.supabase import get_supabase_client
from app.worker import queue
from rq import Retry

router = APIRouter(tags=["payment-webhook"], prefix="/api/v1/payment")

@router.post("/webhook")
async def flutterwave_webhook(
    request: Request,
    verif_hash: str = Header(None, alias="verif-hash"),
        supabase: AsyncClient = Depends(get_supabase_client)
):
    """
    Flutterwave webhook handler.
    Verifies signature, checks idempotency, queues processing in the background.
    """
    # 1. Verify webhook signature (Flutterwave sends verif-hash header)
    if verif_hash != settings.FLW_SECRET_HASH:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # 2. Parse payload
    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data", {})

    # 3. Only process successful charge events
    if event != "charge.completed" or data.get("status") != "successful":
        return {"status": "ignored"}

    tx_ref = data.get("tx_ref")
    paid_amount = data.get("amount")
    flw_ref = data.get("id")

    if not tx_ref:
        return {"status": "error", "message": "Missing tx_ref"}

    # 4. Idempotency check (prevent double-processing)
    existing = await supabase.table("transactions")\
        .select("id")\
        .eq("tx_ref", tx_ref)\
        .execute()

    if existing.data:
        return {"status": "already_processed", "tx_ref": tx_ref}

    # 5. Determine which handler is based on the tx_ref prefix
    handler = None
    if tx_ref.startswith("DEL-"):
        handler = process_successful_delivery_payment
    elif tx_ref.startswith("FOOD-"):
        handler = process_successful_food_payment
    elif tx_ref.startswith("TOPUP-"):
        handler = process_successful_topup_payment
    # elif tx_ref.startswith("LAUNDRY-"):
    #     handler = process_successful_laundry_payment
    # Add PRODUCT-later if needed

    if not handler:
        return {"status": "unknown_transaction_type"}

    # 6. Queue the job with retry (5 attempts, exponential backoff)
    queue.enqueue(
        handler,
        tx_ref,
        paid_amount,
        flw_ref,
        retry=Retry(max=5, interval=[30, 60, 120, 300, 600])  # 30s → 1min → 2min → 5min → 10min
    )

    return {"status": "queued_with_retry"}