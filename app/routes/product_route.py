from fastapi import APIRouter, Depends
from typing import List
from uuid import UUID
from app.database.supabase import get_supabase_client
from supabase import AsyncClient
from app.schemas.product_schemas import (
    ProductItemCreate,
    ProductItemUpdate,
    ProductItemResponse,
    ProductOrderCreate,
    ProductOrderResponse,
    ProductVendorOrderAction,
    ProductVendorOrderActionResponse,
    ProductVendorMarkReadyResponse,
    ProductCustomerConfirmResponse,
)
from app.services import product_service
from app.dependencies.auth import get_current_profile, require_user_type
from app.dependencies.auth import get_customer_contact_info
from app.schemas.user_schemas import UserType

router = APIRouter(prefix="/api/v1/product", tags=["Marketplace"])


# ───────────────────────────────────────────────
# Product Items CRUD (any authenticated user)
# ───────────────────────────────────────────────
@router.post("/items", response_model=ProductItemResponse)
async def create_product_item(
    data: ProductItemCreate,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Any logged-in user can list a product for sale"""
    return await product_service.create_product_item(
        data, current_profile["id"], supabase
    )


@router.get("/items/{item_id}", response_model=ProductItemResponse)
async def get_product_item(
    item_id: UUID, supabase: AsyncClient = Depends(get_supabase_client)
):
    """Public: View a single product detail"""
    return await product_service.get_product_item(item_id, supabase)


@router.get("/my-items", response_model=List[ProductItemResponse])
async def get_my_products(
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Seller views their own listed products"""
    return await product_service.get_my_product_items(current_profile["id"], supabase)


@router.patch("/items/{item_id}", response_model=ProductItemResponse)
async def update_product_item(
    item_id: UUID,
    data: ProductItemUpdate,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Seller updates their own product"""
    return await product_service.update_product_item(
        item_id, data, current_profile["id"], supabase
    )


@router.delete("/items/{item_id}")
async def delete_product_item(
    item_id: UUID,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Seller soft-deletes their own product"""
    return await product_service.delete_product_item(
        item_id, current_profile["id"], supabase
    )


# ───────────────────────────────────────────────
# Payment Initiation (Checkout)
# ───────────────────────────────────────────────
@router.post("/initiate-payment", response_model=ProductOrderResponse)
async def initiate_product_payment(
    data: ProductOrderCreate,
    current_profile: dict = Depends(get_current_profile),
    customer_info: dict = Depends(get_customer_contact_info),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Customer initiates payment for a single product + quantity.
    Returns Flutterwave RN SDK payload.
    """
    return await product_service.initiate_product_payment(
        data, current_profile["id"], customer_info, supabase
    )


# ───────────────────────────────────────────────
# Vendor Order Actions
# ───────────────────────────────────────────────
@router.post(
    "/orders/{order_id}/action", response_model=ProductVendorOrderActionResponse
)
async def vendor_product_order_action(
    order_id: UUID,
    data: ProductVendorOrderAction,
    supabase: AsyncClient = Depends(get_supabase_client),
    current_profile: dict = Depends(
        require_user_type(
            [UserType.CUSTOMER, UserType.RESTAURANT_VENDOR, UserType.LAUNDRY_VENDOR]
        )
    ),
):
    """Vendor accepts or rejects the product order"""
    return await product_service.vendor_product_order_action(
        order_id, data, current_profile["id"], supabase
    )


@router.post(
    "/orders/{order_id}/mark-ready", response_model=ProductVendorMarkReadyResponse
)
async def vendor_mark_product_ready(
    order_id: UUID,
    supabase: AsyncClient = Depends(get_supabase_client),
    current_profile: dict = Depends(
        require_user_type(
            [UserType.RESTAURANT_VENDOR, UserType.LAUNDRY_VENDOR, UserType.CUSTOMER]
        )
    ),
):
    """Vendor marks product order as ready for pickup/delivery"""
    return await product_service.vendor_mark_product_ready(
        order_id, current_profile["id"], supabase
    )


# ───────────────────────────────────────────────
# Customer Confirm Receipt
# ───────────────────────────────────────────────
@router.post(
    "/orders/{order_id}/confirm-receipt", response_model=ProductCustomerConfirmResponse
)
async def customer_confirm_product_order(
    order_id: UUID,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Customer confirms receipt → stock reduced, total_sold increased, payment released"""
    return await product_service.customer_confirm_product_order(
        order_id, current_profile["id"], supabase
    )
