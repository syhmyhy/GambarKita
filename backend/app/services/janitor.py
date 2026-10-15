"""Guaranteed removal of deleted media.

Deleting a photo or event commits a *pending deletion* row in the same
database transaction (see ``Database.delete_photo`` / ``delete_event``).
Removing the files can then take a while: on Windows a file cannot be deleted
while a client is still downloading it. The janitor:

1. **tombstones** pending media right away, so the media mount stops serving
   it even while the file still exists on disk;
2. **retries** with capped exponential backoff until the files are gone, then
   marks the deletion complete;
3. **resumes** pending deletions after a restart, since they are durable;
4. at startup, removes the files of **stale upload intents** (uploads that
   never committed), and only those. It never guesses from the disk: files or
   directories without a database record are left in place, so a restored or
   replaced database can never cause photos to be deleted. Event directories
   without a record (or every directory, when the database is brand new) are
   tombstoned instead, meaning kept on disk but not served.
"""

from __future__ import annotations

import asyncio

import anyio

from app.core.errors import UnsafePathError
from app.core.logging import get_logger
from app.services.database import Database, PendingDeletion
from app.services.storage import LocalStorage

logger = get_logger("janitor")

_INITIAL_RETRY_INTERVAL = 5.0
_MAX_RETRY_INTERVAL = 300.0


class StorageJanitor:
    def __init__(
        self,
        storage: LocalStorage,
        db: Database,
        *,
        retry_interval: float = _INITIAL_RETRY_INTERVAL,
    ) -> None:
        self._storage = storage
        self._db = db
        self._retry_interval = retry_interval
        self._pending: dict[int, PendingDeletion] = {}
        self._blocked_events: set[str] = set()
        self._blocked_files: set[tuple[str, str]] = set()  # (event_id, filename)
        self._task: asyncio.Task[None] | None = None

    # --- Queries (called by the media mount on every request; must be cheap) ------------

    def is_blocked(self, event_id: str, filename: str) -> bool:
        return event_id in self._blocked_events or (event_id, filename) in self._blocked_files

    @property
    def pending(self) -> int:
        return len(self._pending)

    # --- Removal ------------------------------------------------------------------------

    async def remove(self, deletion: PendingDeletion) -> None:
        """Tombstone the media of ``deletion`` and try to remove it now; retry later if locked."""
        self._block(deletion)
        if not await self._attempt(deletion):
            self._pending[deletion.id] = deletion
            self._ensure_retrying()

    async def resume(self) -> None:
        """Pick up deletions left pending by a previous run."""
        for deletion in await self._db.list_pending_deletions():
            await self.remove(deletion)
        if self._pending:
            logger.warning("%d deletions are still waiting for files to be released", len(self._pending))

    async def housekeeping(self, *, database_is_new: bool) -> None:
        """Startup housekeeping. Never raises: it must not prevent the server from starting."""
        try:
            if not database_is_new:
                await self._clean_stale_uploads()
            known = None if database_is_new else await self._db.event_ids()
            report = await anyio.to_thread.run_sync(self._storage.scan_event_dirs, known)
            if report.unknown_events:
                # Kept on disk (they may belong to a restored database) but never served.
                self._blocked_events.update(report.unknown_events)
                logger.warning(
                    "%d event directories have no database record; left on disk but not served: %s%s",
                    len(report.unknown_events),
                    ", ".join(report.unknown_events[:10]),
                    " (the database is new: restore the original to bring them back)" if database_is_new else "",
                )
            if report.links or report.unexpected:
                logger.warning(
                    "Ignored unexpected storage entries (links: %s, other: %s)",
                    report.links[:10],
                    report.unexpected[:10],
                )
        except Exception:  # noqa: BLE001 - housekeeping only
            logger.exception("Startup storage housekeeping failed; continuing startup")

    async def _clean_stale_uploads(self) -> None:
        removed = 0
        for upload in await self._db.list_stale_uploads():
            try:
                done = await anyio.to_thread.run_sync(
                    self._storage.delete_photo, upload.event_id, upload.filename, upload.thumbnail_filename
                )
            except Exception:  # noqa: BLE001 - retried on the next startup
                logger.exception("Could not clean up the files of interrupted upload %d", upload.id)
                continue
            if done:
                await self._db.end_upload(upload.id)
                removed += 1
        if removed:
            logger.info("Removed the files of %d interrupted uploads", removed)

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.wait({self._task})

    # --- Internals ------------------------------------------------------------------------

    def _block(self, deletion: PendingDeletion) -> None:
        if deletion.filename is None:
            self._blocked_events.add(deletion.event_id)
            return
        self._blocked_files.add((deletion.event_id, deletion.filename))
        if deletion.thumbnail_filename:
            self._blocked_files.add((deletion.event_id, deletion.thumbnail_filename))

    def _unblock(self, deletion: PendingDeletion) -> None:
        if deletion.filename is None:
            self._blocked_events.discard(deletion.event_id)
            self._blocked_files = {item for item in self._blocked_files if item[0] != deletion.event_id}
            return
        self._blocked_files.discard((deletion.event_id, deletion.filename))
        if deletion.thumbnail_filename:
            self._blocked_files.discard((deletion.event_id, deletion.thumbnail_filename))

    async def _attempt(self, deletion: PendingDeletion) -> bool:
        """Try once. True when the deletion is finished (or can never succeed)."""
        try:
            if deletion.filename is None:
                done = await anyio.to_thread.run_sync(self._storage.delete_event, deletion.event_id)
            else:
                done = await anyio.to_thread.run_sync(
                    self._storage.delete_photo,
                    deletion.event_id,
                    deletion.filename,
                    deletion.thumbnail_filename or deletion.filename,
                )
        except UnsafePathError:
            logger.error("Dropping pending deletion %d with an invalid path", deletion.id)
            done = True
        except OSError as exc:
            logger.info("Deletion %d not possible yet: %s", deletion.id, exc)
            return False
        except Exception:  # noqa: BLE001 - one bad entry must never stop resume() or the retry loop
            logger.exception("Unexpected error while processing deletion %d", deletion.id)
            return False
        if not done:
            return False
        try:
            await self._db.complete_deletion(deletion.id)
        except Exception:  # noqa: BLE001 - files are gone; the row is retried on next startup
            logger.exception("Could not mark deletion %d complete", deletion.id)
        self._unblock(deletion)
        return True

    def _ensure_retrying(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._retry_loop(), name="storage-janitor")

    async def _retry_loop(self) -> None:
        interval = self._retry_interval
        while self._pending:
            await asyncio.sleep(interval)
            progressed = False
            for deletion_id, deletion in tuple(self._pending.items()):
                if await self._attempt(deletion):
                    self._pending.pop(deletion_id, None)
                    progressed = True
            interval = self._retry_interval if progressed else min(interval * 2, _MAX_RETRY_INTERVAL)
        logger.info("All deferred deletions completed")
