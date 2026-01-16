from pydantic import BaseModel, Field
from uuid import UUID
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, List


class WalletTransactionResponse(BaseModel):
    tx_ref: str
    amount: Decimal = Field(max_digits=6, decimal_places=2, default=0.00)
    from_user_id: Optional[UUID]
    to_user_id: Optional[UUID]
    order_id: Optional[UUID]
    transaction_type: str
    status: str
    payment_method: str
    # details: Dict
    created_at: datetime

class WalletBalanceResponse(BaseModel):
    balance: Decimal = Field(max_digits=6, decimal_places=2, default=0.00)
    escrow_balance: Decimal = Field(max_digits=6, decimal_places=2, default=0.00)
    transactions: List[WalletTransactionResponse]

class TopUpResponse(BaseModel):
    tx_ref: str
    amount: Decimal = Field(max_digits=6, decimal_places=2, default=0.00)
   

class TopUpRequest(BaseModel):
    amount: Decimal = Field(..., description="Amount to be charged (in NGN)", min=1000, max=25_000)
    payment_method: str = "FLUTTERWAVE"

class PayWithWalletRequest(BaseModel):
    amount: Decimal
    to_user_id: Optional[UUID]
    order_id: Optional[UUID]
    transaction_type: str = "ORDER_PAYMENT"


class CustomerInfo(BaseModel):
    email: str = Field(..., description="Customer's email address")
    phone_number: str = Field(..., description="Customer's phone number (E.164 format)")
    name: str = Field(..., description="Customer's full name or display name")

class Customization(BaseModel):
    title: str = Field(..., description="Title shown on the payment page/SDK")
    description: str = Field(..., description="Description shown on the payment page/SDK")

class WalletTopUpInitiationResponse(BaseModel):
    """
    Response schema for initiating a wallet top-up.
    Returned when user requests to add funds to their wallet.
    """
    tx_ref: str = Field(..., description="Unique transaction reference for this top-up")
    amount: float = Field(..., description="Amount to be charged (in NGN)")
    public_key: str = Field(..., description="Flutterwave public key for SDK initialization")
    currency: str = Field("NGN", description="Currency code (always NGN for now)")
    customer: CustomerInfo = Field(..., description="Customer details for Flutterwave SDK")
    customization: Customization = Field(..., description="UI customizations for the payment screen")


class PayWithWalletResponse(BaseModel):
    success: bool = Field(..., description="Payment successful from wallet")
    message: str = Field(..., description="Payment successful from wallet")
    new_balance: Decimal = Field(..., description="New balance after payment")
    tx_ref: str = Field(..., description="Unique transaction reference for this top-up")