# ───────────────────────────────────────────────
# CREATE - Any authenticated user can create
# ───────────────────────────────────────────────
async def create_product_item(
    data: ProductItemCreate,
    seller_id: UUID
) -> ProductItemResponse:
    try:
        item_data = data.dict(exclude_unset=True)
        item_data["seller_id"] = str(seller_id)
        item_data["stock"] = data.stock
        item_data["total_sold"] = 0

        resp = await supabase.table("product_items").insert(item_data).execute()

        if not resp.data:
            raise HTTPException(500, "Failed to create product item")

        return ProductItemResponse(**resp.data[0])

    except Exception as e:
        raise HTTPException(500, f"Create product failed: {str(e)}")


# ───────────────────────────────────────────────
# READ - Get single item (public)
# ───────────────────────────────────────────────
async def get_product_item(item_id: UUID) -> ProductItemResponse:
    item = await supabase.table("product_items")\
        .select("*")\
        .eq("id", str(item_id))\
        .eq("is_deleted", False)\
        .single()\
        .execute()

    if not item.data:
        raise HTTPException(404, "Product item not found or deleted")

    return ProductItemResponse(**item.data)


# ───────────────────────────────────────────────
# READ - Seller's own items
# ───────────────────────────────────────────────
async def get_my_product_items(seller_id: UUID) -> List[ProductItemResponse]:
    items = await supabase.table("product_items")\
        .select("*")\
        .eq("seller_id", str(seller_id))\
        .eq("is_deleted", False)\
        .order("created_at", desc=True)\
        .execute()

    return [ProductItemResponse(**item) for item in items.data]


# ───────────────────────────────────────────────
# UPDATE - Only owner can update
# ───────────────────────────────────────────────
async def update_product_item(
    item_id: UUID,
    data: ProductItemUpdate,
    seller_id: UUID
) -> ProductItemResponse:
    # Check ownership
    item = await supabase.table("product_items")\
        .select("seller_id")\
        .eq("id", str(item_id))\
        .single()\
        .execute()

    if not item.data or item.data["seller_id"] != str(seller_id):
        raise HTTPException(403, "Not your product item")

    update_data = data.dict(exclude_unset=True)
    if not update_data:
        raise HTTPException(400, "No fields to update")

    resp = await supabase.table("product_items")\
        .update(update_data)\
        .eq("id", str(item_id))\
        .execute()

    return ProductItemResponse(**resp.data[0])


# ───────────────────────────────────────────────
# DELETE - Soft delete (only owner)
# ───────────────────────────────────────────────
async def delete_product_item(item_id: UUID, seller_id: UUID) -> dict:
    item = await supabase.table("product_items")\
        .select("seller_id")\
        .eq("id", str(item_id))\
        .single()\
        .execute()

    if not item.data or item.data["seller_id"] != str(seller_id):
        raise HTTPException(403, "Not your product item")

    await supabase.table("product_items")\
        .update({"is_deleted": True})\
        .eq("id", str(item_id))\
        .execute()

    return {"success": True, "message": "Product item deleted (archived)"}


# Initiate payment (single item + quantity)
async def initiate_product_payment(
    data: ProductOrderCreate,
    buyer_id: UUID
) -> dict:
    try:
        # Fetch the product
        item_resp = await supabase.table("product_items")\
            .select("id, seller_id, price, stock, in_stock, sizes, colors")\
            .eq("id", str(data.item_id))\
            .single()\
            .execute()

        if not item_resp.data or not item_resp.data["in_stock"]:
            raise HTTPException(400, "Product not available or out of stock")

        item = item_resp.data

        if item["stock"] < data.quantity:
            raise HTTPException(400, f"Only {item['stock']} units left in stock")

        # Calculate subtotal
        subtotal = Decimal(str(item["price"])) * data.quantity

        # Delivery fee (from seller profile)
        delivery_fee = Decimal("0")
        if data.delivery_option == "VENDOR_DELIVERY":
            seller = await supabase.table("profiles")\
                .select("can_pickup_and_dropoff, pickup_and_delivery_charge")\
                .eq("id", str(item["seller_id"]))\
                .single()\
                .execute()

            if not seller.data or not seller.data["can_pickup_and_dropoff"]:
                raise HTTPException(400, "Seller does not offer delivery")

            delivery_fee = Decimal(str(seller.data["pickup_and_delivery_charge"] or 0))

        grand_total = subtotal + delivery_fee

        # Generate tx_ref
        tx_ref = f"PRODUCT-{uuid.uuid4().hex[:12].upper()}"

        # Save pending state
        pending_data = {
            "buyer_id": str(buyer_id),
            "seller_id": str(item["seller_id"]),
            "item_id": str(data.item_id),
            "quantity": data.quantity,
            "subtotal": float(subtotal),
            "delivery_fee": float(delivery_fee),
            "grand_total": float(grand_total),
            "delivery_option": data.delivery_option,
            "delivery_address": data.delivery_address,
            "additional_info": data.additional_info,
            "tx_ref": tx_ref,
            "created_at": datetime.utcnow().isoformat()
        }
        await save_pending(f"pending_product_{tx_ref}", pending_data)

        # Get customer info for SDK
        customer_info = await get_customer_contact_info()

        return {
            "tx_ref": tx_ref,
            "amount": float(grand_total),
            "public_key": settings.FLUTTERWAVE_PUBLIC_KEY,
            "currency": "NGN",
            "customer": customer_info,
            "customization": {
                "title": "Servipal Product Purchase",
                "description": f"{data.quantity} × product"
            },
            "message": "Ready for payment — use Flutterwave SDK"
        }

    except Exception as e:
        raise HTTPException(500, f"Product payment initiation failed: {str(e)}")



