from fastapi import APIRouter, Depends, Query, Form, File, UploadFile, Request
from typing import List, Optional
from uuid import UUID
from decimal import Decimal
from supabase import AsyncClient
from typing_extensions import Literal

from app.schemas.food_schemas import (
    VendorCardResponse,
    VendorDetailResponse,
    FoodItemUpdate,
    CheckoutRequest,
)
from app.dependencies.auth import get_current_profile, require_user_type, get_customer_contact_info
from app.schemas.user_schemas import UserType
from app.services import food_service
from app.database.supabase import get_supabase_client
from app.config.logging import logger

router = APIRouter(prefix="/api/v1/food", tags=["Food"])


# ───────────────────────────────────────────────
# Vendor Browsing
# ───────────────────────────────────────────────
@router.get("/vendors", response_model=List[VendorCardResponse])
async def list_food_vendors(
    lat: Optional[float] = Query(None, description="Latitude for nearby search"),
    lng: Optional[float] = Query(None, description="Longitude for nearby search"),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Home screen - list of restaurant vendors (nearby if lat/lng provided)"""
    return await food_service.get_food_vendors(supabase, lat, lng)


@router.get("/vendors/{vendor_id}", response_model=VendorDetailResponse)
async def get_vendor_menu(
    vendor_id: UUID, supabase: AsyncClient = Depends(get_supabase_client)
):
    """Vendor detail page with full menu and categories"""
    return await food_service.get_vendor_detail(vendor_id, supabase)


# ───────────────────────────────────────────────
# Vendor Menu Management
# ───────────────────────────────────────────────
@router.post("/menu/items")
async def add_menu_item_with_images(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    price: Decimal = Form(...),
    category_id: Optional[UUID] = Form(None),
    sizes: Optional[List[str]] = Form([]),
    images: List[UploadFile] = File([]),
    request: Request = None,
    current_profile: dict = Depends(require_user_type([UserType.RESTAURANT_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Vendor adds a new food item with optional images"""
    logger.info("add_menu_item_endpoint", vendor_id=current_profile["id"], name=name)
    return await food_service.create_food_item_with_images(
        name=name,
        description=description,
        price=price,
        category_id=category_id,
        sizes=sizes,
        images=images,
        vendor_id=current_profile["id"],
        supabase=supabase,
        request=request,
    )


@router.patch("/menu/items/{item_id}")
async def update_menu_item(
    item_id: UUID,
    item_data: FoodItemUpdate,
    request: Request = None,
    current_profile: dict = Depends(require_user_type([UserType.RESTAURANT_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Vendor updates an existing food item"""
    logger.info(
        "update_menu_item_endpoint",
        vendor_id=current_profile["id"],
        item_id=str(item_id),
    )
    return await food_service.update_food_item(
        item_id, item_data, current_profile["id"], supabase, request
    )


@router.delete("/menu/items/{item_id}")
async def delete_menu_item(
    item_id: UUID,
    request: Request = None,
    current_profile: dict = Depends(require_user_type([UserType.RESTAURANT_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Vendor soft-deletes a food item"""
    logger.info(
        "delete_menu_item_endpoint",
        vendor_id=current_profile["id"],
        item_id=str(item_id),
    )
    return await food_service.delete_food_item(
        item_id, current_profile["id"], supabase, request
    )


@router.get("/menu")
async def get_my_menu(
    current_profile: dict = Depends(require_user_type([UserType.RESTAURANT_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Vendor views their own menu items"""
    resp = (
        await supabase.table("food_items")
        .select("*")
        .eq("vendor_id", current_profile["id"])
        .eq("is_deleted", False)
        .execute()
    )
    return {"items": resp.data}


# ───────────────────────────────────────────────
# Food Order Flow
# ───────────────────────────────────────────────
@router.post("/initiate-payment")
async def initiate_food_payment_endpoint(
    data: CheckoutRequest,
    request: Request = None,
    current_profile: dict = Depends(get_customer_contact_info),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Customer initiates food order payment.
    Validates items, calculates total, returns Flutterwave RN SDK data.
    """
    logger.info(
        "initiate_food_payment_endpoint",
        customer_id=current_profile["id"],
        vendor_id=str(data.vendor_id),
    )
    return await food_service.initiate_food_payment(data, current_profile["id"], supabase, request)


@router.post("/orders/{order_id}/action")
async def vendor_food_order_action_endpoint(
    order_id: UUID,
    action_data: Literal["accept", "reject"],
    request: Request = None,
    current_profile: dict = Depends(require_user_type([UserType.RESTAURANT_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Vendor accepts or rejects a food order"""
    logger.info(
        "vendor_food_order_action_endpoint", order_id=str(order_id), action=action_data
    )
    return await food_service.vendor_food_order_action(
        order_id=order_id,
        vendor_id=current_profile["id"],
        supabase=supabase,
        action=action_data,
        request=request,
    )


@router.post("/orders/{order_id}/mark-ready")
async def vendor_mark_ready_endpoint(
    order_id: UUID,
    current_profile: dict = Depends(require_user_type([UserType.RESTAURANT_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Vendor marks food order as ready for pickup/delivery"""
    return await food_service.vendor_mark_food_order_ready(
        order_id, current_profile["id"], supabase=supabase
    )


@router.post("/orders/{order_id}/confirm-receipt")
async def customer_confirm_food_endpoint(
    order_id: UUID,
    request: Request = None,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Customer confirms receipt of food order → releases payment to vendor"""
    logger.info(
        "customer_confirm_food_endpoint",
        order_id=str(order_id),
        customer_id=current_profile["id"],
    )
    return await food_service.customer_confirm_food_order(
        order_id, current_profile["id"], supabase, request
    )


# ───────────────────────────────────────────────
# Vendor Earnings (Bonus)
# ───────────────────────────────────────────────
@router.get("/earnings")
async def vendor_food_earnings(
    current_profile: dict = Depends(require_user_type([UserType.RESTAURANT_VENDOR])),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Vendor views their earnings dashboard"""
    # You can create an RPC: get_vendor_earnings(vendor_id)
    earnings = await supabase.rpc(
        "get_vendor_earnings", {"p_vendor_id": str(current_profile["id"])}
    ).execute()
    return earnings.data
