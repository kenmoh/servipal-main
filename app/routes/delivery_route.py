from fastapi import APIRouter, Depends, HTTPException, Request, Form, File, UploadFile
from uuid import UUID
import uuid
from typing import Optional

from app.schemas.delivery_schemas import (
    PackageDeliveryCreate,
    AssignRiderRequest,
    AssignRiderResponse,
    DeliveryAction,
    DeliveryActionResponse,
    DeliveryCancelRequest,
    DeliveryCancelResponse,
    DeliveryType,
)
from app.services.delivery_service import (
    initiate_delivery_payment,
    assign_rider_to_order,
    rider_delivery_action,
    rider_picked_up,
    rider_confirm_delivery,
    sender_confirm_receipt,
    cancel_delivery,
    get_delivery_orders,
)
from app.dependencies.auth import (
    get_current_profile,
    require_user_type,
    get_customer_contact_info,
    is_admin_user,
)
from app.database.supabase import get_supabase_client
from app.schemas.user_schemas import UserType
from app.config.logging import logger
from app.utils.storage import upload_to_supabase_storage

router = APIRouter(tags=["Deliveries"], prefix="/api/v1/delivery")


# ───────────────────────────────────────────────
# 1. Initiate Payment (Create Draft Order + Fee)
# ───────────────────────────────────────────────
@router.post("/initiate-payment")
async def initiate_delivery_payment_endpoint(
    receiver_phone: str = Form(...),
    pickup_location: str = Form(...),
    destination: str = Form(...),
    pickup_lat: float = Form(...),
    pickup_lng: float = Form(...),
    dropoff_lat: float = Form(...),
    dropoff_lng: float = Form(...),
    additional_info: Optional[str] = Form(...),
    delivery_type: DeliveryType = Form("STANDARD"),
    package_image: Optional[UploadFile] = File(...),
    current_profile: dict = Depends(get_current_profile),
    supabase=Depends(get_supabase_client),
    customer_info: dict = Depends(get_customer_contact_info),
):
    """
    Initiate a delivery request and calculate payment.
    
    Returns:
        dict: Payment initiation details.
    """
    # Upload image if provided

    folder = f"deliveries/{uuid.uuid4().hex[:8]}"
    url = await upload_to_supabase_storage(
        file=package_image, supabase=supabase, bucket="delivery-images", folder=folder
    )

    data = PackageDeliveryCreate(
        receiver_phone=receiver_phone,
        pickup_location=pickup_location,
        destination=destination,
        pickup_coordinates=(pickup_lat, pickup_lng),
        dropoff_coordinates=(dropoff_lat, dropoff_lng),
        additional_info=additional_info,
        delivery_type=delivery_type,
        package_image_url=url,
    )

    return await initiate_delivery_payment(
        data, current_profile["id"], supabase, customer_info
    )


# ───────────────────────────────────────────────
# 2. Assign Rider After Payment
# ───────────────────────────────────────────────
@router.post(
    "/delivery-orders/{order_id}/assign-rider", response_model=AssignRiderResponse
)
async def assign_rider_endpoint(
    order_id: UUID,
    data: AssignRiderRequest,
    current_profile: dict = Depends(get_current_profile),
    supabase=Depends(get_supabase_client),
):
    """
    Sender chooses rider after successful payment.
    Re-checks rider availability.
    """
    return await assign_rider_to_order(order_id, data, current_profile["id"], supabase)


# ───────────────────────────────────────────────
# 3. Get Available Riders (Distance-Based)
# ───────────────────────────────────────────────
@router.get("/available-riders")
async def get_available_riders(
    lat: float,
    lng: float,
    max_distance_km: int = 20,
    supabase=Depends(get_supabase_client),
):
    """
    Get list of available riders near sender location, sorted by distance.
    Only riders with has_delivery=false, online, active.
    """
    # Call RPC or query with PostGIS
    riders = await supabase.rpc(
        "get_available_riders",
        {"p_lat": lat, "p_lng": lng, "p_max_km": max_distance_km},
    ).execute()

    return {"riders": riders.data}





