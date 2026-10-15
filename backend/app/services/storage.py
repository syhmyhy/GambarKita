"""Local-disk photo storage confined to ``<storage_dir>/events``.

Layout::

    storage/
      events/<event_id>/photos/<photo_id>.<jpg|png|webp>   sanitised display copy
      events/<event_id>/thumbs/<photo_id>.webp             grid thumbnail
      db/gambarkita.sqlite3                                metadata (never web-served)

Path-traversal defence in depth:

* every path segment must match a strict allow-list pattern (no separators,
  no ``..``, no drive letters, no NUL, no Windows device names);
* the joined path is fully resolved and must equal its lexical form, so no
  symlink or junction is ever traversed, and it must stay strictly inside the
  resolved events root;
* files are created with ``O_CREAT | O_EXCL`` (never overwrite) and
  ``O_NOFOLLOW`` where the platform supports it.

All methods block on filesystem I/O. Call them from a worker thread.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import EventNotFoundError, UnsafePathError
from app.core.logging import get_logger
from app.core.security import is_valid_event_id

logger = get_logger("storage")

PHOTOS_DIR = "photos"
THUMBNAILS_DIR = "thumbs"
MEDIA_DIRS = (PHOTOS_DIR, THUMBNAILS_DIR)

_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:\.[A-Za-z0-9]{1,8})?")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)
_DIR_MODE = 0o750
_FILE_MODE = 0o640
_CREATE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
# On Windows a file cannot be removed while another handle (e.g. an in-flight
# download) has it open. Retry briefly; the janitor keeps retrying afterwards.
_DELETE_ATTEMPTS = 3
_DELETE_RETRY_DELAY = 0.1


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._events_root: Path | None = None

    @property
    def events_root(self) -> Path:
        if self._events_root is None:
            raise RuntimeError("LocalStorage.ensure_layout() must be called first")
        return self._events_root

    def ensure_layout(self) -> Path:
        events = self._root / "events"
        events.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        self._events_root = events.resolve(strict=True)
        return self._events_root

    # --- Path handling ----------------------------------------------------------------

    def resolve(self, *segments: str) -> Path:
        """Join validated ``segments`` under the events root, refusing any escape or link."""
        if not segments:
            raise UnsafePathError()
        for segment in segments:
            if (
                not isinstance(segment, str)
                or _SEGMENT_RE.fullmatch(segment) is None
                or segment.split(".", 1)[0].lower() in _WINDOWS_RESERVED_NAMES
            ):
                raise UnsafePathError()
        root = self.events_root
        lexical = root.joinpath(*segments)
        try:
            resolved = lexical.resolve(strict=False)
        except (OSError, RuntimeError):  # e.g. a planted symlink/junction loop
            raise UnsafePathError() from None
        if resolved == root or not resolved.is_relative_to(root) or resolved != lexical:
            raise UnsafePathError()
        return resolved

    def event_dir(self, event_id: str) -> Path:
        if not is_valid_event_id(event_id):
            raise UnsafePathError()
        return self.resolve(event_id)

    def photo_path(self, event_id: str, filename: str) -> Path:
        self.event_dir(event_id)
        return self.resolve(event_id, PHOTOS_DIR, filename)

    def thumbnail_path(self, event_id: str, filename: str) -> Path:
        self.event_dir(event_id)
        return self.resolve(event_id, THUMBNAILS_DIR, filename)

    # --- Writes -----------------------------------------------------------------------

    def create_event_dirs(self, event_id: str) -> None:
        self.event_dir(event_id)
        for sub_dir in MEDIA_DIRS:
            self.resolve(event_id, sub_dir).mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)

    def save_photo(
        self,
        event_id: str,
        filename: str,
        data: bytes,
        thumbnail_filename: str,
        thumbnail_data: bytes,
    ) -> None:
        photo = self.photo_path(event_id, filename)
        thumbnail = self.thumbnail_path(event_id, thumbnail_filename)
        # Directories are created with the event; if they are gone the event was deleted.
        if not photo.parent.is_dir() or not thumbnail.parent.is_dir():
            raise EventNotFoundError()
        _write_exclusive(photo, data)
        try:
            _write_exclusive(thumbnail, thumbnail_data)
        except BaseException:
            _unlink(photo)
            raise

    # --- Deletes (never raise for "file in use"; report success instead) ---------------

    def delete_photo(self, event_id: str, filename: str, thumbnail_filename: str) -> bool:
        """Remove a photo's files. Returns False if any file is still locked (Windows)."""
        photo_gone = _unlink(self.photo_path(event_id, filename))
        thumbnail_gone = _unlink(self.thumbnail_path(event_id, thumbnail_filename))
        return photo_gone and thumbnail_gone

    def delete_event(self, event_id: str) -> bool:
        """Remove an event directory tree. Returns False if something is still locked."""
        path = self._lexical_event_dir(event_id)
        # Never follow a link planted in place of the event directory.
        if path.is_symlink():
            path.unlink()
            return True
        if path.is_junction():
            path.rmdir()  # removes the junction itself, not its target
            return True
        if not path.exists():
            return True
        if not path.is_dir():
            return _unlink(path)
        failures: list[str] = []

        def _on_error(function: object, failed_path: str, exc: BaseException) -> None:
            if isinstance(exc, FileNotFoundError):
                return
            if isinstance(exc, PermissionError) and callable(function):
                try:  # read-only file (e.g. restored from a backup): clear the flag and retry once
                    os.chmod(failed_path, stat.S_IWRITE | stat.S_IREAD)
                    function(failed_path)
                    return
                except OSError:
                    pass
            failures.append(failed_path)

        for attempt in range(_DELETE_ATTEMPTS):
            failures.clear()
            shutil.rmtree(path, onexc=_on_error)
            if not failures and not path.exists():
                return True
            time.sleep(_DELETE_RETRY_DELAY * (attempt + 1))
        logger.warning("Event directory %s not fully deleted yet (%d paths in use)", event_id, len(failures))
        return False

    def _lexical_event_dir(self, event_id: str) -> Path:
        if not is_valid_event_id(event_id):
            raise UnsafePathError()
        return self.events_root / event_id

    # --- Maintenance --------------------------------------------------------------------

    def scan_event_dirs(self, known_event_ids: set[str] | None) -> SweepReport:
        """Classify the top-level entries of the events root. Never deletes anything.

        With ``known_event_ids=None`` (no trustworthy database), every event
        directory is reported as unknown. Links and junctions are reported, never
        followed.
        """
        report = SweepReport()
        for entry in os.scandir(self.events_root):
            try:
                if not is_valid_event_id(entry.name):
                    report.unexpected.append(entry.name)
                elif entry.is_symlink() or Path(entry.path).is_junction():
                    report.links.append(entry.name)
                elif not entry.is_dir(follow_symlinks=False):
                    report.unexpected.append(entry.name)
                elif known_event_ids is None or entry.name not in known_event_ids:
                    report.unknown_events.append(entry.name)
            except OSError as exc:
                logger.warning("Skipping storage entry %r: %s", entry.name, exc)
        return report

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.events_root).free


def _write_exclusive(path: Path, data: bytes) -> None:
    fd = os.open(path, _CREATE_FLAGS, _FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def _unlink(path: Path) -> bool:
    for attempt in range(_DELETE_ATTEMPTS):
        try:
            path.unlink(missing_ok=True)
            return True
        except PermissionError:
            if attempt == 0:
                # Read-only files (e.g. restored from a backup) need the flag cleared first.
                with contextlib.suppress(OSError):
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                continue
            time.sleep(_DELETE_RETRY_DELAY * attempt)
    logger.info("File %s is in use; deletion deferred", path.name)
    return False


@dataclass(slots=True)
class SweepReport:
    unknown_events: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
