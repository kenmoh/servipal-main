from uuid import UUID
import uuid
from datetime import datetime
from typing import Optional, List
from decimal import Decimal
from fastapi import HTTPException, status
from app.utils.redis_utils import save_pending
from app.schemas.common import VendorOrderAction
from app.schemas.laundry_schemas import (
     LaundryVendorDetailResponse,
    LaundryCategoryResponse, LaundryItemResponse, LaundryItemDetailResponse,
 LaundryItemUpdate,
    LaundryVendorMarkReadyResponse, LaundryOrderCreate, LaundryCustomerConfirmResponse
)
from app.utils.storage import upload_to_supabase_storage
from supabase import AsyncClient
from app.config.config import settings
from app.schemas.common import VendorResponse


# ───────────────────────────────────────────────
# Vendors & Detail
# ───────────────────────────────────────────────
async def get_laundry_vendors(supabase: AsyncClient, lat: Optional[float] = None, lng: Optional[float] = None) -> List[
    VendorResponse]:
    params = {"near_lat": lat, "near_lng": lng} if lat and lng else {}
    resp = await supabase.rpc("get_laundry_vendors", params).execute()
    return [VendorResponse(**v) for v in resp.data]


async def get_laundry_vendor_detail(vendor_id: UUID, supabase: AsyncClient) -> LaundryVendorDetailResponse:
    resp = await supabase.rpc("get_laundry_vendor_detail_with_menu", {"vendor_user_id": str(vendor_id)}).execute()

    if not resp.data:
        raise HTTPException(404, "Vendor not found")

    vendor_data = resp.data[0]["vendor_json"]
    menu_map = {}

    for row in resp.data:
        if row["category_json"]:
            cat = row["category_json"]
            if cat["id"] not in menu_map:
                menu_map[cat["id"]] = {"category": LaundryCategoryResponse(**cat), "items": []}
            if row["item_json"]:
                menu_map[cat["id"]]["items"].append(LaundryItemResponse(**row["item_json"]))

    return LaundryVendorDetailResponse(
        **vendor_data,
        categories=[m["category"] for m in menu_map.values()],
        menu=[item for m in menu_map.values() for item in m["items"]]
    )


