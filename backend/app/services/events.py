"""Event lifecycle: creation, lookup, host authorisation, updates and deletion."""

from __future__ import annotations

import asyncio

import anyio

from app.core.config import Settings
from app.core.errors import AppError, EventNotFoundError, UnauthorizedError
from app.core.logging import get_logger
from app.core.security import (
    constant_time_equals,
    hash_token,
    is_valid_event_id,
    new_event_id,
    new_host_token,
    verify_token,
)
from app.models import EventRecord, utcnow
from app.presenters import present_event
from app.services.database import Database
from app.services.janitor import StorageJanitor
from app.services.realtime import CLOSE_EVENT_DELETED, ConnectionManager
from app.services.storage import LocalStorage

logger = get_logger("events")

_MAX_ID_ATTEMPTS = 5


class EventService:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        storage: LocalStorage,
        janitor: StorageJanitor,
        realtime: ConnectionManager,
    ) -> None:
        self._settings = settings
        self._db = db
        self._storage = storage
        self._janitor = janitor
        self._realtime = realtime

    def check_creation_key(self, provided: str | None) -> None:
        expected = self._settings.event_creation_key
        if expected is None:
            return
        if not provided or not constant_time_equals(provided, expected.get_secret_value()):
            raise UnauthorizedError(
                "A valid event creation key is required to create events.",
                details={"header": "X-Event-Creation-Key"},
            )

    async def create(self, name: str) -> tuple[EventRecord, str]:
        """Create an event; returns the record and the one-time plaintext host token."""
        # Run to completion even if the request is cancelled, so the DB row and
        # its directories never end up half-created.
        return await asyncio.shield(asyncio.ensure_future(self._create(name)))

    async def _create(self, name: str) -> tuple[EventRecord, str]:
        host_token = new_host_token()
        for _ in range(_MAX_ID_ATTEMPTS):
            event = EventRecord(
                id=new_event_id(),
                name=name,
                host_token_hash=hash_token(host_token),
                uploads_open=True,
                created_at=utcnow(),
            )
            if await self._db.insert_event(event):
                break
        else:
            raise AppError("Could not allocate a unique event id. Please retry.")

        try:
            await anyio.to_thread.run_sync(self._storage.create_event_dirs, event.id)
        except Exception:
            deletion = await self._db.delete_event(event.id)
            if deletion is not None:
                await self._janitor.remove(deletion)
            raise
        logger.info("Event %s created", event.id)
        return event, host_token

    async def get(self, event_id: str) -> EventRecord:
        if not is_valid_event_id(event_id):
            raise EventNotFoundError()
        event = await self._db.get_event(event_id)
        if event is None:
            raise EventNotFoundError()
        return event

    async def authorize_host(self, event_id: str, host_token: str) -> EventRecord:
        event = await self.get(event_id)
        if not verify_token(host_token, event.host_token_hash):
            raise UnauthorizedError("The host token is not valid for this event.")
        return event

    async def update(
        self, event_id: str, host_token: str, *, name: str | None, uploads_open: bool | None
    ) -> tuple[EventRecord, int]:
        await self.authorize_host(event_id, host_token)
        updated = await self._db.update_event(event_id, name=name, uploads_open=uploads_open)
        if updated is None:
            raise EventNotFoundError()
        photo_count = await self._db.count_photos(event_id)
        self._realtime.publish(
            event_id,
            {
                "type": "event.updated",
                "event_id": event_id,
                "data": present_event(updated, photo_count, self._settings).model_dump(mode="json"),
            },
        )
        return updated, photo_count

    async def rotate_host_token(self, event_id: str, creation_key: str | None) -> tuple[EventRecord, str]:
        """Replace the event's host token; returns the record and the new one-time plaintext token.

        This is the recovery path for a host who lost their link. It is only
        available when the server has an event creation key, because that key is
        the only thing standing between "anyone" and taking over any event.
        """
        if self._settings.event_creation_key is None:
            raise UnauthorizedError(
                "Host token rotation is disabled: this server has no event creation key configured.",
                details={"header": "X-Event-Creation-Key"},
            )
        self.check_creation_key(creation_key)
        await self.get(event_id)  # 404 before issuing anything
        host_token = new_host_token()
        updated = await self._db.update_event(event_id, host_token_hash=hash_token(host_token))
        if updated is None:
            raise EventNotFoundError()
        logger.info("Event %s host token rotated", event_id)
        return updated, host_token

    async def delete(self, event_id: str, host_token: str) -> None:
        await self.authorize_host(event_id, host_token)
        deletion = await self._db.delete_event(event_id)  # cascades to photo rows
        if deletion is None:
            raise EventNotFoundError()
        # Notify and disconnect live walls first; the janitor stops serving the
        # media immediately even if file removal has to wait for a lock.
        self._realtime.publish(event_id, {"type": "event.deleted", "event_id": event_id})
        self._realtime.close_event(event_id, CLOSE_EVENT_DELETED, "Event deleted")
        await self._janitor.remove(deletion)
        logger.info("Event %s deleted", event_id)
