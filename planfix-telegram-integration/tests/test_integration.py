import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import (
    Base,
    ContactMapping,
    MessageDirection,
    MessageLog,
    MessageStatus,
    PlanfixWebhookRequest,
    PlanfixWebhookResponse,
)
from src.main import get_or_create_mapping, log_message


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestContactMapping:
    def test_create_mapping_by_phone(self, db_session):
        mapping = get_or_create_mapping(
            db_session,
            phone="+380501234567",
            planfix_contact_id="pf_100",
            telegram_user_id="tg_200",
            telegram_chat_id="chat_300",
            name="John Doe",
        )
        assert mapping is not None
        assert mapping.phone == "+380501234567"
        assert mapping.planfix_contact_id == "pf_100"
        assert mapping.telegram_user_id == "tg_200"
        assert mapping.telegram_chat_id == "chat_300"
        assert mapping.name == "John Doe"

    def test_find_existing_by_phone(self, db_session):
        get_or_create_mapping(
            db_session,
            phone="+380501234567",
            planfix_contact_id="pf_100",
            telegram_user_id="tg_200",
        )
        # Look up by phone again
        mapping = get_or_create_mapping(db_session, phone="+380501234567")
        assert mapping is not None
        assert mapping.planfix_contact_id == "pf_100"

    def test_find_existing_by_planfix_id(self, db_session):
        get_or_create_mapping(
            db_session,
            phone="+380501234567",
            planfix_contact_id="pf_100",
            telegram_user_id="tg_200",
        )
        mapping = get_or_create_mapping(db_session, planfix_contact_id="pf_100")
        assert mapping is not None
        assert mapping.phone == "+380501234567"

    def test_find_existing_by_telegram_user_id(self, db_session):
        get_or_create_mapping(
            db_session,
            phone="+380501234567",
            planfix_contact_id="pf_100",
            telegram_user_id="tg_200",
        )
        mapping = get_or_create_mapping(db_session, telegram_user_id="tg_200")
        assert mapping is not None
        assert mapping.planfix_contact_id == "pf_100"

    def test_update_missing_fields(self, db_session):
        # Create without chat_id
        get_or_create_mapping(
            db_session,
            phone="+380501234567",
            planfix_contact_id="pf_100",
            telegram_user_id="tg_200",
        )
        # Update with chat_id
        mapping = get_or_create_mapping(
            db_session,
            phone="+380501234567",
            telegram_chat_id="chat_300",
        )
        assert mapping.telegram_chat_id == "chat_300"

    def test_returns_none_without_identifiers(self, db_session):
        mapping = get_or_create_mapping(db_session)
        assert mapping is None


class TestMessageLog:
    def test_log_sent_message(self, db_session):
        entry = log_message(
            db_session,
            direction=MessageDirection.PLANFIX_TO_TELEGRAM,
            planfix_contact_id="pf_100",
            telegram_chat_id="chat_300",
            message_text="Hello!",
            external_id="pf_msg_1",
            status=MessageStatus.SENT,
        )
        assert entry.id is not None
        assert entry.direction == "planfix_to_telegram"
        assert entry.status == "sent"

    def test_log_failed_message(self, db_session):
        entry = log_message(
            db_session,
            direction=MessageDirection.TELEGRAM_TO_PLANFIX,
            planfix_contact_id=None,
            telegram_chat_id="chat_300",
            message_text="Hello",
            external_id="tg_1",
            status=MessageStatus.FAILED,
            error_message="Connection refused",
        )
        assert entry.status == "failed"
        assert entry.error_message == "Connection refused"

    def test_multiple_logs(self, db_session):
        for i in range(5):
            log_message(
                db_session,
                direction=MessageDirection.PLANFIX_TO_TELEGRAM,
                planfix_contact_id=f"pf_{i}",
                telegram_chat_id=f"chat_{i}",
                message_text=f"Message {i}",
                external_id=f"ext_{i}",
                status=MessageStatus.SENT,
            )
        count = db_session.query(MessageLog).count()
        assert count == 5


class TestPlanfixWebhookModels:
    def test_parse_webhook_request(self):
        data = {
            "token": "my-token",
            "messageText": "Hello from Planfix",
            "contactPhone": "+380501234567",
            "contactId": 12345,
            "providerId": "telegram",
        }
        req = PlanfixWebhookRequest(**data)
        assert req.token == "my-token"
        assert req.messageText == "Hello from Planfix"
        assert req.contactPhone == "+380501234567"
        assert req.contactId == 12345
        assert req.providerId == "telegram"

    def test_parse_webhook_request_minimal(self):
        data = {
            "token": "tok",
            "messageText": "Hi",
        }
        req = PlanfixWebhookRequest(**data)
        assert req.token == "tok"
        assert req.contactPhone is None
        assert req.contactId is None
        assert req.providerId == "telegram"

    def test_webhook_success_response(self):
        resp = PlanfixWebhookResponse(success=True, messageId="msg_123")
        data = resp.model_dump()
        assert data["success"] is True
        assert data["messageId"] == "msg_123"
        assert data["error"] is None

    def test_webhook_error_response(self):
        resp = PlanfixWebhookResponse(success=False, error="User not found")
        data = resp.model_dump()
        assert data["success"] is False
        assert data["error"] == "User not found"
