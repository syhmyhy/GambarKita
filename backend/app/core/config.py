"""Runtime configuration.

Every setting can be overridden with an environment variable prefixed ``GK_``
(for example ``GK_MAX_UPLOAD_BYTES``) or through ``backend/.env``. List settings
accept a comma-separated string: ``GK_CORS_ORIGINS=https://a.example,https://b.example``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from limits import parse, parse_many
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Room for multipart boundaries, part headers and the small text fields that
# accompany an upload. The file itself is limited by ``max_upload_bytes``.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def normalize_origin(origin: str) -> str:
    """Return ``scheme://host[:port]`` in lower case, or raise ``ValueError``."""
    parts = urlsplit(origin.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"invalid origin {origin!r}: expected scheme://host[:port]")
    if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(f"invalid origin {origin!r}: must not contain a path, query or credentials")
    return f"{parts.scheme}://{parts.netloc}".lower()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GK_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General -------------------------------------------------------------
    environment: Literal["development", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    enable_docs: bool | None = None  # default: enabled everywhere except production

    # Base URL of the React frontend. Used to build the links encoded in QR codes.
    public_app_url: str = "http://localhost:5173"

    # --- Storage & upload limits --------------------------------------------
    storage_dir: Path = BACKEND_DIR / "storage"
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    # Cap on the stored display copy: re-encoding can inflate small, highly
    # compressible inputs, so the output is limited too (lossy fallback, then 422).
    max_stored_bytes: int = Field(default=10 * 1024 * 1024, ge=64 * 1024, le=100 * 1024 * 1024)
    max_image_pixels: int = Field(default=50_000_000, ge=1, le=250_000_000)
    # PNG/WebP decode at full size and a few KB can declare a huge canvas: lower cap.
    max_lossless_image_pixels: int = Field(default=24_000_000, ge=1, le=250_000_000)
    max_image_dimension: int = Field(default=16_383, ge=16, le=65_535)
    # Longest edge of the stored display copy; larger photos are downscaled. 0 keeps the original size.
    max_stored_dimension: int = Field(default=4096, ge=0, le=16_383)
    thumbnail_size: int = Field(default=640, ge=64, le=2048)
    max_photos_per_event: int = Field(default=2000, ge=1)
    # Photos one guest may add to an event; 0 means no per-guest limit. A guest is
    # a random key their browser keeps for the event (X-Guest-Key), so clearing site
    # data or switching browsers starts a fresh allowance: this stops a camera-roll
    # dump, it is not a hard quota. Photos are tied to a guest only while it is on.
    max_photos_per_guest: int = Field(default=0, ge=0)
    min_free_disk_bytes: int = Field(default=512 * 1024 * 1024, ge=0)
    # Decoding admission control: total pixels being decoded at once (bounds RAM)
    # and the maximum number of simultaneous jobs (bounds CPU).
    image_processing_pixel_budget: int = Field(default=100_000_000, ge=1_000_000)
    image_processing_concurrency: int = Field(default=4, ge=1, le=64)
    image_processing_queue_timeout: float = Field(default=20.0, gt=0)
    # libmagic cross-check: "auto" uses it when available (never on Windows, where
    # python-magic can crash the interpreter), "on" requires it, "off" disables it.
    libmagic: Literal["auto", "on", "off"] = "auto"

    # --- HTTP security ---------------------------------------------------------
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    trusted_hosts: Annotated[list[str], NoDecode] = ["*"]
    hsts_enabled: bool = False
    # When set, creating an event requires the ``X-Event-Creation-Key`` header.
    event_creation_key: SecretStr | None = None

    # --- Rate limiting ("limits" syntax) ---------------------------------------------------
    # Keyed per client IP (IPv6 grouped by /64) and per endpoint, across all events.
    # Guests at a venue often share ONE public IP (Wi-Fi NAT / carrier NAT), so the
    # defaults are sized for a crowd rather than a single person.
    rate_limit_enabled: bool = True
    rate_limit_create_event: str = "20/hour"
    rate_limit_upload: str = "300/minute"
    rate_limit_read: str = "1200/minute"
    rate_limit_manage: str = "120/minute"

    # --- WebSockets -------------------------------------------------------------
    ws_connect_rate: str = "300/minute"
    ws_message_rate: str = "30/minute"
    ws_max_connections_per_ip: int = Field(default=500, ge=1)
    ws_max_connections_per_event: int = Field(default=2000, ge=1)
    # Extra per-event slots only a host-authenticated wall display may use, so a
    # flood of anonymous viewers cannot lock the host out of their own wall.
    ws_reserved_host_connections: int = Field(default=20, ge=0)
    ws_max_connections_total: int = Field(default=10_000, ge=1)
    ws_max_message_bytes: int = Field(default=1024, ge=64, le=64 * 1024)
    ws_send_queue_size: int = Field(default=64, ge=1, le=10_000)
    ws_send_timeout: float = Field(default=10.0, gt=0)
    ws_allow_missing_origin: bool = True

    # --- Server (used by run.py) -------------------------------------------------
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65_535)
    reload: bool = False
    # Comma-separated proxy IPs trusted for X-Forwarded-For (uvicorn default: 127.0.0.1).
    forwarded_allow_ips: str | None = None

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _validate_origins(cls, value: list[str]) -> list[str]:
        return ["*" if origin.strip() == "*" else normalize_origin(origin) for origin in value]

    @field_validator("public_app_url")
    @classmethod
    def _validate_public_app_url(cls, value: str) -> str:
        value = value.strip()
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.query or parts.fragment:
            raise ValueError("public_app_url must be an absolute http(s) URL without query or fragment")
        return value.rstrip("/")

    @field_validator("storage_dir")
    @classmethod
    def _absolute_storage_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else BACKEND_DIR / value

    @field_validator("event_creation_key", mode="before")
    @classmethod
    def _blank_creation_key_disables(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None  # "GK_EVENT_CREATION_KEY=" means "no key required"
        return value

    @field_validator("event_creation_key")
    @classmethod
    def _strong_creation_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 12:
            raise ValueError("event_creation_key must be at least 12 characters")
        return value

    @field_validator("rate_limit_create_event", "rate_limit_upload", "rate_limit_read", "rate_limit_manage")
    @classmethod
    def _validate_rate_limits(cls, value: str) -> str:
        parse_many(value)
        return value

    @field_validator("ws_connect_rate", "ws_message_rate")
    @classmethod
    def _validate_single_rate_limit(cls, value: str) -> str:
        parse(value)
        return value

    @property
    def docs_enabled(self) -> bool:
        if self.enable_docs is not None:
            return self.enable_docs
        return self.environment != "production"

    @property
    def database_path(self) -> Path:
        return self.storage_dir / "db" / "gambarkita.sqlite3"

    @property
    def allowed_origins(self) -> list[str]:
        """CORS / WebSocket origins: the configured list plus the frontend's own origin."""
        origins = list(self.cors_origins)
        parts = urlsplit(self.public_app_url)
        app_origin = f"{parts.scheme}://{parts.netloc}".lower()
        if "*" not in origins and app_origin not in origins:
            origins.append(app_origin)
        return origins

    @property
    def max_request_bytes(self) -> int:
        return self.max_upload_bytes + MULTIPART_OVERHEAD_BYTES


@lru_cache
def get_settings() -> Settings:
    return Settings()
