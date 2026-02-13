import pytest
import httpx
import pytest_asyncio

from src.config import PlanfixConfig
from src.models import PlanfixNotification
from src.planfix_api import PlanfixClient


@pytest.fixture
def planfix_config():
    return PlanfixConfig(
        account="test-account",
        webhook_url="https://test.example.com/webhook/planfix",
        notification_url="https://test.planfix.com/rest/notification/chat",
        token="test-token-123",
    )


@pytest.fixture
def planfix_client(planfix_config):
    return PlanfixClient(planfix_config)


class TestPlanfixClient:
    def test_validate_token_correct(self, planfix_client):
        assert planfix_client.validate_token("test-token-123") is True

    def test_validate_token_incorrect(self, planfix_client):
        assert planfix_client.validate_token("wrong-token") is False

    def test_validate_token_empty(self, planfix_client):
        assert planfix_client.validate_token("") is False

    @pytest.mark.asyncio
    async def test_send_notification_success(self, planfix_client, httpx_mock):
        httpx_mock.add_response(
            url="https://test.planfix.com/rest/notification/chat",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        await planfix_client.start()
        try:
            notification = PlanfixNotification(
                token="test-token-123",
                messageText="Hello from Telegram",
                contact={"phone": "+380501234567", "name": "Test User"},
                providerId="telegram",
                externalId="tg_12345",
            )
            result = await planfix_client.send_notification(notification)
            assert result == {"status": "ok"}
        finally:
            await planfix_client.stop()

    @pytest.mark.asyncio
    async def test_send_notification_http_error(self, planfix_client, httpx_mock):
        httpx_mock.add_response(
            url="https://test.planfix.com/rest/notification/chat",
            method="POST",
            status_code=500,
            text="Internal Server Error",
        )

        await planfix_client.start()
        try:
            notification = PlanfixNotification(
                token="test-token-123",
                messageText="Test",
                contact={"phone": "+380501234567", "name": "User"},
                providerId="telegram",
                externalId="tg_99",
            )
            with pytest.raises(httpx.HTTPStatusError):
                await planfix_client.send_notification(notification)
        finally:
            await planfix_client.stop()

    @pytest.mark.asyncio
    async def test_send_notification_not_started(self, planfix_client):
        notification = PlanfixNotification(
            token="test-token-123",
            messageText="Test",
            contact={"phone": "+380501234567", "name": "User"},
            providerId="telegram",
            externalId="tg_1",
        )
        with pytest.raises(RuntimeError, match="not started"):
            await planfix_client.send_notification(notification)


class TestPlanfixNotificationPayload:
    def test_notification_serialization(self):
        notification = PlanfixNotification(
            token="tok",
            messageText="Hello",
            contact={"phone": "+123", "name": "John"},
            providerId="telegram",
            externalId="ext_1",
        )
        data = notification.model_dump()
        assert data["token"] == "tok"
        assert data["messageText"] == "Hello"
        assert data["contact"]["phone"] == "+123"
        assert data["providerId"] == "telegram"
        assert data["externalId"] == "ext_1"
