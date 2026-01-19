from typing import List
from app.schemas.user_schemas import *
from fastapi import HTTPException, status, UploadFile, Request
from uuid import UUID
from datetime import datetime, timedelta
from supabase import AsyncClient
from app.config.logging import logger
from app.utils.audit import log_audit_event
from decimal import Decimal
from app.config.config import redis
from app.utils.utils import check_login_attempts, record_failed_attempt, reset_login_attempts

# ───────────────────────────────────────────────
# 1. Signup (Customer / Vendor / Dispatch)
# ───────────────────────────────────────────────


async def create_user_account(
    data: UserCreate, supabase: AsyncClient, request: Optional[Request] = None
) -> TokenResponse:
    logger.info("user_signup_attempt", email=data.email, user_type=data.user_type)
    try:
        # Sign up with Supabase Auth
        auth_resp = await supabase.auth.sign_up(
            {
                "email": data.email,
                "password": data.password,
                "options": {"data": {"user_type": data.user_type, "phone": data.phone}},
            }
        )

        if auth_resp.user is None:
            logger.warning(
                "signup_failed", email=data.email, reason="auth_resp.user_is_none"
            )
            raise HTTPException(status_code=400, detail="Signup failed")

        # Use the session from signup response (no need to login again)
        session = auth_resp.session

        # If email confirmation is enabled, session will be None until user confirms
        if session is None:
            # User was created but needs to confirm email
            # Fetch profile to return user info without tokens
            profile_resp = (
                await supabase.table("profiles")
                .select("*")
                .eq("id", auth_resp.user.id)
                .single()
                .execute()
            )

            return TokenResponse(
                access_token="",
                refresh_token="",
                expires_in=0,
                user=UserProfileResponse(**profile_resp.data),
                message="Please check your email to confirm your account before logging in.",
            )

        # Fetch profile
        profile_resp = (
            await supabase.table("profiles")
            .select("*")
            .eq("id", auth_resp.user.id)
            .single()
            .execute()
        )

        user_response = TokenResponse(
            access_token=session.access_token,
            refresh_token=session.refresh_token,
            expires_in=session.expires_in,
            user=UserProfileResponse(**profile_resp.data),
        )

        # Audit log
        await log_audit_event(
            supabase,
            entity_type="USER",
            entity_id=auth_resp.user.id,
            action="SIGNUP",
            new_value={"user_type": data.user_type, "email": data.email},
            actor_id=auth_resp.user.id,
            actor_type="USER",
            notes=f"New user account created: {data.user_type}",
            request=request,
        )

        logger.info(
            "user_signup_success",
            user_id=auth_resp.user.id,
            email=data.email,
            user_type=data.user_type,
        )
        return user_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("signup_error", email=data.email, error=str(e), exc_info=True)
        if "duplicate" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone or email already exists",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ───────────────────────────────────────────────
# 2. Login
# ───────────────────────────────────────────────
async def login_user(
    data: LoginRequest, supabase: AsyncClient, request: Optional[Request] = None
) -> TokenResponse:
    logger.info("login_attempt", email=data.email)
    
    # Check for too many failed attempts
    await check_login_attempts(data.email, redis)
    
    try:
        # Try phone first, then email
        credentials = {"password": data.password, "email": data.email}
        session = await supabase.auth.sign_in_with_password(credentials)

        profile_resp = (
            await supabase.table("profiles")
            .select("*")
            .eq("id", session.user.id)
            .single()
            .execute()
        )

        # Audit log
        await log_audit_event(
            supabase,
            entity_type="USER",
            entity_id=session.user.id,
            action="LOGIN",
            actor_id=session.user.id,
            actor_type="USER",
            notes="User logged in successfully",
            request=request,
        )

        try:
            user_profile = UserProfileResponse(**profile_resp.data)
        except Exception as e:
            logger.error("profile_parsing_error", data=profile_resp.data, error=str(e))
            raise HTTPException(status_code=500, detail="Profile data parsing error")

        # Reset login attempts on successful login
        await reset_login_attempts(data.email, redis)

        logger.info("login_success", user_id=session.user.id, email=data.email)
        return TokenResponse(
            access_token=session.session.access_token,
            refresh_token=session.session.refresh_token,
            expires_in=session.session.expires_in,
            user=user_profile,
        )

    except Exception as e:
        logger.warning("login_failed", email=data.email, error=str(e))
        # Record failed attempt
        await record_failed_attempt(data.email, redis)
        raise HTTPException(status_code=401, detail=f"Invalid credentials.")


