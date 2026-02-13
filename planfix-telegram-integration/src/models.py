from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase


# --- SQLAlchemy ORM models ---

class Base(DeclarativeBase):
    pass


class ContactMapping(Base):
    """Maps Planfix contact IDs to Telegram user IDs."""
    __tablename__ = "contact_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    planfix_contact_id = Column(String(64), unique=True, nullable=False, index=True)
    telegram_user_id = Column(String(64), nullable=False, index=True)
    telegram_chat_id = Column(String(64), nullable=True)
    phone = Column(String(32), nullable=True, index=True)
    name = Column(String(256), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MessageLog(Base):
    """Logs all messages passing through the integration."""
    __tablename__ = "message_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    direction = Column(String(20), nullable=False)  # 'planfix_to_telegram' or 'telegram_to_planfix'
    planfix_contact_id = Column(String(64), nullable=True)
    telegram_chat_id = Column(String(64), nullable=True)
    message_text = Column(Text, nullable=True)
    external_id = Column(String(128), nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending, sent, failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


# --- Pydantic request/response models ---

class MessageDirection(str, Enum):
    PLANFIX_TO_TELEGRAM = "planfix_to_telegram"
    TELEGRAM_TO_PLANFIX = "telegram_to_planfix"


class MessageStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class PlanfixWebhookRequest(BaseModel):
    """Incoming webhook from Planfix Chat API."""
    token: str
    messageText: str = Field(..., alias="messageText")
    contactPhone: Optional[str] = Field(None, alias="contactPhone")
    contactId: Optional[int] = Field(None, alias="contactId")
    providerId: str = Field("telegram", alias="providerId")

    model_config = {"populate_by_name": True}


class PlanfixWebhookResponse(BaseModel):
    """Response to Planfix webhook."""
    success: bool
    messageId: Optional[str] = None
    error: Optional[str] = None


class PlanfixNotification(BaseModel):
    """Notification sent to Planfix when a Telegram message arrives."""
    token: str
    messageText: str
    contact: dict  # {"phone": "...", "name": "..."}
    providerId: str = "telegram"
    externalId: str


class TelegramMessageUpdate(BaseModel):
    """Parsed Telegram message from TDLight update."""
    chat_id: int
    sender_user_id: int
    message_id: int
    text: str
    sender_phone: Optional[str] = None
    sender_name: Optional[str] = None
    date: int = 0


class HealthResponse(BaseModel):
    status: str
    telegram_authorized: bool
    uptime_seconds: float


class AuthStatusResponse(BaseModel):
    authorized: bool
    phone: Optional[str] = None
    state: str = "unknown"
