import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_env(key: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(key, default)
    if required and not value:
        raise RuntimeError(f"Required environment variable {key} is not set")
    return value or ""


@dataclass(frozen=True)
class PlanfixConfig:
    account: str = field(default_factory=lambda: _get_env("PLANFIX_ACCOUNT", required=True))
    webhook_url: str = field(default_factory=lambda: _get_env("PLANFIX_WEBHOOK_URL", required=True))
    notification_url: str = field(default_factory=lambda: _get_env("PLANFIX_NOTIFICATION_URL", required=True))
    token: str = field(default_factory=lambda: _get_env("PLANFIX_TOKEN", required=True))


@dataclass(frozen=True)
class TelegramConfig:
    api_id: int = field(default_factory=lambda: int(_get_env("TELEGRAM_API_ID", required=True)))
    api_hash: str = field(default_factory=lambda: _get_env("TELEGRAM_API_HASH", required=True))
    phone: str = field(default_factory=lambda: _get_env("TELEGRAM_PHONE", required=True))
    database_dir: str = field(default_factory=lambda: _get_env("TELEGRAM_DATABASE_DIR", "/data/telegram"))
    tdjson_path: str = field(default_factory=lambda: _get_env("TDJSON_PATH", ""))


@dataclass(frozen=True)
class ServerConfig:
    host: str = field(default_factory=lambda: _get_env("SERVER_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_get_env("SERVER_PORT", "8000")))
    log_level: str = field(default_factory=lambda: _get_env("LOG_LEVEL", "INFO"))


@dataclass(frozen=True)
class DatabaseConfig:
    url: str = field(default_factory=lambda: _get_env("DATABASE_URL", "sqlite:///./data/integration.db"))


@dataclass(frozen=True)
class AppConfig:
    planfix: PlanfixConfig = field(default_factory=PlanfixConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


def load_config() -> AppConfig:
    return AppConfig()
