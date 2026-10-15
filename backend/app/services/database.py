"""SQLite metadata store for events, photos and pending file deletions.

SQLite runs in WAL mode, and every operation uses its own short-lived
connection inside a worker thread, so the event loop never blocks on disk I/O.
Writes that must be atomic (the photo quota checks plus insert) use
``BEGIN IMMEDIATE`` so concurrent uploads cannot exceed the per-event or
per-guest limit.

File lifecycles are journaled, so nothing ever has to guess from the disk
which files are garbage:

* an **upload intent** is committed before a photo's files are written and is
  removed in the same transaction that inserts the photo row. An intent that
  outlives its upload (crash, failed insert) names exactly the files to clean up;
* a **pending deletion** is committed in the same transaction that deletes a
  photo or event row. The files are removed afterwards (see ``StorageJanitor``),
  so the intent survives restarts and file locks.
"""

from __future__ import annotations

import functools
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

import anyio

from app.models import EventRecord, PhotoRecord, utcnow

T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    host_token_hash TEXT NOT NULL,
    uploads_open    INTEGER NOT NULL DEFAULT 1 CHECK (uploads_open IN (0, 1)),
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    id                 TEXT NOT NULL UNIQUE,
    event_id           TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    filename           TEXT NOT NULL,
    thumbnail_filename TEXT NOT NULL,
    content_type       TEXT NOT NULL,
    width              INTEGER NOT NULL,
    height             INTEGER NOT NULL,
    size_bytes         INTEGER NOT NULL,
    uploader_name      TEXT,
    caption            TEXT,
    created_at         TEXT NOT NULL,
    guest_key_hash     TEXT
);

CREATE INDEX IF NOT EXISTS ix_photos_event_seq ON photos (event_id, seq);

-- Uploads whose files may exist on disk without a photos row yet.
CREATE TABLE IF NOT EXISTS pending_uploads (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id           TEXT NOT NULL,
    filename           TEXT NOT NULL,
    thumbnail_filename TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

-- Files still to be removed from disk. filename IS NULL means the whole event directory.
CREATE TABLE IF NOT EXISTS pending_deletions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id           TEXT NOT NULL,
    filename           TEXT,
    thumbnail_filename TEXT,
    created_at         TEXT NOT NULL
);
"""

# Columns added after the first release: (table, column, definition). Databases
# created before then get them on startup; rows that predate a column hold NULL.
_ADDED_COLUMNS = (("photos", "guest_key_hash", "TEXT"),)

# Indexes over added columns, created once those columns exist.
_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_photos_event_guest ON photos (event_id, guest_key_hash);
"""


@dataclass(frozen=True, slots=True)
class PendingUpload:
    id: int
    event_id: str
    filename: str
    thumbnail_filename: str


@dataclass(frozen=True, slots=True)
class PendingDeletion:
    id: int
    event_id: str
    filename: str | None = None  # None: the whole event directory
    thumbnail_filename: str | None = None


