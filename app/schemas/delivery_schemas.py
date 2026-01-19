from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from enum import Enum


class DeliveryStatus(str, Enum):
    PENDING = "PENDING"
    PAID_NEEDS_RIDER = "PAID_NEEDS_RIDER"
    ASSIGNED = "ASSIGNED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PackageDeliveryCreate(BaseModel):
    receiver_phone: str = Field(
        ...,
        pattern=r"^\+234[789]\d{9}$",
        description="Nigerian phone number in format +234XXXXXXXXXX",
    )
    pickup_location: str
    destination: str
    pickup_coordinates: tuple[float, float]
    dropoff_coordinates: tuple[float, float]
    additional_info: Optional[str] = None
    delivery_type: str = "STANDARD"
    package_image_url: Optional[str] = None


class AssignRiderRequest(BaseModel):
    rider_id: UUID


class AssignRiderResponse(BaseModel):
    success: bool
    message: str
    delivery_status: str
    rider_name: Optional[str]


class DeliveryAction(str, Enum):
    accept = "accept"
    decline = "decline"


class DeliveryActionResponse(BaseModel):
    delivery_id: UUID
    order_id: UUID
    delivery_status: str
    message: str


class DeliveryCancelRequest(BaseModel):
    reason: str


class DeliveryCancelResponse(BaseModel):
    order_id: UUID
    delivery_status: str
    refunded: bool
    message: str


class DeliveryOrderListItem(BaseModel):
    id: UUID
    order_number: Optional[int]
    sender_id: UUID
    receiver_phone: str
    pickup_location: str
    destination: str
    delivery_fee: Decimal
    total_price: Decimal
    status: DeliveryStatus
    payment_status: str
    escrow_status: str
    rider_id: Optional[UUID]
    rider_name: Optional[str]
    dispatch_id: Optional[UUID]
    dispatch_name: Optional[str]
    created_at: datetime
    updated_at: datetime
    package_image_url: Optional[str]
    image_url: Optional[str]  # proof of delivery


class DeliveryOrdersResponse(BaseModel):
    orders: List[DeliveryOrderListItem]
    total_count: int
    has_more: bool


class DeliveryType(str, Enum):
    STANDARD = "STANDARD"
    SCHEDULED = "SCHEDULED"
