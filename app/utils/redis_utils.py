import json
from app.config.config import redis
from fastapi import HTTPException


async def save_pending(key: str, data: dict, expire: int = 1800):
    """Save pending payment data to Redis with expiration"""
    try:
        await redis.set(key, json.dumps(data), ex=expire)
    except Exception as e:
        raise HTTPException(500, f"Redis save failed: {str(e)}")


async def get_pending(key: str) -> dict | None:
    """Get pending payment data from Redis"""
    try:
        json_data = await redis.get(key)
        if json_data:
            return json.loads(json_data)
        return None
    except Exception as e:
        raise HTTPException(500, f"Redis get failed: {str(e)}")


async def delete_pending(key: str):
    """Delete pending payment data from Redis"""
    try:
        await redis.delete(key)
    except Exception as e:
        raise HTTPException(500, f"Redis delete failed: {str(e)}")


async def cache_data(key: str, data: str, expire: int = 86400):
    """Cache data in Redis with expiration"""
    try:
        await redis.set(key, data, ex=expire)
    except Exception as e:
        raise HTTPException(500, f"Redis cache failed: {str(e)}")


async def get_cached_data(key: str) -> str | None:
    """Get cached data from Redis"""
    try:
        return await redis.get(key)
    except Exception as e:
        raise HTTPException(500, f"Redis get failed: {str(e)}")
