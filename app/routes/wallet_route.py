from fastapi import APIRouter, Depends
from uuid import UUID
from app.schemas.wallet_schema import WalletBalanceResponse, TopUpRequest, PayWithWalletRequest, WalletTopUpInitiationResponse
from app.services import wallet_service
from app.dependencies.auth import get_current_profile
from app.database.supabase import get_supabase_client
from supabase import AsyncClient

router = APIRouter(prefix="/api/v1/wallet", tags=["Wallet"])

@router.get("/details")
async def get_my_wallet_details(
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client)
)-> WalletBalanceResponse:
    return await wallet_service.get_wallet_details(current_profile["id"], supabase)


@router.post("/top-up")
async def top_up_my_wallet(
    data: TopUpRequest,
    current_profile: dict = Depends(get_current_profile)
)-> WalletTopUpInitiationResponse:
    return await wallet_service.initiate_wallet_top_up(current_profile["id"], data)

@router.post("/pay")
async def pay_with_my_wallet(
    data: PayWithWalletRequest,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client)
):
    return await wallet_service.pay_with_wallet(current_profile["id"], data, supabase)