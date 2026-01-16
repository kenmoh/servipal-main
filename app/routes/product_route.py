from fastapi import APIRouter, Depends, Query, File, UploadFile, Form
from typing import List, Optional
from uuid import UUID
from decimal import Decimal
from app.schemas.product_schemas import (
    ProductItemCreate, ProductItemUpdate, ProductItemResponse,
    ProductOrderCreate, ProductOrderResponse,
    ProductVendorOrderAction, ProductVendorOrderActionResponse,
    ProductVendorMarkReadyResponse, ProductCustomerConfirmResponse
)
from app.services.product_service import (
    create_product_item, get_product_item, get_my_product_items,
    update_product_item, delete_product_item,
    initiate_product_payment, vendor_product_order_action,
    vendor_mark_product_ready, customer_confirm_product_order
)
from app.dependencies.auth import get_current_profile, require_user_type
from app.utils.user_utils import get_customer_contact_info
from app.schemas.user_schemas import UserType

router = APIRouter(prefix="/api/product", tags=["Product"])

# ───────────────────────────────────────────────
# Product Items CRUD (any authenticated user)
# ───────────────────────────────────────────────
@router.post("/items", response_model=ProductItemResponse)
async def create_product_item(
    data: ProductItemCreate,
    current_profile: dict = Depends(get_current_profile)
):
    """Any logged-in user can list a product for sale"""
    return await create_product_item(data, current_profile["id"])


@router.get("/items/{item_id}", response_model=ProductItemResponse)
async def get_product_item(item_id: UUID):
    """Public: View a single product detail"""
    return await get_product_item(item_id)


@router.get("/my-items", response_model=List[ProductItemResponse])
async def get_my_products(
    current_profile: dict = Depends(get_current_profile)
):
    """Seller views their own listed products"""
    return await get_my_product_items(current_profile["id"])


@router.patch("/items/{item_id}", response_model=ProductItemResponse)
async def update_product_item(
    item_id: UUID,
    data: ProductItemUpdate,
    current_profile: dict = Depends(get_current_profile)
):
    """Seller updates their own product"""
    return await update_product_item(item_id, data, current_profile["id"])


@router.delete("/items/{item_id}")
async def delete_product_item(
    item_id: UUID,
    current_profile: dict = Depends(get_current_profile)
):
    """Seller soft-deletes their own product"""
    return await delete_product_item(item_id, current_profile["id"])


# ───────────────────────────────────────────────
# Payment Initiation (Checkout)
# ───────────────────────────────────────────────
@router.post("/initiate-payment", response_model=ProductOrderResponse)
async def initiate_product_payment(
    data: ProductOrderCreate,
    current_profile: dict = Depends(get_current_profile),
    customer_info: dict = Depends(get_customer_contact_info)
):
    """
    Customer initiates payment for a single product + quantity.
    Returns Flutterwave RN SDK payload.
    """
    return await initiate_product_payment(data, current_profile["id"])


# ───────────────────────────────────────────────
# Vendor Order Actions
# ───────────────────────────────────────────────
@router.post("/orders/{order_id}/action", response_model=ProductVendorOrderActionResponse)
async def vendor_product_order_action(
    order_id: UUID,
    data: ProductVendorOrderAction,
    current_profile: dict = Depends(require_user_type([UserType.PRODUCT_VENDOR]))  # optional role
):
    """Vendor accepts or rejects the product order"""
    return await vendor_product_order_action(order_id, data, current_profile["id"])


@router.post("/orders/{order_id}/mark-ready", response_model=ProductVendorMarkReadyResponse)
async def vendor_mark_product_ready(
    order_id: UUID,
    current_profile: dict = Depends(require_user_type([UserType.PRODUCT_VENDOR]))
):
    """Vendor marks product order as ready for pickup/delivery"""
    return await vendor_mark_product_ready(order_id, current_profile["id"])


# ───────────────────────────────────────────────
# Customer Confirm Receipt
# ───────────────────────────────────────────────
@router.post("/orders/{order_id}/confirm-receipt", response_model=ProductCustomerConfirmResponse)
async def customer_confirm_product_order(
    order_id: UUID,
    current_profile: dict = Depends(get_current_profile)
):
    """Customer confirms receipt → stock reduced, total_sold increased, payment released"""
    return await customer_confirm_product_order(order_id, current_profile["id"])