# ───────────────────────────────────────────────
# 5. Rider Accept/Decline
# ───────────────────────────────────────────────
@router.post("/{delivery_id}/action", response_model=DeliveryActionResponse)
async def rider_act_on_delivery(
    delivery_id: UUID,
    action_data: DeliveryAction,
    current_profile: dict = Depends(require_user_type([UserType.RIDER])),
    supabase=Depends(get_supabase_client),
):
    """
    Rider accepts or declines a delivery request.
    
    Args:
        delivery_id (UUID): The delivery ID.
        action_data (DeliveryAction): 'ACCEPT' or 'DECLINE'.
        
    Returns:
        DeliveryActionResponse: Result of the action.
    """
    return await rider_delivery_action(
        delivery_id, action_data, current_profile["id"], supabase
    )


# ───────────────────────────────────────────────
# 6. Rider Pickup
# ───────────────────────────────────────────────
@router.post("/{delivery_id}/pickup")
async def rider_pickup_package(
    delivery_id: UUID,
    current_profile: dict = Depends(require_user_type([UserType.RIDER])),
    supabase=Depends(get_supabase_client),
):
    """
    Rider confirms pickup of the package.
    
    Args:
        delivery_id (UUID): The delivery ID.
        
    Returns:
        dict: Status update.
    """
    return await rider_picked_up(delivery_id, current_profile["id"], supabase)


# ───────────────────────────────────────────────
# 7. Rider Confirm Delivered
# ───────────────────────────────────────────────
@router.post("/{delivery_id}/confirm-delivery")
async def rider_confirm_delivered(
    delivery_id: UUID,
    current_profile: dict = Depends(require_user_type([UserType.RIDER])),
    supabase=Depends(get_supabase_client),
):
    """
    Rider confirms delivery of the package.
    
    Args:
        delivery_id (UUID): The delivery ID.
        
    Returns:
        dict: Status update.
    """
    return await rider_confirm_delivery(delivery_id, current_profile["id"], supabase)


# ───────────────────────────────────────────────
# 8. Sender Confirm Receipt
# ───────────────────────────────────────────────
@router.post("/{delivery_id}/confirm-receipt")
async def confirm_package_received(
    delivery_id: UUID,
    request: Request = None,
    current_profile: dict = Depends(get_current_profile),
    supabase=Depends(get_supabase_client),
):
    """
    Sender confirms receipt of the package (if applicable).
    
    Args:
        delivery_id (UUID): The delivery ID.
        
    Returns:
        dict: Status update.
    """
    logger.info(
        "confirm_package_received_endpoint",
        delivery_id=str(delivery_id),
        sender_id=current_profile["id"],
    )
    return await sender_confirm_receipt(
        delivery_id, current_profile["id"], supabase, request
    )


# ───────────────────────────────────────────────
# 9. Cancel Delivery
# ───────────────────────────────────────────────
@router.post("/{delivery_id}/cancel", response_model=DeliveryCancelResponse)
async def cancel_delivery_endpoint(
    delivery_id: UUID,
    cancel_data: DeliveryCancelRequest,
    current_profile: dict = Depends(get_current_profile),
    supabase=Depends(get_supabase_client),
):
    """
    Cancel an existing delivery.
    
    Args:
        delivery_id (UUID): The delivery ID.
        cancel_data (DeliveryCancelRequest): Reason for cancellation.
        
    Returns:
        DeliveryCancelResponse: Cancellation result.
    """
    return await cancel_delivery(
        delivery_id,
        cancel_data,
        current_profile["id"],
        current_profile["user_type"],
        supabase,
    )


