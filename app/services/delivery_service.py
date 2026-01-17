from fastapi import HTTPException, status, Request
from typing import Optional
import uuid
from uuid import UUID
import datetime
from app.schemas.delivery_schemas import (
    PackageDeliveryCreate,
    AssignRiderRequest,
    AssignRiderResponse,
    DeliveryCancelRequest,
    DeliveryCancelResponse,
    DeliveryAction,
    DeliveryActionResponse,
    DeliveryOrdersResponse,
    DeliveryOrderListItem,
)
from app.schemas.common import (
    PaymentInitializationResponse,
    PaymentCustomerInfo,
    PaymentCustomization,
)
from supabase import AsyncClient
from app.utils.redis_utils import save_pending
from app.utils.commission import get_commission_rate
from app.config.config import settings
from app.config.logging import logger
from app.utils.audit import log_audit_event
from decimal import Decimal
from app.services.notification_service import notify_user


# ───────────────────────────────────────────────
# 1. Initiate Delivery (Pay First — No Rider Yet)
# ───────────────────────────────────────────────
async def initiate_delivery_payment(
    data: PackageDeliveryCreate,
    sender_id: UUID,
    supabase: AsyncClient,
    customer_info: dict,
) -> dict:
    """
    Step 1: Calculate delivery fee using PostGIS RPC
    Step 2: Generate tx_ref
    Step 3: Save pending state in Redis
    Step 4: Return data for Flutterwave RN SDK
    """
    logger.info(
        "initiate_delivery_payment",
        sender_id=str(sender_id),
        pickup=data.pickup_location,
        destination=data.destination,
    )
    try:
        # 1. Calculate distance using RPC
        distance_resp = await supabase.rpc(
            "calculate_distance",
            {
                "p_lat1": data.pickup_coordinates[0],
                "p_lng1": data.pickup_coordinates[1],
                "p_lat2": data.dropoff_coordinates[0],
                "p_lng2": data.dropoff_coordinates[1],
            },
        ).execute()

        distance_km = distance_resp.data if distance_resp.data is not None else 0.0

        # 2. Get charges from DB
        charges = (
            await supabase.table("charges_and_commissions")
            .select("base_delivery_fee, delivery_fee_per_km")
            .single()
            .execute()
        )

        if not charges.data:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "Charges configuration missing"
            )

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
            "package_image_url": data.package_image_url,
            "distance_km": distance_km,
            "created_at": datetime.datetime.now().isoformat(),
        }
        await save_pending(f"pending_delivery_{tx_ref}", pending_data, expire=1800)

        # 6. Return data for Flutterwave RN SDK
        return PaymentInitializationResponse(
            tx_ref=tx_ref,
            amount=Decimal(str(delivery_fee)),
            public_key=settings.FLUTTERWAVE_PUBLIC_KEY,
            currency="NGN",
            customer=PaymentCustomerInfo(**customer_info),
            customization=PaymentCustomization(
                title="Servipal Delivery",
                description=f"From {data.pickup_location} to {data.destination} ({distance_km:.1f} km)",
            ),
            message="Ready for payment — use Flutterwave SDK",
        ).model_dump()

    except Exception as e:
        logger.error(
            "initiate_delivery_payment_error",
            sender_id=str(sender_id),
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Payment initiation failed: {str(e)}",
        )


# ───────────────────────────────────────────────
# 3. Assign Rider After Payment (RPC already updated earlier)
# ───────────────────────────────────────────────
async def assign_rider_to_order(
    order_id: UUID, data: AssignRiderRequest, sender_id: UUID, supabase: AsyncClient
) -> AssignRiderResponse:
    try:
        order = (
            await supabase.table("delivery_orders")
            .select("id, sender_id, status")
            .eq("id", str(order_id))
            .single()
            .execute()
        )

        if not order.data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery order not found")

        if order.data["sender_id"] != str(sender_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This is not your order")

        if order.data["status"] != "PAID_NEEDS_RIDER":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Order already has a rider or is not ready for assignment",
            )

        assign_resp = await supabase.rpc(
            "assign_rider_to_paid_delivery",
            {"p_order_id": str(order_id), "p_chosen_rider_id": str(data.rider_id)},
        ).execute()

        result = assign_resp.data

        # Notify rider
        if result["success"]:
            await notify_user(
                user_id=data.rider_id,
                title="New Delivery Assigned!",
                body="You have a new order",
                data={"order_id": str(order_id), "type": "DELIVERY_ASSIGNED"},
                supabase=supabase,
            )

        return AssignRiderResponse(
            success=result["success"],
            message=result["message"],
            delivery_status=result.get("delivery_status", "ASSIGNED"),
            rider_name=result.get("rider_name"),
        )

    except Exception as e:
        error_msg = str(e)
        if (
            "Rider is currently suspended" in error_msg
            or "Rider is blocked" in error_msg
            or "Rider not available" in error_msg
        ):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, error_msg)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to assign rider"
        )


