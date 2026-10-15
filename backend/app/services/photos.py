"""Photo upload pipeline, listing and moderation."""

from __future__ import annotations

import asyncio

import anyio
from starlette.datastructures import UploadFile

from app.core.config import Settings
from app.core.errors import (
    EventNotFoundError,
    EventPhotoLimitError,
    FileTooLargeError,
    GuestPhotoLimitError,
    InsufficientStorageError,
    PhotoNotFoundError,
    UploadsClosedError,
)
from app.core.logging import get_logger
from app.core.security import is_valid_photo_id, new_photo_id
from app.models import EventRecord, PhotoRecord, utcnow
from app.presenters import present_photo
from app.services.admission import ProcessingBudget
from app.services.database import Database, InsertPhotoResult
from app.services.events import EventService
from app.services.image_processing import (
    THUMBNAIL_FORMAT,
    ImagePolicy,
    MimeDetector,
    ProcessedImage,
    inspect_image,
    process_image,
)
from app.services.janitor import StorageJanitor
from app.services.realtime import ConnectionManager
from app.services.storage import LocalStorage

logger = get_logger("photos")

_READ_CHUNK_BYTES = 1024 * 1024

_INSERT_ERRORS = {
    InsertPhotoResult.EVENT_NOT_FOUND: EventNotFoundError,
    InsertPhotoResult.UPLOADS_CLOSED: UploadsClosedError,
    InsertPhotoResult.LIMIT_REACHED: EventPhotoLimitError,
}


def format_size(num_bytes: int) -> str:
    """Human-readable size for messages shown to guests (e.g. "10 MB", "48.8 KB")."""
    for unit, factor in (("MB", 1024 * 1024), ("KB", 1024)):
        if num_bytes >= factor:
            value = f"{num_bytes / factor:.1f}".removesuffix(".0")
            return f"{value} {unit}"
    return f"{num_bytes} bytes"