class InsertPhotoResult(StrEnum):
    OK = "ok"
    EVENT_NOT_FOUND = "event_not_found"
    UPLOADS_CLOSED = "uploads_closed"
    LIMIT_REACHED = "limit_reached"
    GUEST_LIMIT_REACHED = "guest_limit_reached"


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # --- Plumbing -----------------------------------------------------------------------

    def initialize(self) -> bool:
        """Create the schema if needed. Returns True when the database is brand new."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            existed = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'"
            ).fetchone() is not None
            conn.executescript(_SCHEMA)
            _add_missing_columns(conn)
            conn.executescript(_INDEXES)
        _restrict_permissions(self.path)
        return not existed

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = NORMAL")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    async def _run(self, func: Callable[..., T], *args: object) -> T:
        return await anyio.to_thread.run_sync(functools.partial(func, *args))

    # --- Health -------------------------------------------------------------------------

    async def ping(self) -> bool:
        def _ping() -> bool:
            with self._connect() as conn:
                return conn.execute("SELECT 1").fetchone()[0] == 1

        return await self._run(_ping)

    # --- Events -------------------------------------------------------------------------

    async def insert_event(self, event: EventRecord) -> bool:
        """Insert ``event``; return ``False`` if the id already exists."""

        def _insert() -> bool:
            try:
                with self._transaction() as conn:
                    conn.execute(
                        "INSERT INTO events (id, name, host_token_hash, uploads_open, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (event.id, event.name, event.host_token_hash, int(event.uploads_open), _ts(event.created_at)),
                    )
                return True
            except sqlite3.IntegrityError:
                return False

        return await self._run(_insert)

    async def get_event(self, event_id: str) -> EventRecord | None:
        def _get() -> EventRecord | None:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            return _event_from_row(row) if row else None

        return await self._run(_get)

    async def get_event_summary(self, event_id: str) -> tuple[EventRecord, int] | None:
        """The event plus its photo count, read in one consistent snapshot."""

        def _get() -> tuple[EventRecord, int] | None:
            with self._transaction() as conn:
                row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
                if row is None:
                    return None
                count = conn.execute("SELECT COUNT(*) FROM photos WHERE event_id = ?", (event_id,)).fetchone()[0]
            return _event_from_row(row), count

        return await self._run(_get)

    async def update_event(
        self,
        event_id: str,
        *,
        name: str | None = None,
        uploads_open: bool | None = None,
        host_token_hash: str | None = None,
    ) -> EventRecord | None:
        def _update() -> EventRecord | None:
            with self._transaction(immediate=True) as conn:
                if name is not None:
                    conn.execute("UPDATE events SET name = ? WHERE id = ?", (name, event_id))
                if uploads_open is not None:
                    conn.execute("UPDATE events SET uploads_open = ? WHERE id = ?", (int(uploads_open), event_id))
                if host_token_hash is not None:
                    conn.execute("UPDATE events SET host_token_hash = ? WHERE id = ?", (host_token_hash, event_id))
                row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            return _event_from_row(row) if row else None

        return await self._run(_update)

    async def delete_event(self, event_id: str) -> PendingDeletion | None:
        """Delete the event (photo rows cascade) and record its directory for removal."""

        def _delete() -> PendingDeletion | None:
            with self._transaction(immediate=True) as conn:
                if conn.execute("DELETE FROM events WHERE id = ?", (event_id,)).rowcount == 0:
                    return None
                cursor = conn.execute(
                    "INSERT INTO pending_deletions (event_id, created_at) VALUES (?, ?)",
                    (event_id, _ts(utcnow())),
                )
            return PendingDeletion(id=cursor.lastrowid or 0, event_id=event_id)

        return await self._run(_delete)

    # --- Photos -------------------------------------------------------------------------

    async def count_photos(self, event_id: str) -> int:
        def _count() -> int:
            with self._connect() as conn:
                return conn.execute("SELECT COUNT(*) FROM photos WHERE event_id = ?", (event_id,)).fetchone()[0]

        return await self._run(_count)

    async def count_guest_photos(self, event_id: str, guest_key_hash: str) -> int:
        def _count() -> int:
            with self._connect() as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM photos WHERE event_id = ? AND guest_key_hash = ?", (event_id, guest_key_hash)
                ).fetchone()[0]

        return await self._run(_count)

    async def begin_upload(self, photo: PhotoRecord) -> PendingUpload:
        """Journal that ``photo``'s files are about to be written (before any file exists)."""

        def _begin() -> PendingUpload:
            with self._transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO pending_uploads (event_id, filename, thumbnail_filename, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (photo.event_id, photo.filename, photo.thumbnail_filename, _ts(utcnow())),
                )
            return PendingUpload(cursor.lastrowid or 0, photo.event_id, photo.filename, photo.thumbnail_filename)

        return await self._run(_begin)

    async def end_upload(self, upload_id: int) -> None:
        """Forget an upload intent once its files are gone (or were never written)."""

        def _end() -> None:
            with self._transaction() as conn:
                conn.execute("DELETE FROM pending_uploads WHERE id = ?", (upload_id,))

        await self._run(_end)

    async def list_stale_uploads(self) -> list[PendingUpload]:
        """Upload intents without a committed photo. Only meaningful when no upload
        is in flight, i.e. at startup while holding the storage lock."""

        def _list() -> list[PendingUpload]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT u.id, u.event_id, u.filename, u.thumbnail_filename FROM pending_uploads u "
                    "WHERE NOT EXISTS (SELECT 1 FROM photos p WHERE p.filename = u.filename) ORDER BY u.id"
                ).fetchall()
            return [PendingUpload(row[0], row[1], row[2], row[3]) for row in rows]

        return await self._run(_list)

    async def insert_photo(
        self,
        photo: PhotoRecord,
        *,
        max_photos: int,
        max_photos_per_guest: int = 0,
        upload_id: int | None = None,
    ) -> tuple[InsertPhotoResult, PhotoRecord | None]:
        """Atomically re-check the event state and quotas, then insert ``photo``.

        ``max_photos_per_guest`` (0: no limit) applies when ``photo`` has a guest key.
        On success the upload intent ``upload_id`` is removed in the same transaction.
        """

        def _insert() -> tuple[InsertPhotoResult, PhotoRecord | None]:
            with self._transaction(immediate=True) as conn:
                event = conn.execute("SELECT uploads_open FROM events WHERE id = ?", (photo.event_id,)).fetchone()
                if event is None:
                    return InsertPhotoResult.EVENT_NOT_FOUND, None
                if not event["uploads_open"]:
                    return InsertPhotoResult.UPLOADS_CLOSED, None
                count = conn.execute("SELECT COUNT(*) FROM photos WHERE event_id = ?", (photo.event_id,)).fetchone()[0]
                if count >= max_photos:
                    return InsertPhotoResult.LIMIT_REACHED, None
                if max_photos_per_guest and photo.guest_key_hash is not None:
                    guest_count = conn.execute(
                        "SELECT COUNT(*) FROM photos WHERE event_id = ? AND guest_key_hash = ?",
                        (photo.event_id, photo.guest_key_hash),
                    ).fetchone()[0]
                    if guest_count >= max_photos_per_guest:
                        return InsertPhotoResult.GUEST_LIMIT_REACHED, None
                # Stamp the time inside the write lock, so created_at order always
                # matches seq (insertion/pagination) order across concurrent uploads.
                record = replace(photo, created_at=utcnow())
                cursor = conn.execute(
                    "INSERT INTO photos (id, event_id, filename, thumbnail_filename, content_type, width, height, "
                    "size_bytes, uploader_name, caption, created_at, guest_key_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.event_id,
                        record.filename,
                        record.thumbnail_filename,
                        record.content_type,
                        record.width,
                        record.height,
                        record.size_bytes,
                        record.uploader_name,
                        record.caption,
                        _ts(record.created_at),
                        record.guest_key_hash,
                    ),
                )
                seq = cursor.lastrowid
                if upload_id is not None:
                    conn.execute("DELETE FROM pending_uploads WHERE id = ?", (upload_id,))
            return InsertPhotoResult.OK, replace(record, seq=seq)

        return await self._run(_insert)

    async def list_photos(self, event_id: str, *, limit: int, before_seq: int | None = None) -> list[PhotoRecord]:
        """Newest first. ``before_seq`` is the exclusive cursor from the previous page."""

        def _list() -> list[PhotoRecord]:
            with self._connect() as conn:
                if before_seq is None:
                    rows = conn.execute(
                        "SELECT * FROM photos WHERE event_id = ? ORDER BY seq DESC LIMIT ?", (event_id, limit)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM photos WHERE event_id = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
                        (event_id, before_seq, limit),
                    ).fetchall()
            return [_photo_from_row(row) for row in rows]

        return await self._run(_list)

    async def event_ids(self) -> set[str]:
        def _ids() -> set[str]:
            with self._connect() as conn:
                return {row[0] for row in conn.execute("SELECT id FROM events")}

        return await self._run(_ids)

    async def delete_photo(self, event_id: str, photo_id: str) -> tuple[PhotoRecord, PendingDeletion] | None:
        """Delete the photo, scoped to ``event_id`` (prevents cross-event IDOR), and
        record its files for removal."""

        def _delete() -> tuple[PhotoRecord, PendingDeletion] | None:
            with self._transaction(immediate=True) as conn:
                row = conn.execute(
                    "SELECT * FROM photos WHERE id = ? AND event_id = ?", (photo_id, event_id)
                ).fetchone()
                if row is None:
                    return None
                conn.execute("DELETE FROM photos WHERE seq = ?", (row["seq"],))
                cursor = conn.execute(
                    "INSERT INTO pending_deletions (event_id, filename, thumbnail_filename, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (event_id, row["filename"], row["thumbnail_filename"], _ts(utcnow())),
                )
            photo = _photo_from_row(row)
            deletion = PendingDeletion(
                id=cursor.lastrowid or 0,
                event_id=event_id,
                filename=photo.filename,
                thumbnail_filename=photo.thumbnail_filename,
            )
            return photo, deletion

        return await self._run(_delete)

    # --- Pending file deletions ---------------------------------------------------------

    async def list_pending_deletions(self) -> list[PendingDeletion]:
        def _list() -> list[PendingDeletion]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, event_id, filename, thumbnail_filename FROM pending_deletions ORDER BY id"
                ).fetchall()
            return [PendingDeletion(row[0], row[1], row[2], row[3]) for row in rows]

        return await self._run(_list)

    async def complete_deletion(self, deletion_id: int) -> None:
        def _complete() -> None:
            with self._transaction() as conn:
                conn.execute("DELETE FROM pending_deletions WHERE id = ?", (deletion_id,))

        await self._run(_complete)


# --- Helpers -------------------------------------------------------------------------------


def _restrict_permissions(database: Path) -> None:
    """Owner/group-only access on POSIX (no-op on Windows, which uses ACLs)."""
    if os.name != "posix":
        return
    for path, mode in ((database.parent, 0o750), (database, 0o640)):
        try:
            if path.exists() and path.stat().st_uid == os.getuid():
                os.chmod(path, mode)
        except OSError:
            pass


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# --- Row mapping ---------------------------------------------------------------------------


def _ts(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def _event_from_row(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        id=row["id"],
        name=row["name"],
        host_token_hash=row["host_token_hash"],
        uploads_open=bool(row["uploads_open"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _photo_from_row(row: sqlite3.Row) -> PhotoRecord:
    return PhotoRecord(
        seq=row["seq"],
        id=row["id"],
        event_id=row["event_id"],
        filename=row["filename"],
        thumbnail_filename=row["thumbnail_filename"],
        content_type=row["content_type"],
        width=row["width"],
        height=row["height"],
        size_bytes=row["size_bytes"],
        uploader_name=row["uploader_name"],
        caption=row["caption"],
        created_at=datetime.fromisoformat(row["created_at"]),
        guest_key_hash=row["guest_key_hash"],
    )