# ───────────────────────────────────────────────
# 4. Rider Delivery Action (accept/decline)
# ───────────────────────────────────────────────
async def rider_delivery_action(
    order_id: UUID, data: DeliveryAction, rider_id: UUID, supabase: AsyncClient
) -> DeliveryActionResponse:
    try:
        order = (
            await supabase.table("delivery_orders")
            .select("id, rider_id, status")
            .eq("id", str(order_id))
            .single()
            .execute()
        )

        if not order.data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery order not found")

        if order.data["rider_id"] != str(rider_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your delivery order")

        if order.data["delivery_status"] != "ASSIGNED":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Order is {order['status']}, cannot act"
            )

        new_status = "PICKED_UP" if data.accept else "PAID_NEEDS_RIDER"

        await (
            supabase.table("delivery_orders")
            .update({"status": new_status})
            .eq("id", str(order_id))
            .execute()
        )

        message = (
            "Delivery accepted successfully!" if data.accept else "Delivery declined"
        )

        return DeliveryActionResponse(
            delivery_id=order_id,
            order_id=order_id,
            delivery_status=new_status,
            message=message,
        )

    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Action failed: {str(e)}"
        )


# ───────────────────────────────────────────────
# 5. Rider Pickup
# ───────────────────────────────────────────────
async def rider_picked_up(
    order_id: UUID,
    rider_id: UUID,
    supabase: AsyncClient,
):
    try:
        order = (
            await supabase.table("delivery_orders")
            .select("id, status, rider_id, sender_id, dispatch_id, delivery_fee")
            .eq("id", str(order_id))
            .single()
            .execute()
        ).data

        if not order:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery order not found")

        if order["rider_id"] != str(rider_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your delivery order")

        if order["status"] not in ("ASSIGNED", "PICKED_UP"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot pick up. Current status: {order['status']}",
            )

        full_amount = order["delivery_fee"]
        dispatch_id = order["dispatch_id"]

        # Credit dispatch escrow (virtual claim)
        credit_resp = await supabase.rpc(
            "credit_dispatch_escrow_on_pickup",
            {
                "p_dispatch_id": str(dispatch_id),
                "p_full_amount": full_amount,
            },
        ).execute()

        result = credit_resp.data

        if not result.get("success", False):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                result.get("message", "Failed to credit dispatch escrow"),
            )

        # Update status to IN_TRANSIT
        await (
            supabase.table("delivery_orders")
            .update({"status": "IN_TRANSIT"})
            .eq("id", str(order_id))
            .execute()
        )

        # Notify sender
        await notify_user(
            user_id=UUID(order["sender_id"]),
            title="Package Picked Up!",
            body="The rider has picked up your package and is on the way.",
            data={"order_id": str(order_id), "type": "DELIVERY_PICKED_UP"},
            supabase=supabase,
        )

        return {
            "success": True,
            "message": "Package picked up. Dispatch escrow credited (virtual hold).",
            "status": "IN_TRANSIT",
            "full_fee_credited_to_dispatch_escrow": full_amount,
        }

    except Exception as e:
        raise HTTPException(500, f"Pickup failed: {str(e)}")


