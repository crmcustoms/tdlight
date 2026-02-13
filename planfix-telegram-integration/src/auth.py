import asyncio
import logging

from .config import TelegramConfig
from .telegram_handler import TDLightClient

logger = logging.getLogger(__name__)


class TelegramAuthManager:
    """Manages Telegram authentication flow via TDLight.

    On startup, TDLight checks for an existing session in the database directory.
    If a valid session exists, the client is authorized automatically.
    Otherwise, the interactive auth flow (phone → code → optional password) is required.
    """

    def __init__(self, client: TDLightClient, config: TelegramConfig):
        self.client = client
        self.config = config
        self._auth_event = asyncio.Event()
        self._pending_code_future: asyncio.Future | None = None
        self._pending_password_future: asyncio.Future | None = None

    async def wait_for_authorization(self, timeout: float = 120.0) -> bool:
        """Wait until the client is authorized or timeout.

        If a saved session exists, this returns quickly.
        Otherwise, the phone number is sent and the caller must
        supply code/password via submit_code() / submit_password().
        """
        self.client.on_update(self._on_auth_update)

        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Authorization timed out after %.0fs", timeout)
            return False

        return self.client.is_authorized

    async def _on_auth_update(self, event: dict) -> None:
        if event.get("@type") != "updateAuthorizationState":
            return

        auth_state = event["authorization_state"]
        state_type = auth_state["@type"]

        if state_type == "authorizationStateWaitTdlibParameters":
            logger.info("Setting TDLib parameters...")
            await self.client.set_tdlib_parameters()

        elif state_type == "authorizationStateWaitPhoneNumber":
            logger.info("Sending phone number for authentication...")
            await self.client.set_authentication_phone_number(self.config.phone)

        elif state_type == "authorizationStateWaitCode":
            logger.info("Waiting for authentication code (submit via API)...")
            # Code must be supplied externally via submit_code()

        elif state_type == "authorizationStateWaitPassword":
            logger.info("Waiting for 2FA password (submit via API)...")
            # Password must be supplied externally via submit_password()

        elif state_type == "authorizationStateReady":
            logger.info("Telegram authorization successful")
            self._auth_event.set()

        elif state_type == "authorizationStateClosed":
            logger.warning("Authorization state closed")
            self._auth_event.set()

    async def submit_code(self, code: str) -> dict:
        """Submit the authentication code received via SMS/App."""
        return await self.client.check_authentication_code(code)

    async def submit_password(self, password: str) -> dict:
        """Submit the 2FA password."""
        return await self.client.check_authentication_password(password)

    async def get_status(self) -> dict:
        return {
            "authorized": self.client.is_authorized,
            "state": self.client.auth_state,
            "phone": self.config.phone,
        }
