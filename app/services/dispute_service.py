from fastapi import HTTPException, status
from uuid import UUID
from decimal import Decimal
from typing import List
from datetime import datetime
from supabase import AsyncClient
from app.schemas.dispute_schema import (
    DisputeCreate,
    DisputeMessageCreate,
    DisputeResolve,
    DisputeResponse,
    DisputeMessageResponse,
)
from app.utils.audit import log_audit_event
from app.utils.dispute_helpers import (
    get_order,
    update_order_status,
    is_admin,
    refund_escrow,
    release_escrow,
    release_escrow_funds_for_dispute,
    get_escrow_agreement,
)
from app.services.notification_service import notify_user


# ───────────────────────────────────────────────
# Create Dispute (Buyer Only)
# ───────────────────────────────────────────────
async def create_dispute(
    data: DisputeCreate, initiator_id: UUID, supabase: AsyncClient
):
    order = await get_order(data.order_id, data.order_type, supabase)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    if order["status"] not in [
        "COMPLETED",
        "READY",
        "ACCEPTED",
    ]:  # only after the order is active
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cannot open dispute for this order status",
        )

    # Determine respondent (seller/vendor)
    respondent_id = order["seller_id"] or order["vendor_id"]

    dispute_data = {
        "order_id": str(data.order_id),
        "order_type": data.order_type,
        "initiator_id": str(initiator_id),
        "respondent_id": str(respondent_id),
        "reason": data.reason,
        "status": "OPEN",
    }

    resp = await supabase.table("disputes").insert(dispute_data).execute()

    # Update order with dispute_id
    await update_order_status(
        data.order_id, data.order_type, resp.data[0]["id"], supabase
    )

    # Log audit
    await log_audit_event(
        entity_type="DISPUTE",
        entity_id=resp.data[0]["id"],
        action="OPENED",
        notes=data.reason,
        actor_id=str(initiator_id),
        actor_type="BUYER",
        supabase=supabase,
    )

    # Send a notification to respondent + admin (later)

    return resp.data[0]


# ───────────────────────────────────────────────
# Post Message
# ───────────────────────────────────────────────
async def post_dispute_message(
    dispute_id: UUID, 
    data: DisputeMessageCreate, 
    sender_id: UUID, supabase: AsyncClient,
    
):
    # Check participant
    dispute = (
        await supabase.table("disputes")
        .select("initiator_id, respondent_id, status")
        .eq("id", str(dispute_id))
        .single()
        .execute()
    )

    if str(sender_id) not in [
        str(dispute.data["initiator_id"]),
        str(dispute.data["respondent_id"]),
    ] and not await is_admin(sender_id, supabase):
        raise HTTPException(403, "You are not part of this dispute")

    if dispute.data["status"] in ["RESOLVED", "CLOSED"]:
        raise HTTPException(400, "Dispute is closed, cannot post new messages")

    message_data = {
        "dispute_id": str(dispute_id),
        "sender_id": str(sender_id),
        "message_text": data.message_text,
        "attachments": data.attachments or [],
    }

    resp = await supabase.table("dispute_messages").insert(message_data).execute()

    # Update dispute updated_at
    await (
        supabase.table("disputes")
        .update({"updated_at": datetime.now()})
        .eq("id", str(dispute_id))
        .execute()
    )

    # Log audit
    await log_audit_event(
        entity_type="DISPUTE_MESSAGE",
        entity_id=resp.data[0]["id"],
        action="POSTED",
        notes="New message in dispute",
        actor_id=str(sender_id),
        actor_type="USER",
        supabase=supabase,
    )

    # Realtime broadcast happens automatically via subscription on frontend

    return resp.data[0]


# ───────────────────────────────────────────────
# Resolve Dispute (Admin Only)
# ───────────────────────────────────────────────
async def resolve_dispute(
    dispute_id: UUID, data: DisputeResolve, admin_id: UUID, supabase: AsyncClient
):
    # Check admin
    if not await is_admin(admin_id, supabase):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can resolve disputes",
        )

    # Fetch dispute + order
    dispute = (
        await supabase.table("disputes")
        .select("order_id, order_type, status, initiator_id, respondent_id")
        .eq("id", str(dispute_id))
        .single()
        .execute()
    )

    if dispute.data["status"] not in ["OPEN", "UNDER_REVIEW", "ESCALATED"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispute not open for resolution",
        )

    # Update status
    await (
        supabase.table("disputes")
        .update(
            {
                "status": "RESOLVED",
                "resolution_notes": data.notes,
                "resolved_by_id": str(admin_id),
                "resolved_at": datetime.now(),
            }
        )
        .eq("id", str(dispute_id))
        .execute()
    )

    # Handle escrow based on resolution
    order = await get_order(
        dispute.data["order_id"], dispute.data["order_type"], supabase
    )
    if dispute.data["order_type"] == "ESCROW_AGREEMENT":
        # For escrow, handle differently
        agreement = await get_escrow_agreement(dispute.data["order_id"], supabase)
        total_amount = Decimal(str(agreement["amount"])) + Decimal(
            str(agreement["commission_amount"])
        )
        net_amount = Decimal(str(agreement["amount"]))

        if data.resolution == "BUYER_FAVOR":
            # Refund to initiator
            await refund_escrow(agreement["initiator_id"], total_amount, supabase)
            await update_order_status(
                order["id"], dispute.data["order_type"], "CANCELLED", supabase
            )
        elif data.resolution == "SELLER_FAVOR":
            # Release funds to recipients
            await release_escrow_funds_for_dispute(order["id"], supabase)
            await update_order_status(
                order["id"], dispute.data["order_type"], "COMPLETED", supabase
            )
    else:
        tx = (
            await supabase.table("transactions")
            .select("id, amount, from_user_id, to_user_id")
            .eq("order_id", order["id"])
            .single()
            .execute()
        )
        amount = tx.data["amount"]

        if data.resolution == "BUYER_FAVOR":
            # Full refund to buyer
            await refund_escrow(tx.data["from_user_id"], amount, supabase)
            await update_order_status(
                order["id"], dispute.data["order_type"], "CANCELLED", supabase
            )

        elif data.resolution == "SELLER_FAVOR":
            # Full release to seller
            await release_escrow(
                tx.data["from_user_id"], tx.data["to_user_id"], amount, supabase
            )
            await update_order_status(
                order["id"], dispute.data["order_type"], "COMPLETED", supabase
            )

        elif data.resolution == "COMPROMISE":
            # Partial refund (add split_amount to data later)
            pass  # Implement split if needed

    # Log audit
    await log_audit_event(
        entity_type="DISPUTE",
        entity_id=str(dispute_id),
        action="RESOLVED",
        notes=data.notes,
        actor_id=str(admin_id),
        actor_type="ADMIN",
        supabase=supabase,
    )

    # Notify participants
    await notify_user(
        dispute.data["initiator_id"],
        "Dispute resolved",
        "Your dispute has been resolved",
        data={"dispute_id": str(dispute_id)},
        supabase=supabase,
    )
    await notify_user(
        dispute.data["respondent_id"],
        "Dispute resolved",
        "Your dispute has been resolved",
        data={"dispute_id": str(dispute_id)},
        supabase=supabase,
    )

    return {"success": True, "message": "Dispute resolved"}


