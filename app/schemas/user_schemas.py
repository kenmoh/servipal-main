from pydantic import BaseModel, EmailStr, Field, UUID4
from typing import Optional, Literal
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from enum import Enum

class UserType(str, Enum):
    CUSTOMER = "CUSTOMER"
    DISPATCH = "DISPATCH"
    RESTAURANT_VENDOR = "RESTAURANT_VENDOR"
    LAUNDRY_VENDOR = "LAUNDRY_VENDOR"
    RIDER = "RIDER"
    ADMIN = "ADMIN"
    MODERATOR = "MODERATOR"
    SUPER_ADMIN = "SUPER_ADMIN"
    

# Signup
class UserCreate(BaseModel):
    email: Optional[EmailStr] = None
    phone: str = Field(..., pattern=r"^\+\d{10,15}$")  # E.164 format
    password: str = Field(..., min_length=8)
    user_type: Literal["CUSTOMER", "DISPATCH", "RESTAURANT_VENDOR", "LAUNDRY_VENDOR"]

class RiderCreateByDispatch(BaseModel):
    email: EmailStr
    full_name: str
    phone: str = Field(..., pattern=r"^\+\d{10,15}$")
    bike_number: Optional[str] = None

# Login
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserProfileResponse"
    message: Optional[str] = None  # For email confirmation messages

# Profile
class UserProfileResponse(BaseModel):
    id: UUID4
    email: Optional[EmailStr]
    phone_number: str
    user_type: UserType
    full_name: Optional[str]
    store_name: Optional[str]
    business_name: Optional[str]
    profile_image_url: Optional[str]
    backdrop_image_url: Optional[str]
    is_online: bool
    is_verified: bool
    is_blocked: bool
    account_status: str
    created_at: datetime
    last_seen_at: Optional[datetime]

    class Config:
        from_attributes = True

class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    store_name: Optional[str] = None
    business_name: Optional[str] = None
    business_address: Optional[str] = None
    state: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    account_holder_name: Optional[str] = None
    can_pickup_and_dropoff: Optional[bool] = None
    pickup_and_delivery_charge: Optional[float] = None


class AvailableRiderResponse(BaseModel):
    id: UUID
    full_name: Optional[str]
    phone_number: str
    profile_image_url: Optional[str]
    bike_number: Optional[str]
    business_name: Optional[str] = Field(None, description="Rider's personal business name (if any)")
    total_distance_travelled: Optional[Decimal] = 0.0
    
    total_deliveries: int = 0
    average_rating: Optional[Decimal] = Field(0.0, description="Rider's personal rating")
    review_count: int = 0
    
    dispatch_id: Optional[UUID] = None
    dispatch_business_name: Optional[str] = None
    dispatch_average_rating: Optional[Decimal] = Field(0.0, description="Fleet rating = average of all riders")
    dispatch_total_reviews: int = 0
    dispatch_rider_count: int = 0
    
    distance_km: Optional[Decimal] = None

    class Config:
        from_attributes = True
        # Allow decimal for numeric fields from Postgres
        json_encoders = {
            Decimal: lambda v: float(v) if v is not None else None
        }

class RiderDetailResponse(BaseModel):
    id: UUID
    full_name: Optional[str]
    phone_number: str
    profile_image_url: Optional[str]
    backdrop_image_url: Optional[str]
    bike_number: Optional[str]
    business_name: Optional[str]          # Fleet name
    business_address: Optional[str]
    state: Optional[str]
    total_distance_travelled: float = 0.0
    total_deliveries: int = 0
    average_rating: float = 4.0
    review_count: int = 0
    is_online: bool
    is_verified: bool
    is_blocked: bool
    has_delivery: bool
    order_cancel_count: int = 0
    rider_is_suspended_for_order_cancel: bool = False
    rider_suspension_until: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class DispatchRiderResponse(BaseModel):
    id: UUID
    full_name: Optional[str]
    phone_number: str
    profile_image_url: Optional[str]
    bike_number: Optional[str]
    is_online: bool
    is_verified: bool
    has_delivery: bool
    average_rating: Optional[Decimal] = 0.0
    review_count: int = 0
    total_deliveries: int = 0
    total_distance_travelled: Optional[Decimal] = 0.0
    order_cancel_count: int = 0
    rider_is_suspended_for_order_cancel: bool = False
    rider_suspension_until: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RiderSuspensionRequest(BaseModel):
    rider_id: UUID
    suspend: bool = True  # True = suspend, False = unsuspend
    reason: Optional[str] = None
    suspension_days: Optional[int] = None  # If temporary suspension

class RiderSuspensionResponse(BaseModel):
    rider_id: UUID
    suspended: bool
    suspension_until: Optional[datetime] = None
    message: str


class RiderEarningsResponse(BaseModel):
    rider_id: UUID
    rider_name: str
    total_earnings: Decimal = Decimal("0.00")
    completed_deliveries: int = 0
    pending_earnings: Decimal = Decimal("0.00")  # In escrow, not released yet
    total_distance: Decimal = Decimal("0.00")
    period: str = "all_time"  # Can extend to weekly/monthly later