class PhotoService:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        storage: LocalStorage,
        janitor: StorageJanitor,
        realtime: ConnectionManager,
        events: EventService,
        mime_detector: MimeDetector,
    ) -> None:
        self._settings = settings
        self._db = db
        self._storage = storage
        self._janitor = janitor
        self._realtime = realtime
        self._events = events
        self._mime_detector = mime_detector
        self._budget = ProcessingBudget(
            max_pixels=settings.image_processing_pixel_budget,
            max_jobs=settings.image_processing_concurrency,
            queue_timeout=settings.image_processing_queue_timeout,
        )
        self._policy = ImagePolicy(
            max_pixels=settings.max_image_pixels,
            max_dimension=settings.max_image_dimension,
            max_stored_dimension=settings.max_stored_dimension,
            thumbnail_size=settings.thumbnail_size,
            max_lossless_pixels=settings.max_lossless_image_pixels,
            max_stored_bytes=settings.max_stored_bytes,
        )
        self._background: set[asyncio.Future[object]] = set()

    # --- Upload -------------------------------------------------------------------------

    @property
    def max_photos_per_guest(self) -> int:
        """0 when guests have no individual limit."""
        return self._settings.max_photos_per_guest

    async def guest_photo_count(self, event_id: str, guest_key_hash: str) -> int:
        await self._events.get(event_id)
        return await self._db.count_guest_photos(event_id, guest_key_hash)

    async def ensure_can_upload(self, event_id: str, guest_key_hash: str | None = None) -> EventRecord:
        """Cheap pre-flight checks, run before the request body is parsed."""
        event = await self._events.get(event_id)
        if not event.uploads_open:
            raise UploadsClosedError()
        if await self._db.count_photos(event_id) >= self._settings.max_photos_per_event:
            raise EventPhotoLimitError()
        guest_limit = self.max_photos_per_guest
        if guest_limit and guest_key_hash is not None:
            if await self._db.count_guest_photos(event_id, guest_key_hash) >= guest_limit:
                raise GuestPhotoLimitError(guest_limit)
        free_bytes = await anyio.to_thread.run_sync(self._storage.free_bytes)
        worst_case_write = 2 * self._settings.max_stored_bytes
        if free_bytes - worst_case_write < self._settings.min_free_disk_bytes:
            logger.error("Refusing upload: free disk space below threshold (%d bytes free)", free_bytes)
            raise InsufficientStorageError()
        return event

    async def create_from_upload(
        self,
        event: EventRecord,
        upload: UploadFile,
        *,
        uploader_name: str | None,
        caption: str | None,
        guest_key_hash: str | None = None,
    ) -> PhotoRecord:
        data = await self._read_limited(upload)
        processed = await self._process(data)
        del data

        photo_id = new_photo_id()
        photo = PhotoRecord(
            id=photo_id,
            event_id=event.id,
            # The client filename is never used: names come from a CSPRNG id plus
            # the extension of the format actually stored.
            filename=f"{photo_id}.{processed.format.extension}",
            thumbnail_filename=f"{photo_id}.{THUMBNAIL_FORMAT.extension}",
            content_type=processed.format.mime,
            width=processed.original.width,
            height=processed.original.height,
            size_bytes=len(processed.original.data),
            uploader_name=uploader_name,
            caption=caption,
            created_at=utcnow(),
            guest_key_hash=guest_key_hash,
        )

        # Persist in a task of its own: if this request is cancelled (client
        # gone, shutdown timeout), the write and insert still run to a known
        # outcome, and files are only cleaned up when no row was committed.
        persist = asyncio.ensure_future(self._persist(photo, processed))
        try:
            result, stored = await asyncio.shield(persist)
        except asyncio.CancelledError:
            self._background.add(persist)
            persist.add_done_callback(self._finish_orphaned_persist)
            raise
        if result is InsertPhotoResult.GUEST_LIMIT_REACHED:
            raise GuestPhotoLimitError(self.max_photos_per_guest)
        if result is not InsertPhotoResult.OK or stored is None:
            raise _INSERT_ERRORS.get(result, EventNotFoundError)()

        self._announce(stored)
        logger.info("Photo %s stored for event %s (%d bytes)", stored.id, event.id, stored.size_bytes)
        return stored

    async def _persist(
        self, photo: PhotoRecord, processed: ProcessedImage
    ) -> tuple[InsertPhotoResult, PhotoRecord | None]:
        # Journal the upload before any file exists. The intent is removed in
        # the same transaction as the photo insert; if the process dies in
        # between, the next startup deletes exactly these files, and nothing
        # else has to be guessed from the disk.
        upload = await self._db.begin_upload(photo)
        try:
            await anyio.to_thread.run_sync(
                self._storage.save_photo,
                photo.event_id,
                photo.filename,
                processed.original.data,
                photo.thumbnail_filename,
                processed.thumbnail.data,
            )
            result, stored = await self._db.insert_photo(
                photo,
                max_photos=self._settings.max_photos_per_event,
                max_photos_per_guest=self.max_photos_per_guest,
                upload_id=upload.id,
            )
        except Exception:
            await self._abandon_upload(photo, upload.id)
            raise
        # Deliberately no cleanup on cancellation: the insert may still commit in
        # its worker thread. If it did not, the intent row stays and startup cleans up.
        if result is not InsertPhotoResult.OK:
            await self._abandon_upload(photo, upload.id)
        return result, stored

    async def _abandon_upload(self, photo: PhotoRecord, upload_id: int) -> None:
        """Remove the files of an upload that will not be committed, then its intent."""
        try:
            done = await anyio.to_thread.run_sync(
                self._storage.delete_photo, photo.event_id, photo.filename, photo.thumbnail_filename
            )
            if done:
                await self._db.end_upload(upload_id)
        except Exception:  # noqa: BLE001 - the intent row remains; startup cleans up
            logger.exception("Failed to clean up files for photo %s", photo.id)

    def _finish_orphaned_persist(self, task: asyncio.Future[object]) -> None:
        """Completion of a persist whose request was cancelled: announce what was committed."""
        self._background.discard(task)
        if task.cancelled() or task.exception() is not None:
            return
        result, stored = task.result()  # type: ignore[misc]
        if result is InsertPhotoResult.OK and stored is not None:
            self._announce(stored)

    def _announce(self, photo: PhotoRecord) -> None:
        self._realtime.publish(
            photo.event_id,
            {"type": "photo.created", "event_id": photo.event_id, "data": present_photo(photo).model_dump(mode="json")},
        )

    async def _read_limited(self, upload: UploadFile) -> bytes:
        limit = self._settings.max_upload_bytes
        too_large = FileTooLargeError(
            f"Photos must be {format_size(limit)} or smaller.", details={"max_bytes": limit}
        )
        if upload.size is not None and upload.size > limit:
            raise too_large
        chunks: list[bytes] = []
        total = 0
        while chunk := await upload.read(_READ_CHUNK_BYTES):
            total += len(chunk)
            if total > limit:
                raise too_large
            chunks.append(chunk)
        return b"".join(chunks)

    async def _process(self, data: bytes) -> ProcessedImage:
        # Cheap header inspection first (no pixel buffers), then admission
        # weighted by the pixel count that decoding will actually cost.
        info = await anyio.to_thread.run_sync(inspect_image, data, self._policy, self._mime_detector)
        weight = await self._budget.acquire(info.pixels)
        # A worker thread cannot be interrupted, so the reservation is released
        # when the *thread* finishes, even if this request is cancelled meanwhile.
        # Otherwise aborted requests would let decoding run beyond the budget.
        work = asyncio.ensure_future(anyio.to_thread.run_sync(process_image, data, self._policy, None, info))
        self._background.add(work)

        def _finished(task: asyncio.Future[object]) -> None:
            self._budget.release(weight)
            self._background.discard(task)
            if not task.cancelled():
                task.exception()  # mark retrieved; the awaiting request reports it

        work.add_done_callback(_finished)
        return await asyncio.shield(work)  # type: ignore[return-value]

    # --- Listing ------------------------------------------------------------------------

    async def list(self, event_id: str, *, limit: int, cursor: str | None) -> tuple[list[PhotoRecord], str | None]:
        await self._events.get(event_id)
        before_seq = int(cursor) if cursor else None
        rows = await self._db.list_photos(event_id, limit=limit + 1, before_seq=before_seq)
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = str(rows[-1].seq) if has_more and rows else None
        return rows, next_cursor

    # --- Moderation ---------------------------------------------------------------------

    async def delete(self, event_id: str, photo_id: str, host_token: str) -> None:
        await self._events.authorize_host(event_id, host_token)
        if not is_valid_photo_id(photo_id):
            raise PhotoNotFoundError()
        deleted = await self._db.delete_photo(event_id, photo_id)
        if deleted is None:
            raise PhotoNotFoundError()
        _, deletion = deleted
        # Tell the walls first. File removal can be delayed (Windows file locks),
        # but the janitor stops serving the photo immediately, and the pending
        # deletion committed with the row survives restarts.
        self._realtime.publish(event_id, {"type": "photo.deleted", "event_id": event_id, "data": {"id": photo_id}})
        await self._janitor.remove(deletion)
        logger.info("Photo %s deleted from event %s", photo_id, event_id)