async def vendor_laundry_order_action(
    order_id: UUID,
    data: VendorOrderAction,
    vendor_id: UUID,
    supabase: AsyncClient
) -> LaundryVendorMarkReadyResponse:
    """
    Vendor accepts or rejects a laundry order.
    - On accept: move to PREPARING
    - On reject: cancel order + refund escrow to customer balance via RPC
    """
    try:
        # 1. Fetch order with necessary fields
        order_resp = await supabase.table("laundry_orders")\
            .select("id, vendor_id, order_status, payment_status, grand_total")\
            .eq("id", str(order_id))\
            .single()\
            .execute()

        if not order_resp.data:
            raise HTTPException(404, "Laundry order not found")

        order = order_resp.data

        # 2. Security & validation
        if order["vendor_id"] != str(vendor_id):
            raise HTTPException(403, "This is not your order")

        if order["order_status"] != "PENDING":
            raise HTTPException(400, f"Order already processed (current status: {order['order_status']})")

        if order["payment_status"] != "PAID":
            raise HTTPException(400, "Payment not completed")

        # 3. Process action
        if data.action == "accept":
            new_status = "PREPARING"
            message = "Order accepted. Processing laundry now."
            # refund = False

        else:  # reject
            new_status = "CANCELLED"
            message = "Order rejected."
            # refund = True

            # Get transaction for refund
            tx_resp = await supabase.table("transactions")\
                .select("id, amount, from_user_id, status")\
                .eq("order_id", str(order_id))\
                .single()\
                .execute()

            if not tx_resp.data:
                raise HTTPException(404, "Transaction not found for this order")

            tx = tx_resp.data
            amount = tx["amount"]

            # Refund: escrow → customer balance (atomic RPC)
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

            # Mark transaction as refunded
            await supabase.table("transactions")\
                .update({"status": "REFUNDED"})\
                .eq("id", tx["id"])\
                .execute()

        # 4. Update order status
        await supabase.table("laundry_orders")\
            .update({"order_status": new_status})\
            .eq("id", str(order_id))\
            .execute()

        return LaundryVendorMarkReadyResponse(
            order_id=order_id,
            order_status=new_status,
            message=message
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(500, f"Order action failed: {str(e)}")


async def customer_confirm_laundry_order(
    order_id: UUID,
    customer_id: UUID,
    supabase: AsyncClient
) -> LaundryCustomerConfirmResponse:
    """
    Customer confirms receipt of laundry order.
    - Deducts full amount from customer escrow
    - Credits full amount to vendor balance (via atomic RPC)
    - Updates transaction to RELEASED
    - Updates order to COMPLETED
    """
    try:
        # 1. Fetch order with necessary fields
        order_resp = await supabase.table("laundry_orders")\
            .select("id, customer_id, vendor_id, order_status, grand_total")\
            .eq("id", str(order_id))\
            .single()\
            .execute()

        if not order_resp.data:
            raise HTTPException(404, "Laundry order not found")

        order = order_resp.data

        # 2. Security & validation
        if order["customer_id"] != str(customer_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This is not your order"
            )

        if order["order_status"] != "READY":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Order not ready for confirmation yet. Current status: {order['order_status']}"
            )

        # 3. Get transaction (for amount & prevent double-release)
        tx_resp = await supabase.table("transactions")\
            .select("id, amount, to_user_id, status")\
            .eq("order_id", str(order_id))\
            .single()\
            .execute()

        if not tx_resp.data:
            raise HTTPException(404, "Transaction not found for this order")

        tx = tx_resp.data

        if tx["status"] == "RELEASED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order already confirmed and payment released"
            )

        full_amount = tx["amount"]
        vendor_id = order["vendor_id"] or tx["to_user_id"]

        # 4. Atomic payment release (deduct escrow → credit balance)
        await supabase.rpc("release_order_payment", {
            "p_customer_id": str(customer_id),
            "p_vendor_id": str(vendor_id),
            "p_full_amount": full_amount
        }).execute()

        # 5. Update transaction status
        await supabase.table("transactions")\
            .update({"status": "RELEASED"})\
            .eq("id", tx["id"])\
            .execute()

        # 6. Update order status
        await supabase.table("laundry_orders")\
            .update({"order_status": "COMPLETED"})\
            .eq("id", str(order_id))\
            .execute()

        return LaundryCustomerConfirmResponse(
            order_id=order_id,
            amount_released=full_amount,
            message="Order confirmed! Payment released to vendor."
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Confirmation failed: {str(e)}"
        )

# ───────────────────────────────────────────────
# Menu Management
# ───────────────────────────────────────────────
async def create_laundry_item_with_images(
        name: str,
        vendor_id: UUID,
        price: Decimal,
        description: Optional[str],
        supabase: AsyncClient,
        images=None

) -> dict:
    if images is None:
        images = []
    try:
        item_resp = await supabase.table("laundry_items").insert({
            "vendor_id": str(vendor_id),
            "name": name,
            "description": description,
            "price": float(price),
        }).execute()

        item_id = item_resp.data[0]["id"]

        image_urls = []
        for file in images:
            url = await upload_to_supabase_storage(
                file=file,
                bucket="menu-images",
                folder=f"vendor_{vendor_id}/laundry_item_{item_id}",
                supabase=supabase
            )
            image_urls.append(url)

        if image_urls:
            await supabase.table("laundry_items").update({"images": image_urls}).eq("id", item_id).execute()

        return {
            "success": True,
            "item_id": item_id,
            "image_urls": image_urls
        }

    except Exception as e:
        raise HTTPException(500, f"Failed to create laundry item: {str(e)}")


