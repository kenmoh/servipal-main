from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from uuid import UUID
from decimal import Decimal
from datetime import datetime

class LaundryCategoryResponse(BaseModel):
    id: UUID
    name: str

class LaundryOrderItemCreate(BaseModel):
    item_id: UUID
    quantity: int = 1

class LaundryOrderResponse(BaseModel):
    order_id: UUID
    order_number: int
    vendor_name: str
    total_price: Decimal
    delivery_fee: Decimal = 0.0
    grand_total: Decimal
    status: str
    payment_status: str

class LaundryItemResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    price: Decimal
    stock: Optional[int]
    in_stock: bool
    total_sold: int = 0
    average_rating: Decimal = 0.0
    review_count: int = 0
    images: List[str] = []
    category: Optional[LaundryCategoryResponse]

class LaundryVendorCardResponse(BaseModel):
    id: UUID
    store_name: str
    business_name: Optional[str]
    profile_image_url: Optional[str]
    backdrop_image_url: Optional[str]
    average_rating: Decimal = 0.0
    review_count: int = 0
    state: Optional[str]
    distance_km: Optional[Decimal] = None
    is_open: bool = False
    total_items: int = 0
    can_pickup_and_dropoff: bool = False
    pickup_and_delivery_charge: Decimal = Decimal("0.00")

class LaundryVendorDetailResponse(BaseModel):
    id: UUID
    store_name: str
    business_name: Optional[str]
    full_name: Optional[str]
    phone_number: str
    profile_image_url: Optional[str]
    backdrop_image_url: Optional[str]
    business_address: Optional[str]
    state: Optional[str]
    opening_hours: Optional[dict]
    average_rating: Decimal = 0.0
    review_count: int = 0
    total_items: int = 0
    categories: List[LaundryCategoryResponse] = []
    menu: List[LaundryItemResponse] = []

class LaundryVendorOrderItemResponse(BaseModel):
    item_id: UUID
    name: str
    quantity: int
    price: Decimal
    total: Decimal

class LaundryVendorOrderResponse(BaseModel):
    order_id: UUID
    order_number: int
    customer_name: str
    customer_phone: str
    subtotal: Decimal
    delivery_fee: Decimal = 0.0
    grand_total: Decimal
    washing_instructions: Optional[str] = None
    items: List[LaundryVendorOrderItemResponse]
    order_status: str
    payment_status: str
    created_at: datetime
    delivery_option: str = "PICKUP"

    class Config:
        from_attributes = True


class LaundryItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: Decimal = Field(..., gt=0)
    stock: Optional[int] = None
    category_id: Optional[UUID] = None

class LaundryItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[Decimal] = None
    stock: Optional[int] = None
    in_stock: Optional[bool] = None
    category_id: Optional[UUID] = None

class LaundryItemDetailResponse(LaundryItemResponse):
    vendor_id: UUID
    is_deleted: bool = False

class LaundryCartItem(BaseModel):
    item_id: UUID
    name: str
    price: Decimal
    quantity: int


class LaundryVendorMarkReadyResponse(BaseModel):
    order_id: UUID
    order_status: str = "READY"
    message: str = "Order marked as ready for pickup/delivery"

class LaundryCustomerConfirmResponse(BaseModel):
    order_id: UUID
    order_status: str = "COMPLETED"
    amount_released: Decimal
    message: str = "Order confirmed! Payment released to vendor"

class LaundryItemOrder(BaseModel):
    item_id: UUID = Field(..., description="ID of the laundry item")
    quantity: int = Field(..., gt=0, description="Number of units (e.g., shirts, kg)")
    sizes: Optional[List[str]] = Field(None, description="Selected sizes if applicable")
    colors: Optional[List[str]] = Field(None, description="Selected colors if applicable")

class LaundryOrderCreate(BaseModel):
    vendor_id: UUID = Field(..., description="ID of the laundry vendor")
    items: List[LaundryItemOrder] = Field(description="List of laundry items to order")
    delivery_option: Literal["PICKUP", "VENDOR_DELIVERY"] = Field(
        ..., description="Pickup at shop or vendor delivers"
    )
    washing_instructions: Optional[str] = Field(None, description="Special instructions for washing")
