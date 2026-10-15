"""Internal domain records (what the database stores)."""

from dataclasses import dataclass
from datetime import UTC, datetime


def utcnow() -> datetime:
    # Millisecond precision: exactly what the database stores, so a record
    # returned right after creation matches every later read of it.
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


@dataclass(frozen=True, slots=True)
class EventRecord:
    id: str
    name: str
    host_token_hash: str
    uploads_open: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PhotoRecord:
    id: str
    event_id: str
    filename: str
    thumbnail_filename: str
    content_type: str
    width: int
    height: int
    size_bytes: int
    uploader_name: str | None
    caption: str | None
    created_at: datetime
    seq: int | None = None  # monotonically increasing insertion order; used as the pagination cursor
    # Which guest sent it (see security.guest_key_hash). Never leaves the server.
    guest_key_hash: str | None = None
