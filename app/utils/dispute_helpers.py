from fastapi import HTTPException, status
from uuid import UUID
from typing import Dict, Any, Literal
from supabase import AsyncClient
from decimal import Decimal


# Helper: Fetch order from the correct table based on order_type
async def get_order(
    order_id: UUID,
    order_type: Literal["DELIVERY", "PRODUCT", "FOOD", "LAUNDRY"],
    supabase: AsyncClient,
) -> Dict[str, Any]:
    """
    Fetch order details from the appropriate table.
    Returns dict with at least: buyer_id/customer_id, seller_id/vendor_id, status/order_status, payment_status, grand_total/amount
    """
    table_map = {
        "DELIVERY": "delivery_orders",
        "PRODUCT": "product_orders",
        "FOOD": "food_orders",
        "LAUNDRY": "laundry_orders",
    }

    table_name = table_map.get(order_type)
    if not table_name:
        raise ValueError(f"Invalid order type: {order_type}")

    resp = (
        await supabase.table(table_name)
        .select(
            "id, buyer_id, customer_id, seller_id, vendor_id, order_status, status, payment_status, grand_total, amount"
        )
        .eq("id", str(order_id))
        .single()
        .execute()
    )

    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{order_type} order not found",
        )

    order = resp.data

    # Normalize field names
    return {
        "id": order["id"],
        "buyer_id": order.get("buyer_id") or order.get("customer_id"),
        "seller_id": order.get("seller_id") or order.get("vendor_id"),
        "status": order.get("order_status") or order.get("status"),
        "payment_status": order["payment_status"],
        "amount": order.get("grand_total") or order.get("amount"),
    }


# Helper: Update order status in correct table
async def update_order_status(
    order_id: UUID,
    order_type: Literal["DELIVERY", "PRODUCT", "FOOD", "LAUNDRY"],
    new_status: str,
    supabase: AsyncClient,
):
    table_map = {
        "DELIVERY": "delivery_orders",
        "PRODUCT": "product_orders",
        "FOOD": "food_orders",
        "LAUNDRY": "laundry_orders",
    }

    table_name = table_map.get(order_type)
    if not table_name:
        raise ValueError(f"Invalid order type: {order_type}")

    status_field = "order_status" if "order_status" in table_name else "status"
    await (
        supabase.table(table_name)
        .update({status_field: new_status})
        .eq("id", str(order_id))
        .execute()
    )


# Helper: Check if user is admin or moderator
async def is_admin(user_id: UUID, supabase: AsyncClient) -> bool:
    profile = (
        await supabase.table("profiles")
        .select("user_type")
        .eq("id", str(user_id))
        .single()
        .execute()
    )

    if not profile.data:
        return False

    return profile.data["user_type"] in ["ADMIN", "MODERATOR", "SUPER_ADMIN"]


# Helper: Refund escrow to buyer (atomic)
async def refund_escrow(buyer_id: UUID, amount: Decimal, supabase: AsyncClient):
    await supabase.rpc(
        "update_wallet_balance",
        {"p_user_id": str(buyer_id), "p_delta": -amount, "p_field": "escrow_balance"},
    ).execute()

    await supabase.rpc(
        "update_wallet_balance",
        {"p_user_id": str(buyer_id), "p_delta": amount, "p_field": "balance"},
    ).execute()


# Helper: Release escrow to seller (atomic)
async def release_escrow(
    buyer_id: UUID, seller_id: UUID, amount: Decimal, supabase: AsyncClient
):
    await supabase.rpc(
        "update_wallet_balance",
        {"p_user_id": str(buyer_id), "p_delta": -amount, "p_field": "escrow_balance"},
    ).execute()

    await supabase.rpc(
        "update_wallet_balance",
        {"p_user_id": str(seller_id), "p_delta": amount, "p_field": "balance"},
    ).execute()


# Helper: Release escrow funds for dispute (to all recipients)
async def release_escrow_funds_for_dispute(agreement_id: UUID, supabase: AsyncClient):
    agreement = (
        await supabase.table("escrow_agreements")
        .select("amount, commission_rate, initiator_id")
        .eq("id", str(agreement_id))
        .single()
        .execute()
        .data
    )

    full_amount = Decimal(str(agreement["amount"]))
    commission_amount = full_amount * Decimal(str(agreement["commission_rate"]))

    recipients = (
        await supabase.table("escrow_agreement_parties")
        .select("user_id, share_amount")
        .eq("agreement_id", str(agreement_id))
        .eq("role", "RECIPIENT")
        .execute()
        .data
    )

    for r in recipients:
        share = Decimal(str(r["share_amount"]))
        await supabase.rpc(
            "update_wallet_balance",
            {
                "p_user_id": str(agreement["initiator_id"]),
                "p_delta": -share,
                "p_field": "escrow_balance",
            },
        ).execute()

        await supabase.rpc(
            "update_wallet_balance",
            {
                "p_user_id": str(r["user_id"]),
                "p_delta": share,
                "p_field": "balance",
            },
        ).execute()

    await supabase.rpc(
        "update_wallet_balance",
        {
            "p_user_id": str(agreement["initiator_id"]),
            "p_delta": -commission_amount,
            "p_field": "escrow_balance",
        },
    ).execute()

    # Commission to platform
    await (
        supabase.table("platform_commissions")
        .insert(
            {
                "service_type": "ESCROW_AGREEMENT",
                "commission_amount": float(commission_amount),
                "description": f"Commission from escrow dispute resolution {agreement_id}",
            }
        )
        .execute()
    )


# Helper: Fetch escrow agreement details
async def get_escrow_agreement(
    agreement_id: UUID, supabase: AsyncClient
) -> Dict[str, Any]:
    """
    Fetch escrow agreement details.
    Returns dict with initiator_id, amount, commission_amount, status, etc.
    """
    resp = (
        await supabase.table("escrow_agreements")
        .select("id, initiator_id, amount, commission_amount, status")
        .eq("id", str(agreement_id))
        .single()
        .execute()
    )

    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escrow agreement not found",
        )

    return resp.data
