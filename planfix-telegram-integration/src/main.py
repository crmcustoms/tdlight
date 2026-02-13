import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .auth import TelegramAuthManager
from .config import AppConfig, load_config
from .models import (
    AuthStatusResponse,
    Base,
    ContactMapping,
    HealthResponse,
    MessageDirection,
    MessageLog,
    MessageStatus,
    PlanfixNotification,
    PlanfixWebhookRequest,
    PlanfixWebhookResponse,
    TelegramMessageUpdate,
)
from .planfix_api import PlanfixClient
from .telegram_handler import TDLightClient

logger = logging.getLogger(__name__)

# Global state
config: AppConfig | None = None
td_client: TDLightClient | None = None
auth_manager: TelegramAuthManager | None = None
planfix_client: PlanfixClient | None = None
db_session_factory: sessionmaker | None = None
start_time: float = 0


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def init_database(database_url: str) -> sessionmaker:
    engine = create_engine(database_url, echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def get_db() -> Session:
    if db_session_factory is None:
        raise RuntimeError("Database not initialized")
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def log_message(
    db: Session,
    direction: str,
    planfix_contact_id: str | None,
    telegram_chat_id: str | None,
    message_text: str | None,
    external_id: str | None,
    status: str,
    error_message: str | None = None,
) -> MessageLog:
    entry = MessageLog(
        direction=direction,
        planfix_contact_id=planfix_contact_id,
        telegram_chat_id=telegram_chat_id,
        message_text=message_text,
        external_id=external_id,
        status=status,
        error_message=error_message,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def get_or_create_mapping(
    db: Session,
    phone: str | None = None,
    planfix_contact_id: str | None = None,
    telegram_user_id: str | None = None,
    telegram_chat_id: str | None = None,
    name: str | None = None,
) -> ContactMapping | None:
    mapping = None

    if planfix_contact_id:
        mapping = db.query(ContactMapping).filter_by(
            planfix_contact_id=planfix_contact_id
        ).first()
    if not mapping and phone:
        mapping = db.query(ContactMapping).filter_by(phone=phone).first()
    if not mapping and telegram_user_id:
        mapping = db.query(ContactMapping).filter_by(
            telegram_user_id=telegram_user_id
        ).first()

    if mapping:
        if telegram_user_id and not mapping.telegram_user_id:
            mapping.telegram_user_id = telegram_user_id
        if telegram_chat_id and not mapping.telegram_chat_id:
            mapping.telegram_chat_id = telegram_chat_id
        if planfix_contact_id and not mapping.planfix_contact_id:
            mapping.planfix_contact_id = planfix_contact_id
        if name and not mapping.name:
            mapping.name = name
        db.commit()
        return mapping

    if phone or (planfix_contact_id and telegram_user_id):
        mapping = ContactMapping(
            planfix_contact_id=planfix_contact_id or "",
            telegram_user_id=telegram_user_id or "",
            telegram_chat_id=telegram_chat_id,
            phone=phone,
            name=name,
        )
        db.add(mapping)
        db.commit()
        db.refresh(mapping)
        return mapping

    return None


async def handle_telegram_update(event: dict) -> None:
    """Process incoming Telegram messages and forward to Planfix."""
    if event.get("@type") != "updateNewMessage":
        return

    message = event.get("message", {})
    content = message.get("content", {})

    # Only handle text messages
    if content.get("@type") != "messageText":
        return

    text = content.get("text", {}).get("text", "")
    if not text:
        return

    chat_id = message.get("chat_id", 0)
    sender = message.get("sender_id", {})
    sender_user_id = sender.get("user_id", 0) if sender.get("@type") == "messageSenderUser" else 0

    # Skip messages sent by ourselves
    if message.get("is_outgoing", False):
        return

    logger.info(
        "New Telegram message: chat_id=%s, sender=%s, text=%s",
        chat_id, sender_user_id, text[:50],
    )

    db = db_session_factory()
    try:
        # Look up sender info
        phone = ""
        name = ""
        if sender_user_id and td_client:
            try:
                user = await td_client.get_user(sender_user_id)
                phone = user.get("phone_number", "")
                first = user.get("first_name", "")
                last = user.get("last_name", "")
                name = f"{first} {last}".strip()
            except Exception:
                logger.warning("Could not get user info for %s", sender_user_id)

        # Find or create mapping
        mapping = get_or_create_mapping(
            db,
            phone=phone or None,
            telegram_user_id=str(sender_user_id),
            telegram_chat_id=str(chat_id),
            name=name or None,
        )

        external_id = f"tg_{message.get('id', 0)}"

        # Build Planfix notification
        notification = PlanfixNotification(
            token=config.planfix.token,
            messageText=text,
            contact={
                "phone": phone if phone else (mapping.phone if mapping else ""),
                "name": name if name else (mapping.name if mapping else "Unknown"),
            },
            providerId="telegram",
            externalId=external_id,
        )

        # Send to Planfix
        try:
            await planfix_client.send_notification(notification)
            log_message(
                db,
                direction=MessageDirection.TELEGRAM_TO_PLANFIX,
                planfix_contact_id=mapping.planfix_contact_id if mapping else None,
                telegram_chat_id=str(chat_id),
                message_text=text,
                external_id=external_id,
                status=MessageStatus.SENT,
            )
        except Exception as e:
            logger.error("Failed to send to Planfix: %s", e)
            log_message(
                db,
                direction=MessageDirection.TELEGRAM_TO_PLANFIX,
                planfix_contact_id=mapping.planfix_contact_id if mapping else None,
                telegram_chat_id=str(chat_id),
                message_text=text,
                external_id=external_id,
                status=MessageStatus.FAILED,
                error_message=str(e),
            )
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global config, td_client, auth_manager, planfix_client, db_session_factory, start_time

    start_time = time.time()
    config = load_config()
    setup_logging(config.server.log_level)

    logger.info("Starting Planfix-Telegram Integration")

    # Database
    db_session_factory = init_database(config.database.url)
    logger.info("Database initialized")

    # Planfix client
    planfix_client = PlanfixClient(config.planfix)
    await planfix_client.start()
    logger.info("Planfix client started")

    # TDLight client
    td_client = TDLightClient(config.telegram)
    td_client.on_update(handle_telegram_update)
    await td_client.start()
    logger.info("TDLight client started")

    # Auth manager
    auth_manager = TelegramAuthManager(td_client, config.telegram)
    asyncio.create_task(auth_manager.wait_for_authorization(timeout=300))

    yield

    # Shutdown
    logger.info("Shutting down...")
    await td_client.stop()
    await planfix_client.stop()


app = FastAPI(
    title="Planfix-Telegram Integration",
    description="Bridge between Planfix CRM Chat API and Telegram via TDLight",
    version="1.0.0",
    lifespan=lifespan,
)


# --- Routes ---


@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="ok",
        telegram_authorized=td_client.is_authorized if td_client else False,
        uptime_seconds=time.time() - start_time,
    )


@app.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status():
    if not auth_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")
    status = await auth_manager.get_status()
    return AuthStatusResponse(**status)


@app.post("/auth/code")
async def submit_auth_code(request: Request):
    """Submit Telegram authentication code (from SMS/App)."""
    if not auth_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")

    body = await request.json()
    code = body.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' field")

    result = await auth_manager.submit_code(str(code))
    return {"success": True, "result": result}


@app.post("/auth/password")
async def submit_auth_password(request: Request):
    """Submit 2FA password for Telegram authentication."""
    if not auth_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")

    body = await request.json()
    password = body.get("password")
    if not password:
        raise HTTPException(status_code=400, detail="Missing 'password' field")

    result = await auth_manager.submit_password(password)
    return {"success": True, "result": result}


@app.get("/debug/memory")
async def memory_statistics(full: bool = False):
    """TDLight-specific: return memory usage of all internal TDLight managers.

    Available only when using TDLight (https://github.com/tdlight-team/tdlight).
    """
    if not td_client:
        raise HTTPException(status_code=503, detail="Service not initialized")
    try:
        result = await td_client.get_memory_statistics(full=full)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"getMemoryStatistics failed: {e}")


