from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class FCMTokenRegister(BaseModel):
    token: str
    platform: Optional[str] = None  # "expo", "android", etc.

class FCMTokenResponse(BaseModel):
    token: str
    platform: Optional[str]