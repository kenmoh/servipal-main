from fastapi import APIRouter, Depends, Form, File, UploadFile
from typing import List, Optional
from decimal import Decimal
from uuid import UUID
import uuid
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
ProductType
)
from app.services import product_service
from app.dependencies.auth import get_current_profile, require_user_type
from app.dependencies.auth import get_customer_contact_info
from app.schemas.user_schemas import UserType
from app.utils.storage import upload_to_supabase_storage

router = APIRouter(prefix="/api/v1/product", tags=["Marketplace"])


# ───────────────────────────────────────────────
# Product Items CRUD (any authenticated user)
# ───────────────────────────────────────────────
@router.post("/items", response_model=ProductItemResponse)
async def create_product_item(
    name: str = Form(..., min_length=3, max_length=200),
    description: Optional[str] = Form(None),
    price: Decimal = Form(..., gt=0),
    product_type: ProductType = Form(ProductType.PHYSICAL),
    stock: int = Form(..., ge=0),
    sizes: Optional[str] = Form(None, description="Comma-separated sizes"),
    colors: Optional[str] = Form(None, description="Comma-separated colors"),
    category_id: Optional[UUID] = Form(None),
    images: List[UploadFile] = File(default=[]),
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    List a new product for sale.
    
    Args:
        name (str): Product name.
        description (str, optional): Product description.
        price (Decimal): Price.
        product_type (ProductType): Type of product.
        stock (int): Stock quantity.
        sizes (str, optional): Comma-separated sizes.
        colors (str, optional): Comma-separated colors.
        category_id (UUID, optional): Category ID.
        images (List[UploadFile]): Product images.
        
    Returns:
        ProductItemResponse: Created product details.
    """

    parsed_sizes = [s.strip() for s in sizes.split(",")] if sizes else []
    parsed_colors = [c.strip() for c in colors.split(",")] if colors else []

    uploaded_images = []
    if images:
        product_folder = f"products/{uuid.uuid4().hex}"
        for file in images:
            url = await upload_to_supabase_storage(
                file=file,
                supabase=supabase,
                bucket="product-images",
                folder=product_folder
            )
            uploaded_images.append(url)
            
    data = ProductItemCreate(
        name=name,
        description=description,
        price=price,
        product_type=product_type,
        stock=stock,
        sizes=parsed_sizes,
        colors=parsed_colors,
        category_id=category_id,
        images=uploaded_images,
    )
    return await product_service.create_product_item(
        data=data,
        seller_id=current_profile["id"],
        supabase=supabase,
    )

@router.get("/items/{item_id}", response_model=ProductItemResponse)
async def get_product_item(
    item_id: UUID, supabase: AsyncClient = Depends(get_supabase_client)
):
    """
    View a single product detail.
    
    Args:
        item_id (UUID): The product ID.
        
    Returns:
        ProductItemResponse: Product details.
    """
    return await product_service.get_product_item(item_id, supabase)


@router.get("/my-items", response_model=List[ProductItemResponse])
async def get_my_products(
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Seller views their own listed products.
    
    Returns:
        List[ProductItemResponse]: List of products.
    """
    return await product_service.get_my_product_items(current_profile["id"], supabase)


@router.patch("/items/{item_id}", response_model=ProductItemResponse)
async def update_product_item(
    item_id: UUID,
    data: ProductItemUpdate,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Seller updates their own product.
    
    Args:
        item_id (UUID): The product ID.
        data (ProductItemUpdate): Fields to update.
        
    Returns:
        ProductItemResponse: Updated product.
    """
    return await product_service.update_product_item(
        item_id, data, current_profile["id"], supabase
    )


@router.delete("/items/{item_id}")
async def delete_product_item(
    item_id: UUID,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Seller soft-deletes their own product.
    
    Args:
        item_id (UUID): The product ID.
        
    Returns:
        dict: Success status.
    """
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
    
    Args:
        data (ProductOrderCreate): Order details.
        
    Returns:
        ProductOrderResponse: Payment details (Flutterwave).
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
    """
    Vendor accepts or rejects the product order.
    
    Args:
        order_id (UUID): The order ID.
        data (ProductVendorOrderAction): Action data.
        
    Returns:
        ProductVendorOrderActionResponse: Action result.
    """
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
    """
    Vendor marks product order as ready for pickup/delivery.
    
    Args:
        order_id (UUID): The order ID.
        
    Returns:
        ProductVendorMarkReadyResponse: Updated status.
    """
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
    """
    Customer confirms receipt.
    Stock reduced, total_sold increased, payment released.
    
    Args:
        order_id (UUID): The order ID.
        
    Returns:
        ProductCustomerConfirmResponse: Confirmation result.
    """
    return await product_service.customer_confirm_product_order(
        order_id, current_profile["id"], supabase
    )
