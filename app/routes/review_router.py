from fastapi import APIRouter, Depends
from uuid import UUID
from app.schemas.review_schemas import ReviewCreate, ReviewsListResponse
from app.services.review_service import create_review, get_reviews_for_entity
from app.dependencies.auth import get_current_profile

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])

@router.post("/orders/{order_id}/{order_type}")
async def submit_review(
    order_id: UUID,
    order_type: str,
    review_data: ReviewCreate,
    current_profile: dict = Depends(get_current_profile)
):
    """
    Submit review after order completion
    order_type: DELIVERY, FOOD, LAUNDRY, PRODUCT
    """
    return await create_review(order_id, order_type, review_data, current_profile["id"])

@router.get("/entity/{entity_id}/{entity_type}", response_model=ReviewsListResponse)
async def get_entity_reviews(
    entity_id: UUID,
    entity_type: str  # RIDER, VENDOR, DISPATCH
):
    """
    Get all reviews for a rider, vendor, or dispatch
    Used in profile pages and available list
    """
    return await get_reviews_for_entity(entity_id, entity_type)