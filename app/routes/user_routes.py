from fastapi import APIRouter, Depends, Query, File
from app.services.user_service import *
from typing import List, Optional
from app.schemas.user_schemas import (
    UserType,
    ProfileUpdate,
    UserLocationUpdate,
    DetailedRiderResponse,
)
from app.services import user_service
from app.dependencies.auth import get_current_profile, require_user_type
from app.database.supabase import get_supabase_client, get_supabase_admin_client
from app.config.logging import logger
from supabase import AsyncClient

router = APIRouter(prefix="/api/v1/users", tags=["Users"])


@router.post("/riders", response_model=UserProfileResponse)
async def create_rider(
    data: RiderCreateByDispatch,
    request: Request,
    current_user: dict = Depends(get_current_profile),
    dispatch_user=Depends(require_user_type([UserType.DISPATCH])),
    supabase: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    Dispatch owner creates a new rider account.
    
    Args:
        data (RiderCreateByDispatch): Rider details (phone, name, etc.).
        
    Returns:
        UserProfileResponse: The created rider's profile.
    """
    logger.info(
        "create_rider_requested", dispatch_id=current_user["id"], rider_phone=data.phone
    )
    result = await create_rider_by_dispatch(data, current_user, supabase, request)
    logger.info("rider_created", dispatch_id=current_user["id"], rider_id=result.id)
    return result


@router.get("/me", response_model=UserProfileResponse)
async def get_my_profile(
    profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Get the current user's profile.
    
    Returns:
        UserProfileResponse: User profile details.
    """
    logger.debug("get_profile_requested", user_id=profile["id"])
    return await get_user_profile(profile["id"], supabase)


@router.patch("/me", response_model=UserProfileResponse)
async def update_profile(
    data: ProfileUpdate,
    request: Request,
    profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Update the current user's profile.
    
    Args:
        data (ProfileUpdate): Fields to update.
        
    Returns:
        UserProfileResponse: Updated profile.
    """
    logger.info(
        "update_profile_requested",
        user_id=profile["id"],
        updates=data.model_dump(exclude_unset=True),
    )
    result = await update_user_profile(profile["id"], data, supabase, request)
    logger.info("profile_updated", user_id=profile["id"])
    return result


@router.get("/my-riders")
async def get_my_riders(
    current_profile: dict = Depends(require_user_type([UserType.DISPATCH])),
    supabase=Depends(get_supabase_client),
):
    """
    Get all riders managed by the current dispatch user (raw RPC call).
    
    Returns:
        list: List of riders.
    """
    resp = await supabase.rpc(
        "get_my_riders", {"dispatch_user_id": current_profile["id"]}
    ).execute()
    return resp.data


@router.get("/available-riders", response_model=List[AvailableRiderResponse])
async def list_available_riders(
    lat: Optional[float] = Query(None, description="Pickup latitude"),
    lng: Optional[float] = Query(None, description="Pickup longitude"),
    max_km: int = Query(20, description="Max distance in KM"),
    supabase=Depends(get_supabase_client),
):
    """
    Get a list of available riders near a pickup point.
    If no lat/lng is provided, returns all available riders nationwide.
    """
    return await get_available_riders(supabase, lat, lng, max_km)


@router.get("/my-riders", response_model=List[DispatchRiderResponse])
async def get_dispatch_riders(
    current_profile: dict = Depends(require_user_type([UserType.DISPATCH])),
    supabase=Depends(get_supabase_client),
):
    """
    Dispatch owner gets a list of all their riders
    with performance stats for management
    """
    return await get_my_riders(current_profile["id"], supabase)


@router.post("/riders/suspend", response_model=RiderSuspensionResponse)
async def suspend_rider(
    data: RiderSuspensionRequest,
    request: Request,
    current_profile: dict = Depends(require_user_type([UserType.DISPATCH])),
    supabase=Depends(get_supabase_admin_client),
):
    """
    Dispatch can suspend or unsuspend their riders
    Optional: temporary suspension with end date
    """
    logger.info(
        "rider_suspension_requested",
        dispatch_id=current_profile["id"],
        rider_id=data.rider_id,
        suspend=data.suspend,
    )
    result = await suspend_or_unsuspend_rider(
        data, current_profile["id"], supabase, request
    )
    logger.info(
        "rider_suspension_completed",
        dispatch_id=current_profile["id"],
        rider_id=data.rider_id,
        suspended=data.suspend,
    )
    return result


@router.get("/riders/{rider_id}/earnings", response_model=RiderEarningsResponse)
async def view_rider_earnings(
    rider_id: UUID,
    current_profile: dict = Depends(require_user_type([UserType.DISPATCH])),
    supabase=Depends(get_supabase_client),
):
    """
    View earnings for a specific rider.
    
    Args:
        rider_id (UUID): The ID of the rider.
        
    Returns:
        RiderEarningsResponse: Earnings data.
    """
    return await get_rider_earnings(rider_id, current_profile["id"], supabase)


@router.get("/earnings")
async def vendor_earnings_dashboard(
    current_profile: dict = Depends(
        require_user_type(
            [UserType.RESTAURANT_VENDOR, UserType.LAUNDRY_VENDOR, UserType.DISPATCH]
        )
    ),
    supabase=Depends(get_supabase_client),
):
    """
    Get earnings dashboard for vendors and dispatch.
    
    Returns:
        dict: Earnings summary and details.
    """
    return await get_vendor_earnings(current_profile["id"], supabase)


@router.post("/profile/image")
async def upload_profile_pic(
    file: UploadFile = File(...),
    request: Request = None,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Upload a profile picture.
    
    Args:
        file (UploadFile): The image file.
        
    Returns:
        dict: Success status and image URL.
    """
    logger.info(
        "profile_image_upload_requested",
        user_id=current_profile["id"],
        image_type="profile",
    )
    url = await upload_profile_image(
        file, current_profile["id"], "profile", supabase, request
    )
    logger.info(
        "profile_image_uploaded",
        user_id=current_profile["id"],
        image_type="profile",
        url=url,
    )
    return {"success": True, "url": url}


@router.post("/profile/backdrop")
async def upload_backdrop(
    file: UploadFile = File(...),
    request: Request = None,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Upload a backdrop/cover image.
    
    Args:
        file (UploadFile): The image file.
        
    Returns:
        dict: Success status and image URL.
    """
    logger.info(
        "backdrop_image_upload_requested",
        user_id=current_profile["id"],
        image_type="backdrop",
    )
    url = await upload_profile_image(
        file, current_profile["id"], "backdrop", supabase, request
    )
    logger.info(
        "backdrop_image_uploaded",
        user_id=current_profile["id"],
        image_type="backdrop",
        url=url,
    )
    return {"success": True, "url": url}


# ───────────────────────────────────────────────
# 4. Get Rider Details
# ───────────────────────────────────────────────
@router.get("/riders/{rider_id}", response_model=DetailedRiderResponse)
async def get_rider_details(
    rider_id: UUID,
    supabase: AsyncClient = Depends(get_supabase_client)
):
    """
    Get full rider profile + stats + dispatch-level aggregated stats.
    """
    return await user_service.get_rider_details(rider_id, supabase)


@router.post("/set-online")
async def set_online_status(
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """
    Toggle the user's online/offline status (for riders/vendors).
    
    Returns:
        dict: New status.
    """
    return await toggle_online_status(current_profile["id"], supabase)


@router.post("/update-location")
async def update_location_endpoint(
    data: UserLocationUpdate,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    """Update your current location (used for nearby matching, rider assignment, etc.)"""
    return await update_user_location(current_profile["id"], data, supabase)