# ───────────────────────────────────────────────
# 6. Rider Confirm Delivery (Delivered)
# ───────────────────────────────────────────────
async def rider_confirm_delivery(
    order_id: UUID,
    rider_id: UUID,
    supabase: AsyncClient,
):
    try:
        order = (
            await supabase.table("delivery_orders")
            .select("id, status, rider_id")
            .eq("id", str(order_id))
            .single()
            .execute()
        )

        if not order.data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery order not found")

        if order.data["rider_id"] != str(rider_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your delivery order")

        if order.data["status"] != "IN_TRANSIT":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot confirm delivery. Current status: {order['status']}",
            )

        await (
            supabase.table("delivery_orders")
            .update({"status": "DELIVERED"})
            .eq("id", str(order_id))
            .execute()
        )

        # Notify sender
        # We need sender_id, let's get it if not available
        sender_id_resp = (
            await supabase.table("delivery_orders")
            .select("sender_id")
            .eq("id", str(order_id))
            .single()
            .execute()
        )
        if sender_id_resp.data:
            await notify_user(
                user_id=UUID(sender_id_resp.data["sender_id"]),
                title="Package Delivered!",
                body="Your package has been delivered. Please confirm receipt to release payment.",
                data={"order_id": str(order_id), "type": "DELIVERY_DELIVERED"},
                supabase=supabase,
            )

        return {
            "success": True,
            "message": "Delivery confirmed! Waiting for sender to confirm receipt.",
            "status": "DELIVERED",
        }

    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Delivery confirmation failed: {str(e)}",
        )


# ───────────────────────────────────────────────
# 7. Sender Confirm Receipt (Release Payment)
# ───────────────────────────────────────────────
async def sender_confirm_receipt(
    order_id: UUID,
    sender_id: UUID,
    supabase: AsyncClient,
    request: Optional[Request] = None,
):
    logger.info(
        "sender_confirm_receipt", order_id=str(order_id), sender_id=str(sender_id)
    )
    try:
        order = (
            await supabase.table("delivery_orders")
            .select("id, sender_id, status, dispatch_id, delivery_fee")
            .eq("id", str(order_id))
            .single()
            .execute()
        ).data

        if not order:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery order not found")

        if order["sender_id"] != str(sender_id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "You are not the sender of this package"
            )

        if order["status"] != "DELIVERED":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot confirm receipt. Current status: {order['status']}",
            )

        full_amount = order["delivery_fee"]
        commission_rate = await get_commission_rate("DELIVERY", supabase)
        dispatch_amount = full_amount * commission_rate

        dispatch_id = order["dispatch_id"]

        # Release from dispatch escrow → dispatch balance + platform fee
        release_resp = await supabase.rpc(
            "release_from_dispatch_escrow",
            {
                "p_dispatch_id": str(dispatch_id),
                "p_full_amount": full_amount,
                "p_dispatch_amount": dispatch_amount,
            },
        ).execute()

        result = release_resp.data

        if not result.get("success", False):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                result.get("message", "Failed to release funds"),
            )

        await (
            supabase.table("delivery_orders")
            .update({"status": "COMPLETED"})
            .eq("id", str(order_id))
            .execute()
        )

        # Notify rider/dispatch
        if order.get("rider_id"):
            await notify_user(
                user_id=UUID(order["rider_id"]),
                title="Delivery Completed!",
                body=f"The sender has confirmed receipt. NGN {dispatch_amount} has been added to your balance.",
                data={"order_id": str(order_id), "type": "DELIVERY_COMPLETED"},
                supabase=supabase,
            )

        await log_audit_event(
            supabase,
            entity_type="DELIVERY_ORDER",
            entity_id=str(order_id),
            action="SENDER_CONFIRM_RECEIPT",
            old_value={"status": "DELIVERED"},
            new_value={"status": "COMPLETED"},
            change_amount=Decimal(str(full_amount)),
            actor_id=str(sender_id),
            actor_type="USER",
            notes=f"Sender confirmed receipt. Dispatch escrow cleared, dispatch got {dispatch_amount} to balance, platform kept remainder",
            request=request,
        )

        logger.info(
            "sender_confirm_receipt_success",
            order_id=str(order_id),
            dispatch_amount=float(dispatch_amount),
            platform_fee=float(full_amount - dispatch_amount),
        )

        return {
            "success": True,
            "message": "Receipt confirmed! Dispatch paid commission, platform kept fee remainder.",
            "status": "COMPLETED",
            "dispatch_received": dispatch_amount,
            "platform_fee": full_amount - dispatch_amount,
            "total_cleared_from_sender_escrow": full_amount,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "sender_confirm_receipt_error",
            order_id=str(order_id),
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Confirmation failed: {str(e)}"
        )


