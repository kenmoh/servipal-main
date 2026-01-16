from fastapi import HTTPException, status
from supabase import AsyncClient
from app.schemas.review_schemas import *

async def create_review(
    order_id: UUID,
    order_type: str,
    data: ReviewCreate,
    reviewer_id: UUID,
        supabase: AsyncClient
) -> dict:
    try:
        if order_type == "DELIVERY":
            # Fetch delivery to get rider_id or dispatch_id
            delivery_resp = await supabase.table("deliveries")\
                .select("rider_id, dispatch_id")\
                .eq("order_id", str(order_id))\
                .single()\
                .execute()

            if not delivery_resp.data:
                raise HTTPException(404, "Delivery order not found")

            delivery = delivery_resp.data

            if data.reviewee_type == "RIDER":
                reviewee_id = delivery["rider_id"]
            elif data.reviewee_type == "DISPATCH":
                reviewee_id = delivery["dispatch_id"]
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail= "Invalid reviewee_type for delivery order")

        elif order_type in ("FOOD", "LAUNDRY", "PRODUCT"):
            # For vendor-managed orders: reviewee is always the vendor
            # (you can extend later to allow item-specific reviews)
            order_table = {
                "FOOD": "food_orders",
                "LAUNDRY": "laundry_orders",
                "PRODUCT": "product_orders"
            }[order_type]

            order_resp = await supabase.table(order_table)\
                .select("vendor_id")\
                .eq("id", str(order_id))\
                .single()\
                .execute()

            if not order_resp.data:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{order_type} order not found")

            reviewee_id = order_resp.data["vendor_id"]

            if data.reviewee_type != "VENDOR":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only vendor can be reviewed for this order type")

        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail= "Unsupported order type")

        # Final validation: reviewee_id must be set
        if not reviewee_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not determine reviewee")

        # Prevent duplicate review
        existing = await supabase.table("reviews")\
            .select("id")\
            .eq("reviewer_id", str(reviewer_id))\
            .eq("order_id", str(order_id))\
            .eq("reviewee_type", data.reviewee_type)\
            .execute()

        if existing.data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You have already reviewed this")

        # Insert the review
        insert_resp = await supabase.table("reviews").insert({
            "reviewer_id": str(reviewer_id),
            "reviewee_id": reviewee_id,
            "reviewee_type": data.reviewee_type,
            "item_id": str(data.item_id) if data.item_id else None,
            "order_id": str(order_id),
            "order_type": order_type,
            "rating": data.rating,
            "comment": data.comment
        }).execute()

        return {
            "success": True,
            "message": "Review submitted successfully",
            "review_id": insert_resp.data[0]["id"]
        }

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Review creation failed: {str(e)}")


async def get_dispatch_rating(dispatch_id: UUID, supabase: AsyncClient) -> dict:

    resp = await supabase.rpc("get_dispatch_rating", {"dispatch_user_id": str(dispatch_id)}).execute()
    
    data = resp.data[0] if resp.data else {"average_rating": 0.0, "total_reviews": 0, "total_riders": 0}
    
    return {
        "dispatch_rating": data["average_rating"],
        "based_on_reviews": data["total_reviews"],
        "from_riders": data["total_riders"]
    }

async def get_reviews_for_entity(
    entity_id: UUID,
    entity_type: str,  # RIDER, VENDOR, DISPATCH
        supabase: AsyncClient
) -> ReviewsListResponse:

    resp = await supabase.table("reviews")\
        .select("""
            id,
            rating,
            comment,
            created_at,
            reviewer_id!inner(full_name, profile_image_url)
        """)\
        .eq("reviewee_id", str(entity_id))\
        .eq("reviewee_type", entity_type)\
        .order("created_at", desc=True)\
        .execute()

    reviews = []
    total_rating = 0
    for r in resp.data:
        total_rating += r["rating"]
        reviews.append(ReviewResponse(
            id=r["id"],
            reviewer_name=r["reviewer_id"]["full_name"],
            reviewer_profile_url=r["reviewer_id"]["profile_image_url"],
            rating=r["rating"],
            comment=r["comment"],
            created_at=r["created_at"]
        ))

    avg = round(total_rating / len(reviews), 2) if reviews else 0.0

    return ReviewsListResponse(
        reviews=reviews,
        average_rating=avg,
        total_reviews=len(reviews)
    )