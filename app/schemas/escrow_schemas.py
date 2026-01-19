from pydantic import BaseModel, Field, EmailStr, HttpUrl
from typing import Optional, List
from uuid import UUID
from decimal import Decimal
from datetime import datetime
from enum import Enum


class EscrowRole(str, Enum):
    INITIATOR = "INITIATOR"
    RECIPIENT = "RECIPIENT"
    OBSERVER = "OBSERVER"


class EscrowPartyCreate(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    role: EscrowRole = Field(...)
    share_amount: Decimal = Field(..., gt=0)


class EscrowAgreementCreate(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10)
    amount: Decimal = Field(..., gt=0)
    parties: List[EscrowPartyCreate] = Field(..., min_items=1)
    terms: str = Field(..., min_length=20)
    expires_at: Optional[datetime] = Field(default=None)


class EscrowAgreementResponse(BaseModel):
    id: UUID
    initiator_id: UUID
    title: str
    description: str
    amount: Decimal
    commission_rate: Decimal
    commission_amount: Decimal
    net_amount: Decimal
    status: str
    terms: str
    invite_code: str
    expires_at: datetime
    funded_at: Optional[datetime]
    completed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    cancelled_reason: Optional[str]
    dispute_id: Optional[UUID]
    created_at: datetime
    parties: List[dict]


class EscrowAcceptRequest(BaseModel):
    accept: bool = Field(...)
    notes: Optional[str] = None


class EscrowRejectRequest(BaseModel):
    reason: str = Field(..., min_length=10)
    evidence_urls: Optional[List[HttpUrl]] = None


class EscrowCompletionProposal(BaseModel):
    evidence_urls: List[HttpUrl] = Field(default=[])
    notes: Optional[str] = None


class EscrowCompletionVote(BaseModel):
    confirm: bool = Field(...)
    notes: Optional[str] = None
