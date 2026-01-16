from fastapi import HTTPException, status
import uuid
from uuid import UUID
import datetime
from app.schemas.delivery_schemas import (PackageDeliveryCreate, AssignRiderRequest,
                                          AssignRiderResponse,
                                          DeliveryCancelRequest,
                                          DeliveryCancelResponse,
                                          DeliveryAction,
                                          DeliveryActionResponse
                                          )
from supabase import AsyncClient
from app.utils.redis_utils import save_pending
from app.utils.commission import get_commission_rate
from app.config.config import settings


# 1. Initiate Delivery (Pay First — No Rider Yet)
async def initiate_delivery_payment(
    data: PackageDeliveryCreate,
    sender_id: UUID,
    supabase: AsyncClient,
    customer_info: dict
) -> dict:
    """
    Step 1: Calculate delivery fee using PostGIS RPC
    Step 2: Generate tx_ref
    Step 3: Save pending state in Redis
    Step 4: Return data for Flutterwave RN SDK (no payment link)
    """
    try:
        # 1. Calculate distance using RPC
        distance_resp = await supabase.rpc("calculate_distance", {
            "p_lat1": data.pickup_coordinates[0],
            "p_lng1": data.pickup_coordinates[1],
            "p_lat2": data.dropoff_coordinates[0],
            "p_lng2": data.dropoff_coordinates[1]
        }).execute()

        distance_km = distance_resp.data if distance_resp.data is not None else 0.0

        # 2. Get charges from DB
        charges = await supabase.table("charges_and_commissions")\
            .select("base_delivery_fee, delivery_fee_per_km")\
            .single()\
            .execute()

        if not charges.data:
            raise HTTPException(500, "Charges configuration missing")

        base_fee = charges.data["base_delivery_fee"]
        per_km_fee = charges.data["delivery_fee_per_km"]

        # 3. Calculate final fee
        delivery_fee = base_fee + (per_km_fee * distance_km)
        delivery_fee = round(delivery_fee, 2)

        # 4. Generate unique tx_ref
        tx_ref = f"DEL-{uuid.uuid4().hex[:12].upper()}"

        # 5. Save pending state in Redis
        pending_data = {
            "sender_id": str(sender_id),
            "delivery_data": data.model_dump(),
            "delivery_fee": float(delivery_fee),
            "tx_ref": tx_ref,
            "distance_km": distance_km,
            "created_at": datetime.datetime.now().isoformat()
        }
        await save_pending(f"pending_delivery_{tx_ref}", pending_data, expire=1800)

        # 6. Return data for Flutterwave RN SDK
        return {
            "tx_ref": tx_ref,
            "amount": delivery_fee,
            "public_key": settings.FLUTTERWAVE_PUBLIC_KEY,
            "currency": "NGN",
            "customer": customer_info,
            "customization": {
                "title": "Servipal Delivery",
                "description": f"From {data.pickup_location} to {data.destination} ({distance_km:.1f} km)"
            },
            "message": "Ready for payment — use Flutterwave SDK"
        }

    except Exception as e:
        raise HTTPException(500, f"Payment initiation failed: {str(e)}")

# 2. Assign Rider After Payment
async def assign_rider_to_order(
    order_id: UUID,
    data: AssignRiderRequest,
    sender_id: UUID,
        supabase: AsyncClient
) -> AssignRiderResponse:
    try:
        order = await supabase.table("delivery_orders")\
            .select("id, sender_id, order_status")\
            .eq("id", str(order_id))\
            .single()\
            .execute()

        if order.data["sender_id"] != str(sender_id):
            raise HTTPException(403, "This is not your order")

        if order.data["order_status"] != "PAID_NEEDS_RIDER":
            raise HTTPException(400, "Order already has a rider or is not ready for assignment")

        assign_resp = await supabase.rpc("assign_rider_to_paid_delivery", {
            "order_id": str(order_id),
            "chosen_rider_id": str(data.rider_id)
        }).execute()

        result = assign_resp.data

        return AssignRiderResponse(
            success=result["success"],
            message=result["message"],
            delivery_status=result.get("delivery_status", "ASSIGNED"),
            rider_name=result.get("rider_name")
        )

    except Exception as e:
        error_msg = str(e)
        if "Rider not available" in error_msg:
            raise HTTPException(400, "Selected rider is no longer available. Please choose another.")
        raise HTTPException(500, "Failed to assign rider")

