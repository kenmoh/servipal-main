
from uuid import UUID
from supabase import AsyncClient
from fastapi import HTTPException
from app.schemas.notification_schemas import *

async def register_fcm_token(
    data: FCMTokenRegister,
    user_id: UUID,
        supabase: AsyncClient
) -> FCMTokenResponse:
    try:
        # Upsert: if user_id exists → update token/platform/updated_at
        # if not → insert new
        await supabase.table("fcm_tokens")\
            .upsert({
                "user_id": str(user_id),
                "token": data.token,
                "platform": data.platform,
                "updated_at": datetime.now().isoformat()
            }, 
            on_conflict="user_id"
            )\
            .execute()
        return FCMTokenResponse(token=data.token, platform=data.platform)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to register token: {str(e)}"
        )




async def get_my_fcm_token(user_id: UUID, supabase: AsyncClient) -> Optional[FCMTokenResponse]:
    resp = await supabase.table("fcm_tokens")\
        .select("*")\
        .eq("user_id", str(user_id))\
        .single()\
        .execute()

    if not resp.data:
        return None

    return FCMTokenResponse(**resp.data)