# ───────────────────────────────────────────────
# 3. Create Rider (by Dispatch only)
# ───────────────────────────────────────────────
async def create_rider_by_dispatch(
    data: RiderCreateByDispatch,
    current_profile: dict,
    supabase_admin: AsyncClient,
    request: Optional[Request] = None,
) -> UserProfileResponse:
    logger.info(
        "create_rider_attempt",
        dispatch_id=current_profile["id"],
        rider_phone=data.phone,
    )

    # Define dispatcher_id for later use
    dispatcher_id = current_profile["id"]

    # Fetch the dispatcher profile with required fields
    dispatch_profile_resp = (
        await supabase_admin.table("profiles")
        .select("user_type, business_name, business_address, state, business_registration_number")
        .eq("id", current_profile["id"])
        .single()
        .execute()
    )

    dispatch_profile = dispatch_profile_resp.data

    # Security: Only DISPATCH can create riders
    if dispatch_profile["user_type"] != UserType.DISPATCH.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only dispatch users can create riders"
        )
    
    riders = await get_my_riders()
    # Validation: Limit riders if no business registration number
    if  dispatch_profile['business_registration_number'] is None and len(riders) >= 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Rider limit reached. Add a valid business registration number."
        )

    # Validation: Dispatch must have completed business details
    missing_fields = []
    if not dispatch_profile.get("business_name"):
        missing_fields.append("business_name")
    if not dispatch_profile.get("business_address"):
        missing_fields.append("business_address")
    if not dispatch_profile.get("state"):
        missing_fields.append("state")

    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Please complete your profile first: {', '.join(missing_fields)} required to create riders.",
        )

    try:
        # Create a user in Supabase Auth using an admin client
        admin_resp = await supabase_admin.auth.admin.create_user(
            {
                "email": data.email,
                "phone": data.phone,
                "password": "TempPass123!",
                "phone_confirm": True,
                "user_metadata": {
                    "created_by": "dispatch",
                    "user_type": UserType.RIDER.value,
                    "phone_number": data.phone,
                    "bike_number": data.bike_number,
                    "full_name": data.full_name,
                    "dispatcher_id": str(dispatcher_id),
                },
                "email_confirm": True,
            }
        )

        user_id = admin_resp.user.id

        # Upsert rider profile with inherited dispatch business details
        rider_profile_data = {
            "id": str(user_id),
            "user_type": UserType.RIDER.value,
            "phone_number": data.phone,
            "full_name": data.full_name,
            "bike_number": data.bike_number,
            "dispatcher_id": str(dispatcher_id),
            # Inherited from dispatch
            "business_name": dispatch_profile["business_name"],
            "business_address": dispatch_profile["business_address"],
            "state": dispatch_profile["state"],
            # Rider-specific defaults
            "is_verified": False,
            "account_status": "PENDING",
            "has_delivery": False,
            "is_online": False,
        }

        await supabase_admin.table("profiles").upsert(rider_profile_data).execute()

        # Fetch final profile
        profile_resp = (
            await supabase_admin.table("profiles")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )

        result = UserProfileResponse(**profile_resp.data)

        # Audit log
        await log_audit_event(
            supabase_admin,
            entity_type="USER",
            entity_id=str(user_id),
            action="CREATE_RIDER",
            new_value={
                "rider_phone": data.phone,
                "rider_name": data.full_name,
                "bike_number": data.bike_number,
            },
            actor_id=str(dispatcher_id),
            actor_type="DISPATCH",
            notes=f"Rider created by dispatch: {data.full_name}",
            request=request,
        )

        logger.info(
            "rider_created_success",
            dispatch_id=str(dispatcher_id),
            rider_id=str(user_id),
            rider_phone=data.phone,
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        logger.error(
            "create_rider_error",
            dispatch_id=str(dispatcher_id),
            error=str(e),
            exc_info=True,
        )
        if "duplicate" in error_msg or "already registered" in error_msg:
            raise HTTPException(
                status_code=409, detail="Phone number already registered"
            )
        raise HTTPException(
            status_code=500, detail=f"Failed to create rider account: {str(e)}"
        )


# ───────────────────────────────────────────────
# 4. Get Current User Profile
# ───────────────────────────────────────────────
async def get_user_profile(user_id: UUID, supabase: AsyncClient) -> UserProfileResponse:
    resp = (
        await supabase.table("profiles")
        .select("*")
        .eq("id", user_id)
        .single()
        .execute()
    )

    if not resp.data:
        raise HTTPException(status_code=404, detail="Profile not found")

    return UserProfileResponse(**resp.data)


# ───────────────────────────────────────────────
# 5. Update Profile
# ───────────────────────────────────────────────
async def update_user_profile(
    user_id: UUID,
    data: ProfileUpdate,
    supabase: AsyncClient,
    request: Optional[Request] = None,
) -> UserProfileResponse:
    logger.info("update_profile_attempt", user_id=str(user_id))
    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No data provided"
        )

    # Get the current profile to check user_type (because user_type might not be in update_data)
    current_profile = (
        await supabase.table("profiles")
        .select("user_type, can_pickup_and_dropoff")
        .eq("id", str(user_id))
        .single()
        .execute()
    )

    old_value = current_profile.data.copy()
    current_type = current_profile.data["user_type"]
    current_self_delivery = current_profile.data["can_pickup_and_dropoff"]

    # Determine if self-delivery will be enabled after the update
    will_enable_self_delivery = (
        update_data.get("can_pickup_and_dropoff", current_self_delivery) == True
    )

    # If user is vendor and self-delivery will be enabled
    if (
        current_type in ["RESTAURANT_VENDOR", "LAUNDRY_VENDOR"]
        and will_enable_self_delivery
    ):
        new_charge = update_data.get("pickup_and_delivery_charge")
        if new_charge is None or new_charge <= 0:
            raise HTTPException(
                status_code=400,
                detail="Delivery charge must be greater than 0 when self-delivery is enabled",
            )

    # Proceed with the update
    resp = (
        await supabase.table("profiles")
        .update(update_data)
        .eq("id", str(user_id))
        .execute()
    )

    if not resp.data:
        raise HTTPException(status_code=404, detail="Profile not found")

    new_value = resp.data[0]

    # Audit log
    await log_audit_event(
        supabase,
        entity_type="PROFILE",
        entity_id=str(user_id),
        action="UPDATE",
        old_value=old_value,
        new_value=new_value,
        actor_id=str(user_id),
        actor_type="USER",
        notes="Profile updated",
        request=request,
    )

    logger.info("profile_updated_success", user_id=str(user_id))
    return UserProfileResponse(**new_value)


