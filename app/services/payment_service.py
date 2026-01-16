from app.utils.redis_utils import  get_pending, delete_pending
from supabase import AsyncClient
from app.utils.commission import get_commission_rate
from app.config.logging import logger

async def process_successful_delivery_payment(
    tx_ref: str,
    paid_amount: float,
    flw_ref: str,
        supabase: AsyncClient
):
    pending_key = f"pending_delivery_{tx_ref}"
    pending = await get_pending(pending_key)

    if not pending:
        return  # already processed or expired

    expected_fee = pending["delivery_fee"]
    sender_id = pending["sender_id"]
    delivery_data = pending["delivery_data"]

    if paid_amount != expected_fee:
        await delete_pending(pending_key)
        return

    try:
        # Create delivery_order (no rider yet)
        order_resp = await supabase.table("delivery_orders").insert({
            "sender_id": sender_id,
            "receiver_phone": delivery_data["receiver_phone"],
            "pickup_location": delivery_data["pickup_location"],
            "destination": delivery_data["destination"],
            "pickup_coordinates": f"POINT({delivery_data['pickup_coordinates'][1]} {delivery_data['pickup_coordinates'][0]})",
            "dropoff_coordinates": f"POINT({delivery_data['dropoff_coordinates'][1]} {delivery_data['dropoff_coordinates'][0]})",
            "additional_info": delivery_data.get("additional_info"),
            "delivery_type": delivery_data["delivery_type"],
            "total_price": expected_fee,
            "grand_total": expected_fee,
            "amount_due_dispatch": expected_fee * await get_commission_rate("DELIVERY", supabase),
            "order_status": "PAID_NEEDS_RIDER",
            "payment_status": "PAID",
            "escrow_status": "HELD"
        }).execute()

        order_id = order_resp.data[0]["id"]

        # Create an initial deliveries record
        await supabase.table("deliveries").insert({
            "order_id": order_id,
            "order_type": "DELIVERY",
            "sender_id": sender_id,
            "pickup_coordinates": f"POINT({delivery_data['pickup_coordinates'][1]} {delivery_data['pickup_coordinates'][0]})",
            "dropoff_coordinates": f"POINT({delivery_data['dropoff_coordinates'][1]} {delivery_data['dropoff_coordinates'][0]})",
            "origin": delivery_data["pickup_location"],
            "destination": delivery_data["destination"],
            "delivery_fee": expected_fee,
            "distance": pending.get("distance_km", 0),
            "delivery_status": "PAID_NEEDS_RIDER",
            "delivery_type": delivery_data["delivery_type"],
            "amount_due_dispatch": expected_fee * await get_commission_rate("DELIVERY", supabase)
        }).execute()

        # Hold fee in sender escrow
        await supabase.rpc("update_wallet_balance", {
            "p_user_id": sender_id,
            "p_delta": expected_fee,
            "p_field": "escrow_balance"
        }).execute()

        # Create transaction
        await supabase.table("transactions").insert({
            "tx_ref": tx_ref,
            "amount": expected_fee,
            "from_user_id": sender_id,
            "to_user_id": None,
            "order_id": order_id,
            "transaction_type": "DELIVERY_FEE",
            "status": "HELD",
            "payment_status": "PAID",
            "payment_method": "FLUTTERWAVE",
            "details": {"flw_ref": flw_ref}
        }).execute()

        await delete_pending(pending_key)

    except Exception as e:
        logger.error("Processing error for %s: %s", tx_ref, e, exc_info=True)
        await delete_pending(pending_key)
        raise

async def process_successful_food_payment(
    tx_ref: str,
    paid_amount: float,
    flw_ref: str,
        supabase: AsyncClient
):
    """
    Webhook handler for successful food order payment.
    - Fetches pending state from Redis
    - Validates amount
    - Creates food_orders + food_order_items
    - Holds full amount in customer escrow (via RPC)
    - Creates transaction record (HELD)
    - Cleans up Redis
    """
    pending_key = f"pending_food_{tx_ref}"
    pending = await get_pending(pending_key)

    if not pending:
        return None

    expected_total = pending["grand_total"]
    customer_id = pending["customer_id"]
    vendor_id = pending["vendor_id"]
    order_data = pending["order_data"]  # contains items, subtotal, delivery_fee, etc.

    # Idempotency + amount validation
    existing_tx = await supabase.table("transactions")\
        .select("id")\
        .eq("tx_ref", tx_ref)\
        .execute()

    if existing_tx.data:
        await delete_pending(pending_key)
        return {"status": "already_processed"}

    if paid_amount != expected_total:
        await delete_pending(pending_key)
        # Optional: log mismatch or trigger refund
        return {"status": "amount_mismatch"}

    try:
        # 1. Create food_order record
        order_resp = await supabase.table("food_orders").insert({
            "customer_id": customer_id,
            "vendor_id": vendor_id,
            "subtotal": order_data["subtotal"],
            "delivery_fee": order_data["delivery_fee"],
            "grand_total": expected_total,
            "cooking_instructions": order_data.get("cooking_instructions"),
            "delivery_option": order_data["delivery_option"],
            "order_status": "PENDING",
            "payment_status": "PAID",
            "escrow_status": "HELD"
        }).execute()

        order_id = order_resp.data[0]["id"]

        # 2. Create food_order_items (multiple rows)
        for item in order_data["items"]:
            await supabase.table("food_order_items").insert({
                "order_id": order_id,
                "item_id": item["item_id"],
                "quantity": item["quantity"],
                "sizes": item.get("sizes", []),
                "colors": item.get("colors", [])
            }).execute()

        # 3. Hold full amount in customer escrow (positive delta)
        await supabase.rpc("update_wallet_balance", {
            "p_user_id": customer_id,
            "p_delta": expected_total,
            "p_field": "escrow_balance"
        }).execute()

        # 4. Create transaction record (HELD)
        await supabase.table("transactions").insert({
            "tx_ref": tx_ref,
            "amount": expected_total,
            "from_user_id": customer_id,
            "to_user_id": vendor_id,
            "order_id": order_id,
            "transaction_type": "FOOD_ORDER",
            "status": "HELD",
            "payment_status": "PAID",
            "payment_method": "FLUTTERWAVE",
            "details": {"flw_ref": flw_ref}
        }).execute()

        # 5. Cleanup Redis
        await delete_pending(pending_key)

        return {"status": "success", "order_id": str(order_id)}

    except Exception as e:
        # Critical: attempt refund on error (implement refund_flutterwave if needed)
        logger.error("Food payment processing error for %s: %s", tx_ref, e, exc_info=True)
        await delete_pending(pending_key)
        # Optional: await refund_flutterwave(tx_ref)
        raise


