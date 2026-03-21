"""
OTP (activation code) API for robot verification.
"""
from datetime import datetime, timedelta, timezone
import random
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

# In-memory cache for demo (replace with Redis/db in production)
otp_cache = {}
OTP_EXPIRE_SECONDS = 300  # 5 minutes

router = APIRouter(tags=["otp"])

class OTPRequest(BaseModel):
    mac_address: str

class OTPVerifyRequest(BaseModel):
    mac_address: str
    otp: str

@router.post("/api/robot/request_otp")
async def request_otp(data: OTPRequest):
    """Robot requests a new OTP (activation code)."""
    otp = f"{random.randint(0, 999999):06d}"
    otp_cache[data.mac_address] = {
        "otp": otp,
        "expires": datetime.now(timezone.utc) + timedelta(seconds=OTP_EXPIRE_SECONDS),
        "verified": False,
    }
    # In real use, send OTP to user/app for input
    return {"mac_address": data.mac_address, "otp": otp, "expires_in": OTP_EXPIRE_SECONDS}

@router.post("/api/robot/verify_otp")
async def verify_otp(data: OTPVerifyRequest):
    """Robot submits OTP for verification."""
    entry = otp_cache.get(data.mac_address)
    if not entry:
        raise HTTPException(status_code=404, detail="No OTP requested for this device")
    if entry["verified"]:
        return {"result": "already_verified"}
    if entry["expires"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OTP expired")
    if entry["otp"] != data.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    entry["verified"] = True
    return {"result": "success"}
