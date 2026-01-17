from fastapi import APIRouter, Depends, Query, File, UploadFile, Form, Request
from typing import List, Optional
from uuid import UUID
from decimal import Decimal
from app.schemas.laundry_schemas import (
    LaundryVendorDetailResponse,
    LaundryItemDetailResponse,
    LaundryItemUpdate,
    LaundryVendorMarkReadyResponse,
    LaundryCustomerConfirmResponse,
    LaundryOrderCreate,
)

from app.services import laundry_service
from app.dependencies.auth import get_current_profile, require_user_type
from app.dependencies.auth import get_customer_contact_info
from app.schemas.user_schemas import UserType
from app.database.supabase import get_supabase_client
from supabase import AsyncClient
from app.schemas.common import (
    VendorOrderAction,
    VendorOrderActionResponse,
    VendorResponse,
)

router = APIRouter(prefix="/api/v1/laundry", tags=["Laundry"])


@router.get("/vendors", response_model=List[VendorResponse])
async def list_laundry_vendors(
    lat: Optional[float] = Query(None),
    lng: Optional[float] = Query(None),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    List laundry vendors.
    
    Args:
        lat (float, optional): Latitude for nearby search.
        lng (float, optional): Longitude for nearby search.
        
    Returns:
        List[VendorResponse]: List of vendors.
    """
    return await laundry_service.get_laundry_vendors(supabase, lat, lng)


@router.get("/vendors/{vendor_id}", response_model=LaundryVendorDetailResponse)
async def get_laundry_vendor_detail(
    vendor_id: UUID, supabase: AsyncClient = Depends(get_supabase_client)
):
    """
    Get laundry vendor details and menu.
    
    Args:
        vendor_id (UUID): The vendor ID.
        
    Returns:
        LaundryVendorDetailResponse: Vendor details.
    """
    return await laundry_service.get_laundry_vendor_detail(vendor_id, supabase)


@router.post("/initiate-payment")
async def initiate_laundry_payment_endpoint(
    data: LaundryOrderCreate,
    current_profile: dict = Depends(get_current_profile),
    customer_info: dict = Depends(get_customer_contact_info),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Initiate laundry payment.
    
    Args:
        data (LaundryOrderCreate): Order details.
        
    Returns:
        dict: Flutterwave RN SDK payment data.
    """
    return await laundry_service.initiate_laundry_payment(
        data, current_profile["id"], customer_info, supabase
    )


@router.post("/orders/{order_id}/action", response_model=VendorOrderActionResponse)
async def vendor_laundry_order_action_endpoint(
    order_id: UUID,
    data: VendorOrderAction,
    current_profile: dict = Depends(require_user_type([UserType.LAUNDRY_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Vendor accepts or rejects laundry order.
    
    Args:
        order_id (UUID): The order ID.
        data (VendorOrderAction): The action (accept/reject).
        
    Returns:
        VendorOrderActionResponse: Action result.
    """
    return await laundry_service.vendor_laundry_order_action(
        order_id, data, current_profile["id"], supabase
    )


@router.post(
    "/orders/{order_id}/mark-ready", response_model=LaundryVendorMarkReadyResponse
)
async def vendor_mark_laundry_ready_endpoint(
    order_id: UUID,
    current_profile: dict = Depends(require_user_type([UserType.LAUNDRY_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Vendor marks laundry order as ready.
    
    Args:
        order_id (UUID): The order ID.
        
    Returns:
        LaundryVendorMarkReadyResponse: Updated status.
    """
    return await laundry_service.vendor_mark_laundry_order_ready(
        order_id, current_profile["id"], supabase
    )


@router.post(
    "/orders/{order_id}/confirm-receipt", response_model=LaundryCustomerConfirmResponse
)
async def customer_confirm_laundry_receipt(
    order_id: UUID,
    request: Request,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Customer confirms receipt of laundry order.
    Releases payment to vendor.
    
    Args:
        order_id (UUID): The order ID.
        
    Returns:
        LaundryCustomerConfirmResponse: Confirmation result.
    """
    from app.config.logging import logger

    logger.info(
        "customer_confirm_laundry_receipt_endpoint",
        order_id=str(order_id),
        customer_id=current_profile["id"],
    )
    return await laundry_service.customer_confirm_laundry_order(
        order_id, current_profile["id"], supabase, request
    )


@router.get("/menu")
async def get_my_laundry_menu(
    current_profile: dict = Depends(require_user_type([UserType.LAUNDRY_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Vendor views their own laundry menu.
    
    Returns:
        dict: List of laundry items.
    """
    resp = (
        await supabase.table("laundry_items")
        .select("*")
        .eq("vendor_id", current_profile["id"])
        .eq("is_deleted", False)
        .execute()
    )
    return {"items": resp.data}


@router.post("/menu/items")
async def add_laundry_item_with_images(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    price: Decimal = Form(...),
    images: List[UploadFile] = File([]),
    current_profile: dict = Depends(require_user_type([UserType.LAUNDRY_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Vendor adds a new laundry item with images.
    
    Args:
        name (str): Item name.
        description (str, optional): item description.
        price (Decimal): Price.
        images (List[UploadFile]): Images.
        
    Returns:
        dict: Created item details.
    """
    return await laundry_service.create_laundry_item_with_images(
        name=name,
        description=description,
        price=price,
        images=images,
        vendor_id=current_profile["id"],
        supabase=supabase,
    )


@router.patch("/menu/items/{item_id}", response_model=LaundryItemDetailResponse)
async def update_laundry_item_endpoint(
    item_id: UUID,
    data: LaundryItemUpdate,
    current_profile: dict = Depends(require_user_type([UserType.LAUNDRY_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Vendor updates a laundry item.
    
    Args:
        item_id (UUID): The item ID.
        data (LaundryItemUpdate): Fields to update.
        
    Returns:
        LaundryItemDetailResponse: Updated item.
    """
    return await laundry_service.update_laundry_item(
        item_id, data, current_profile["id"], supabase
    )


@router.delete("/menu/items/{item_id}")
async def delete_laundry_item_endpoint(
    item_id: UUID,
    current_profile: dict = Depends(require_user_type([UserType.LAUNDRY_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Vendor archives a laundry item"""
    return await laundry_service.delete_laundry_item(
        item_id, current_profile["id"], supabase
    )
