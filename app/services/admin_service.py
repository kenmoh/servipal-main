from datetime import timedelta
from typing import cast
from supabase import AsyncClient
from postgrest.types import CountMethod
from fastapi import HTTPException, Request
from app.config.logging import logger
from app.schemas.admin_schemas import *
from app.schemas.user_schemas import UserType
from app.utils.audit import log_audit_event

# ───────────────────────────────────────────────
# USER MANAGEMENT
# ───────────────────────────────────────────────


async def list_users(
    filters: UserFilterParams, pagination: PaginationParams, admin_client: AsyncClient
) -> UsersListResponse:
    """List all users with filters and pagination"""
    try:
        query = admin_client.table("profiles").select("*", count="exact")

        # Apply filters
        if filters.user_type:
            query = query.eq("user_type", filters.user_type.value)
        if filters.is_verified is not None:
            query = query.eq("is_verified", filters.is_verified)
        if filters.is_blocked is not None:
            query = query.eq("is_blocked", filters.is_blocked)
        if filters.account_status:
            query = query.eq("account_status", filters.account_status)
        if filters.created_from:
            query = query.gte("created_at", filters.created_from.isoformat())
        if filters.created_to:
            query = query.lte("created_at", filters.created_to.isoformat())
        if filters.search:
            # Search in name, email, phone - use ilike with separate filters
            search_term = f"%{filters.search}%"

            pass  # Will filter after fetch

        # Get total count
        count_resp = await query.execute()
        total = count_resp.count if count_resp.count else 0

        # Get all matching users first (for search filtering)
        resp = await query.order("created_at", desc=True).execute()

        # Apply search filter in Python if needed
        filtered_data = resp.data
        if filters.search:
            search_term = filters.search.lower()
            filtered_data = [
                u
                for u in resp.data
                if search_term in (u.get("full_name", "") or "").lower()
                or search_term in (u.get("email", "") or "").lower()
                or search_term in (u.get("phone_number", "") or "").lower()
            ]

        # Apply pagination after filtering
        total = len(filtered_data)
        offset = (pagination.page - 1) * pagination.page_size
        paginated_data = filtered_data[offset : offset + pagination.page_size]

        # Enhance with stats
        users = []
        for user_data in paginated_data:
            user_id = user_data["id"]

            # Get order stats
            order_stats = await get_user_order_stats(user_id, admin_client)
            wallet_stats = await get_user_wallet_stats(user_id, admin_client)

            user_data["total_orders"] = order_stats.get("total_orders", 0)
            user_data["total_spent"] = wallet_stats.get("total_spent", 0)
            user_data["total_earned"] = wallet_stats.get("total_earned", 0)

            users.append(AdminUserResponse(**user_data))

        return UsersListResponse(
            users=users,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )
    except Exception as e:
        logger.error("admin_list_users_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list users: {str(e)}")


