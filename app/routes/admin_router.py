from fastapi import APIRouter, Depends, Query, Request, HTTPException, status
from app.services import admin_service
from app.schemas.admin_schemas import *
from app.dependencies.auth import get_current_profile
from app.database.supabase import get_supabase_admin_client
from app.schemas.user_schemas import UserType
from app.config.logging import logger
from supabase import AsyncClient
from uuid import UUID
from datetime import datetime

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


# Helper dependency for admin role check - requires ADMIN, MODERATOR, or SUPER_ADMIN
async def require_admin_role(profile: dict = Depends(get_current_profile)):
    """Require admin, moderator, or superadmin role"""
    allowed_roles = [
        UserType.ADMIN.value,
        UserType.MODERATOR.value,
        UserType.SUPER_ADMIN.value,
    ]
    user_type = profile.get("user_type")
    if user_type not in allowed_roles:
        logger.warning(
            "admin_access_denied",
            user_id=profile.get("id"),
            user_type=profile.get("user_type"),
            required_roles=allowed_roles,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return profile


require_admin = require_admin_role

# ========================
# USER MANAGEMENT ENDPOINTS
# ========================


@router.get("/users", response_model=UsersListResponse)
async def list_users(
    user_type: Optional[UserType] = Query(None, description="Filter by user type"),
    is_verified: Optional[bool] = Query(
        None, description="Filter by verification status"
    ),
    is_blocked: Optional[bool] = Query(None, description="Filter by blocked status"),
    account_status: Optional[str] = Query(None, description="Filter by account status"),
    search: Optional[str] = Query(None, description="Search in name, email, phone"),
    created_from: Optional[str] = Query(
        None, description="Filter from date (ISO format)"
    ),
    created_to: Optional[str] = Query(None, description="Filter to date (ISO format)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    List all users with filtering and pagination.
    
    Args:
        user_type (UserType, optional): Filter by user type.
        is_verified (bool, optional): Filter by verification status.
        is_blocked (bool, optional): Filter by blocked status.
        account_status (str, optional): Filter by account status.
        search (str, optional): Search term.
        created_from (str, optional): Start date.
        created_to (str, optional): End date.
        
    Returns:
        UsersListResponse: List of users.
    """
    logger.info("admin_list_users", admin_id=current_profile["id"])

    filters = UserFilterParams(
        user_type=user_type,
        is_verified=is_verified,
        is_blocked=is_blocked,
        account_status=account_status,
        search=search,
        created_from=datetime.fromisoformat(created_from) if created_from else None,
        created_to=datetime.fromisoformat(created_to) if created_to else None,
    )

    pagination = PaginationParams(page=page, page_size=page_size)

    return await admin_service.list_users(filters, pagination, admin_client)


@router.get("/users/{user_id}", response_model=AdminUserResponse)
async def get_user(
    user_id: UUID,
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    Get detailed user information.
    
    Args:
        user_id (UUID): The user ID.
        
    Returns:
        AdminUserResponse: User details.
    """
    logger.info("admin_get_user", admin_id=current_profile["id"], user_id=str(user_id))
    return await admin_service.get_user_details(user_id, admin_client)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: UUID,
    updates: AdminUserUpdate,
    request: Request,
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    Update user profile (admin).
    
    Args:
        user_id (UUID): The user ID.
        updates (AdminUserUpdate): Fields to update.
        
    Returns:
        AdminUserResponse: Updated user.
    """
    logger.info(
        "admin_update_user", admin_id=current_profile["id"], user_id=str(user_id)
    )
    return await admin_service.update_user(
        user_id, updates, UUID(current_profile["id"]), admin_client, request
    )


@router.post("/users/{user_id}/block")
async def block_user(
    user_id: UUID,
    request: Request,
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    Block a user.
    
    Args:
        user_id (UUID): The user ID.
        
    Returns:
        dict: Success status.
    """
    logger.info(
        "admin_block_user", admin_id=current_profile["id"], user_id=str(user_id)
    )
    return await admin_service.block_unblock_user(
        user_id, True, UUID(current_profile["id"]), admin_client, request
    )


@router.post("/users/{user_id}/unblock")
async def unblock_user(
    user_id: UUID,
    request: Request,
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    Unblock a user.
    
    Args:
        user_id (UUID): The user ID.
        
    Returns:
        dict: Success status.
    """
    logger.info(
        "admin_unblock_user", admin_id=current_profile["id"], user_id=str(user_id)
    )
    return await admin_service.block_unblock_user(
        user_id, False, UUID(current_profile["id"]), admin_client, request
    )


@router.post("/users/{user_id}/verify")
async def verify_user(
    user_id: UUID,
    verified: bool = Query(True),
    reason: Optional[str] = Query(None),
    request: Request = None,
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    Verify or unverify a user (vendor/rider).
    
    Args:
        user_id (UUID): The user ID.
        verified (bool): Verification status.
        reason (str, optional): Reason for action.
        
    Returns:
        dict: Success status.
    """
    logger.info(
        "admin_verify_user",
        admin_id=current_profile["id"],
        user_id=str(user_id),
        verified=verified,
    )
    return await admin_service.verify_user(
        user_id, verified, UUID(current_profile["id"]), admin_client, request
    )


# ========================
# ORDER MANAGEMENT ENDPOINTS
# ========================


@router.get("/orders", response_model=OrdersListResponse)
async def list_orders(
    order_type: Optional[str] = Query(
        None, description="Filter by order type: food, delivery, laundry"
    ),
    status: Optional[str] = Query(None, description="Filter by order status"),
    payment_status: Optional[str] = Query(None, description="Filter by payment status"),
    customer_id: Optional[UUID] = Query(None, description="Filter by customer ID"),
    vendor_id: Optional[UUID] = Query(None, description="Filter by vendor ID"),
    rider_id: Optional[UUID] = Query(None, description="Filter by rider ID"),
    created_from: Optional[str] = Query(
        None, description="Filter from date (ISO format)"
    ),
    created_to: Optional[str] = Query(None, description="Filter to date (ISO format)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    List all orders with filtering and pagination.
    
    Args:
        order_type (str, optional): Filter by order type.
        status (str, optional): Filter by status.
        payment_status (str, optional): Filter by payment status.
        customer_id (UUID, optional): Filter by customer.
        vendor_id (UUID, optional): Filter by vendor.
        rider_id (UUID, optional): Filter by rider.
        
    Returns:
        OrdersListResponse: List of orders.
    """
    logger.info("admin_list_orders", admin_id=current_profile["id"])

    filters = OrderFilterParams(
        order_type=order_type,
        status=status,
        payment_status=payment_status,
        customer_id=customer_id,
        vendor_id=vendor_id,
        rider_id=rider_id,
        created_from=datetime.fromisoformat(created_from) if created_from else None,
        created_to=datetime.fromisoformat(created_to) if created_to else None,
    )

    pagination = PaginationParams(page=page, page_size=page_size)

    return await admin_service.list_orders(filters, pagination, admin_client)


# ========================
# TRANSACTION MANAGEMENT ENDPOINTS
# ========================


@router.get("/transactions", response_model=TransactionsListResponse)
async def list_transactions(
    transaction_type: Optional[str] = Query(
        None, description="Filter by transaction type"
    ),
    status: Optional[str] = Query(None, description="Filter by status"),
    from_user_id: Optional[UUID] = Query(None, description="Filter by sender user ID"),
    to_user_id: Optional[UUID] = Query(None, description="Filter by recipient user ID"),
    created_from: Optional[str] = Query(
        None, description="Filter from date (ISO format)"
    ),
    created_to: Optional[str] = Query(None, description="Filter to date (ISO format)"),
    min_amount: Optional[float] = Query(None, description="Minimum amount"),
    max_amount: Optional[float] = Query(None, description="Maximum amount"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    List all transactions with filtering and pagination.
    
    Args:
        transaction_type (str, optional): Filter by type.
        status (str, optional): Filter by status.
        from_user_id (UUID, optional): Filter by sender.
        to_user_id (UUID, optional): Filter by recipient.
        min_amount (float, optional): Min amount.
        max_amount (float, optional): Max amount.
        
    Returns:
        TransactionsListResponse: List of transactions.
    """
    logger.info("admin_list_transactions", admin_id=current_profile["id"])

    from decimal import Decimal

    filters = TransactionFilterParams(
        transaction_type=transaction_type,
        status=status,
        from_user_id=from_user_id,
        to_user_id=to_user_id,
        created_from=datetime.fromisoformat(created_from) if created_from else None,
        created_to=datetime.fromisoformat(created_to) if created_to else None,
        min_amount=Decimal(str(min_amount)) if min_amount else None,
        max_amount=Decimal(str(max_amount)) if max_amount else None,
    )

    pagination = PaginationParams(page=page, page_size=page_size)

    return await admin_service.list_transactions(filters, pagination, admin_client)


# ========================
# WALLET MANAGEMENT ENDPOINTS
# ========================


@router.get("/wallets", response_model=WalletsListResponse)
async def list_wallets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    List all wallets with pagination.
    
    Returns:
        WalletsListResponse: List of wallets.
    """
    logger.info("admin_list_wallets", admin_id=current_profile["id"])

    pagination = PaginationParams(page=page, page_size=page_size)

    return await admin_service.list_wallets(pagination, admin_client)


@router.post("/wallets/adjust", response_model=AdminWalletResponse)
async def adjust_wallet(
    adjustment: WalletAdjustmentRequest,
    request: Request,
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    Adjust wallet balance (admin only).
    
    Args:
        adjustment (WalletAdjustmentRequest): Adjustment details.
        
    Returns:
        AdminWalletResponse: Updated wallet.
    """
    logger.info(
        "admin_adjust_wallet",
        admin_id=current_profile["id"],
        user_id=str(adjustment.user_id),
    )
    return await admin_service.adjust_wallet(
        adjustment, UUID(current_profile["id"]), admin_client, request
    )


# ========================
# DASHBOARD & ANALYTICS ENDPOINTS
# ========================


@router.get("/dashboard/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    Get overall dashboard statistics.
    
    Returns:
        DashboardStatsResponse: Stats.
    """
    logger.info("admin_dashboard_stats", admin_id=current_profile["id"])
    return await admin_service.get_dashboard_stats(admin_client)


# ========================
# AUDIT LOG ENDPOINTS
# ========================


@router.get("/audit-logs", response_model=AuditLogsListResponse)
async def list_audit_logs(
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    action: Optional[str] = Query(None, description="Filter by action"),
    actor_id: Optional[UUID] = Query(None, description="Filter by actor ID"),
    actor_type: Optional[str] = Query(None, description="Filter by actor type"),
    created_from: Optional[str] = Query(
        None, description="Filter from date (ISO format)"
    ),
    created_to: Optional[str] = Query(None, description="Filter to date (ISO format)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_profile: dict = Depends(require_admin),
    admin_client: AsyncClient = Depends(get_supabase_admin_client),
):
    """
    List audit logs with filtering and pagination.
    
    Args:
        entity_type (str, optional): Filter by entity.
        action (str, optional): Filter by action.
        actor_id (UUID, optional): Filter by actor.
        
    Returns:
        AuditLogsListResponse: List of logs.
    """
    logger.info("admin_list_audit_logs", admin_id=current_profile["id"])

    filters = AuditLogFilterParams(
        entity_type=entity_type,
        action=action,
        actor_id=actor_id,
        actor_type=actor_type,
        created_from=datetime.fromisoformat(created_from) if created_from else None,
        created_to=datetime.fromisoformat(created_to) if created_to else None,
    )

    pagination = PaginationParams(page=page, page_size=page_size)

    return await admin_service.list_audit_logs(filters, pagination, admin_client)
