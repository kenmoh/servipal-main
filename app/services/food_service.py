import uuid
from supabase.client import AsyncClient
from fastapi import HTTPException, UploadFile
from app.schemas.food_schemas import *
from app.utils.storage import upload_to_supabase_storage
from app.utils.redis_utils import save_pending
from app.config.config import settings
from app.dependencies.auth import get_customer_contact_info


# ───────────────────────────────────────────────
# 1. Get Vendors (Nearby or All)
# ───────────────────────────────────────────────
async def get_food_vendors(supabase: AsyncClient, lat: Optional[float] = None, lng: Optional[float] = None) -> List[VendorCardResponse]:
    params = {"near_lat": lat, "near_lng": lng} if lat and lng else {}
    resp = await supabase.rpc("get_food_vendors", params).execute()
    return [VendorCardResponse(**v) for v in resp.data]


# ───────────────────────────────────────────────
# 2. Get Vendor Detail + Menu
# ───────────────────────────────────────────────
async def get_vendor_detail(vendor_id: UUID, supabase: AsyncClient) -> VendorDetailResponse:
    resp = await supabase.rpc("get_vendor_detail_with_menu", {"vendor_user_id": str(vendor_id)}).execute()

    if not resp.data:
        raise HTTPException(404, "Vendor not found")

    vendor_data = resp.data[0]["vendor_json"]
    menu_map = {}

    for row in resp.data:
        if row["category_json"]:
            cat = row["category_json"]
            if cat["id"] not in menu_map:
                menu_map[cat["id"]] = {
                    "category": FoodCategoryResponse(**cat),
                    "items": []
                }
            if row["item_json"]:
                menu_map[cat["id"]]["items"].append(FoodItemResponse(**row["item_json"]))

    return VendorDetailResponse(
        **vendor_data,
        categories=[m["category"] for m in menu_map.values()],
        menu=[item for m in menu_map.values() for item in m["items"]]
    )


# ───────────────────────────────────────────────
# 3. Vendor Accept/Reject Order
# ───────────────────────────────────────────────
async def vendor_food_order_action(
        order_id: UUID,
        vendor_id: UUID,
        supabase: AsyncClient,
        action: Literal["accept", "reject"],
) -> dict:
    try:
        order = await supabase.table("food_orders") \
            .select("id, vendor_id, order_status, payment_status, grand_total") \
            .eq("id", str(order_id)) \
            .single() \
            .execute()

        if order.data["vendor_id"] != str(vendor_id):
            raise HTTPException(403, "Not your order")

        if order.data["order_status"] != "PENDING":
            raise HTTPException(400, "Order already processed")

        if order.data["payment_status"] != "PAID":
            raise HTTPException(400, "Payment not completed")

        if action == "accept":
            new_status = "PREPARING"
            message = "Order accepted. Preparing food now."
        else:
            new_status = "CANCELLED"
            message = "Order rejected."

            # Refund escrow → customer balance
            tx = await supabase.table("transactions") \
                .select("id, amount, from_user_id") \
                .eq("order_id", str(order_id)) \
                .single() \
                .execute()

            amount = tx.data["amount"]

            # Use RPC for atomic refund
            await supabase.rpc("update_wallet_balance", {
                "p_user_id": tx.data["from_user_id"],
                "p_delta": -amount,
                "p_field": "escrow_balance"
            }).execute()

            await supabase.rpc("update_wallet_balance", {
                "p_user_id": tx.data["from_user_id"],
                "p_delta": amount,
                "p_field": "balance"
            }).execute()

            await supabase.table("transactions") \
                .update({"status": "REFUNDED"}) \
                .eq("id", tx.data["id"]) \
                .execute()

        await supabase.table("food_orders") \
            .update({"order_status": new_status}) \
            .eq("id", str(order_id)) \
            .execute()

        return {
            "success": True,
            "message": message,
            "order_status": new_status
        }

    except Exception as e:
        raise HTTPException(500, f"Action failed: {str(e)}")


# ───────────────────────────────────────────────
# 4. Vendor Mark Order Ready
# ───────────────────────────────────────────────
async def vendor_mark_food_order_ready(order_id: UUID, vendor_id: UUID, supabase: AsyncClient) -> dict:
    try:
        order = await supabase.table("food_orders") \
            .select("id, vendor_id, order_status") \
            .eq("id", str(order_id)) \
            .single() \
            .execute()

        if order.data["vendor_id"] != str(vendor_id):
            raise HTTPException(403, "Not your order")

        if order.data["order_status"] != "PREPARING":
            raise HTTPException(400, "Order must be in PREPARING status")

        await supabase.table("food_orders") \
            .update({"order_status": "READY"}) \
            .eq("id", str(order_id)) \
            .execute()

        return {
            "success": True,
            "message": "Order marked as ready for pickup/delivery!",
            "order_status": "READY"
        }

    except Exception as e:
        raise HTTPException(500, f"Failed to mark ready: {str(e)}")


