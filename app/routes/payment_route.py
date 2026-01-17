from fastapi import APIRouter, Request, Header, HTTPException, Depends
from supabase import AsyncClient
from app.services.payment_service import (
    process_successful_delivery_payment,
    process_successful_food_payment,
    process_successful_topup_payment,
)
from app.config.config import settings
from app.database.supabase import get_supabase_client
from app.worker import queue
from rq import Retry
from app.config.logging import logger

router = APIRouter(tags=["payment-webhook"], prefix="/api/v1/payment")


@router.post("/webhook")
async def flutterwave_webhook(
    request: Request,
    verif_hash: str = Header(None, alias="verif-hash"),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Handle Flutterwave payment webhooks.
    
    Verifies signature, checks idempotency, and queues processing in background.
    
    Args:
        request (Request): The raw request.
        verif_hash (str): The verification hash header.
        
    Returns:
        dict: Processing status.
    """
    # 1. Verify webhook signature (Flutterwave sends verif-hash header)
    if verif_hash != settings.FLW_SECRET_HASH:
        logger.warning(
            "webhook_signature_invalid",
            client_ip=request.client.host if request.client else None,
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # 2. Parse payload
    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data", {})

    logger.info(
        "webhook_received",
        event=event,
        status=data.get("status"),
        tx_ref=data.get("tx_ref"),
    )

    # 3. Only process successful charge events
    if event != "charge.completed" or data.get("status") != "successful":
        logger.debug("webhook_event_ignored", event=event, status=data.get("status"))
        return {"status": "ignored"}

    tx_ref = data.get("tx_ref")
    paid_amount = data.get("amount")
    flw_ref = data.get("id")

    if not tx_ref:
        logger.warning("webhook_missing_tx_ref", payload=payload)
        return {"status": "error", "message": "Missing tx_ref"}

    # 4. Idempotency check (prevent double-processing)
    existing = (
        await supabase.table("transactions").select("id").eq("tx_ref", tx_ref).execute()
    )

    if existing.data:
        logger.info("webhook_already_processed", tx_ref=tx_ref)
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
        request,  # Pass request for audit logging
        retry=Retry(
            max=5, interval=[30, 60, 120, 300, 600]
        ),  # 30s → 1min → 2min → 5min → 10min
    )

    from app.config.logging import logger

    logger.info("payment_webhook_queued", tx_ref=tx_ref, handler=handler.__name__)
    return {"status": "queued_with_retry"}
