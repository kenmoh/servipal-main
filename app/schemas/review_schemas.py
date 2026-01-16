from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime

class ReviewCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")
    comment: Optional[str] = None
    reviewee_type: str = Field(..., description="RIDER, VENDOR, or DISPATCH")
    # Optional for item-specific reviews (food, product, laundry)
    item_id: Optional[UUID] = None

class ReviewResponse(BaseModel):
    id: UUID
    reviewer_name: Optional[str]
    reviewer_profile_url: Optional[str]
    rating: int
    comment: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

class ReviewsListResponse(BaseModel):
    reviews: list[ReviewResponse]
    average_rating: float
    total_reviews: int