# ───────────────────────────────────────────────
# 6. Refresh Online Status (call on every protected request)
# ───────────────────────────────────────────────
async def refresh_online_status(user_id: UUID, supabase: AsyncClient):
    await (
        supabase.table("profiles")
        .update({"is_online": True, "last_seen_at": datetime.now().isoformat()})
        .eq("id", user_id)
        .execute()
    )


# ───────────────────────────────────────────────
# 7. Get all available riders
# ───────────────────────────────────────────────


async def get_available_riders(
    supabase: AsyncClient,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    max_distance_km: int = 20,
) -> List[AvailableRiderResponse]:
    params = {
        "near_lat": latitude,
        "near_lng": longitude,
        "max_distance_km": max_distance_km,
    }

    try:
        resp = await supabase.rpc("get_available_riders", params).execute()

        if not resp.data:
            return []

        return [AvailableRiderResponse(**rider) for rider in resp.data]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch riders: {str(e)}")


# ───────────────────────────────────────────────
# 8. Get Rider Details
# ───────────────────────────────────────────────


async def get_rider_details(
    rider_id: UUID, supabase: AsyncClient
) -> DetailedRiderResponse:
    """
    Get full rider profile + stats + dispatch-level aggregated stats.
    """
    try:
        # Fetch rider profile with basic fields
        rider_resp = (
            await supabase.table("profiles")
            .select(
                "id, full_name, phone_number, profile_image_url, bike_number, "
                "average_rating, business_name, review_count, is_online, dispatcher_id, user_type"
            )
            .eq("id", rider_id)
            .eq("user_type", "RIDER")
            .execute()
        )

        if not rider_resp.data or len(rider_resp.data) == 0:
            logger.warning("rider_not_found", rider_id=str(rider_id))
            raise HTTPException(404, "Rider not found")

        rider = rider_resp.data[0]

        # Log the user_type for debugging
        logger.debug(
            "fetched_rider_data",
            rider_id=str(rider_id),
            user_type=rider.get("user_type"),
        )

        dispatch_stats = None

        # Get dispatch name if dispatcher_id exists
        if rider.get("dispatcher_id"):
            # Get dispatch aggregated stats
            stats_resp = (
                await supabase.table("profiles")
                .select("dispatch_average_rating, dispatch_review_count")
                .eq("id", str(rider["dispatcher_id"]))
                .execute()
            )

            dispatch_stats = (
                stats_resp.data[0]
                if stats_resp.data and len(stats_resp.data) > 0
                else {}
            )

        # Build final response
        return DetailedRiderResponse(**rider, dispatch_stats=dispatch_stats)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "get_rider_details failed",
            rider_id=str(rider_id),
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(500, "Failed to fetch rider details")


