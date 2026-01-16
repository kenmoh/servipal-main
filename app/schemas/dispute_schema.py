from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from uuid import UUID
from datetime import datetime


class DisputeCreate(BaseModel):
    order_id: UUID
    order_type: Literal["DELIVERY", "PRODUCT", "FOOD", "LAUNDRY"]
    reason: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Why are you opening this dispute?",
    )
    attachments: Optional[List[str]] = Field(
        None, description="URLs of evidence images/files"
    )


class DisputeMessageCreate(BaseModel):
    message_text: str = Field(..., min_length=1, max_length=2000)
    attachments: Optional[List[str]] = Field(
        None, description="URLs of attached images/files"
    )


class DisputeMessageResponse(BaseModel):
    id: UUID
    sender_id: UUID
    message_text: str
    attachments: List[str]
    created_at: datetime


class DisputeResponse(BaseModel):
    id: UUID
    order_id: UUID
    order_type: str
    initiator_id: UUID
    respondent_id: UUID
    reason: str
    status: str
    resolution_notes: Optional[str]
    resolved_by_id: Optional[UUID]
    resolved_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    messages: List[DisputeMessageResponse] = []


class DisputeResolve(BaseModel):
    resolution: Literal["BUYER_FAVOR", "SELLER_FAVOR", "COMPROMISE"]
    notes: str = Field(..., min_length=10, description="How the dispute was resolved")