async def get_user_details(
    user_id: UUID, admin_client: AsyncClient
) -> AdminUserResponse:
    """Get detailed user information"""
    try:
        resp = (
            await admin_client.table("profiles")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not resp.data:
            raise HTTPException(status_code=404, detail="User not found")

        user_data = resp.data

        # Get additional stats
        order_stats = await get_user_order_stats(user_id, admin_client)
        wallet_stats = await get_user_wallet_stats(user_id, admin_client)

        user_data["total_orders"] = order_stats.get("total_orders", 0)
        user_data["total_spent"] = wallet_stats.get("total_spent", 0)
        user_data["total_earned"] = wallet_stats.get("total_earned", 0)

        return AdminUserResponse(**user_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "admin_get_user_error", user_id=str(user_id), error=str(e), exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to get user: {str(e)}")


async def update_user(
    user_id: UUID,
    updates: AdminUserUpdate,
    admin_id: UUID,
    admin_client: AsyncClient,
    request: Optional[Request] = None,
) -> AdminUserResponse:
    """Update user profile (admin)"""
    try:
        # Get current user data for audit
        current_resp = (
            await admin_client.table("profiles")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not current_resp.data:
            raise HTTPException(status_code=404, detail="User not found")

        old_data = current_resp.data.copy()

        # Prepare update dict
        update_dict = updates.model_dump(exclude_unset=True)
        if not update_dict:
            raise HTTPException(status_code=400, detail="No updates provided")

        # Handle user_type enum
        if "user_type" in update_dict and isinstance(
            update_dict["user_type"], UserType
        ):
            update_dict["user_type"] = update_dict["user_type"].value

        # Update profile
        resp = (
            await admin_client.table("profiles")
            .update(update_dict)
            .eq("id", user_id)
            .execute()
        )

        if not resp.data:
            raise HTTPException(status_code=500, detail="Update failed")

        # Audit log
        await log_audit_event(
            admin_client,
            entity_type="USER",
            entity_id=str(user_id),
            action="ADMIN_UPDATE",
            old_value=old_data,
            new_value=resp.data[0],
            actor_id=str(admin_id),
            actor_type="ADMIN",
            notes=f"Admin updated user profile",
            request=request,
        )

        logger.info(
            "admin_user_updated",
            admin_id=str(admin_id),
            user_id=str(user_id),
            updates=list(update_dict.keys()),
        )

        # Return enhanced response
        user_data = resp.data[0]
        order_stats = await get_user_order_stats(user_id, admin_client)
        wallet_stats = await get_user_wallet_stats(user_id, admin_client)

        user_data["total_orders"] = order_stats.get("total_orders", 0)
        user_data["total_spent"] = wallet_stats.get("total_spent", 0)
        user_data["total_earned"] = wallet_stats.get("total_earned", 0)

        return AdminUserResponse(**user_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "admin_update_user_error", user_id=str(user_id), error=str(e), exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to update user: {str(e)}")


async def block_unblock_user(
    user_id: UUID,
    block: bool,
    admin_id: UUID,
    admin_client: AsyncClient,
    request: Optional[Request] = None,
) -> AdminUserResponse:
    """Block or unblock a user"""
    try:
        # Get current data
        current_resp = (
            await admin_client.table("profiles")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not current_resp.data:
            raise HTTPException(status_code=404, detail="User not found")

        old_data = current_resp.data.copy()

        # Update
        update_dict = {
            "is_blocked": block,
            "account_status": "BLOCKED" if block else "ACTIVE",
        }

        resp = (
            await admin_client.table("profiles")
            .update(update_dict)
            .eq("id", user_id)
            .execute()
        )

        # Audit log
        await log_audit_event(
            admin_client,
            entity_type="USER",
            entity_id=str(user_id),
            action="BLOCK" if block else "UNBLOCK",
            old_value=old_data,
            new_value=resp.data[0],
            actor_id=str(admin_id),
            actor_type="ADMIN",
            notes=f"User {'blocked' if block else 'unblocked'} by admin",
            request=request,
        )

        logger.info(
            "admin_user_blocked",
            admin_id=str(admin_id),
            user_id=str(user_id),
            blocked=block,
        )

        user_data = resp.data[0]
        order_stats = await get_user_order_stats(user_id, admin_client)
        wallet_stats = await get_user_wallet_stats(user_id, admin_client)

        user_data["total_orders"] = order_stats.get("total_orders", 0)
        user_data["total_spent"] = wallet_stats.get("total_spent", 0)
        user_data["total_earned"] = wallet_stats.get("total_earned", 0)

        return AdminUserResponse(**user_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "admin_block_user_error", user_id=str(user_id), error=str(e), exc_info=True
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to block/unblock user: {str(e)}"
        )


async def verify_user(
    user_id: UUID,
    verified: bool,
    admin_id: UUID,
    admin_client: AsyncClient,
    request: Optional[Request] = None,
) -> AdminUserResponse:
    """Verify or unverify a user (vendor/rider)"""
    try:
        current_resp = (
            await admin_client.table("profiles")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not current_resp.data:
            raise HTTPException(status_code=404, detail="User not found")

        old_data = current_resp.data.copy()

        resp = (
            await admin_client.table("profiles")
            .update({"is_verified": verified})
            .eq("id", user_id)
            .execute()
        )

        # Audit log
        await log_audit_event(
            admin_client,
            entity_type="USER",
            entity_id=str(user_id),
            action="VERIFY" if verified else "UNVERIFY",
            old_value=old_data,
            new_value=resp.data[0],
            actor_id=str(admin_id),
            actor_type="ADMIN",
            notes=f"User verification {'approved' if verified else 'revoked'} by admin",
            request=request,
        )

        logger.info(
            "admin_user_verified",
            admin_id=str(admin_id),
            user_id=str(user_id),
            verified=verified,
        )

        user_data = resp.data[0]
        order_stats = await get_user_order_stats(user_id, admin_client)
        wallet_stats = await get_user_wallet_stats(user_id, admin_client)

        user_data["total_orders"] = order_stats.get("total_orders", 0)
        user_data["total_spent"] = wallet_stats.get("total_spent", 0)
        user_data["total_earned"] = wallet_stats.get("total_earned", 0)

        return AdminUserResponse(**user_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "admin_verify_user_error", user_id=str(user_id), error=str(e), exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to verify user: {str(e)}")


# ───────────────────────────────────────────────
# HELPER FUNCTIONS
# ───────────────────────────────────────────────


async def get_user_order_stats(
    user_id: UUID, admin_client: AsyncClient
) -> Dict[str, Any]:
    """Get order statistics for a user"""
    try:
        # Food orders as customer
        food_orders = (
            await admin_client.table("food_orders")
            .select("grand_total", count="exact")
            .eq("customer_id", user_id)
            .execute()
        )

        # Food orders as vendor
        vendor_orders = (
            await admin_client.table("food_orders")
            .select("grand_total", count="exact")
            .eq("vendor_id", user_id)
            .execute()
        )

        # Delivery orders
        delivery_orders = (
            await admin_client.table("delivery_orders")
            .select("grand_total", count="exact")
            .eq("sender_id", user_id)
            .execute()
        )

        total_orders = (food_orders.count or 0) + (delivery_orders.count or 0)

        return {
            "total_orders": total_orders,
            "food_orders_as_customer": food_orders.count or 0,
            "food_orders_as_vendor": vendor_orders.count or 0,
            "delivery_orders": delivery_orders.count or 0,
        }
    except Exception as e:
        return {"total_orders": 0, "error": f"Failed to get order stats: {str(e)}"}


async def get_user_wallet_stats(
    user_id: UUID, admin_client: AsyncClient
) -> Dict[str, Any]:
    """Get wallet statistics for a user"""
    try:
        wallet_resp = (
            await admin_client.table("wallets")
            .select("balance, total_deposited, total_withdrawn")
            .eq("user_id", user_id)
            .single()
            .execute()
        )

        if wallet_resp.data:
            return {
                "balance": wallet_resp.data.get("balance", 0),
                "total_spent": wallet_resp.data.get("total_withdrawn", 0),
                "total_earned": wallet_resp.data.get("total_deposited", 0),
            }

        # Get from transactions if wallet doesn't exist
        transactions = (
            await admin_client.table("transactions")
            .select("amount, transaction_type")
            .or_(f"from_user_id.eq.{user_id},to_user_id.eq.{user_id}")
            .execute()
        )

        total_earned = Decimal("0")
        total_spent = Decimal("0")

        for tx in transactions.data:
            amount = Decimal(str(tx["amount"]))
            tx_type = tx["transaction_type"]

            if tx.get("to_user_id") == str(user_id):
                if tx_type in ["PAYMENT", "REFUND", "TOP_UP"]:
                    total_earned += amount
            elif tx.get("from_user_id") == str(user_id):
                if tx_type in ["PAYMENT", "WITHDRAWAL"]:
                    total_spent += amount

        return {
            "balance": 0,
            "total_spent": float(total_spent),
            "total_earned": float(total_earned),
        }
    except Exception as e:
        return {"balance": 0, "total_spent": 0, "total_earned": 0, "error": str(e)}


# ───────────────────────────────────────────────
# ORDER MANAGEMENT
# ───────────────────────────────────────────────


async def list_orders(
    filters: OrderFilterParams, pagination: PaginationParams, admin_client: AsyncClient
) -> OrdersListResponse:
    """List all orders with filters"""
    try:
        orders = []

        # Food orders
        if not filters.order_type or filters.order_type == "food":
            food_query = admin_client.table("food_orders").select("*")

            if filters.status:
                food_query = food_query.eq("order_status", filters.status)
            if filters.payment_status:
                food_query = food_query.eq("payment_status", filters.payment_status)
            if filters.customer_id:
                food_query = food_query.eq("customer_id", filters.customer_id)
            if filters.vendor_id:
                food_query = food_query.eq("vendor_id", filters.vendor_id)
            if filters.created_from:
                food_query = food_query.gte(
                    "created_at", filters.created_from.isoformat()
                )
            if filters.created_to:
                food_query = food_query.lte(
                    "created_at", filters.created_to.isoformat()
                )

            food_resp = await food_query.order("created_at", desc=True).execute()

            # Get user names separately if needed
            customer_ids = set()
            vendor_ids = set()
            for order in food_resp.data:
                if order.get("customer_id"):
                    customer_ids.add(order["customer_id"])
                if order.get("vendor_id"):
                    vendor_ids.add(order["vendor_id"])

            # Fetch profiles in batch
            customer_profiles = {}
            vendor_profiles = {}
            if customer_ids:
                customer_resp = (
                    await admin_client.table("profiles")
                    .select("id, full_name")
                    .in_("id", list(customer_ids))
                    .execute()
                )
                customer_profiles = {p["id"]: p for p in customer_resp.data}
            if vendor_ids:
                vendor_resp = (
                    await admin_client.table("profiles")
                    .select("id, full_name, store_name")
                    .in_("id", list(vendor_ids))
                    .execute()
                )
                vendor_profiles = {p["id"]: p for p in vendor_resp.data}

            for order in food_resp.data:
                customer = customer_profiles.get(order.get("customer_id"), {})
                vendor = vendor_profiles.get(order.get("vendor_id"), {})

                orders.append(
                    {
                        "id": order["id"],
                        "order_type": "food",
                        "customer_id": order["customer_id"],
                        "customer_name": customer.get("full_name"),
                        "vendor_id": order.get("vendor_id"),
                        "vendor_name": vendor.get("store_name")
                        or vendor.get("full_name"),
                        "rider_id": order.get("rider_id"),
                        "rider_name": None,
                        "status": order["order_status"],
                        "payment_status": order["payment_status"],
                        "total_amount": order["grand_total"],
                        "created_at": order["created_at"],
                        "updated_at": order.get("updated_at"),
                    }
                )

        # Delivery orders
        if not filters.order_type or filters.order_type == "delivery":
            delivery_query = admin_client.table("delivery_orders").select("*")

            if filters.status:
                delivery_query = delivery_query.eq("order_status", filters.status)
            if filters.customer_id:
                delivery_query = delivery_query.eq("sender_id", filters.customer_id)
            if filters.rider_id:
                delivery_query = delivery_query.eq("rider_id", filters.rider_id)
            if filters.created_from:
                delivery_query = delivery_query.gte(
                    "created_at", filters.created_from.isoformat()
                )
            if filters.created_to:
                delivery_query = delivery_query.lte(
                    "created_at", filters.created_to.isoformat()
                )

            delivery_resp = await delivery_query.order(
                "created_at", desc=True
            ).execute()

            # Get sender names
            sender_ids = {
                order["sender_id"]
                for order in delivery_resp.data
                if order.get("sender_id")
            }
            sender_profiles = {}
            if sender_ids:
                sender_profile_resp = (
                    await admin_client.table("profiles")
                    .select("id, full_name")
                    .in_("id", list(sender_ids))
                    .execute()
                )
                sender_profiles = {p["id"]: p for p in sender_profile_resp.data}

            for order in delivery_resp.data:
                sender = sender_profiles.get(order.get("sender_id"), {})

                orders.append(
                    {
                        "id": order["id"],
                        "order_type": "delivery",
                        "customer_id": order["sender_id"],
                        "customer_name": sender.get("full_name"),
                        "vendor_id": None,
                        "vendor_name": None,
                        "rider_id": order.get("rider_id"),
                        "rider_name": None,
                        "status": order.get("order_status", "PENDING"),
                        "payment_status": order.get("payment_status", "PAID"),
                        "total_amount": order["grand_total"],
                        "created_at": order["created_at"],
                        "updated_at": order.get("updated_at"),
                    }
                )

        # Sort all orders by created_at desc
        orders.sort(key=lambda x: x["created_at"], reverse=True)

        # Apply pagination
        total = len(orders)
        offset = (pagination.page - 1) * pagination.page_size
        paginated_orders = orders[offset : offset + pagination.page_size]

        return OrdersListResponse(
            orders=[AdminOrderResponse(**order) for order in paginated_orders],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )
    except Exception as e:
        logger.error("admin_list_orders_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list orders: {str(e)}")


# ───────────────────────────────────────────────
# TRANSACTION MANAGEMENT
# ───────────────────────────────────────────────


async def list_transactions(
    filters: TransactionFilterParams,
    pagination: PaginationParams,
    admin_client: AsyncClient,
) -> TransactionsListResponse:
    """List all transactions with filters"""
    try:
        query = admin_client.table("transactions").select(
            "*, profiles!transactions_from_user_id_fkey(full_name), profiles!transactions_to_user_id_fkey(full_name)",
            count="exact",
        )

        if filters.transaction_type:
            query = query.eq("transaction_type", filters.transaction_type)
        if filters.status:
            query = query.eq("status", filters.status)
        if filters.from_user_id:
            query = query.eq("from_user_id", filters.from_user_id)
        if filters.to_user_id:
            query = query.eq("to_user_id", filters.to_user_id)
        if filters.created_from:
            query = query.gte("created_at", filters.created_from.isoformat())
        if filters.created_to:
            query = query.lte("created_at", filters.created_to.isoformat())
        if filters.min_amount:
            query = query.gte("amount", float(filters.min_amount))
        if filters.max_amount:
            query = query.lte("amount", float(filters.max_amount))

        count_resp = await query.execute()
        total = count_resp.count if count_resp.count else 0

        offset = (pagination.page - 1) * pagination.page_size
        query = query.order("created_at", desc=True).range(
            offset, offset + pagination.page_size - 1
        )

        resp = await query.execute()

        transactions = []
        for tx in resp.data:
            transactions.append(
                AdminTransactionResponse(
                    id=tx["id"],
                    tx_ref=tx["tx_ref"],
                    amount=tx["amount"],
                    transaction_type=tx["transaction_type"],
                    status=tx["status"],
                    payment_method=tx.get("payment_method"),
                    from_user_id=tx.get("from_user_id"),
                    from_user_name=None,  # Would need proper join
                    to_user_id=tx.get("to_user_id"),
                    to_user_name=None,  # Would need proper join
                    created_at=tx["created_at"],
                    details=tx.get("details"),
                )
            )

        return TransactionsListResponse(
            transactions=transactions,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )
    except Exception as e:
        logger.error("admin_list_transactions_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to list transactions: {str(e)}"
        )


# ───────────────────────────────────────────────
# WALLET MANAGEMENT
# ───────────────────────────────────────────────


async def list_wallets(
    pagination: PaginationParams, admin_client: AsyncClient
) -> WalletsListResponse:
    """List all wallets"""
    try:
        query = admin_client.table("wallets").select("*, profiles(*)", count="exact")

        count_resp = await query.execute()
        total = count_resp.count if count_resp.count else 0

        offset = (pagination.page - 1) * pagination.page_size
        query = query.order("created_at", desc=True).range(
            offset, offset + pagination.page_size - 1
        )

        resp = await query.execute()

        wallets = []
        for wallet in resp.data:
            profile = (
                wallet.get("profiles", {})
                if isinstance(wallet.get("profiles"), dict)
                else {}
            )

            wallets.append(
                AdminWalletResponse(
                    id=wallet["id"],
                    user_id=wallet["user_id"],
                    user_name=profile.get("full_name") or profile.get("store_name"),
                    user_type=UserType(profile.get("user_type", "CUSTOMER")),
                    balance=wallet.get("balance", 0),
                    escrow_balance=wallet.get("escrow_balance", 0),
                    total_deposited=wallet.get("total_deposited", 0),
                    total_withdrawn=wallet.get("total_withdrawn", 0),
                    created_at=wallet["created_at"],
                    updated_at=wallet.get("updated_at"),
                )
            )

        return WalletsListResponse(
            wallets=wallets,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )
    except Exception as e:
        logger.error("admin_list_wallets_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list wallets: {str(e)}")


async def adjust_wallet(
    adjustment: WalletAdjustmentRequest,
    admin_id: UUID,
    admin_client: AsyncClient,
    request: Optional[Request] = None,
) -> AdminWalletResponse:
    """Adjust wallet balance (admin only)"""
    try:
        # Get current wallet
        wallet_resp = (
            await admin_client.table("wallets")
            .select("*")
            .eq("user_id", adjustment.user_id)
            .single()
            .execute()
        )

        if not wallet_resp.data:
            raise HTTPException(status_code=404, detail="Wallet not found")

        old_balance = wallet_resp.data["balance"]

        # Calculate new balance
        delta = (
            float(adjustment.amount)
            if adjustment.adjustment_type == "credit"
            else -float(adjustment.amount)
        )
        new_balance = Decimal(str(old_balance)) + Decimal(str(delta))

        if new_balance < 0:
            raise HTTPException(
                status_code=400, detail="Insufficient balance for debit"
            )

        # Update wallet using RPC if available, otherwise direct update
        try:
            await admin_client.rpc(
                "update_wallet_balance",
                {
                    "p_user_id": str(adjustment.user_id),
                    "p_delta": delta,
                    "p_field": "balance",
                },
            ).execute()
        except Exception:
            # Fallback to direct update
            await (
                admin_client.table("wallets")
                .update({"balance": float(new_balance)})
                .eq("user_id", adjustment.user_id)
                .execute()
            )

        # Create transaction record
        await (
            admin_client.table("transactions")
            .insert(
                {
                    "tx_ref": f"ADMIN-ADJ-{datetime.utcnow().timestamp()}",
                    "amount": abs(delta),
                    "from_user_id": str(adjustment.user_id)
                    if adjustment.adjustment_type == "debit"
                    else None,
                    "to_user_id": str(adjustment.user_id)
                    if adjustment.adjustment_type == "credit"
                    else None,
                    "transaction_type": "ADMIN_ADJUSTMENT",
                    "status": "COMPLETED",
                    "details": {
                        "reason": adjustment.reason,
                        "notes": adjustment.notes,
                        "admin_id": str(admin_id),
                        "adjustment_type": adjustment.adjustment_type,
                    },
                }
            )
            .execute()
        )

        # Audit log
        await log_audit_event(
            admin_client,
            entity_type="WALLET",
            entity_id=str(adjustment.user_id),
            action="ADMIN_ADJUSTMENT",
            old_value={"balance": float(old_balance)},
            new_value={"balance": float(new_balance)},
            change_amount=Decimal(str(abs(delta))),
            actor_id=str(admin_id),
            actor_type="ADMIN",
            notes=f"Wallet adjusted: {adjustment.adjustment_type} {adjustment.amount} - {adjustment.reason}",
            request=request,
        )

        # Get updated wallet
        updated_wallet = (
            await admin_client.table("wallets")
            .select("*")
            .eq("user_id", adjustment.user_id)
            .single()
            .execute()
        )

        wallet_data = updated_wallet.data

        # Get profile separately
        profile_resp = (
            await admin_client.table("profiles")
            .select("id, full_name, store_name, user_type")
            .eq("id", adjustment.user_id)
            .single()
            .execute()
        )

        profile = profile_resp.data if profile_resp.data else {}

        logger.info(
            "admin_wallet_adjusted",
            admin_id=str(admin_id),
            user_id=str(adjustment.user_id),
            delta=delta,
        )

        return AdminWalletResponse(
            id=wallet_data["id"],
            user_id=wallet_data["user_id"],
            user_name=profile.get("full_name") or profile.get("store_name"),
            user_type=UserType(profile.get("user_type", "CUSTOMER")),
            balance=wallet_data.get("balance", 0),
            escrow_balance=wallet_data.get("escrow_balance", 0),
            total_deposited=wallet_data.get("total_deposited", 0),
            total_withdrawn=wallet_data.get("total_withdrawn", 0),
            created_at=wallet_data["created_at"],
            updated_at=wallet_data.get("updated_at"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("admin_adjust_wallet_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to adjust wallet: {str(e)}"
        )


# ========================
# DASHBOARD & ANALYTICS
# ========================


async def get_dashboard_stats(admin_client: AsyncClient) -> DashboardStatsResponse:
    """Get overall dashboard statistics"""
    try:
        # User stats
        all_users = (
            await admin_client.table("profiles")
            .select("id, user_type, is_blocked, is_online", count="exact")
            .execute()
        )
        active_users = (
            await admin_client.table("profiles")
            .select("id", count="exact")
            .eq("is_online", True)
            .execute()
        )
        blocked_users = (
            await admin_client.table("profiles")
            .select("id", count="exact")
            .eq("is_blocked", True)
            .execute()
        )

        # Order stats
        food_orders = (
            await admin_client.table("food_orders")
            .select("order_status, grand_total, created_at", count="exact")
            .execute()
        )
        delivery_orders = (
            await admin_client.table("delivery_orders")
            .select("order_status, grand_total, created_at", count="exact")
            .execute()
        )

        # Transaction stats
        all_transactions = (
            await admin_client.table("transactions")
            .select("amount, created_at, status", count="exact")
            .execute()
        )

        # Calculate revenue
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        revenue_today = Decimal("0")
        revenue_week = Decimal("0")
        revenue_month = Decimal("0")
        total_revenue = Decimal("0")

        for tx in all_transactions.data or []:
            if tx.get("status") == "COMPLETED" and tx.get("transaction_type") in [
                "PAYMENT",
                "TOP_UP",
            ]:
                amount = Decimal(str(tx["amount"]))
                total_revenue += amount

                tx_date = datetime.fromisoformat(
                    tx["created_at"].replace("Z", "+00:00")
                )
                if tx_date >= today:
                    revenue_today += amount
                if tx_date >= week_ago:
                    revenue_week += amount
                if tx_date >= month_ago:
                    revenue_month += amount

        # Users by type
        users_by_type = {}
        for user in all_users.data or []:
            user_type = user.get("user_type", "UNKNOWN")
            users_by_type[user_type] = users_by_type.get(user_type, 0) + 1

        # Orders by type
        orders_by_type = {
            "food": len([o for o in food_orders.data or []]) if food_orders.data else 0,
            "delivery": len([o for o in delivery_orders.data or []])
            if delivery_orders.data
            else 0,
        }

        # Pending orders
        pending_orders = len(
            [
                o
                for o in (food_orders.data or []) + (delivery_orders.data or [])
                if o.get("order_status") == "PENDING"
            ]
        )
        completed_orders = len(
            [
                o
                for o in (food_orders.data or []) + (delivery_orders.data or [])
                if o.get("order_status") == "COMPLETED"
            ]
        )

        # Pending withdrawals (approximate)
        pending_withdrawals = Decimal("0")  # Would need withdrawal table

        return DashboardStatsResponse(
            total_users=all_users.count or 0,
            active_users=active_users.count or 0,
            blocked_users=blocked_users.count or 0,
            total_orders=(food_orders.count or 0) + (delivery_orders.count or 0),
            pending_orders=pending_orders,
            completed_orders=completed_orders,
            total_revenue=total_revenue,
            total_transactions=all_transactions.count or 0,
            pending_withdrawals=pending_withdrawals,
            users_by_type=users_by_type,
            orders_by_type=orders_by_type,
            revenue_today=revenue_today,
            revenue_this_week=revenue_week,
            revenue_this_month=revenue_month,
        )
    except Exception as e:
        logger.error("admin_dashboard_stats_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get dashboard stats: {str(e)}"
        )


# ========================
# AUDIT LOGS
# ========================


async def list_audit_logs(
    filters: AuditLogFilterParams,
    pagination: PaginationParams,
    admin_client: AsyncClient,
) -> AuditLogsListResponse:
    """List audit logs"""
    try:
        query = admin_client.table("audit_logs").select("*", count="exact")

        if filters.entity_type:
            query = query.eq("entity_type", filters.entity_type)
        if filters.action:
            query = query.eq("action", filters.action)
        if filters.actor_id:
            query = query.eq("actor_id", filters.actor_id)
        if filters.actor_type:
            query = query.eq("actor_type", filters.actor_type)
        if filters.created_from:
            query = query.gte("created_at", filters.created_from.isoformat())
        if filters.created_to:
            query = query.lte("created_at", filters.created_to.isoformat())

        count_resp = await query.execute()
        total = count_resp.count if count_resp.count else 0

        offset = (pagination.page - 1) * pagination.page_size
        query = query.order("created_at", desc=True).range(
            offset, offset + pagination.page_size - 1
        )

        resp = await query.execute()

        logs = [AuditLogResponse(**log) for log in resp.data]

        return AuditLogsListResponse(
            logs=logs, total=total, page=pagination.page, page_size=pagination.page_size
        )
    except Exception as e:
        logger.error("admin_list_audit_logs_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to list audit logs: {str(e)}"
        )
