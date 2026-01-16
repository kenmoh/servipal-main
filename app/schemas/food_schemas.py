from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from uuid import UUID
from decimal import Decimal
from datetime import datetime
from enum import Enum

class FoodGroup(str, Enum):
    PROTEIN = "PROTEIN"
    CARB = "CARB"
    VEGETABLE = "VEGETABLE"
    FRUIT = "FRUIT"
    DRINK = "DRINK"
    SNACK = "SNACK"
    SIDES = "SIDES"
    OTHER = "OTHER"


class FoodCategoryResponse(BaseModel):
    id: UUID
    name: str


class FoodOrderItemCreate(BaseModel):
    item_id: UUID
    quantity: int = 1
    sizes: Optional[list[str]] = []
    colors: Optional[list[str]] = []

class FoodOrderCreate(BaseModel):
    vendor_id: UUID
    items: list[FoodOrderItemCreate]
    require_delivery: bool = False
    preferred_rider_id: Optional[UUID] = None  # if delivery needed
    cooking_instructions: Optional[str] = None
    pickup_location: Optional[str] = None
    destination: Optional[str] = None
    pickup_coordinates: Optional[tuple[float, float]] = None
    dropoff_coordinates: Optional[tuple[float, float]] = None
    delivery_type: Literal["PICKUP", "APP_DELIVERY", "VENDOR_DELIVERY"] = "PICKUP"

class FoodOrderResponse(BaseModel):
    order_id: UUID
    order_number: int
    vendor_name: str
    total_price: Decimal
    delivery_fee: Decimal = 0.0
    grand_total: Decimal
    status: str
    payment_status: str
    require_delivery: bool


class FoodItemResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    price: Decimal
    sizes: List[str] = []
    sides: List[str] = []
    colors: List[str] = []
    stock: Optional[int]
    in_stock: bool
    total_sold: int = 0
    average_rating: Decimal = 0.0
    review_count: int = 0
    images: List[str] = []
    category: Optional[FoodCategoryResponse]

class VendorCardResponse(BaseModel):
    id: UUID
    store_name: str
    business_name: Optional[str]
    profile_image_url: Optional[str]
    backdrop_image_url: Optional[str]
    average_rating: Decimal = 0.0
    review_count: int = 0
    state: Optional[str]
    distance_km: Optional[Decimal] = None
    is_open: bool = False  # We'll calculate from opening_hours
    total_items: int = 0
    can_pickup_and_dropoff: bool = False
    pickup_and_delivery_charge: Decimal = Decimal("0.00")

class VendorDetailResponse(BaseModel):
    id: UUID
    store_name: str
    business_name: Optional[str]
    full_name: Optional[str]
    phone_number: str
    profile_image_url: Optional[str]
    backdrop_image_url: Optional[str]
    business_address: Optional[str]
    state: Optional[str]
    opening_hours: Optional[dict]  # {"mon": "09:00-22:00"}
    average_rating: Decimal = 0.0
    review_count: int = 0
    total_items: int = 0
    categories: List[FoodCategoryResponse] = []
    menu: List[FoodItemResponse] = []  # Grouped by category in frontend



class VendorOrderItemResponse(BaseModel):
    item_id: UUID
    name: str
    quantity: int
    price: Decimal
    total: Decimal
    sizes: List[str] = []
    colors: List[str] = []

class VendorOrderResponse(BaseModel):
    order_id: UUID
    order_number: int
    customer_name: str
    customer_phone: str
    subtotal: Decimal
    delivery_fee: Decimal = 0.0
    grand_total: Decimal
    cooking_instructions: Optional[str] = None
    items: List[VendorOrderItemResponse]
    order_status: str
    payment_status: str
    created_at: datetime
    delivery_option: str = "PICKUP"  # PICKUP or VENDOR_DELIVERY

    class Config:
        from_attributes = True   


class FoodItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: Decimal = Field(..., gt=0)
    sizes: List[str] = []
    sides: List[str] = []
    colors: List[str] = []
    stock: Optional[int] = None
    category_id: Optional[UUID] = None
    food_group: Optional[FoodGroup] = None  # From your enum

class FoodItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[Decimal] = None
    sizes: Optional[List[str]] = None
    sides: Optional[List[str]] = None
    colors: Optional[List[str]] = None
    stock: Optional[int] = None
    in_stock: Optional[bool] = None
    category_id: Optional[UUID] = None
    food_group: Optional[FoodGroup] = None

class FoodItemDetailResponse(FoodItemResponse):
    vendor_id: UUID
    is_deleted: bool = False


class CartItem(BaseModel):
    item_id: UUID
    name: str
    price: Decimal
    quantity: int
    sizes: List[str] = []
    colors: List[str] = []

class CheckoutRequest(BaseModel):
    vendor_id: UUID
    items: List[CartItem]
    delivery_option: Literal["PICKUP", "VENDOR_DELIVERY"] = "PICKUP"
    cooking_instructions: Optional[str] = None

class CheckoutResponse(BaseModel):
    order_preview_id: str  # temporary ID or tx_ref
    vendor_name: str
    subtotal: Decimal
    delivery_fee: Decimal
    grand_total: Decimal
    payment_link: str
    tx_ref: str
    message: str = "Proceed to payment to confirm your order"


class VendorMarkReadyResponse(BaseModel):
    order_id: UUID
    order_status: str = "READY"
    message: str = "Order marked as ready for pickup/delivery"

class CustomerConfirmResponse(BaseModel):
    order_id: UUID
    order_status: str = "COMPLETED"
    amount_released: Decimal
    message: str = "Order confirmed! Payment released to vendor"