async def rider_delivery_action(
    delivery_id: UUID,
    data: DeliveryAction,
    rider_id: UUID,
supabase: AsyncClient
) -> DeliveryActionResponse:
    try:

        delivery_resp = await supabase.table("deliveries")\
            .select("id, order_id, rider_id, delivery_status")\
            .eq("id", str(delivery_id))\
            .single()\
            .execute()

        if not delivery_resp.data:
            raise HTTPException(404, "Delivery not found")

        delivery = delivery_resp.data

        if delivery["rider_id"] != str(rider_id):
            raise HTTPException(403, "Not your delivery")

        if delivery["delivery_status"] != "ASSIGNED":
            raise HTTPException(400, f"Delivery is {delivery['delivery_status']}, cannot act")

        new_status = "ACCEPTED" if data.action == "accept" else "PENDING"

        await supabase.table("deliveries")\
            .update({"delivery_status": new_status})\
            .eq("id", str(delivery_id))\
            .execute()

        rider_freed = data.action == "decline"

        message = "Delivery accepted successfully!" if data.action == "accept" else "Delivery declined."

        return DeliveryActionResponse(
            delivery_id=delivery_id,
            order_id=delivery["order_id"],
            delivery_status=new_status,
            message=message,
            rider_freed=rider_freed if rider_freed else None
        )

    except Exception as e:
        raise HTTPException(500, f"Action failed: {str(e)}")



async def rider_picked_up(delivery_id: UUID, rider_id: UUID, supabase: AsyncClient):
    """
    Rider confirms pickup of package.
    - Adds full amount (or dispatch share) to dispatch escrow_balance via RPC
    - Does NOT touch sender escrow (stays held until sender confirms receipt)
    - Updates delivery status to IN_TRANSIT
    - Updates transaction status to TRANSFERRED_TO_DISPATCH
    """
    try:
        # 1. Fetch delivery with all necessary info
        delivery_resp = await supabase.table("deliveries")\
            .select("""
                id,
                order_id,
                delivery_status,
                rider_id,
                sender_id,
                dispatch_id,
                delivery_fee
            """)\
            .eq("id", str(delivery_id))\
            .single()\
            .execute()

        delivery = delivery_resp.data

        # 2. Security validation
        if delivery["rider_id"] != str(rider_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This delivery is not assigned to you"
            )

        if delivery["delivery_status"] not in ("ACCEPTED", "ASSIGNED"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot pick up. Current status: {delivery['delivery_status']}"
            )

        # 3. Get the transaction record
        tx_resp = await supabase.table("transactions")\
            .select("id, amount, from_user_id, to_user_id")\
            .eq("order_id", delivery["order_id"])\
            .single()\
            .execute()

        tx = tx_resp.data
        full_amount = tx["amount"]
        dispatch_amount = full_amount

        dispatch_id = delivery["dispatch_id"] or tx["to_user_id"]

        # 4. Add amount to dispatch escrow ONLY (positive delta)
        await supabase.rpc("update_wallet_balance", {
            "p_user_id": str(dispatch_id),
            "p_delta": dispatch_amount,
            "p_field": "escrow_balance"
        }).execute()

        # 5. Update delivery status to IN_TRANSIT
        await supabase.table("deliveries")\
            .update({"delivery_status": "IN_TRANSIT"})\
            .eq("id", str(delivery_id))\
            .execute()

        # 6. Update transaction status
        await supabase.table("transactions")\
            .update({"status": "TRANSFERRED_TO_DISPATCH"})\
            .eq("id", tx["id"])\
            .execute()

        return {
            "success": True,
            "message": "Package picked up. Funds secured in dispatch escrow.",
            "delivery_status": "IN_TRANSIT",
            "amount_secured_in_dispatch_escrow": dispatch_amount
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pickup failed: {str(e)}"
        )
