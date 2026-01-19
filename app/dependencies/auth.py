from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from typing import Dict, Any
from app.database.supabase import get_supabase_client
from app.schemas.user_schemas import UserType
from supabase import AsyncClient
from app.config.logging import logger

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    supabase_client: AsyncClient = Depends(get_supabase_client),
) -> dict:
    try:
        response = await supabase_client.auth.get_user(token)
        if not response.user:
            logger.warning("authentication_failed", reason="invalid_or_expired_token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

        logger.debug("user_authenticated", user_id=response.user.id)
        return response.user
    except HTTPException:
        raise
    except Exception as e:
        logger.error("authentication_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )


async def get_current_profile(
    token: str = Depends(oauth2_scheme),
    current_user: dict = Depends(get_current_user),
    supabase_client: AsyncClient = Depends(get_supabase_client),
) -> dict:
    # Authenticate the client with the user's token so RLS policies work
    supabase_client.postgrest.auth(token)

    # Validated: Use standard select and check list length to safely handle missing profiles
    resp = (
        await supabase_client.table("profiles")
        .select("*")
        .eq("id", current_user.id)
        .execute()
    )

    if not resp.data or len(resp.data) == 0:
        logger.warning("profile_not_found", user_id=current_user.id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found."
        )

    logger.debug(
        "profile_retrieved",
        user_id=current_user.id,
        user_type=resp.data[0].get("user_type"),
    )
    return resp.data[0]


def require_user_type(allowed_types: list[UserType]):
    async def _require_type(profile: dict = Depends(get_current_profile)):
        user_type = profile.get("user_type")
        allowed_values = [t.value for t in allowed_types]
        if user_type not in allowed_values:
            logger.warning(
                "access_denied",
                user_id=profile.get("id"),
                user_type=user_type,
                required_types=allowed_values,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access restricted to {allowed_values}",
            )
        return profile

    return _require_type


async def require_authenticated_user(profile: dict = Depends(get_current_profile)):
    return profile


async def get_customer_contact_info(
    current_profile: dict = Depends(get_current_profile),
    supabase_client: AsyncClient = Depends(get_supabase_client),
) -> Dict[str, Any]:
    """
    Dependency to fetch authenticated user's contact info for payment SDK.

    Returns:
        {
            "email": str,
            "phone_number": str,
            "name": str
        }

    Raises:
        500 if profile fetch fails
        404 if profile not found
    """
    try:
        profile_resp = (
            await supabase_client.table("profiles")
            .select("email, phone_number, full_name, business_name")
            .eq("id", current_profile.get("id"))
            .single()
            .execute()
        )

        if not profile_resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found"
            )

        profile = profile_resp.data

        return {
            "email": current_profile.get("email"),
            "phone_number": profile.get("phone_number", ""),
            "full_name": profile.get("full_name")
            if profile.get("full_name")
            else profile.get("business_name", ""),
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch user contact info: {str(e)}",
        )


def is_admin_user(current_profile: dict = Depends(get_current_profile)) -> bool:
    return current_profile.get("user_type") in [
        UserType.ADMIN.value,
        UserType.SUPER_ADMIN.value,
        UserType.MODERATOR.value,
    ]