# ───────────────────────────────────────────────
# 8. Cancel Delivery
# ───────────────────────────────────────────────
async def cancel_delivery(
    order_id: UUID,
    data: DeliveryCancelRequest,
    current_user_id: UUID,
    current_user_type: str,
    supabase: AsyncClient,
) -> DeliveryCancelResponse:
    try:
        order = (
            await supabase.table("delivery_orders")
            .select("status, sender_id, rider_id")
            .eq("id", str(order_id))
            .single()
            .execute()
        ).data

        if not order:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery order not found")

        is_sender = str(current_user_id) == order["sender_id"]
        is_rider = (
            current_user_type == "RIDER" and str(current_user_id) == order["rider_id"]
        )

        if not (is_sender or is_rider):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "You cannot cancel this delivery"
            )

        cancelled_by = "SENDER" if is_sender else "RIDER"

        if order["status"] in ("DELIVERED", "COMPLETED"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Cannot cancel completed delivery"
            )

        await (
            supabase.table("delivery_orders")
            .update(
                {
                    "status": "CANCELLED",
                    "cancelled_by": cancelled_by,
                    "cancel_reason": data.reason,
                    "cancelled_at": "now()",
                }
            )
            .eq("id", str(order_id))
            .execute()
        )

        refunded = order["status"] in ("PAID_NEEDS_RIDER", "ASSIGNED")

        message = (
            "Delivery cancelled. Full refund processed."
            if refunded
            else "Delivery cancelled. Rider will return item. You will pay delivery fee on receipt confirmation."
        )

        return DeliveryCancelResponse(
            order_id=order_id,
            delivery_status="CANCELLED",
            refunded=refunded,
            message=message,
        )

    except Exception as e:
        raise HTTPException(500, f"Cancel failed: {str(e)}")


# ───────────────────────────────────────────────
# 9. Get Delivery Orders
# ───────────────────────────────────────────────
async def get_delivery_orders(
    current_user_id: UUID,
    is_admin: bool,
    limit: int = 20,
    offset: int = 0,
    status_filter: Optional[str] = None,
    supabase=None,
) -> DeliveryOrdersResponse:
    try:
        query = (
            supabase.table("delivery_orders")
            .select("""
                id,
                order_number,
                sender_id,
                receiver_phone,
                pickup_location,
                destination,
                delivery_fee,
                total_price,
                status,
                payment_status,
                escrow_status,
                rider_id,
                dispatch_id,
                rider_phone_number,
                created_at,
                updated_at,
                package_image_url,
                image_url,
                profiles!inner(full_name as rider_name)  # rider name
            """)
            .order("status", desc=False)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )

        if not is_admin:
            query = query.or_(
                f"sender_id.eq.{current_user_id},"
                f"rider_id.eq.{current_user_id},"
                f"dispatch_id.eq.{current_user_id}"
            )

        if status_filter:
            query = query.eq("status", status_filter)

        resp = await query.execute()
        orders = resp.data or []

        count_query = supabase.table("delivery_orders").select("count", count="exact")
        if not is_admin:
            count_query = count_query.or_(
                f"sender_id.eq.{current_user_id},"
                f"rider_id.eq.{current_user_id},"
                f"dispatch_id.eq.{current_user_id}"
            )
        total_count = (await count_query.execute()).count or 0

        return DeliveryOrdersResponse(
            orders=[DeliveryOrderListItem(**o) for o in orders],
            total_count=total_count,
            has_more=(offset + len(orders)) < total_count,
        )

    except Exception as e:
        raise HTTPException(500, f"Failed to fetch delivery orders: {str(e)}")


