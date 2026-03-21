"""
Robot and RobotConfig models for the robot management system.
"""
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class RobotBase(BaseModel):
    mac_address: str
    robot_id: str
    name: Optional[str] = None


class RobotCreate(RobotBase):
    pass


class RobotUpdate(BaseModel):
    name: Optional[str] = None


class RobotInDB(RobotBase):
    owner_username: Optional[str] = None
    is_online: bool = False
    last_seen: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class RobotConfigBase(BaseModel):
    mac_address: str
    system_prompt: Optional[str] = None
    voice_config: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None
    tts_config: Optional[Dict[str, Any]] = None
    stt_config: Optional[Dict[str, Any]] = None
    version: int = 1


class RobotConfigCreate(RobotConfigBase):
    pass


class RobotConfigUpdate(BaseModel):
    system_prompt: Optional[str] = None
    voice_config: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None
    tts_config: Optional[Dict[str, Any]] = None
    stt_config: Optional[Dict[str, Any]] = None


class RobotConfigInDB(RobotConfigBase):
    created_at: datetime
    updated_at: datetime


class RobotStatus(BaseModel):
    mac_address: str
    is_online: bool
    last_seen: Optional[datetime] = None
    robot_id: str
    name: Optional[str] = None