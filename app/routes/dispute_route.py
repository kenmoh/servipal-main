from fastapi import APIRouter, Depends
from typing import List
from uuid import UUID
from app.services import dispute_service
from app.schemas.dispute_schema import (
    DisputeCreate,
    DisputeMessageCreate,
    DisputeResolve,
    DisputeResponse,

)
from app.dependencies.auth import get_current_profile, require_user_type
from app.schemas.user_schemas import UserType
from supabase import AsyncClient
from app.database.supabase import get_supabase_client

router = APIRouter(prefix="/api/v1/disputes", tags=["Disputes"])


@router.post("/", response_model=DisputeResponse)
async def create_dispute(
    data: DisputeCreate, current_profile: dict = Depends(get_current_profile),
        supabase: AsyncClient = Depends(get_supabase_client)
):
    return await dispute_service.create_dispute(data, current_profile["id"], supabase)


@router.get("/my-disputes", response_model=List[DisputeResponse])
async def get_my_disputes(
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    # Fetch disputes where initiator or respondent
    return await dispute_service.get_my_disputes(current_profile["id"], supabase)


@router.get("/{dispute_id}", response_model=DisputeResponse)
async def get_dispute_detail(
    dispute_id: UUID, supabase: AsyncClient = Depends(get_supabase_client)
):
    # Fetch dispute + messages
    return await dispute_service.get_dispute_detail(dispute_id, supabase)


@router.post("/{dispute_id}/messages")
async def post_dispute_message(
    dispute_id: UUID,
    data: DisputeMessageCreate,
    current_profile: dict = Depends(get_current_profile),
    supabase: AsyncClient = Depends(get_supabase_client),
):
    return await dispute_service.post_dispute_message(
        dispute_id, data, current_profile["id"], supabase
    )


@router.post("/{dispute_id}/resolve")
async def resolve_dispute(
    dispute_id: UUID,
    data: DisputeResolve,
    current_profile: dict = Depends(
        require_user_type([UserType.ADMIN, UserType.MODERATOR, UserType.SUPER_ADMIN])
    ),
    supabase: AsyncClient = Depends(get_supabase_client),
):

    return await dispute_service.resolve_dispute(dispute_id=dispute_id, data=data, admin_id=current_profile["id"], supabase=supabase)