@router.get("/delivery-orders")
async def get_delivery_details(
    current_user=Depends(get_current_profile),
    supabase=Depends(get_supabase_client),
    is_admin: bool = Depends(is_admin_user),
):
    """
    Get delivery orders for the current user.
    
    Returns:
        list: List of delivery orders.
    """
    return await get_delivery_orders(
        current_user_id=current_user["id"], supabase=supabase, is_admin=is_admin
    )


# from fastapi import APIRouter, Depends
#
# from app.schemas.user_schemas import UserType
# from app.services.delivery_service import (
#     create_delivery_order,
#     rider_delivery_action,
#     rider_picked_up,
#     sender_confirm_receipt,
#     rider_confirm_delivery,
#     cancel_delivery
# )
# from app.schemas.delivery_schemas import *
# from uuid import UUID
# from app.dependencies.auth import get_current_profile, require_user_type
# from app.database.supabase import get_supabase_client
#
#
#
# router = APIRouter(tags=['Deliveries'], prefix="/api/v1/deliveries")
#
# @router.post("/delivery-orders", response_model=DeliveryOrderResponse)
# async def create_delivery(
#     order_data: DeliveryOrderCreate,
#     current_user: dict = Depends(get_current_profile),
#         supabase=Depends(get_supabase_client)
# ):
#     return await create_delivery_order(order_data, current_user["id"], supabase=supabase)
#
#
# @router.post("/{delivery_id}/action", response_model=DeliveryActionResponse)
# async def rider_act_on_delivery(
#     delivery_id: UUID,
#     action_data: DeliveryAction,
#     current_profile: dict = Depends(require_user_type([UserType.RIDER])),
#         supabase=Depends(get_supabase_client)
# ):
#     """
#     Rider accepts or declines an assigned delivery.
#     On decline: rider is automatically freed via trigger.
#     """
#     return await rider_delivery_action(delivery_id, action_data, current_profile["id"], supabase=supabase)
#
#
#
# @router.post("/{delivery_id}/pickup")
# async def rider_pickup_package(
#     delivery_id: UUID,
#     current_profile: dict = Depends(require_user_type([UserType.RIDER])),
#         supabase=Depends(get_supabase_client)
# ):
#     """
#     Rider confirms they have picked up the package from the sender.
#     Triggers money transfer from sender escrow → dispatch escrow.
#     """
#     return await rider_picked_up(delivery_id, current_profile["id"], supabase=supabase)
#
#
# @router.post("/{delivery_id}/confirm-delivery")
# async def rider_confirm_delivered(
#     delivery_id: UUID,
#     current_profile: dict = Depends(require_user_type([UserType.RIDER])),
#         supabase=Depends(get_supabase_client)
# ):
#     """
#     Rider confirms the package has been delivered.
#     Optional: upload photo proof.
#     Status changes to DELIVERED → sender can now confirm receipt.
#     """
#     return await rider_confirm_delivery(
#         delivery_id=delivery_id,
#         rider_id=current_profile["id"],
#         supabase=supabase
#     )
#
# @router.post("/{delivery_id}/confirm-receipt")
# async def confirm_package_received(
#     delivery_id: UUID,
#     current_profile: dict = Depends(get_current_profile),
#         supabase=Depends(get_supabase_client)
# ):
#     """
#     Sender confirms they have received the package.
#     Triggers final payment release from dispatch escrow → balance.
#     """
#     return await sender_confirm_receipt(delivery_id, current_profile["id"], supabase=supabase)
#
#
# @router.post("/{delivery_id}/cancel", response_model=DeliveryCancelResponse)
# async def cancel_delivery_endpoint(
#     delivery_id: UUID,
#     cancel_data: DeliveryCancelRequest,
#     current_profile: dict = Depends(get_current_profile),
#         supabase=Depends(get_supabase_client)
# ):
#     return await cancel_delivery(
#         delivery_id=delivery_id,
#         data=cancel_data,
#         current_user_id=current_profile["id"],
#         current_user_type=current_profile["user_type"],
#         supabase=supabase
#     )