async def process_successful_topup_payment(
    tx_ref: str,
    paid_amount: float,
    flw_ref: str,
    supabase: AsyncClient
):
    pending_key = f"pending_topup_{tx_ref}"
    pending = await get_pending(pending_key)

    if not pending:
        return  # already processed

    expected_amount = pending["amount"]
    user_id = pending["user_id"]

    if paid_amount != expected_amount:
        await delete_pending(pending_key)
        return

    try:
        # Add funds to wallet balance (atomic RPC)
        await supabase.rpc("update_wallet_balance", {
            "p_user_id": user_id,
            "p_delta": paid_amount,
            "p_field": "balance"
        }).execute()

        # Record transaction
        await supabase.table("transactions").insert({
            "tx_ref": tx_ref,
            "amount": paid_amount,
            "from_user_id": user_id,  # external payment
            "to_user_id": user_id,
            "transaction_type": "TOP_UP",
            "status": "COMPLETED",
            "payment_method": "FLUTTERWAVE",
            "details": {"flw_ref": flw_ref}
        }).execute()

        await log_audit_event(
            supabase,
            entity_type="WALLET",
            entity_id=user_id,
            action="TOP_UP",
            old_value={"balance": old_balance},
            new_value={"balance": new_balance},
            change_amount=amount,
            actor_id=user_id,
            actor_type="USER",
            notes=f"Top-up of {amount} via Flutterwave"
        )

        await delete_pending(pending_key)

    except Exception as e:
        logger.error("Top-up processing error for %s: %s", tx_ref, e, exc_info=True)
        await delete_pending(pending_key)
        raise


async def process_successful_product_payment(
    tx_ref: str,
    paid_amount: float,
    flw_ref: str
):
    pending_key = f"pending_product_{tx_ref}"
    pending = await get_pending(pending_key)

    if not pending:
        return

    expected_total = pending["grand_total"]
    buyer_id = pending["buyer_id"]
    seller_id = pending["seller_id"]
    item_id = pending["item_id"]
    quantity = pending["quantity"]

    # Idempotency check
    existing = await supabase.table("transactions")\
        .select("id")\
        .eq("tx_ref", tx_ref)\
        .execute()

    if existing.data:
        await delete_pending(pending_key)
        return

    if paid_amount != expected_total:
        await delete_pending(pending_key)
        return

    try:
        # Create product_order
        order_resp = await supabase.table("product_orders").insert({
            "buyer_id": buyer_id,
            "seller_id": seller_id,
            "subtotal": pending["subtotal"],
            "delivery_fee": pending["delivery_fee"],
            "grand_total": grand_total,
            "delivery_option": pending["delivery_option"],
            "delivery_address": pending["delivery_address"],
            "additional_info": pending["additional_info"],
            "order_status": "PENDING",
            "payment_status": "PAID",
            "escrow_status": "HELD"
        }).execute()

        order_id = order_resp.data[0]["id"]

        # Create product_order_item (single item)
        await supabase.table("product_order_items").insert({
            "order_id": order_id,
            "item_id": item_id,
            "quantity": quantity
        }).execute()

        # Hold full amount in buyer escrow
        await supabase.rpc("update_wallet_balance", {
            "p_user_id": buyer_id,
            "p_delta": expected_total,
            "p_field": "escrow_balance"
        }).execute()

        # Create transaction record
        await supabase.table("transactions").insert({
            "tx_ref": tx_ref,
            "amount": expected_total,
            "from_user_id": buyer_id,
            "to_user_id": seller_id,
            "order_id": order_id,
            "transaction_type": "PRODUCT_ORDER",
            "status": "HELD",
            "payment_method": "FLUTTERWAVE",
            "details": {"flw_ref": flw_ref}
        }).execute()

        await delete_pending(pending_key)

    except Exception as e:
        print(f"Product payment processing error for {tx_ref}: {e}")
        await delete_pending(pending_key)
        raise