# ───────────────────────────────────────────────
# 5. Customer Confirm Food Order (Payment Release)
# ───────────────────────────────────────────────
async def customer_confirm_food_order(order_id: UUID, customer_id: UUID, supabase: AsyncClient) -> dict:
    try:
        order = await supabase.table("food_orders") \
            .select("id, customer_id, vendor_id, grand_total, order_status") \
            .eq("id", str(order_id)) \
            .single() \
            .execute()

        if order.data["customer_id"] != str(customer_id):
            raise HTTPException(403, "Not your order")

        if order.data["order_status"] != "READY":
            raise HTTPException(400, "Order not ready for confirmation yet")

        tx = await supabase.table("transactions") \
            .select("id, amount, to_user_id, status") \
            .eq("order_id", str(order_id)) \
            .single() \
            .execute()

        if tx.data["status"] == "RELEASED":
            raise HTTPException(400, "Already confirmed")

        full_amount = tx.data["amount"]
        vendor_id = order.data["vendor_id"] or tx.data["to_user_id"]

        # Atomic release: deduct customer escrow + credit vendor balance
        await supabase.rpc("release_order_payment", {
            "p_customer_id": str(customer_id),
            "p_vendor_id": str(vendor_id),
            "p_full_amount": full_amount
        }).execute()

        await supabase.table("transactions") \
            .update({"status": "RELEASED"}) \
            .eq("id", tx.data["id"]) \
            .execute()

        await supabase.table("food_orders") \
            .update({"order_status": "COMPLETED"}) \
            .eq("id", str(order_id)) \
            .execute()

        return {
            "success": True,
            "message": "Order confirmed! Payment released to vendor.",
            "order_status": "COMPLETED",
            "amount_released": full_amount
        }

    except Exception as e:
        raise HTTPException(500, f"Confirmation failed: {str(e)}")


# ───────────────────────────────────────────────
# 6. Create Food Item (with Images)
# ───────────────────────────────────────────────
async def create_food_item_with_images(
        name: str,
        description: Optional[str],
        price: Decimal,
        category_id: Optional[UUID],
        sizes: List[str],
        images: List[UploadFile],
        vendor_id: UUID,
        supabase: AsyncClient
) -> dict:
    try:
        item_data = {
            "vendor_id": str(vendor_id),
            "name": name,
            "description": description,
            "price": float(price),
            "category_id": str(category_id) if category_id else None,
            "sizes": sizes
        }

        resp = await supabase.table("food_items").insert(item_data).execute()
        item_id = resp.data[0]["id"]

        image_urls = []
        for file in images:
            url = await upload_to_supabase_storage(
                file=file,
                bucket="menu-images",
                folder=f"vendor_{vendor_id}/item_{item_id}",
                supabase=supabase
            )
            image_urls.append(url)

        if image_urls:
            await supabase.table("food_items") \
                .update({"images": image_urls}) \
                .eq("id", item_id) \
                .execute()

        return {
            "success": True,
            "item_id": item_id,
            "message": "Item created with images",
            "image_urls": image_urls
        }

    except Exception as e:
        raise HTTPException(500, f"Failed to create item: {str(e)}")

# ───────────────────────────────────────────────
# 7. Update Food Item (with Images)
# ───────────────────────────────────────────────
async def update_food_item(
    item_id: UUID,
    data: FoodItemUpdate,
    vendor_id: UUID,
    supabase: AsyncClient
) -> FoodItemDetailResponse:
    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(400, "No data provided")


    # Validate ownership
    item = await supabase.table("food_items")\
        .select("vendor_id")\
        .eq("id", str(item_id))\
        .single()\
        .execute()

    if item.data["vendor_id"] != str(vendor_id):
        raise HTTPException(403, "Not your item")

    resp = await supabase.table("food_items")\
        .update(update_data)\
        .eq("id", str(item_id))\
        .execute()

    return FoodItemDetailResponse(**resp.data[0])

# ───────────────────────────────────────────────
# 8. Delete Food Item
# ───────────────────────────────────────────────
async def delete_food_item(
    item_id: UUID,
    vendor_id: UUID,
supabase: AsyncClient
):

    # Soft delete
    item = await supabase.table("food_items")\
        .select("vendor_id")\
        .eq("id", str(item_id))\
        .single()\
        .execute()

    if item.data["vendor_id"] != str(vendor_id):
        raise HTTPException(403, "Not your item")

    await supabase.table("food_items")\
        .update({"is_deleted": True})\
        .eq("id", str(item_id))\
        .execute()

    return {"success": True, "message": "Item deleted"}


