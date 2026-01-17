from fastapi import APIRouter, Depends
from app.schemas.notification_schemas import FCMTokenRegister, FCMTokenResponse
from app.services.notification_service import register_fcm_token, get_my_fcm_token
from app.dependencies.auth import get_current_profile
from app.database.supabase import get_supabase_client

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.post("/register-token")
async def register_push_token(
    data: FCMTokenRegister,
    current_profile: dict = Depends(get_current_profile),
    supabase=Depends(get_supabase_client),
) -> FCMTokenResponse:
    """
    Register or update device push token for notifications.
    Called on app start or token refresh.
    
    Args:
        data (FCMTokenRegister): Token details.
        
    Returns:
        FCMTokenResponse: Registration status.
    """
    return await register_fcm_token(data, current_profile["id"], supabase)


@router.get("/register-token")
async def get_push_token(
    current_profile: dict = Depends(get_current_profile),
    supabase=Depends(get_supabase_client),
) -> FCMTokenResponse:
    """
    Get user token.
    
    Returns:
        FCMTokenResponse: Token details.
    """
    return await get_my_fcm_token(current_profile["id"], supabase)