# ───────────────────────────────────────────────
# Get My Disputes
# ───────────────────────────────────────────────
async def get_my_disputes(
    current_user_id: UUID, supabase: AsyncClient
) -> List[DisputeResponse]:
    """
    Fetch all disputes where the current user is either the initiator (buyer) or respondent (seller/vendor).
    Ordered by most recent update.
    """
    try:
        # Explicit filter: user is initiator OR respondent
        disputes_resp = (
            await supabase.table("disputes")
            .select("""
                id,
                order_id,
                order_type,
                initiator_id,
                respondent_id,
                reason,
                status,
                resolution_notes,
                resolved_by_id,
                resolved_at,
                created_at,
                updated_at
            """)
            .or_(
                f"initiator_id.eq.{current_user_id},respondent_id.eq.{current_user_id}"
            )
            .order("updated_at", desc=True)
            .execute()
        )

        disputes = disputes_resp.data or []

        # Build responses (lightweight - no full messages here)
        result = []
        for d in disputes:
            # Optional: get message count for preview
            count_resp = (
                await supabase.table("dispute_messages")
                .select("count", count="exact")
                .eq("dispute_id", d["id"])
                .execute()
            )

            dispute_data = DisputeResponse(
                id=d["id"],
                order_id=d["order_id"],
                order_type=d["order_type"],
                initiator_id=d["initiator_id"],
                respondent_id=d["respondent_id"],
                reason=d["reason"],
                status=d["status"],
                resolution_notes=d["resolution_notes"],
                resolved_by_id=d["resolved_by_id"],
                resolved_at=d["resolved_at"],
                created_at=d["created_at"],
                updated_at=d["updated_at"],
                messages=[],
            )
            dispute_data.message_count = count_resp.count or 0  # optional field

            result.append(dispute_data)

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch my disputes: {str(e)}",
        )


# ───────────────────────────────────────────────
# Get Dispute Detail
# ───────────────────────────────────────────────
async def get_dispute_detail(
    dispute_id: UUID, supabase: AsyncClient
) -> DisputeResponse:
    """
    Fetch a single dispute + its full message thread.
    Only accessible if user is initiator, respondent, or admin/moderator.
    """
    try:
        # Fetch dispute (RLS filters access)
        dispute_resp = (
            await supabase.table("disputes")
            .select("""
                id,
                order_id,
                order_type,
                initiator_id,
                respondent_id,
                reason,
                status,
                resolution_notes,
                resolved_by_id,
                resolved_at,
                created_at,
                updated_at
            """)
            .eq("id", str(dispute_id))
            .single()
            .execute()
        )

        if not dispute_resp.data:
            raise HTTPException(404, "Dispute not found or you don't have access")

        dispute = dispute_resp.data

        # Fetch all messages (ordered by time)
        messages_resp = (
            await supabase.table("dispute_messages")
            .select("""
                id,
                sender_id,
                message_text,
                attachments,
                created_at
            """)
            .eq("dispute_id", str(dispute_id))
            .order("-created_at")
            .execute()
        )

        messages = [DisputeMessageResponse(**msg) for msg in messages_resp.data or []]

        return DisputeResponse(
            id=dispute["id"],
            order_id=dispute["order_id"],
            order_type=dispute["order_type"],
            initiator_id=dispute["initiator_id"],
            respondent_id=dispute["respondent_id"],
            reason=dispute["reason"],
            status=dispute["status"],
            resolution_notes=dispute["resolution_notes"],
            resolved_by_id=dispute["resolved_by_id"],
            resolved_at=dispute["resolved_at"],
            created_at=dispute["created_at"],
            updated_at=dispute["updated_at"],
            messages=messages,
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch dispute detail: {str(e)}",
        )
