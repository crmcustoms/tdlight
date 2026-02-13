import logging
from typing import Optional

import httpx

from .config import PlanfixConfig
from .models import PlanfixNotification

logger = logging.getLogger(__name__)


class PlanfixClient:
    """Client for sending notifications to Planfix Chat API."""

    def __init__(self, config: PlanfixConfig):
        self.config = config
        self._http_client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"Content-Type": "application/json"},
        )

    async def stop(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def send_notification(self, notification: PlanfixNotification) -> dict:
        """Send a message notification to Planfix (Telegram → Planfix direction).

        Posts to the Planfix notificationUrl so the message appears
        in the contact's card inside Planfix.
        """
        if not self._http_client:
            raise RuntimeError("PlanfixClient not started")

        payload = notification.model_dump()
        logger.info(
            "Sending notification to Planfix: contact=%s, externalId=%s",
            notification.contact.get("phone", "?"),
            notification.externalId,
        )

        try:
            response = await self._http_client.post(
                self.config.notification_url,
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            logger.info("Planfix response: %s", result)
            return result
        except httpx.HTTPStatusError as e:
            logger.error(
                "Planfix HTTP error %d: %s", e.response.status_code, e.response.text
            )
            raise
        except httpx.RequestError as e:
            logger.error("Planfix request error: %s", e)
            raise

    def validate_token(self, token: str) -> bool:
        """Validate that the incoming webhook token matches our configured token."""
        return token == self.config.token