# ───────────────────────────────────────────────
# 9. Get My Riders (Dispatch)
# ───────────────────────────────────────────────
async def get_my_riders(
    dispatch_user_id: UUID, supabase: AsyncClient
) -> List[DispatchRiderResponse]:
    try:
        resp = await supabase.rpc(
            "get_my_dispatch_riders", {"dispatch_user_id": str(dispatch_user_id)}
        ).execute()

        if not resp.data:
            return []

        return [DispatchRiderResponse(**rider) for rider in resp.data]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch riders: {str(e)}")


# ───────────────────────────────────────────────
# 10. Suspend/Unsuspend Rider
# ───────────────────────────────────────────────
async def suspend_or_unsuspend_rider(
    data: RiderSuspensionRequest,
    dispatcher_id: UUID,
    supabase: AsyncClient,
    request: Optional[Request] = None,
) -> RiderSuspensionResponse:
    logger.info(
        "rider_suspension_attempt",
        dispatch_id=str(dispatcher_id),
        rider_id=str(data.rider_id),
        suspend=data.suspend,
    )
    try:
        # 1. Validate rider belongs to this dispatch
        rider_resp = (
            await supabase.table("profiles")
            .select("id, dispatcher_id, full_name")
            .eq("id", str(data.rider_id))
            .single()
            .execute()
        )

        if not rider_resp.data:
            raise HTTPException(404, "Rider not found")

        rider = rider_resp.data

        if rider["dispatcher_id"] != str(dispatcher_id):
            raise HTTPException(403, "This rider does not belong to your fleet")

        # 2. Calculate suspension until if temporary
        suspension_until = None
        if data.suspend and data.suspension_days:
            suspension_until = datetime.now() + timedelta(days=data.suspension_days)

        # 3. Update rider status
        update_data = {
            "rider_is_suspended_for_order_cancel": data.suspend,
            "rider_suspension_until": suspension_until.isoformat()
            if suspension_until
            else None,
        }

        await (
            supabase.table("profiles")
            .update(update_data)
            .eq("id", str(data.rider_id))
            .execute()
        )

        action = "suspended" if data.suspend else "unsuspended"
        message = f"Rider {rider['full_name']} has been {action}."

        if data.suspend and data.suspension_days:
            message += f" Suspension ends on {suspension_until.date()}."

        result = RiderSuspensionResponse(
            rider_id=data.rider_id,
            suspended=data.suspend,
            suspension_until=suspension_until,
            message=message,
        )

        # Audit log
        await log_audit_event(
            supabase,
            entity_type="RIDER",
            entity_id=str(data.rider_id),
            action="SUSPEND" if data.suspend else "UNSUSPEND",
            old_value={"suspended": not data.suspend},
            new_value={
                "suspended": data.suspend,
                "suspension_until": suspension_until.isoformat()
                if suspension_until
                else None,
            },
            actor_id=str(dispatcher_id),
            actor_type="DISPATCH",
            notes=message,
            request=request,
        )

        logger.info(
            "rider_suspension_completed",
            dispatch_id=str(dispatcher_id),
            rider_id=str(data.rider_id),
            suspended=data.suspend,
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "rider_suspension_error",
            dispatch_id=str(dispatcher_id),
            rider_id=str(data.rider_id),
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(500, f"Operation failed: {str(e)}")


# ───────────────────────────────────────────────
# 11. Rider & Vendor Earnings
# ───────────────────────────────────────────────
async def get_rider_earnings(
    rider_id: UUID, dispatcher_id: UUID, supabase: AsyncClient
) -> RiderEarningsResponse:
    # Validates rider belongs to dispatch
    rider = (
        await supabase.table("profiles")
        .select("full_name, dispatcher_id")
        .eq("id", str(rider_id))
        .single()
        .execute()
    )

    if rider.data["dispatcher_id"] != str(dispatcher_id):
        raise HTTPException(403, "Not your rider")

    resp = await supabase.rpc(
        "get_rider_earnings", {"rider_user_id": str(rider_id)}
    ).execute()
    earnings = (
        resp.data[0]
        if resp.data
        else {
            "total_earnings": 0,
            "completed_deliveries": 0,
            "pending_earnings": 0,
            "total_distance": 0,
        }
    )

    return RiderEarningsResponse(
        rider_id=rider_id,
        rider_name=rider.data["full_name"],
        total_earnings=Decimal(earnings["total_earnings"]),
        completed_deliveries=earnings["completed_deliveries"],
        pending_earnings=Decimal(earnings["pending_earnings"]),
        total_distance=Decimal(earnings["total_distance"]),
    )


async def get_vendor_earnings(vendor_id: UUID, supabase: AsyncClient) -> dict:
    resp = await supabase.rpc(
        "get_vendor_earnings", {"vendor_user_id": str(vendor_id)}
    ).execute()
    data = (
        resp.data[0]
        if resp.data
        else {
            "total_earnings": 0,
            "completed_orders": 0,
            "pending_earnings": 0,
            "pending_orders": 0,
            "todays_earnings": 0,
            "this_month_earnings": 0,
        }
    )

    return {
        "total_earnings": data["total_earnings"],
        "completed_orders": data["completed_orders"],
        "pending_earnings": data["pending_earnings"],
        "pending_orders": data["pending_orders"],
        "today": data["todays_earnings"],
        "this_month": data["this_month_earnings"],
    }


# ───────────────────────────────────────────────
# 12. Upload Profile Image
# ───────────────────────────────────────────────
async def upload_profile_image(
    file: UploadFile,
    user_id: UUID,
    image_type: Literal["profile", "backdrop"],
    supabase: AsyncClient,
    request: Optional[Request] = None,
) -> str:
    """Upload image and return public URL"""
    from app.utils.storage import upload_to_supabase_storage

    logger.info(
        "upload_profile_image_started",
        user_id=str(user_id),
        image_type=image_type,
        filename=file.filename,
    )
    try:
        folder = f"users/{user_id}/{image_type}"
        url = await upload_to_supabase_storage(
            file=file, supabase=supabase, bucket="profile-images", folder=folder
        )

        # Insert into profile_images table
        await (
            supabase.table("profile_images")
            .insert(
                {
                    "user_id": str(user_id),
                    "image_type": image_type,
                    "image_url": url,
                    "file_path": f"{folder}/{file.filename}",
                    "file_name": file.filename,
                    "mime_type": file.content_type,
                    "size_bytes": file.size,
                    "metadata": {},
                }
            )
            .execute()
        )

        # Update profiles table with latest active URL
        await (
            supabase.table("profiles")
            .update({f"{image_type}_image_url": url})
            .eq("id", str(user_id))
            .execute()
        )

        # Audit log
        await log_audit_event(
            supabase,
            entity_type="PROFILE",
            entity_id=str(user_id),
            action="UPLOAD_IMAGE",
            new_value={f"{image_type}_image_url": url},
            actor_id=str(user_id),
            actor_type="USER",
            notes=f"Uploaded {image_type} image",
            request=request,
        )

        logger.info(
            "upload_profile_image_success",
            user_id=str(user_id),
            image_type=image_type,
            url=url,
        )
        return url
    except Exception as e:
        logger.error(
            "upload_profile_image_error",
            user_id=str(user_id),
            image_type=image_type,
            error=str(e),
            exc_info=True,
        )
        raise


# ───────────────────────────────────────────────
# 13. Update User Location
# ───────────────────────────────────────────────
async def update_user_location(
    user_id: UUID,
    data: UserLocationUpdate,
    supabase: AsyncClient,
) -> dict:
    """
    Update user's current location (stored as PostGIS geography point).
    Used for rider/driver proximity matching, nearby vendors, etc.
    """
    try:
        # Basic validation (already done by Pydantic, but extra safety)
        if not (-90 <= data.latitude <= 90) or not (-180 <= data.longitude <= 180):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Invalid latitude/longitude values"
            )

        # Convert to PostGIS point string: POINT(long lat)
        point_str = f"POINT({data.longitude} {data.latitude})"

        # Update location
        await (
            supabase.table("profiles")
            .update(
                {
                    "location_coordinates": point_str,
                    "updated_at": datetime.now().isoformat(),
                }
            )
            .eq("id", str(user_id))
            .execute()
        )

        return {
            "success": True,
            "message": "Location updated successfully",
            "latitude": data.latitude,
            "longitude": data.longitude,
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Failed to update location: {str(e)}",
        )


