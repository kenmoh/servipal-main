from pydantic import BaseModel
from uuid import UUID
from decimal import Decimal
from typing import Optional, Literal

class VendorOrderAction(BaseModel):
    action: Literal["accept", "reject"]
    reason: Optional[str] = None  # if reject

class VendorOrderActionResponse(BaseModel):
    order_id: UUID
    order_status: str
    message: str

class VendorResponse(BaseModel):
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