@app.post("/webhook/planfix", response_model=PlanfixWebhookResponse)
async def planfix_webhook(payload: PlanfixWebhookRequest):
    """Receive a message from Planfix and send it to Telegram.

    Planfix calls this endpoint when an operator sends a message
    from a contact card in Planfix.
    """
    if not td_client or not planfix_client:
        raise HTTPException(status_code=503, detail="Service not initialized")

    if not td_client.is_authorized:
        raise HTTPException(status_code=503, detail="Telegram not authorized")

    # Validate token
    if not planfix_client.validate_token(payload.token):
        raise HTTPException(status_code=403, detail="Invalid token")

    db = db_session_factory()
    try:
        phone = payload.contactPhone
        contact_id = str(payload.contactId) if payload.contactId else None

        # Try to find existing mapping
        mapping = get_or_create_mapping(db, phone=phone, planfix_contact_id=contact_id)

        chat_id = None

        # If we have a cached chat_id, use it
        if mapping and mapping.telegram_chat_id:
            chat_id = int(mapping.telegram_chat_id)
        elif phone:
            # Search user by phone number
            user = await td_client.search_user_by_phone(phone)
            if user:
                user_id = user.get("id", 0)
                chat_result = await td_client.create_private_chat(user_id)
                chat_id = chat_result.get("id", 0)

                user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                mapping = get_or_create_mapping(
                    db,
                    phone=phone,
                    planfix_contact_id=contact_id,
                    telegram_user_id=str(user_id),
                    telegram_chat_id=str(chat_id),
                    name=user_name,
                )

        if not chat_id:
            log_message(
                db,
                direction=MessageDirection.PLANFIX_TO_TELEGRAM,
                planfix_contact_id=contact_id,
                telegram_chat_id=None,
                message_text=payload.messageText,
                external_id=None,
                status=MessageStatus.FAILED,
                error_message=f"Could not find Telegram user for phone={phone}",
            )
            return PlanfixWebhookResponse(
                success=False,
                error=f"Telegram user not found for phone {phone}",
            )

        # Send the message
        result = await td_client.send_message(chat_id, payload.messageText)
        msg_id = result.get("id", 0)
        external_id = f"pf_{msg_id}"

        log_message(
            db,
            direction=MessageDirection.PLANFIX_TO_TELEGRAM,
            planfix_contact_id=contact_id,
            telegram_chat_id=str(chat_id),
            message_text=payload.messageText,
            external_id=external_id,
            status=MessageStatus.SENT,
        )

        return PlanfixWebhookResponse(
            success=True,
            messageId=external_id,
        )

    except Exception as e:
        logger.exception("Error processing Planfix webhook")
        log_message(
            db,
            direction=MessageDirection.PLANFIX_TO_TELEGRAM,
            planfix_contact_id=str(payload.contactId) if payload.contactId else None,
            telegram_chat_id=None,
            message_text=payload.messageText,
            external_id=None,
            status=MessageStatus.FAILED,
            error_message=str(e),
        )
        return PlanfixWebhookResponse(success=False, error=str(e))
    finally:
        db.close()


def main():
    """Entry point for running the server directly."""
    import uvicorn

    cfg = load_config()
    setup_logging(cfg.server.log_level)
    uvicorn.run(
        "src.main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.server.log_level.lower(),
    )


if __name__ == "__main__":
    main()
