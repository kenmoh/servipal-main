from fastapi import APIRouter, Depends, Request
from uuid import UUID
from app.schemas.wallet_schema import (
    WalletBalanceResponse,
    TopUpRequest,
    PayWithWalletRequest,
    WalletTopUpInitiationResponse,
)
from app.services import wallet_service
from app.dependencies.auth import get_current_profile
from app.database.supabase import get_supabase_client
from supabase import AsyncClient
from app.config.logging import logger

router = APIRouter(prefix="/api/v1/wallet", tags=["Wallet"])


@router.get("/details")
async def get_my_wallet_details(
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> WalletBalanceResponse:
    """
    Get current user's wallet details.
    
    Returns:
        WalletBalanceResponse: Balance and currency.
    """
    logger.debug("get_wallet_details_endpoint", user_id=current_profile["id"])
    return await wallet_service.get_wallet_details(current_profile["id"], supabase)


@router.post("/top-up")
async def top_up_my_wallet(
    data: TopUpRequest,
    request: Request,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> WalletTopUpInitiationResponse:
    """
    Initiate wallet top-up.
    
    Args:
        data (TopUpRequest): Amount to add.
        
    Returns:
        WalletTopUpInitiationResponse: Payment initiation details.
    """
    logger.info(
        "topup_endpoint_called",
        user_id=current_profile["id"],
        amount=float(data.amount),
    )
    return await wallet_service.initiate_wallet_top_up(
        data, current_profile["id"], supabase, request
    )


@router.post("/pay")
async def pay_with_my_wallet(
    data: PayWithWalletRequest,
    request: Request,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Make a payment using wallet balance.
    
    Args:
        data (PayWithWalletRequest): Payment details.
        
    Returns:
        dict: Transaction status.
    """
    logger.info(
        "wallet_pay_endpoint_called",
        user_id=current_profile["id"],
        amount=float(data.amount),
    )
    return await wallet_service.pay_with_wallet(
        current_profile["id"], data, supabase, request
    )