async def rider_confirm_delivery(
    delivery_id: UUID,
    rider_id: UUID,
supabase: AsyncClient,
):
    try:

        # 1. Fetch delivery
        delivery_resp = await supabase.table("deliveries")\
            .select("id, delivery_status, rider_id, order_id, image_url")\
            .eq("id", str(delivery_id))\
            .single()\
            .execute()

        delivery = delivery_resp.data

        # 2. Validation
        if delivery["rider_id"] != str(rider_id):
            raise HTTPException(403, "This delivery is not assigned to you")

        if delivery["delivery_status"] != "IN_TRANSIT":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot confirm delivery. Current status: {delivery['delivery_status']}"
            )


        # 4. Update delivery to DELIVERED
        await supabase.table("deliveries")\
            .update({
                "delivery_status": "DELIVERED",
            })\
            .eq("id", str(delivery_id))\
            .execute()

        return {
            "success": True,
            "message": "Delivery confirmed! Waiting for sender to confirm receipt.",
            "delivery_status": "DELIVERED",
        }

    except Exception as e:
        raise HTTPException(500, f"Delivery confirmation failed: {str(e)}")


# Updated sender_confirm_receipt (atomic release)
async def sender_confirm_receipt(delivery_id: UUID, sender_id: UUID, supabase: AsyncClient):
    try:
        delivery_resp = await supabase.table("deliveries")\
            .select("""
                id,
                order_id,
                delivery_status,
                sender_id,
                dispatch_id,
                delivery_fee
            """)\
            .eq("id", str(delivery_id))\
            .single()\
            .execute()

        delivery = delivery_resp.data

        if delivery["sender_id"] != str(sender_id):
            raise HTTPException(403, "You are not the sender of this package")

        if delivery["delivery_status"] != "DELIVERED":
            raise HTTPException(400, f"Cannot confirm receipt. Current status: {delivery['delivery_status']}")

        tx_resp = await supabase.table("transactions")\
            .select("id, amount, to_user_id, status")\
            .eq("order_id", delivery["order_id"])\
            .single()\
            .execute()

        tx = tx_resp.data

        if tx["status"] == "RELEASED":
            raise HTTPException(400, "Receipt already confirmed and payment released")

        full_amount = tx["amount"]
        commission_rate = await get_commission_rate("DELIVERY", supabase=supabase)
        dispatch_amount = full_amount * commission_rate
        dispatch_id = delivery["dispatch_id"] or tx["to_user_id"]

        # Atomic release
        await supabase.rpc("release_delivery_payment", {
            "p_sender_id": str(sender_id),
            "p_dispatch_id": str(dispatch_id),
            "p_full_amount": full_amount,
            "p_dispatch_amount": dispatch_amount
        }).execute()

        await supabase.table("transactions")\
            .update({"status": "RELEASED"})\
            .eq("id", tx["id"])\
            .execute()

        await supabase.table("deliveries")\
            .update({"delivery_status": "COMPLETED"})\
            .eq("id", str(delivery_id))\
            .execute()

        return {
            "success": True,
            "message": "Receipt confirmed! Payment released to dispatch.",
            "delivery_status": "COMPLETED",
            "amount_released_to_dispatch": dispatch_amount,
            "total_deducted_from_sender_escrow": full_amount,
            "dispatch_id": dispatch_id
        }

    except Exception as e:
        raise HTTPException(500, f"Confirmation failed: {str(e)}")


