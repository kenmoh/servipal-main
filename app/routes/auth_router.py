from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from app.services import user_service
from app.schemas.user_schemas import UserCreate, LoginRequest, TokenResponse
from app.database.supabase import get_supabase_client, get_supabase_admin_client
from app.config.logging import logger

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@router.post("/signup", response_model=TokenResponse)
async def signup(
    user_data: UserCreate, request: Request, supabase=Depends(get_supabase_admin_client)
):
    """
    Register a new user account.

    Args:
        user_data (UserCreate): The user registration details.

    Returns:
        TokenResponse: Access token and user profile information.
    """
    logger.info(
        "signup_endpoint_called",
        email=user_data.email,
        user_type=user_data.user_type.value,
    )
    return await user_service.create_user_account(user_data, supabase, request)


@router.post("/login", response_model=TokenResponse)
async def login(
    login_data: LoginRequest, request: Request, supabase=Depends(get_supabase_client)
):
    """
    Authenticate a user and return access token.

    Args:
        login_data (LoginRequest): Email and password.

    Returns:
        TokenResponse: Access token and user profile information.
    """
    logger.info("login_endpoint_called", email=login_data.email)
    return await user_service.login_user(login_data, supabase, request)


@router.post("/token", response_model=TokenResponse, include_in_schema=False)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
    supabase=Depends(get_supabase_client),
):
    """OAuth2 compatible token endpoint for Swagger UI authentication."""
    logger.info("token_endpoint_called", username=form_data.username)
    login_data = LoginRequest(email=form_data.username, password=form_data.password)
    return await user_service.login_user(login_data, supabase, request)
