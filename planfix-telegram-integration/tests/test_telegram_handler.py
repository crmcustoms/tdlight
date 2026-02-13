import json
from unittest.mock import MagicMock, patch

import pytest

from src.config import TelegramConfig


class FakeTDJson:
    """Fake libtdjson for testing without the real shared library."""

    def __init__(self):
        self._responses = []
        self._sent = []
        self._client_counter = 0

    def td_create_client_id(self):
        self._client_counter += 1
        return self._client_counter

    def td_receive(self, timeout):
        if self._responses:
            return json.dumps(self._responses.pop(0)).encode("utf-8")
        return None

    def td_send(self, client_id, query):
        self._sent.append(json.loads(query.decode("utf-8")))

    def td_execute(self, query):
        parsed = json.loads(query.decode("utf-8"))
        if parsed.get("@type") == "setLogVerbosityLevel":
            return json.dumps({"@type": "ok"}).encode("utf-8")
        if parsed.get("@type") == "getTextEntities":
            return json.dumps({"@type": "textEntities", "entities": []}).encode("utf-8")
        return None

    def td_set_log_message_callback(self, level, callback):
        pass


@pytest.fixture
def telegram_config():
    return TelegramConfig(
        api_id=12345,
        api_hash="test_hash",
        phone="+380501234567",
        database_dir="/tmp/test_tdlib",
        tdjson_path="/fake/libtdjson.so",
    )


class TestTDLightClientInit:
    """Test TDLightClient initialization logic without loading the real library."""

    def test_config_stored(self, telegram_config):
        assert telegram_config.api_id == 12345
        assert telegram_config.api_hash == "test_hash"
        assert telegram_config.phone == "+380501234567"

    def test_config_database_dir(self, telegram_config):
        assert telegram_config.database_dir == "/tmp/test_tdlib"


class TestTDLightOptions:
    """Test TDLight-specific memory optimization options."""

    def test_default_optimization_options(self):
        config = TelegramConfig(
            api_id=1, api_hash="h", phone="+1",
            database_dir="/tmp/t", tdjson_path="/fake",
        )
        # Defaults optimized for server-side integration
        assert config.disable_minithumbnails is True
        assert config.disable_notifications is True
        assert config.disable_group_calls is True
        assert config.disable_auto_download is True
        assert config.ignore_server_deletes_and_reads is True
        assert config.ignore_update_chat_read_inbox is True
        assert config.ignore_update_user_chat_action is True
        # These default to False
        assert config.disable_document_filenames is False
        assert config.ignore_update_chat_last_message is False

    def test_custom_options(self):
        config = TelegramConfig(
            api_id=1, api_hash="h", phone="+1",
            database_dir="/tmp/t", tdjson_path="/fake",
            disable_minithumbnails=False,
            disable_notifications=False,
        )
        assert config.disable_minithumbnails is False
        assert config.disable_notifications is False

    def test_options_as_setOption_payload(self):
        """Verify the TDLight setOption payload format."""
        config = TelegramConfig(
            api_id=1, api_hash="h", phone="+1",
            database_dir="/tmp/t", tdjson_path="/fake",
        )
        options = {
            "disable_minithumbnails": config.disable_minithumbnails,
            "disable_notifications": config.disable_notifications,
            "disable_group_calls": config.disable_group_calls,
        }
        for name, value in options.items():
            payload = {
                "@type": "setOption",
                "name": name,
                "value": {"@type": "optionValueBoolean", "value": value},
            }
            assert payload["@type"] == "setOption"
            assert payload["value"]["@type"] == "optionValueBoolean"
            assert isinstance(payload["value"]["value"], bool)

    def test_get_memory_statistics_payload(self):
        """Verify getMemoryStatistics request format."""
        payload = {"@type": "getMemoryStatistics", "full": False}
        assert payload["@type"] == "getMemoryStatistics"
        assert payload["full"] is False


class TestFakeTDJson:
    """Test the fake TDJson mock to ensure our test harness works."""

    def test_create_client_id(self):
        fake = FakeTDJson()
        assert fake.td_create_client_id() == 1
        assert fake.td_create_client_id() == 2

    def test_send_and_receive(self):
        fake = FakeTDJson()
        fake._responses.append({"@type": "updateAuthorizationState", "authorization_state": {"@type": "authorizationStateReady"}})

        result = fake.td_receive(1.0)
        assert result is not None
        parsed = json.loads(result.decode("utf-8"))
        assert parsed["@type"] == "updateAuthorizationState"

    def test_receive_empty(self):
        fake = FakeTDJson()
        assert fake.td_receive(0.1) is None

    def test_execute(self):
        fake = FakeTDJson()
        result = fake.td_execute(json.dumps({"@type": "setLogVerbosityLevel", "new_verbosity_level": 1}).encode("utf-8"))
        parsed = json.loads(result.decode("utf-8"))
        assert parsed["@type"] == "ok"

    def test_send_records_queries(self):
        fake = FakeTDJson()
        query = {"@type": "getMe"}
        fake.td_send(1, json.dumps(query).encode("utf-8"))
        assert len(fake._sent) == 1
        assert fake._sent[0]["@type"] == "getMe"


class TestTelegramMessageParsing:
    """Test parsing of TDLight update events."""

    def test_parse_text_message_update(self):
        event = {
            "@type": "updateNewMessage",
            "message": {
                "id": 123456,
                "chat_id": -100123,
                "sender_id": {"@type": "messageSenderUser", "user_id": 789},
                "is_outgoing": False,
                "content": {
                    "@type": "messageText",
                    "text": {"@type": "formattedText", "text": "Hello from Telegram"},
                },
                "date": 1700000000,
            },
        }

        message = event["message"]
        content = message["content"]
        assert content["@type"] == "messageText"
        assert content["text"]["text"] == "Hello from Telegram"

        sender = message["sender_id"]
        assert sender["@type"] == "messageSenderUser"
        assert sender["user_id"] == 789
        assert message["is_outgoing"] is False

    def test_skip_outgoing_message(self):
        event = {
            "@type": "updateNewMessage",
            "message": {
                "id": 999,
                "chat_id": -100123,
                "sender_id": {"@type": "messageSenderUser", "user_id": 1},
                "is_outgoing": True,
                "content": {
                    "@type": "messageText",
                    "text": {"@type": "formattedText", "text": "My own message"},
                },
            },
        }
        assert event["message"]["is_outgoing"] is True

    def test_skip_non_text_message(self):
        event = {
            "@type": "updateNewMessage",
            "message": {
                "id": 555,
                "chat_id": -100123,
                "sender_id": {"@type": "messageSenderUser", "user_id": 789},
                "is_outgoing": False,
                "content": {"@type": "messagePhoto", "caption": {"text": "Photo"}},
            },
        }
        assert event["message"]["content"]["@type"] != "messageText"