# from fastapi import HTTPException, status, Request
# from typing import Optional
# import uuid
# from uuid import UUID
# import datetime
# from app.schemas.delivery_schemas import (
#     PackageDeliveryCreate,
#     AssignRiderRequest,
#     AssignRiderResponse,
#     DeliveryCancelRequest,
#     DeliveryCancelResponse,
#     DeliveryAction,
#     DeliveryActionResponse,
# DeliveryOrdersResponse, DeliveryOrderListItem
# )
# from app.schemas.common import (
#     PaymentInitializationResponse,
#     PaymentCustomerInfo,
#     PaymentCustomization,
# )
# from supabase import AsyncClient
# from app.utils.redis_utils import save_pending
# from app.utils.commission import get_commission_rate
# from app.config.config import settings
# from app.config.logging import logger
# from app.utils.audit import log_audit_event
# from decimal import Decimal
#
#
# # 1. Initiate Delivery (Pay First — No Rider Yet)
# async def initiate_delivery_payment(
#     data: PackageDeliveryCreate,
#     sender_id: UUID,
#     supabase: AsyncClient,
#     customer_info: dict,
#     request: Optional[Request] = None,
# ) -> dict:
#     """
#     Step 1: Calculate delivery fee using PostGIS RPC
#     Step 2: Generate tx_ref
#     Step 3: Save pending state in Redis
#     Step 4: Return data for Flutterwave RN SDK (no payment link)
#     """
#     logger.info(
#         "initiate_delivery_payment",
#         sender_id=str(sender_id),
#         pickup=data.pickup_location,
#         destination=data.destination,
#     )
#     try:
#         # 1. Calculate distance using RPC
#         distance_resp = await supabase.rpc(
#             "calculate_distance",
#             {
#                 "p_lat1": data.pickup_coordinates[0],
#                 "p_lng1": data.pickup_coordinates[1],
#                 "p_lat2": data.dropoff_coordinates[0],
#                 "p_lng2": data.dropoff_coordinates[1],
#             },
#         ).execute()
#
#         distance_km = distance_resp.data if distance_resp.data is not None else 0.0
#
#         # 2. Get charges from DB
#         charges = (
#             await supabase.table("charges_and_commissions")
#             .select("base_delivery_fee, delivery_fee_per_km")
#             .single()
#             .execute()
#         )
#
#         if not charges.data:
#             raise HTTPException(500, "Charges configuration missing")
#
#         base_fee = charges.data["base_delivery_fee"]
#         per_km_fee = charges.data["delivery_fee_per_km"]
#
#         # 3. Calculate final fee
#         delivery_fee = base_fee + (per_km_fee * distance_km)
#         delivery_fee = round(delivery_fee, 2)
#
#         # 4. Generate unique tx_ref
#         tx_ref = f"DEL-{uuid.uuid4().hex[:12].upper()}"
#
#         # 5. Save pending state in Redis
#         pending_data = {
#             "sender_id": str(sender_id),
#             "delivery_data": data.model_dump(),
#             "delivery_fee": float(delivery_fee),
#             "tx_ref": tx_ref,
#             "package_image_url": data.package_image_url,
#             "distance_km": distance_km,
#             "created_at": datetime.datetime.now().isoformat(),
#         }
#         await save_pending(f"pending_delivery_{tx_ref}", pending_data, expire=1800)
#
#         # 6. Return data for Flutterwave RN SDK
#         return PaymentInitializationResponse(
#             tx_ref=tx_ref,
#             amount=Decimal(str(delivery_fee)),
#             public_key=settings.FLUTTERWAVE_PUBLIC_KEY,
#             currency="NGN",
#             customer=PaymentCustomerInfo(**customer_info),
#             customization=PaymentCustomization(
#                 title="Servipal Delivery",
#                 description=f"From {data.pickup_location} to {data.destination} ({distance_km:.1f} km)",
#             ),
#             message="Ready for payment — use Flutterwave SDK",
#         ).model_dump()
#
#     except Exception as e:
#         logger.error(
#             "initiate_delivery_payment_error",
#             sender_id=str(sender_id),
#             error=str(e),
#             exc_info=True,
#         )
#         raise HTTPException(500, f"Payment initiation failed: {str(e)}")
#
#
# # 2. Assign Rider After Payment
# async def assign_rider_to_order(
#     order_id: UUID, data: AssignRiderRequest, sender_id: UUID, supabase: AsyncClient
# ) -> AssignRiderResponse:
#     try:
#         order = (
#             await supabase.table("delivery_orders")
#             .select("id, sender_id, order_status")
#             .eq("id", str(order_id))
#             .single()
#             .execute()
#         )
#
#         if order.data["sender_id"] != str(sender_id):
#             raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This is not your order")
#
#         if order.data["order_status"] != "PAID_NEEDS_RIDER":
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST, detail="Order already has a rider or is not ready for assignment"
#             )
#
#         assign_resp = await supabase.rpc(
#             "assign_rider_to_paid_delivery",
#             {"order_id": str(order_id), "chosen_rider_id": str(data.rider_id)},
#         ).execute()
#
#         result = assign_resp.data
#
#         return AssignRiderResponse(
#             success=result["success"],
#             message=result["message"],
#             delivery_status=result.get("delivery_status", "ASSIGNED"),
#             rider_name=result.get("rider_name"),
#         )
#
#     except Exception as e:
#         error_msg = str(e)
#         if "Rider not available" in error_msg:
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST, detail="Selected rider is no longer available. Please choose another."
#             )
#         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to assign rider")
#
#
# async def rider_delivery_action(
#     delivery_id: UUID, data: DeliveryAction, rider_id: UUID, supabase: AsyncClient
# ) -> DeliveryActionResponse:
#     try:
#         delivery_resp = (
#             await supabase.table("deliveries")
#             .select("id, order_id, rider_id, delivery_status")
#             .eq("id", str(delivery_id))
#             .single()
#             .execute()
#         )
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
#             raise HTTPException(
#                 400, f"Delivery is {delivery['delivery_status']}, cannot act"
#             )
#
#         new_status = "ACCEPTED" if data.action == "accept" else "PENDING"
#
#         await (
#             supabase.table("deliveries")
#             .update({"delivery_status": new_status})
#             .eq("id", str(delivery_id))
#             .execute()
#         )
#
#
#
#         message = (
#             "Delivery accepted successfully!"
#             if data.action == "accept"
#             else "Delivery declined."
#         )
#
#         return DeliveryActionResponse(
#             delivery_id=delivery_id,
#             order_id=delivery["order_id"],
#             delivery_status=new_status,
#             message=message,
#
#         )
#
#     except Exception as e:
#         raise HTTPException(500, f"Action failed: {str(e)}")
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
#         delivery_resp = (
#             await supabase.table("deliveries")
#             .select("""
#                 id,
#                 order_id,
#                 delivery_status,
#                 rider_id,
#                 sender_id,
#                 dispatch_id,
#                 delivery_fee
#             """)
#             .eq("id", str(delivery_id))
#             .single()
#             .execute()
#         )
#
#         delivery = delivery_resp.data
#
#         # 2. Security validation
#         if delivery["rider_id"] != str(rider_id):
#             raise HTTPException(
#                 status_code=status.HTTP_403_FORBIDDEN,
#                 detail="This delivery is not assigned to you",
#             )
#
#         if delivery["delivery_status"] not in ("ACCEPTED", "ASSIGNED"):
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail=f"Cannot pick up. Current status: {delivery['delivery_status']}",
#             )
#
#         # 3. Get the transaction record
#         tx_resp = (
#             await supabase.table("transactions")
#             .select("id, amount, from_user_id, to_user_id")
#             .eq("order_id", delivery["order_id"])
#             .single()
#             .execute()
#         )
#
#         tx = tx_resp.data
#         full_amount = tx["amount"]
#         dispatch_amount = full_amount
#
#         dispatch_id = delivery["dispatch_id"] or tx["to_user_id"]
#
#         # 4. Add amount to dispatch escrow ONLY (positive delta)
#         await supabase.rpc(
#             "update_wallet_balance",
#             {
#                 "p_user_id": str(dispatch_id),
#                 "p_delta": dispatch_amount,
#                 "p_field": "escrow_balance",
#             },
#         ).execute()
#
#         # 5. Update delivery status to IN_TRANSIT
#         await (
#             supabase.table("deliveries")
#             .update({"delivery_status": "IN_TRANSIT"})
#             .eq("id", str(delivery_id))
#             .execute()
#         )
#
#         # 6. Update transaction status
#         await (
#             supabase.table("transactions")
#             .update({"status": "TRANSFERRED_TO_DISPATCH"})
#             .eq("id", tx["id"])
#             .execute()
#         )
#
#         return {
#             "success": True,
#             "message": "Package picked up. Funds secured in dispatch escrow.",
#             "delivery_status": "IN_TRANSIT",
#             "amount_secured_in_dispatch_escrow": dispatch_amount,
#         }
#
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Pickup failed: {str(e)}",
#         )
#
#
# async def rider_confirm_delivery(
#     delivery_id: UUID,
#     rider_id: UUID,
#     supabase: AsyncClient,
# ):
#     try:
#         # 1. Fetch delivery
#         delivery_resp = (
#             await supabase.table("deliveries")
#             .select("id, delivery_status, rider_id, order_id, image_url")
#             .eq("id", str(delivery_id))
#             .single()
#             .execute()
#         )
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
#                 f"Cannot confirm delivery. Current status: {delivery['delivery_status']}",
#             )
#
#         # 4. Update delivery to DELIVERED
#         await (
#             supabase.table("deliveries")
#             .update(
#                 {
#                     "delivery_status": "DELIVERED",
#                 }
#             )
#             .eq("id", str(delivery_id))
#             .execute()
#         )
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
# # Updated sender_confirm_receipt (atomic release)
# async def sender_confirm_receipt(
#     delivery_id: UUID,
#     sender_id: UUID,
#     supabase: AsyncClient,
#     request: Optional[Request] = None,
# ):
#     logger.info(
#         "sender_confirm_receipt", delivery_id=str(delivery_id), sender_id=str(sender_id)
#     )
#     try:
#         delivery_resp = (
#             await supabase.table("deliveries")
#             .select("""
#                 id,
#                 order_id,
#                 delivery_status,
#                 sender_id,
#                 dispatch_id,
#                 delivery_fee
#             """)
#             .eq("id", str(delivery_id))
#             .single()
#             .execute()
#         )
#
#         delivery = delivery_resp.data
#
#         if delivery["sender_id"] != str(sender_id):
#             raise HTTPException(403, "You are not the sender of this package")
#
#         if delivery["delivery_status"] != "DELIVERED":
#             raise HTTPException(
#                 400,
#                 f"Cannot confirm receipt. Current status: {delivery['delivery_status']}",
#             )
#
#         tx_resp = (
#             await supabase.table("transactions")
#             .select("id, amount, to_user_id, status")
#             .eq("order_id", delivery["order_id"])
#             .single()
#             .execute()
#         )
#
#         tx = tx_resp.data
#
#         if tx["status"] == "RELEASED":
#             raise HTTPException(400, "Receipt already confirmed and payment released")
#
#         full_amount = tx["amount"]
#         commission_rate = await get_commission_rate("DELIVERY", supabase=supabase)
#         dispatch_amount = full_amount * commission_rate
#         dispatch_id = delivery["dispatch_id"] or tx["to_user_id"]
#
#         # Atomic release
#         await supabase.rpc(
#             "release_delivery_payment",
#             {
#                 "p_sender_id": str(sender_id),
#                 "p_dispatch_id": str(dispatch_id),
#                 "p_full_amount": full_amount,
#                 "p_dispatch_amount": dispatch_amount,
#             },
#         ).execute()
#
#         await (
#             supabase.table("transactions")
#             .update({"status": "RELEASED"})
#             .eq("id", tx["id"])
#             .execute()
#         )
#
#         await (
#             supabase.table("deliveries")
#             .update({"delivery_status": "COMPLETED"})
#             .eq("id", str(delivery_id))
#             .execute()
#         )
#
#         # Audit log
#         await log_audit_event(
#             supabase,
#             entity_type="DELIVERY",
#             entity_id=str(delivery_id),
#             action="SENDER_CONFIRM_RECEIPT",
#             old_value={"delivery_status": "DELIVERED", "escrow_status": "HELD"},
#             new_value={"delivery_status": "COMPLETED", "escrow_status": "RELEASED"},
#             change_amount=Decimal(str(dispatch_amount)),
#             actor_id=str(sender_id),
#             actor_type="USER",
#             notes=f"Sender confirmed receipt, payment released to dispatch",
#             request=request,
#         )
#
#         logger.info(
#             "sender_confirm_receipt_success",
#             delivery_id=str(delivery_id),
#             amount_released=float(dispatch_amount),
#         )
#         return {
#             "success": True,
#             "message": "Receipt confirmed! Payment released to dispatch.",
#             "delivery_status": "COMPLETED",
#             "amount_released_to_dispatch": dispatch_amount,
#             "total_deducted_from_sender_escrow": full_amount,
#             "dispatch_id": dispatch_id,
#         }
#
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(
#             "sender_confirm_receipt_error",
#             delivery_id=str(delivery_id),
#             error=str(e),
#             exc_info=True,
#         )
#         raise HTTPException(500, f"Confirmation failed: {str(e)}")
#
#
# async def cancel_delivery(
#     delivery_id: UUID,
#     data: DeliveryCancelRequest,
#     current_user_id: UUID,
#     current_user_type: str,
#     supabase: AsyncClient,
# ) -> DeliveryCancelResponse:
#     # Fetch delivery
#
#     del_resp = (
#         await supabase.table("deliveries")
#         .select("delivery_status, rider_id, sender_id, order_id")
#         .eq("id", str(delivery_id))
#         .single()
#         .execute()
#     )
#
#     delivery = del_resp.data
#
#     # Authorization
#     is_rider = (
#         current_user_type == "RIDER" and str(current_user_id) == delivery["rider_id"]
#     )
#     is_sender = str(current_user_id) == delivery["sender_id"]
#
#     if not (is_rider or is_sender):
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="You cannot cancel this delivery",
#         )
#
#     cancelled_by = "RIDER" if is_rider else "SENDER"
#
#     # Prevent cancel if already completed/delivered
#     if delivery["delivery_status"] in ("DELIVERED", "COMPLETED"):
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Cannot cancel completed delivery",
#         )
#
#     # Update with cancel info — trigger handles everything else
#     await (
#         supabase.table("deliveries")
#         .update(
#             {
#                 "cancelled_by": cancelled_by,
#                 "cancel_reason": data.reason,
#                 "delivery_status": "CANCELLED"
#                 if delivery["delivery_status"] in ("ASSIGNED", "ACCEPTED")
#                 else delivery["delivery_status"],
#             }
#         )
#         .eq("id", str(delivery_id))
#         .execute()
#     )
#
#     refunded = delivery["delivery_status"] in ("ASSIGNED", "ACCEPTED")
#
#     message = (
#         "Delivery cancelled. Full refund processed."
#         if refunded
#         else "Delivery cancelled. Rider will return item. You will pay delivery fee on receipt confirmation."
#     )
#
#     return DeliveryCancelResponse(
#         order_id=delivery["order_id"],
#         delivery_status="CANCELLED",
#         refunded=refunded,
#         message=message,
#     )
#
#
#
# async def get_delivery_orders(
#     current_user_id: UUID,
#     user_type: str,
#     limit: int = 20,
#     offset: int = 0,
#     status_filter: Optional[str] = None,
#     supabase = None  # injected dependency
# ) -> DeliveryOrdersResponse:
#     """
#     Role-based delivery orders fetch:
#     - SENDER: only their own orders
#     - RIDER: only orders assigned to them
#     - DISPATCH: all orders for riders under their dispatch group
#     - ADMIN/MODERATOR: all orders
#     Sorted: latest non-completed first, then completed
#     """
#     try:
#         query = supabase.table("delivery_orders")\
#             .select("""
#                 id,
#                 order_number,
#                 sender_id,
#                 receiver_phone,
#                 pickup_location,
#                 destination,
#                 delivery_fee,
#                 grand_total,
#                 order_status,
#                 payment_status,
#                 escrow_status,
#                 rider_id,
#                 created_at,
#                 updated_at,
#                 profiles!inner(full_name as rider_name)  # join rider name
#             """)\
#             .order("order_status", desc=False)  # non-completed first (assuming ASC order_status sorts pending → completed)
#             .order("created_at", desc=True)\
#             .range(offset, offset + limit - 1)
#
#         # Role-based filtering
#         if user_type == "SENDER":
#             query = query.eq("sender_id", str(current_user_id))
#
#         elif user_type == "RIDER":
#             query = query.eq("rider_id", str(current_user_id))
#
#         elif user_type == "DISPATCH":
#             # Find all riders under this dispatcher
#             riders = await supabase.table("profiles")\
#                 .select("id")\
#                 .eq("dispatcher_id", str(current_user_id))\
#                 .eq("user_type", "RIDER")\
#                 .execute()
#
#             rider_ids = [r["id"] for r in riders.data or []]
#             if rider_ids:
#                 query = query.in_("rider_id", rider_ids)
#             else:
#                 # No riders → empty result
#                 return DeliveryOrdersResponse(orders=[], total_count=0, has_more=False)
#
#         elif user_type in ["ADMIN", "MODERATOR", "SUPERADMIN"]:
#             # No filter — see everything
#             pass
#
#         else:
#             raise HTTPException(403, "Insufficient permissions to view delivery orders")
#
#         # Optional status filter
#         if status_filter:
#             query = query.eq("order_status", status_filter)
#
#         # Execute
#         resp = await query.execute()
#         orders = resp.data or []
#
#         # Total count (for pagination)
#         count_query = supabase.table("delivery_orders").select("count", count="exact")
#         # Apply same filters to count query (repeat logic or extract to helper)
#         total_count = (await count_query.execute()).count or 0
#
#         return DeliveryOrdersResponse(
#             orders=[DeliveryOrderListItem(**o) for o in orders],
#             total_count=total_count,
#             has_more=(offset + len(orders)) < total_count
#         )
#
#     except Exception as e:
#         raise HTTPException(500, f"Failed to fetch delivery orders: {str(e)}")