async def customer_confirm_product_order(order_id: UUID, customer_id: UUID) -> dict:
    try:
        order = await supabase.table("product_orders")\
            .select("id, buyer_id, seller_id, grand_total, order_status")\
            .eq("id", str(order_id))\
            .single()\
            .execute()

        if order.data["buyer_id"] != str(customer_id):
            raise HTTPException(403, "Not your order")

        if order.data["order_status"] != "READY":
            raise HTTPException(400, "Order not ready for confirmation")

        tx = await supabase.table("transactions")\
            .select("id, amount, to_user_id, status")\
            .eq("order_id", str(order_id))\
            .single()\
            .execute()

        if tx.data["status"] == "RELEASED":
            raise HTTPException(400, "Already confirmed")

        full_amount = tx.data["amount"]
        seller_id = order.data["seller_id"]

        # Get commission rate
        commission_rate = await get_commission_rate("PRODUCT")
        seller_amount = full_amount * commission_rate

        # Atomic release: escrow → seller balance (use same RPC)
        await supabase.rpc("release_order_payment", {
            "p_customer_id": str(customer_id),
            "p_vendor_id": str(seller_id),
            "p_full_amount": full_amount
        }).execute()

        # Update transaction
        await supabase.table("transactions")\
            .update({"status": "RELEASED"})\
            .eq("id", tx.data["id"])\
            .execute()

        # Update order to COMPLETED
        await supabase.table("product_orders")\
            .update({"order_status": "COMPLETED"})\
            .eq("id", str(order_id))\
            .execute()

        # Stock reduction + total_sold increment happens via trigger (see earlier)

        return {
            "success": True,
            "message": "Order confirmed! Payment released to seller.",
            "order_status": "COMPLETED",
            "amount_released": float(full_amount)
        }

    except Exception as e:
        raise HTTPException(500, f"Confirmation failed: {str(e)}")


async def vendor_product_order_action(
    order_id: UUID,
    data: ProductVendorOrderAction,
    seller_id: UUID,
    supabase: AsyncClient
) -> ProductVendorOrderActionResponse:
    """
    Seller accepts or rejects a product order.
    - Accept: move to ACCEPTED
    - Reject: cancel + refund escrow to buyer balance (via RPC)
    """
    try:
        # 1. Fetch order
        order_resp = await supabase.table("product_orders")\
            .select("id, seller_id, buyer_id, order_status, payment_status, grand_total")\
            .eq("id", str(order_id))\
            .single()\
            .execute()

        if not order_resp.data:
            raise HTTPException(404, "Product order not found")

        order = order_resp.data

        # 2. Security & validation
        if order["seller_id"] != str(seller_id):
            raise HTTPException(403, "This is not your order")

        if order["order_status"] != "PENDING":
            raise HTTPException(400, f"Order already processed (status: {order['order_status']})")

        if order["payment_status"] != "PAID":
            raise HTTPException(400, "Payment not completed")

        # 3. Process action
        if data.action == "accept":
            new_status = "ACCEPTED"
            message = "Order accepted. Preparing item for delivery/pickup."
            refund = False

        else:  # reject
            new_status = "CANCELLED"
            message = "Order rejected."
            refund = True

            # Refund escrow → buyer balance
            tx_resp = await supabase.table("transactions")\
                .select("id, amount, from_user_id, status")\
                .eq("order_id", str(order_id))\
                .single()\
                .execute()

            if not tx_resp.data:
                raise HTTPException(404, "Transaction not found")

            tx = tx_resp.data
            amount = tx["amount"]

            # Atomic refund
            await supabase.rpc("update_wallet_balance", {
                "p_user_id": tx["from_user_id"],
                "p_delta": -amount,
                "p_field": "escrow_balance"
            }).execute()

            await supabase.rpc("update_wallet_balance", {
                "p_user_id": tx["from_user_id"],
                "p_delta": amount,
                "p_field": "balance"
            }).execute()

            # Mark transaction refunded
            await supabase.table("transactions")\
                .update({"status": "REFUNDED"})\
                .eq("id", tx["id"])\
                .execute()

        # 4. Update order status
        await supabase.table("product_orders")\
            .update({"order_status": new_status})\
            .eq("id", str(order_id))\
            .execute()

        return ProductVendorOrderActionResponse(
            order_id=order_id,
            order_status=new_status,
            message=message
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(500, f"Order action failed: {str(e)}")


async def vendor_mark_product_ready(
    order_id: UUID,
    seller_id: UUID,
    supabase: AsyncClient
) -> ProductVendorMarkReadyResponse:
    """
    Seller marks product order as ready for pickup or delivery.
    """
    try:
        # 1. Fetch order
        order_resp = await supabase.table("product_orders")\
            .select("id, seller_id, order_status")\
            .eq("id", str(order_id))\
            .single()\
            .execute()

        if not order_resp.data:
            raise HTTPException(404, "Product order not found")

        order = order_resp.data

        # 2. Validation
        if order["seller_id"] != str(seller_id):
            raise HTTPException(403, "This is not your order")

        if order["order_status"] != "ACCEPTED":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot mark as ready. Current status: {order['order_status']}"
            )

        # 3. Update to READY
        await supabase.table("product_orders")\
            .update({"order_status": "READY"})\
            .eq("id", str(order_id))\
            .execute()

        return ProductVendorMarkReadyResponse(
            order_id=order_id,
            message="Order marked as ready for pickup/delivery!"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(500, f"Failed to mark ready: {str(e)}")