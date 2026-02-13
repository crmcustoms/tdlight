import asyncio
import json
import logging
import os
import threading
from ctypes import CDLL, CFUNCTYPE, c_char_p, c_double, c_int
from ctypes.util import find_library
from typing import Any, Callable, Optional

from .config import TelegramConfig

logger = logging.getLogger(__name__)


class TDLightClient:
    """Async wrapper around TDLight JSON interface (libtdjson).

    Uses TDLight fork (https://github.com/tdlight-team/tdlight) which provides
    memory optimization options and getMemoryStatistics API beyond standard TDLib.
    """

    def __init__(self, config: TelegramConfig):
        self.config = config
        self._authorized = False
        self._auth_state = "unknown"
        self._current_user: dict | None = None
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._request_id = 0
        self._update_handlers: list[Callable] = []
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._receive_thread: threading.Thread | None = None

        self._load_library()
        self._setup_functions()
        self._setup_logging()
        self.client_id = self._td_create_client_id()

    def _load_library(self) -> None:
        tdjson_path = self.config.tdjson_path or find_library("tdjson")
        if tdjson_path is None:
            raise RuntimeError(
                "Cannot find 'tdjson' library. Set TDJSON_PATH or install libtdjson."
            )
        self._tdjson = CDLL(tdjson_path)

    def _setup_functions(self) -> None:
        self._td_create_client_id = self._tdjson.td_create_client_id
        self._td_create_client_id.restype = c_int
        self._td_create_client_id.argtypes = []

        self._td_receive = self._tdjson.td_receive
        self._td_receive.restype = c_char_p
        self._td_receive.argtypes = [c_double]

        self._td_send = self._tdjson.td_send
        self._td_send.restype = None
        self._td_send.argtypes = [c_int, c_char_p]

        self._td_execute = self._tdjson.td_execute
        self._td_execute.restype = c_char_p
        self._td_execute.argtypes = [c_char_p]

        self._log_message_callback_type = CFUNCTYPE(None, c_int, c_char_p)
        self._td_set_log_message_callback = self._tdjson.td_set_log_message_callback
        self._td_set_log_message_callback.restype = None
        self._td_set_log_message_callback.argtypes = [
            c_int,
            self._log_message_callback_type,
        ]

    def _setup_logging(self) -> None:
        @self._log_message_callback_type
        def on_log_message(verbosity_level, message):
            msg = message.decode("utf-8") if message else ""
            if verbosity_level == 0:
                logger.critical("TDLib fatal: %s", msg)
            elif verbosity_level == 1:
                logger.error("TDLib: %s", msg)

        self._log_callback = on_log_message  # prevent GC
        self._td_set_log_message_callback(2, on_log_message)
        self.execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": 1})

    def execute(self, query: dict) -> dict | None:
        result = self._td_execute(json.dumps(query).encode("utf-8"))
        if result:
            return json.loads(result.decode("utf-8"))
        return None

    def _send_raw(self, query: dict) -> None:
        self._td_send(self.client_id, json.dumps(query).encode("utf-8"))

    def _receive_raw(self, timeout: float = 1.0) -> dict | None:
        result = self._td_receive(timeout)
        if result:
            return json.loads(result.decode("utf-8"))
        return None

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def send(self, query: dict, timeout: float = 30.0) -> dict:
        """Send an async request and wait for the response."""
        req_id = self._next_request_id()
        query["@extra"] = req_id

        future: asyncio.Future = self._loop.create_future()
        self._pending_requests[req_id] = future

        self._send_raw(query)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            raise TimeoutError(f"TDLight request timed out: {query.get('@type')}")

    def send_fire_and_forget(self, query: dict) -> None:
        """Send a request without waiting for a response."""
        self._send_raw(query)

    def on_update(self, handler: Callable) -> None:
        """Register a handler for incoming TDLight updates."""
        self._update_handlers.append(handler)

    def _receive_loop(self) -> None:
        """Background thread that polls TDLib for updates."""
        while self._running:
            event = self._receive_raw(timeout=1.0)
            if event and self._loop:
                self._loop.call_soon_threadsafe(self._dispatch_event, event)

    def _dispatch_event(self, event: dict) -> None:
        extra = event.get("@extra")
        if extra is not None and extra in self._pending_requests:
            future = self._pending_requests.pop(extra)
            if not future.done():
                future.set_result(event)
            return

        event_type = event.get("@type", "")

        if event_type == "updateAuthorizationState":
            self._handle_auth_state(event)

        for handler in self._update_handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                logger.exception("Error in update handler")

    def _handle_auth_state(self, event: dict) -> None:
        auth_state = event.get("authorization_state", {})
        self._auth_state = auth_state.get("@type", "unknown")
        logger.info("Authorization state: %s", self._auth_state)

        if self._auth_state == "authorizationStateReady":
            self._authorized = True
        elif self._auth_state == "authorizationStateClosed":
            self._authorized = False
            self._running = False

    async def start(self) -> None:
        """Start the TDLight client and begin receiving updates."""
        self._loop = asyncio.get_running_loop()
        self._running = True

        self._receive_thread = threading.Thread(
            target=self._receive_loop, daemon=True, name="tdlight-receive"
        )
        self._receive_thread.start()

        # Trigger initialization
        self.send_fire_and_forget({"@type": "getOption", "name": "version"})

    async def stop(self) -> None:
        """Stop the TDLight client."""
        self._running = False
        if self._receive_thread and self._receive_thread.is_alive():
            self._receive_thread.join(timeout=5)
        logger.info("TDLight client stopped")

    @property
    def is_authorized(self) -> bool:
        return self._authorized

    @property
    def auth_state(self) -> str:
        return self._auth_state

    # --- Telegram API methods ---

    async def set_tdlib_parameters(self) -> dict:
        os.makedirs(self.config.database_dir, exist_ok=True)
        result = await self.send({
            "@type": "setTdlibParameters",
            "database_directory": self.config.database_dir,
            "use_message_database": True,
            "use_secret_chats": False,
            "api_id": self.config.api_id,
            "api_hash": self.config.api_hash,
            "system_language_code": "en",
            "device_model": "Planfix-Telegram Integration",
            "application_version": "1.0",
        })

        # Apply TDLight-specific memory optimizations
        await self._apply_tdlight_options()
        return result

    async def _apply_tdlight_options(self) -> None:
        """Set TDLight-specific options for memory optimization.

        These options are only available in TDLight (https://github.com/tdlight-team/tdlight),
        not in standard TDLib. They reduce RAM usage for server-side integrations.
        """
        tdlight_options = {
            "disable_minithumbnails": self.config.disable_minithumbnails,
            "disable_document_filenames": self.config.disable_document_filenames,
            "disable_notifications": self.config.disable_notifications,
            "disable_group_calls": self.config.disable_group_calls,
            "disable_auto_download": self.config.disable_auto_download,
            "ignore_server_deletes_and_reads": self.config.ignore_server_deletes_and_reads,
            "ignore_update_chat_last_message": self.config.ignore_update_chat_last_message,
            "ignore_update_chat_read_inbox": self.config.ignore_update_chat_read_inbox,
            "ignore_update_user_chat_action": self.config.ignore_update_user_chat_action,
        }

        for name, value in tdlight_options.items():
            try:
                await self.send({
                    "@type": "setOption",
                    "name": name,
                    "value": {"@type": "optionValueBoolean", "value": value},
                })
                logger.debug("TDLight option %s = %s", name, value)
            except Exception:
                logger.debug("TDLight option %s not supported (standard TDLib?)", name)

    async def set_authentication_phone_number(self, phone: str) -> dict:
        return await self.send({
            "@type": "setAuthenticationPhoneNumber",
            "phone_number": phone,
        })

    async def check_authentication_code(self, code: str) -> dict:
        return await self.send({
            "@type": "checkAuthenticationCode",
            "code": code,
        })

    async def check_authentication_password(self, password: str) -> dict:
        return await self.send({
            "@type": "checkAuthenticationPassword",
            "password": password,
        })

    async def get_me(self) -> dict:
        result = await self.send({"@type": "getMe"})
        self._current_user = result
        return result

    async def get_contacts(self) -> dict:
        return await self.send({"@type": "getContacts"})

    async def search_contacts(self, query: str, limit: int = 20) -> dict:
        return await self.send({
            "@type": "searchContacts",
            "query": query,
            "limit": limit,
        })

    async def get_user(self, user_id: int) -> dict:
        return await self.send({
            "@type": "getUser",
            "user_id": user_id,
        })

    async def create_private_chat(self, user_id: int) -> dict:
        return await self.send({
            "@type": "createPrivateChat",
            "user_id": user_id,
            "force": False,
        })

    async def search_user_by_phone(self, phone: str) -> dict | None:
        """Search for a Telegram user by phone number."""
        try:
            result = await self.send({
                "@type": "searchUserByPhoneNumber",
                "phone_number": phone,
            })
            if result.get("@type") == "user":
                return result
        except Exception:
            logger.debug("searchUserByPhoneNumber not available, falling back to importContacts")

        # Fallback: import the contact temporarily
        try:
            result = await self.send({
                "@type": "importContacts",
                "contacts": [{
                    "@type": "contact",
                    "phone_number": phone,
                    "first_name": "Planfix",
                    "last_name": "Contact",
                }],
            })
            user_ids = result.get("user_ids", [])
            if user_ids and user_ids[0] != 0:
                return await self.get_user(user_ids[0])
        except Exception:
            logger.exception("Failed to search user by phone %s", phone)

        return None

    async def send_message(self, chat_id: int, text: str) -> dict:
        return await self.send({
            "@type": "sendMessage",
            "chat_id": chat_id,
            "input_message_content": {
                "@type": "inputMessageText",
                "text": {
                    "@type": "formattedText",
                    "text": text,
                },
            },
        })

    async def get_chat(self, chat_id: int) -> dict:
        return await self.send({
            "@type": "getChat",
            "chat_id": chat_id,
        })

    async def get_authorization_state(self) -> dict:
        return await self.send({"@type": "getAuthorizationState"})

    async def get_memory_statistics(self, full: bool = False) -> dict:
        """TDLight-specific: get memory usage statistics of all internal managers.

        Available only in TDLight (https://github.com/tdlight-team/tdlight).
        Returns a JSON string with detailed memory breakdown per manager.
        """
        return await self.send({
            "@type": "getMemoryStatistics",
            "full": full,
        })
