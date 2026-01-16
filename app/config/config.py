import os
from typing import Optional
from pydantic_settings import BaseSettings
from redis import asyncio as aioredis

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    Pydantic automatically loads these from env vars - no need for os.getenv()!
    """

    # Application settings
    APP_NAME: str = "ServiPal"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # LOGFIRE
    LOGFIRE_TOKEN: Optional[str] = None

    # FLUTTERWAVE
    FLW_PUBLIC_KEY: Optional[str] = None
    FLW_SECRET_KEY: Optional[str] = None
    FLW_SECRET_HASH: Optional[str] = None
    FLUTTERWAVE_PUBLIC_KEY: Optional[str] = None

    # SUPABASE
    SUPABASE_URL: str = os.getenv("SUPABASE_URL")
    SUPABASE_PUBLISHABLE_KEY: str = os.getenv("SUPABASE_PUBLISHABLE_KEY")
    SUPABASE_SECRET_KEY: str= os.getenv("SUPABASE_SECRET_KEY")

    # REDIS
    REDIS_URL: str = "redis://localhost:6379/0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"

settings = Settings()

# Redis initialization
redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)