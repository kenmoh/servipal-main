from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from uuid import UUID
from decimal import Decimal
from datetime import datetime

# ───────────────────────────────────────────────
# Product Item (what sellers list)
# ───────────────────────────────────────────────
class ProductItemCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = None
    price: Decimal = Field(..., gt=0)
    stock: int = Field(..., ge=0, description="Initial stock quantity")
    sizes: Optional[List[str]] = Field(None, description="Available sizes e.g. ['S', 'M', 'L']")
    colors: Optional[List[str]] = Field(None, description="Available colors e.g. ['Red', 'Blue']")
    category_id: Optional[UUID] = None
    images: List[str] = Field(default_factory=list, description="URLs after upload")

class ProductItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[Decimal] = None
    stock: Optional[int] = None
    sizes: Optional[List[str]] = None
    colors: Optional[List[str]] = None
    category_id: Optional[UUID] = None

class ProductItemResponse(BaseModel):
    id: UUID
    seller_id: UUID
    name: str
    description: Optional[str]
    price: Decimal
    stock: int
    in_stock: bool
    total_sold: int
    sizes: Optional[List[str]]
    colors: Optional[List[str]]
    category_id: Optional[UUID]
    images: List[str]
    created_at: datetime
    updated_at: datetime

# ───────────────────────────────────────────────
# Product Order Creation (customer checkout)
# ───────────────────────────────────────────────
class ProductOrderItem(BaseModel):
    item_id: UUID
    quantity: int = Field(..., ge=1)

class ProductOrderCreate(BaseModel):
    item: ProductOrderItem = Field(..., description="Single product + quantity")
    delivery_option: Literal["PICKUP", "VENDOR_DELIVERY"]
    delivery_address: Optional[str] = Field(None, description="Full delivery address if VENDOR_DELIVERY")
    additional_info: Optional[str] = Field(None, description="Extra notes/instructions")

class ProductOrderResponse(BaseModel):
    order_id: UUID
    tx_ref: str
    amount: float
    public_key: str
    currency: str = "NGN"
    customer: dict
    customization: dict
    message: str

# ───────────────────────────────────────────────
# Vendor Order Action
# ───────────────────────────────────────────────
class ProductVendorOrderAction(BaseModel):
    action: Literal["accept", "reject"]
    reason: Optional[str] = None

class ProductVendorOrderActionResponse(BaseModel):
    order_id: UUID
    order_status: str
    message: str

# ───────────────────────────────────────────────
# Vendor Mark Ready & Customer Confirm
# ───────────────────────────────────────────────
class ProductVendorMarkReadyResponse(BaseModel):
    order_id: UUID
    message: str

class ProductCustomerConfirmResponse(BaseModel):
    order_id: UUID
    amount_released: Decimal
    message: str