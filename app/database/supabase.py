from typing import AsyncGenerator
from supabase import AsyncClient, acreate_client, AsyncClientOptions


from app.config.config import settings


async def create_supabase_client() -> AsyncClient:
    """Create a standard Supabase client (anon key).

    This is suitable for most user-facing operations and respects RLS.
    """
    supabase: AsyncClient = await acreate_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_PUBLISHABLE_KEY,
        # options=AsyncClientOptions(
        #     storage=settings.SUPABASE_STORAGE_BUCKET_URL,
        # ),
    )
    return supabase


async def create_supabase_admin_client() -> AsyncClient:
    """Create an admin Supabase client (service role key).

    Use this only where necessary (e.g., privileged admin ops).
    """
    supabase: AsyncClient = await acreate_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SECRET_KEY,
    )
    return supabase


async def get_supabase_client() -> AsyncGenerator[AsyncClient, None]:
    """FastAPI dependency that yields a Supabase client.

    NOTE: For now we do not explicitly close the client. If connection
    management becomes an issue, we can add explicit cleanup here.
    """
    supabase = await create_supabase_client()
    yield supabase


async def get_supabase_admin_client() -> AsyncGenerator[AsyncClient, None]:
    """FastAPI dependency that yields an admin Supabase client."""
    supabase = await create_supabase_admin_client()
    yield supabase
