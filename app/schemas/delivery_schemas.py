from pydantic import BaseModel, Field
from typing import Optional, Literal
from uuid import UUID
from datetime import datetime
from enum import Enum

class DeliveryType(str, Enum):
    STANDARD = "STANDARD"
    EXPRESS = "EXPRESS"
    SCHEDULED = "SCHEDULED"

class PackageDeliveryCreate(BaseModel):
    receiver_phone: str = Field(..., pattern=r"^\+\d{10,15}$")
    pickup_location: str
    destination: str
    pickup_coordinates: tuple[float, float]
    dropoff_coordinates: tuple[float, float]
    additional_info: Optional[str] = None
    delivery_type: DeliveryType = DeliveryType.STANDARD


class AssignRiderRequest(BaseModel):
    rider_id: UUID

class AssignRiderResponse(BaseModel):
    success: bool
    message: str
    delivery_status: str = "ASSIGNED"
    rider_name: Optional[str] = None

class DeliveryOrderResponse(BaseModel):
    id: UUID
    order_number: int
    sender_id: UUID
    rider_id: Optional[UUID]
    dispatch_id: Optional[UUID]
    receiver_phone: str
    pickup_location: str
    destination: str
    delivery_fee: float
    delivery_status: str
    delivery_type: DeliveryType
    created_at: datetime

class DeliveryAction(BaseModel):
    action: Literal["accept", "decline"]
    reason: Optional[str] = None

class DeliveryActionResponse(BaseModel):
    delivery_id: UUID
    order_id: UUID
    delivery_status: str
    message: str
    rider_freed: Optional[bool] = None

class DeliveryCancelRequest(BaseModel):
    reason: Optional[str] = None

class DeliveryCancelResponse(BaseModel):
    order_id: UUID
    delivery_status: str
    refunded: bool = False
    message: str