from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when runtime configuration cannot satisfy the canonical config contract."""


@dataclass(frozen=True)
class StorageConfig:
    data_root: Path


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class RuntimeConfig:
    email_poll_interval_seconds: int = 900


@dataclass(frozen=True)
class CmbEmailSourceConfig:
    enabled: bool = True
    host: str = "imap.163.com"
    port: int = 993
    mailbox: str = "\u62db\u884c\u4fe1\u7528\u5361"
    subject_keyword: str = "\u62db\u5546\u94f6\u884c\u4fe1\u7528\u5361\u7535\u5b50\u8d26\u5355"


@dataclass(frozen=True)
class SourceConfig:
    cmb_email: CmbEmailSourceConfig = CmbEmailSourceConfig()


@dataclass(frozen=True)
class AppConfig:
    storage: StorageConfig
    server: ServerConfig = ServerConfig()
    runtime: RuntimeConfig = RuntimeConfig()
    sources: SourceConfig = SourceConfig()


@dataclass(frozen=True)
class EmailCredentials:
    address: str
    auth_code: str


def _table(raw: object, label: str) -> dict[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{label} must be a TOML table")
    return dict(raw)


def _text(table: Mapping[str, object], key: str, default: str | None = None) -> str:
    raw = table.get(key, default)
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return raw.strip()


def _integer(table: Mapping[str, object], key: str, default: int) -> int:
    raw = table.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigurationError(f"{key} must be an integer")
    return raw


def _boolean(table: Mapping[str, object], key: str, default: bool) -> bool:
    raw = table.get(key, default)
    if not isinstance(raw, bool):
        raise ConfigurationError(f"{key} must be a boolean")
    return raw


def load_app_config(path: Path | str) -> AppConfig:
    """Load non-secret runtime config and anchor relative data_root to the config file."""
    config_path = Path(path).expanduser().resolve()
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Unable to load config {config_path}: {exc}") from exc

    storage_table = _table(raw.get("storage"), "storage")
    if "data_root" not in storage_table:
        raise ConfigurationError("storage.data_root is required")
    data_root_text = _text(storage_table, "data_root")
    data_root = Path(data_root_text).expanduser()
    if not data_root.is_absolute():
        data_root = config_path.parent / data_root
    data_root = data_root.resolve()

    server_table = _table(raw.get("server"), "server")
    server = ServerConfig(
        host=_text(server_table, "host", "127.0.0.1"),
        port=_integer(server_table, "port", 8765),
    )
    if not 1 <= server.port <= 65535:
        raise ConfigurationError("server.port must be between 1 and 65535")

    runtime_table = _table(raw.get("runtime"), "runtime")
    runtime = RuntimeConfig(
        email_poll_interval_seconds=_integer(
            runtime_table,
            "email_poll_interval_seconds",
            900,
        )
    )
    if runtime.email_poll_interval_seconds <= 0:
        raise ConfigurationError("runtime.email_poll_interval_seconds must be positive")

    sources_table = _table(raw.get("sources"), "sources")
    cmb_table = _table(sources_table.get("cmb_email"), "sources.cmb_email")
    cmb_email = CmbEmailSourceConfig(
        enabled=_boolean(cmb_table, "enabled", True),
        host=_text(cmb_table, "host", "imap.163.com"),
        port=_integer(cmb_table, "port", 993),
        mailbox=_text(cmb_table, "mailbox", CmbEmailSourceConfig().mailbox),
        subject_keyword=_text(
            cmb_table,
            "subject_keyword",
            CmbEmailSourceConfig().subject_keyword,
        ),
    )
    if not 1 <= cmb_email.port <= 65535:
        raise ConfigurationError("sources.cmb_email.port must be between 1 and 65535")

    return AppConfig(
        storage=StorageConfig(data_root=data_root),
        server=server,
        runtime=runtime,
        sources=SourceConfig(cmb_email=cmb_email),
    )


def load_email_credentials(
    environ: Mapping[str, str] | None = None,
) -> EmailCredentials:
    """Read CMB credentials only from process environment, never from the TOML file."""
    values = os.environ if environ is None else environ
    address = values.get("EMAIL_ADDR", "").strip()
    auth_code = values.get("EMAIL_AUTH_CODE", "").strip()
    if not address:
        raise ConfigurationError("Missing required environment variable: EMAIL_ADDR")
    if not auth_code:
        raise ConfigurationError("Missing required environment variable: EMAIL_AUTH_CODE")
    return EmailCredentials(address=address, auth_code=auth_code)