async def initiate_food_payment(
    data: CheckoutRequest,
    customer_id: UUID,
    supabase: AsyncClient
) -> dict:
    """
    Validate items, calculate total, save pending state in Redis,
    return data for Flutterwave RN SDK (no payment link).
    Real order is created in webhook after successful payment.
    """
    try:
        # 1. Validate vendor
        vendor_resp = await supabase.table("profiles")\
            .select("id, store_name, can_pickup_and_dropoff, pickup_and_delivery_charge")\
            .eq("id", str(data.vendor_id))\
            .eq("user_type", "RESTAURANT_VENDOR")\
            .single()\
            .execute()

        if not vendor_resp.data:
            raise HTTPException(404, "Vendor not found")

        vendor = vendor_resp.data

        # 2. Validate items & calculate subtotal
        item_ids = [str(item.item_id) for item in data.items]
        db_items = await supabase.table("food_items")\
            .select("id, name, price, in_stock, vendor_id")\
            .in_("id", item_ids)\
            .eq("vendor_id", str(data.vendor_id))\
            .execute()

        items_map = {item["id"]: item for item in db_items.data}
        subtotal = Decimal("0")

        for cart_item in data.items:
            db_item = items_map.get(str(cart_item.item_id))
            if not db_item or not db_item["in_stock"]:
                raise HTTPException(400, f"Item {cart_item.name} not available or out of stock")

            item_total = Decimal(str(db_item["price"])) * cart_item.quantity
            subtotal += item_total

        # 3. Delivery fee (only if vendor offers self-delivery)
        delivery_fee = Decimal("0")
        if data.delivery_option == "VENDOR_DELIVERY":
            if not vendor["can_pickup_and_dropoff"]:
                raise HTTPException(400, "This vendor does not offer delivery")
            delivery_fee = Decimal(str(vendor["pickup_and_delivery_charge"] or 0))

        grand_total = subtotal + delivery_fee

        # 4. Generate tx_ref
        tx_ref = f"FOOD-{uuid.uuid4().hex[:12].upper()}"

        # 5. Save pending state in Redis
        pending_data = {
            "customer_id": str(customer_id),
            "vendor_id": str(data.vendor_id),
            "items": [item.model_dump() for item in data.items],
            "subtotal": float(subtotal),
            "delivery_fee": float(delivery_fee),
            "grand_total": float(grand_total),
            "delivery_option": data.delivery_option,
            "cooking_instructions": data.cooking_instructions,
            "tx_ref": tx_ref,
            "created_at": datetime.now().isoformat()
        }
        await save_pending(f"pending_food_{tx_ref}", pending_data, expire=1800)

        # 6. Get real customer info (DI)
        customer_info = await get_customer_contact_info()

        # 7. Return SDK-ready data
        return {
            "tx_ref": tx_ref,
            "amount": float(grand_total),
            "public_key": settings.FLUTTERWAVE_PUBLIC_KEY,
            "currency": "NGN",
            "customer": customer_info,
            "customization": {
                "title": "Servipal Food Order",
                "description": f"Order from {vendor['store_name']}"
            },
            "message": "Ready for payment — use Flutterwave SDK"
        }

    except Exception as e:
        raise HTTPException(500, f"Food payment initiation failed: {str(e)}")


# async def customer_confirm_food_order(
#     order_id: UUID,
#     customer_id: UUID,
# supabase: AsyncClient
# ) -> CustomerConfirmResponse:
#     try:
#
#         order = await supabase.table("food_orders")\
#             .select("id, customer_id, vendor_id, order_status, grand_total")\
#             .eq("id", str(order_id))\
#             .single()\
#             .execute()
#
#         if order.data["customer_id"] != str(customer_id):
#             raise HTTPException(403, "Not your order")
#
#         if order.data["order_status"] != "READY":
#             raise HTTPException(400, "Order not ready for confirmation yet")
#
#         tx = await supabase.table("transactions")\
#             .select("id, amount, to_user_id, status")\
#             .eq("order_id", str(order_id))\
#             .single()\
#             .execute()
#
#         if tx.data["status"] == "RELEASED":
#             raise HTTPException(400, "Already confirmed")
#
#         amount = tx.data["amount"]
#         vendor_id = order.data["vendor_id"]
#
#         # Release escrow → vendor balance
#         await supabase.table("wallets")\
#             .update({
#                 "escrow_balance": supabase.raw(f"escrow_balance - {amount}"),
#                 "balance": supabase.raw(f"balance + {amount}")
#             })\
#             .eq("user_id", vendor_id)\
#             .execute()
#
#         # Update transaction & order
#         await supabase.table("transactions")\
#             .update({"status": "RELEASED"})\
#             .eq("id", tx.data["id"])\
#             .execute()
#
#         await supabase.table("food_orders")\
#             .update({"order_status": "COMPLETED"})\
#             .eq("id", str(order_id))\
#             .execute()
#
#         return CustomerConfirmResponse(
#             order_id=order_id,
#             amount_released=amount
#         )
#
#     except Exception as e:
#         raise HTTPException(500, f"Confirmation failed: {str(e)}")
