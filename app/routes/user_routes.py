from fastapi import APIRouter, Depends, Query, File, UploadFile
from app.services.user_service import *
from app.dependencies.auth import get_current_profile, require_user_type
from app.database.supabase import get_supabase_client, get_supabase_admin_client

router = APIRouter(prefix="/api/v1/users", tags=["Users"])


@router.post("/riders", response_model=UserProfileResponse)
async def create_rider(
    data: RiderCreateByDispatch,
    current_user: dict = Depends(get_current_profile),
        superuser=Depends(require_user_type([UserType.DISPATCH])),
        supabase: AsyncClient =Depends(get_supabase_admin_client)
):
    return await create_rider_by_dispatch(data, current_user, supabase)

@router.get("/me", response_model=UserProfileResponse)
async def get_my_profile(profile: dict = Depends(get_current_profile),
                         supabase: AsyncClient = Depends(get_supabase_client)):

    return await get_user_profile(profile["id"], supabase)


@router.patch("/me", response_model=UserProfileResponse)
async def update_profile(
    data: ProfileUpdate,
    profile: dict = Depends(get_current_profile),
supabase: AsyncClient = Depends(get_supabase_client)
):
    return await update_user_profile(profile["id"], data, supabase)

@router.get("/my-riders")
async def get_my_riders(current_profile: dict = Depends(require_user_type([UserType.DISPATCH])),
                        supabase=Depends(get_supabase_client)):
    resp = await supabase.rpc("get_my_riders", {"dispatch_user_id": current_profile["id"]}).execute()
    return resp.data

@router.get("/available-riders", response_model=List[AvailableRiderResponse])
async def list_available_riders(
    lat: Optional[float] = Query(None, description="Pickup latitude"),
    lng: Optional[float] = Query(None, description="Pickup longitude"),
    max_km: int = Query(20, description="Max distance in KM"),
supabase=Depends(get_supabase_client)
):
    """
    Get a list of available riders near a pickup point.
    If no lat/lng is provided, returns all available riders nationwide.
    """
    return await get_available_riders(supabase, lat, lng, max_km)


@router.get("/riders/{rider_id}", response_model=RiderDetailResponse)
async def get_single_rider(
    rider_id: UUID,
        supabase=Depends(get_supabase_client)
):
    """
    Get a detailed profile of a specific rider.
    Used when a customer taps on a rider from the available list.
    """
    return await get_rider_details(rider_id, supabase)


@router.get("/my-riders", response_model=List[DispatchRiderResponse])
async def get_dispatch_riders(
    current_profile: dict = Depends(require_user_type([UserType.DISPATCH])),
        supabase=Depends(get_supabase_client)
):
    """
    Dispatch owner gets a list of all their riders
    with performance stats for management
    """
    return await get_my_riders(current_profile["id"], supabase)


@router.post("/riders/suspend", response_model=RiderSuspensionResponse)
async def suspend_rider(
    data: RiderSuspensionRequest,
    current_profile: dict = Depends(require_user_type([UserType.DISPATCH])),
        supabase=Depends(get_supabase_admin_client)
):
    """
    Dispatch can suspend or unsuspend their riders
    Optional: temporary suspension with end date
    """
    return await suspend_or_unsuspend_rider(data, current_profile["id"], supabase)


@router.get("/riders/{rider_id}/earnings", response_model=RiderEarningsResponse)
async def view_rider_earnings(
    rider_id: UUID,
    current_profile: dict = Depends(require_user_type([UserType.DISPATCH])),
        supabase=Depends(get_supabase_client)
):
    return await get_rider_earnings(rider_id, current_profile["id"], supabase)


@router.get("/earnings")
async def vendor_earnings_dashboard(
    current_profile: dict = Depends(require_user_type([UserType.RESTAURANT_VENDOR, UserType.LAUNDRY_VENDOR, UserType.DISPATCH])),
        supabase=Depends(get_supabase_client)
):
    return await get_vendor_earnings(current_profile["id"], supabase)


@router.post("/profile/image")
async def upload_profile_pic(
    file: UploadFile = File(...),
    current_profile: dict = Depends(get_current_profile)
):
    url = await upload_profile_image(file, current_profile["id"], "profile")
    return {"success": True, "url": url}

@router.post("/profile/backdrop")
async def upload_backdrop(
    file: UploadFile = File(...),
    current_profile: dict = Depends(get_current_profile)
):
    url = await upload_profile_image(file, current_profile["id"], "backdrop")
    return {"success": True, "url": url}