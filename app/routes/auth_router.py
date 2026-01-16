from fastapi import APIRouter, Depends, Query
from fastapi.security import OAuth2PasswordRequestForm
from app.services import user_service 
from app.schemas.user_schemas import UserCreate, LoginRequest, TokenResponse
from app.dependencies.auth import get_current_profile, require_user_type
from app.database.supabase import get_supabase_client, get_supabase_admin_client

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

@router.post("/signup", response_model=TokenResponse)
async def signup(user_data: UserCreate, supabase=Depends(get_supabase_admin_client)):
    return await user_service.create_user_account(user_data, supabase)

@router.post("/login", response_model=TokenResponse)
async def login(login_data: LoginRequest, supabase=Depends(get_supabase_client)):
    return await user_service.login_user(login_data, supabase)

@router.post("/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    supabase=Depends(get_supabase_client)
):
    """OAuth2 compatible token endpoint for Swagger UI authentication."""
    login_data = LoginRequest(email=form_data.username, password=form_data.password)
    return await user_service.login_user(login_data, supabase)