async def toggle_online_or_can_pickup(
    user_id: UUID,
    supabase: AsyncClient,
    field: str = "is_online",
) -> OnlineStatusResponse:
    """
    Automatically toggle the user's online status:
    - If currently online → set to offline
    - If currently offline → set to online
    Returns the new status.
    """
    try:
        # 1. Read current status
        current = (
            await supabase.table("profiles")
            .select(field)
            .eq("id", str(user_id))
            .single()
            .execute()
        )

        if not current.data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User profile not found")

        old_status = current.data[field]
        new_status = not old_status  # flip it!

        # 2. Update
        await (
            supabase.table("profiles")
            .update({"is_online": new_status, "updated_at": datetime.now().isoformat()})
            .eq("id", str(user_id))
            .execute()
        )

        is_online_msg = "online" if new_status else "offline"
        can_pickup_and_dropoff_msg = 'Pickup enabled' if new_status else 'Pickup disabled'
        key = field == "is_online" and "is_online" or "can_pickup_and_dropoff"
        return {
            "success": True,
            "message": field == "is_online" and f"You are now {is_online_msg}." or f"{can_pickup_and_dropoff_msg}.",
            key: new_status,
        }

    except Exception as e:
        logger.error(
            "Toggle online status failed",
            user_id=str(user_id),
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Failed to toggle online status: {str(e)}",
        )