async def cancel_delivery(
    delivery_id: UUID,
    data: DeliveryCancelRequest,
    current_user_id: UUID,
    current_user_type: str,
supabase: AsyncClient
) -> DeliveryCancelResponse:
    # Fetch delivery

    del_resp = await supabase.table("deliveries")\
        .select("delivery_status, rider_id, sender_id, order_id")\
        .eq("id", str(delivery_id))\
        .single()\
        .execute()

    delivery = del_resp.data

    # Authorization
    is_rider = current_user_type == "RIDER" and str(current_user_id) == delivery["rider_id"]
    is_sender = str(current_user_id) == delivery["sender_id"]

    if not (is_rider or is_sender):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You cannot cancel this delivery")

    cancelled_by = "RIDER" if is_rider else "SENDER"

    # Prevent cancel if already completed/delivered
    if delivery["delivery_status"] in ("DELIVERED", "COMPLETED"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot cancel completed delivery")

    # Update with cancel info — trigger handles everything else
    await supabase.table("deliveries").update({
        "cancelled_by": cancelled_by,
        "cancel_reason": data.reason,
        "delivery_status": "CANCELLED" if delivery["delivery_status"] in ("ASSIGNED", "ACCEPTED") else delivery["delivery_status"]
    }).eq("id", str(delivery_id)).execute()

    refunded = delivery["delivery_status"] in ("ASSIGNED", "ACCEPTED")

    message = ("Delivery cancelled. Full refund processed." if refunded
               else "Delivery cancelled. Rider will return item. You will pay delivery fee on receipt confirmation.")

    return DeliveryCancelResponse(
        order_id=delivery["order_id"],
        delivery_status="CANCELLED",
        refunded=refunded,
        message=message
    )



# from supabase import AsyncClient
# from app.schemas.delivery_schemas import *
# from fastapi import HTTPException, status
# from uuid import UUID
# import datetime
# from app.utils.commission import get_commission_rate
#
# # 1. Initiate Delivery Order (Pay First — No Rider Yet)
# async def create_delivery_order(
#     data: DeliveryOrderCreate,
#     sender_id: UUID,
#         supabase: AsyncClient
# ) -> DeliveryOrderResponse:
#     params = {
#         "sender_id": str(sender_id),
#         "receiver_phone": data.receiver_phone,
#         "pickup_location": data.pickup_location,
#         "destination": data.destination,
#         "pickup_lat": data.pickup_coordinates[0],
#         "pickup_lng": data.pickup_coordinates[1],
#         "dropoff_lat": data.dropoff_coordinates[0],
#         "dropoff_lng": data.dropoff_coordinates[1],
#         "additional_info": data.additional_info,
#         "delivery_type": data.delivery_type.value,
#         "chosen_rider_id": None
#     }
#
#     try:
#
#         resp = await supabase.rpc("create_p2p_delivery", params).execute()
#         result = resp.data
#
#         return DeliveryOrderResponse(
#             id=result["order_id"],
#             order_number=result["order_number"],
#             sender_id=sender_id,
#             rider_id=None,
#             dispatch_id=None,
#             receiver_phone=data.receiver_phone,
#             pickup_location=data.pickup_location,
#             destination=data.destination,
#             delivery_fee=result["delivery_fee"],
#             delivery_status="PAID_NEEDS_RIDER",
#             delivery_type=data.delivery_type,
#             created_at=datetime.now()
#         )
#
#     except Exception as e:
#         error_msg = str(e).lower()
#         if "permission" in error_msg:
#             raise HTTPException(status_code=403, detail="Permission denied")
#         raise HTTPException(status_code=500, detail="Failed to initiate delivery order")
#
# # 2. Assign Rider After Payment
# async def assign_rider_to_order(
#     order_id: UUID,
#     data: AssignRiderRequest,
#     sender_id: UUID,
# supabase: AsyncClient
# ) -> AssignRiderResponse:
#     try:
#         # Validate order belongs to the sender and is waiting for rider
#
#         order = await supabase.table("delivery_orders")\
#             .select("id, sender_id, order_status")\
#             .eq("id", str(order_id))\
#             .single()\
#             .execute()
#
#         if order.data["sender_id"] != str(sender_id):
#             raise HTTPException(403, "This is not your order")
#
#         if order.data["order_status"] != "PAID_NEEDS_RIDER":
#             raise HTTPException(400, "Order already has a rider or is not ready for assignment")
#
#         # Call RPC to assign rider (re-check availability)
#         assign_resp = await supabase.rpc("assign_rider_to_paid_delivery", {
#             "order_id": str(order_id),
#             "chosen_rider_id": str(data.rider_id)
#         }).execute()
#
#         result = assign_resp.data
#
#         return AssignRiderResponse(
#             success=result["success"],
#             message=result["message"],
#             delivery_status=result.get("delivery_status", "ASSIGNED"),
#             rider_name=result.get("rider_name")
#         )
#
#     except Exception as e:
#         error_msg = str(e)
#         if "Rider not available" in error_msg:
#             raise HTTPException(400, "Selected rider is no longer available. Please choose another.")
#         raise HTTPException(500, "Failed to assign rider")
#
# # 3. Rider Accept/Decline
# async def rider_delivery_action(
#     delivery_id: UUID,
#     data: DeliveryAction,
#     rider_id: UUID,
# supabase: AsyncClient
# ) -> DeliveryActionResponse:
#     try:
#
#         delivery_resp = await supabase.table("deliveries")\
#             .select("id, order_id, rider_id, delivery_status")\
#             .eq("id", str(delivery_id))\
#             .single()\
#             .execute()
#
#         if not delivery_resp.data:
#             raise HTTPException(404, "Delivery not found")
#
#         delivery = delivery_resp.data
#
#         if delivery["rider_id"] != str(rider_id):
#             raise HTTPException(403, "Not your delivery")
#
#         if delivery["delivery_status"] != "ASSIGNED":
#             raise HTTPException(400, f"Delivery is {delivery['delivery_status']}, cannot act")
#
#         new_status = "ACCEPTED" if data.action == "accept" else "PENDING"
#
#         await supabase.table("deliveries")\
#             .update({"delivery_status": new_status})\
#             .eq("id", str(delivery_id))\
#             .execute()
#
#         rider_freed = data.action == "decline"
#
#         message = "Delivery accepted successfully!" if data.action == "accept" else "Delivery declined."
#
#         return DeliveryActionResponse(
#             delivery_id=delivery_id,
#             order_id=delivery["order_id"],
#             delivery_status=new_status,
#             message=message,
#             rider_freed=rider_freed if rider_freed else None
#         )
#
#     except Exception as e:
#         raise HTTPException(500, f"Action failed: {str(e)}")
#
#
#
# async def rider_picked_up(delivery_id: UUID, rider_id: UUID, supabase: AsyncClient):
#     """
#     Rider confirms pickup of package.
#     - Adds full amount (or dispatch share) to dispatch escrow_balance via RPC
#     - Does NOT touch sender escrow (stays held until sender confirms receipt)
#     - Updates delivery status to IN_TRANSIT
#     - Updates transaction status to TRANSFERRED_TO_DISPATCH
#     """
#     try:
#         # 1. Fetch delivery with all necessary info
#         delivery_resp = await supabase.table("deliveries")\
#             .select("""
#                 id,
#                 order_id,
#                 delivery_status,
#                 rider_id,
#                 sender_id,
#                 dispatch_id,
#                 delivery_fee
#             """)\
#             .eq("id", str(delivery_id))\
#             .single()\
#             .execute()
#
#         delivery = delivery_resp.data
#
#         # 2. Security validation
#         if delivery["rider_id"] != str(rider_id):
#             raise HTTPException(
#                 status_code=status.HTTP_403_FORBIDDEN,
#                 detail="This delivery is not assigned to you"
#             )
#
#         if delivery["delivery_status"] not in ("ACCEPTED", "ASSIGNED"):
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail=f"Cannot pick up. Current status: {delivery['delivery_status']}"
#             )
#
#         # 3. Get the transaction record
#         tx_resp = await supabase.table("transactions")\
#             .select("id, amount, from_user_id, to_user_id")\
#             .eq("order_id", delivery["order_id"])\
#             .single()\
#             .execute()
#
#         tx = tx_resp.data
#         full_amount = tx["amount"]
#         dispatch_amount = full_amount
#
#         dispatch_id = delivery["dispatch_id"] or tx["to_user_id"]
#
#         # 4. Add amount to dispatch escrow ONLY (positive delta)
#         await supabase.rpc("update_wallet_balance", {
#             "p_user_id": str(dispatch_id),
#             "p_delta": dispatch_amount,
#             "p_field": "escrow_balance"
#         }).execute()
#
#         # 5. Update delivery status to IN_TRANSIT
#         await supabase.table("deliveries")\
#             .update({"delivery_status": "IN_TRANSIT"})\
#             .eq("id", str(delivery_id))\
#             .execute()
#
#         # 6. Update transaction status
#         await supabase.table("transactions")\
#             .update({"status": "TRANSFERRED_TO_DISPATCH"})\
#             .eq("id", tx["id"])\
#             .execute()
#
#         return {
#             "success": True,
#             "message": "Package picked up. Funds secured in dispatch escrow.",
#             "delivery_status": "IN_TRANSIT",
#             "amount_secured_in_dispatch_escrow": dispatch_amount
#         }
#
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Pickup failed: {str(e)}"
#         )
# async def rider_confirm_delivery(
#     delivery_id: UUID,
#     rider_id: UUID,
# supabase: AsyncClient,
# ):
#     try:
#
#         # 1. Fetch delivery
#         delivery_resp = await supabase.table("deliveries")\
#             .select("id, delivery_status, rider_id, order_id, image_url")\
#             .eq("id", str(delivery_id))\
#             .single()\
#             .execute()
#
#         delivery = delivery_resp.data
#
#         # 2. Validation
#         if delivery["rider_id"] != str(rider_id):
#             raise HTTPException(403, "This delivery is not assigned to you")
#
#         if delivery["delivery_status"] != "IN_TRANSIT":
#             raise HTTPException(
#                 status.HTTP_400_BAD_REQUEST,
#                 f"Cannot confirm delivery. Current status: {delivery['delivery_status']}"
#             )
#
#
#         # 4. Update delivery to DELIVERED
#         await supabase.table("deliveries")\
#             .update({
#                 "delivery_status": "DELIVERED",
#             })\
#             .eq("id", str(delivery_id))\
#             .execute()
#
#         return {
#             "success": True,
#             "message": "Delivery confirmed! Waiting for sender to confirm receipt.",
#             "delivery_status": "DELIVERED",
#         }
#
#     except Exception as e:
#         raise HTTPException(500, f"Delivery confirmation failed: {str(e)}")
#
#
# async def sender_confirm_receipt(delivery_id: UUID, sender_id: UUID, supabase: AsyncClient):
#     """
#     Sender confirms receipt of package → releases payment from escrow to dispatch.
#     Uses atomic DB function to handle all wallet movements safely.
#     """
#     try:
#         # 1. Fetch delivery with necessary info
#         delivery_resp = await supabase.table("deliveries")\
#             .select("""
#                 id,
#                 order_id,
#                 delivery_status,
#                 sender_id,
#                 dispatch_id,
#                 delivery_fee
#             """)\
#             .eq("id", str(delivery_id))\
#             .single()\
#             .execute()
#
#         delivery = delivery_resp.data
#
#         # 2. Security: Only the sender can confirm
#         if delivery["sender_id"] != str(sender_id):
#             raise HTTPException(
#                 status_code=status.HTTP_403_FORBIDDEN,
#                 detail="You are not the sender of this package"
#             )
#
#         # 3. Validate current status
#         if delivery["delivery_status"] != "DELIVERED":
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail=f"Cannot confirm receipt. Package not yet delivered. Current status: {delivery['delivery_status']}"
#             )
#
#         # 4. Get transaction
#         tx_resp = await supabase.table("transactions")\
#             .select("id, amount, to_user_id, status")\
#             .eq("order_id", delivery["order_id"])\
#             .single()\
#             .execute()
#
#         tx = tx_resp.data
#
#         # Prevent double confirmation
#         if tx["status"] == "RELEASED":
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail="Receipt already confirmed and payment released"
#             )
#
#         full_amount = tx["amount"]
#         commission_rate = await get_commission_rate("DELIVERY", supabase=supabase)
#         dispatch_amount = full_amount * commission_rate
#         dispatch_id = delivery["dispatch_id"] or tx["to_user_id"]
#
#         # 5. Atomic wallet update:
#         #    - Deduct full from sender escrow
#         #    - Deduct dispatch share from dispatch escrow
#         #    - Credit dispatch share to dispatch balance
#         await supabase.rpc("release_delivery_payment", {
#             "p_sender_id": str(sender_id),
#             "p_dispatch_id": str(dispatch_id),
#             "p_full_amount": full_amount,
#             "p_dispatch_amount": dispatch_amount
#         }).execute()
#
#         # 6. Update transaction to RELEASED
#         await supabase.table("transactions")\
#             .update({"status": "RELEASED"})\
#             .eq("id", tx["id"])\
#             .execute()
#
#         # 7. Update delivery status to COMPLETED
#         await supabase.table("deliveries")\
#             .update({"delivery_status": "COMPLETED"})\
#             .eq("id", str(delivery_id))\
#             .execute()
#
#         return {
#             "success": True,
#             "message": "Receipt confirmed! Payment released to dispatch.",
#             "delivery_status": "COMPLETED",
#             "amount_released_to_dispatch": dispatch_amount,
#             "total_deducted_from_sender_escrow": full_amount,
#             "dispatch_id": dispatch_id
#         }
#
#     except Exception as e:
#         raise HTTPException(
#             status_code=500,
#             detail=f"Confirmation failed: {str(e)}"
#         )
#