# ───────────────────────────────────────────────
# Payment Initiation (Pay First)
# ───────────────────────────────────────────────
async def initiate_laundry_payment(
        data: LaundryOrderCreate,
        customer_id: UUID,
        customer_info: dict,
        supabase: AsyncClient,
) -> dict:
    """
    Validate vendor & items, calculate total, save pending in Redis,
    return Flutterwave RN SDK data (no payment link).
    """
    try:
        # Validate vendor
        vendor = await supabase.table("profiles") \
            .select("id, company_name, can_pickup_and_dropoff, pickup_and_delivery_charge") \
            .eq("id", str(data.vendor_id)) \
            .eq("user_type", "LAUNDRY_VENDOR") \
            .single() \
            .execute()

        if not vendor.data:
            raise HTTPException(404, "Laundry vendor not found")

        vendor = vendor.data

        # Validate items & calculate subtotal
        item_ids = [str(item.item_id) for item in data.items]
        db_items = await supabase.table("laundry_items") \
            .select("id, name, price, vendor_id") \
            .in_("id", item_ids) \
            .eq("vendor_id", str(data.vendor_id)) \
            .execute()

        items_map = {item["id"]: item for item in db_items.data}
        subtotal = Decimal("0")

        for cart_item in data.items:
            db_item = items_map.get(str(cart_item.item_id))


            item_total = Decimal(str(db_item["price"])) * cart_item.quantity
            subtotal += item_total

        # Delivery fee
        delivery_fee = Decimal("0")
        if data.delivery_option == "VENDOR_DELIVERY":
            if not vendor["can_pickup_and_dropoff"]:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vendor does not offer delivery")
            delivery_fee = Decimal(str(vendor["pickup_and_delivery_charge"] or 0))

        grand_total = subtotal + delivery_fee

        # Generate tx_ref
        tx_ref = f"LAUNDRY-{uuid.uuid4().hex[:12].upper()}"

        # Save pending in Redis
        pending_data = {
            "customer_id": str(customer_id),
            "vendor_id": str(data.vendor_id),
            "items": [item.model_dump() for item in data.items],
            "subtotal": float(subtotal),
            "delivery_fee": float(delivery_fee),
            "grand_total": float(grand_total),
            "delivery_option": data.delivery_option,
            "washing_instructions": data.washing_instructions,
            "tx_ref": tx_ref,
            "created_at": datetime.now().isoformat()
        }
        await save_pending(f"pending_laundry_{tx_ref}", pending_data, expire=1800)

        # Return SDK-ready data
        return {
            "tx_ref": tx_ref,
            "amount": Decimal(grand_total),
            "public_key": settings.FLUTTERWAVE_PUBLIC_KEY,
            "currency": "NGN",
            "customer": customer_info,
            "customization": {
                "title": "Servipal Laundry Order",
                "description": f"Order from {vendor['store_name']}"
            },
            "message": "Ready for payment — use Flutterwave SDK"
        }

    except Exception as e:
        raise HTTPException(500, f"Laundry payment initiation failed: {str(e)}")

async def update_laundry_item(item_id: UUID, data: LaundryItemUpdate, vendor_id: UUID, supabase: AsyncClient) -> LaundryItemDetailResponse:
    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(400, "No data provided")
    
    item = await supabase.table("laundry_items").select("vendor_id").eq("id", str(item_id)).single().execute()
    if item.data["vendor_id"] != str(vendor_id):
        raise HTTPException(403, "Not your item")

    resp = await supabase.table("laundry_items").update(update_data).eq("id", str(item_id)).execute()
    return LaundryItemDetailResponse(**resp.data[0])

async def delete_laundry_item(item_id: UUID, vendor_id: UUID, supabase: AsyncClient):
    
    item = await supabase.table("laundry_items").select("vendor_id").eq("id", str(item_id)).single().execute()
    if item.data["vendor_id"] != str(vendor_id):
        raise HTTPException(403, "Not your item")

    await supabase.table("laundry_items").update({"is_deleted": True}).eq("id", str(item_id)).execute()
    return {"success": True, "message": "Item deleted"}




async def vendor_mark_laundry_order_ready(order_id: UUID, vendor_id: UUID, supabase: AsyncClient) -> LaundryVendorMarkReadyResponse:
    try:
  
        order = await supabase.table("laundry_orders").select("id, vendor_id, order_status").eq("id", str(order_id)).single().execute()
        if order.data["vendor_id"] != str(vendor_id):
            raise HTTPException(403, "Not your order")
        if order.data["order_status"] != "PREPARING":
            raise HTTPException(400, "Order must be in PREPARING status")

        await supabase.table("laundry_orders").update({"order_status": "READY"}).eq("id", str(order_id)).execute()
        return LaundryVendorMarkReadyResponse(order_id=order_id)
    except Exception as e:
        raise HTTPException(500, f"Failed to mark ready: {str(e)}")



