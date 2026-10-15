"""Wires services together; one ``Services`` instance lives on ``app.state``."""

from dataclasses import dataclass

import anyio

from app.core.config import Settings
from app.services.database import Database
from app.services.events import EventService
from app.services.image_processing import MimeDetector
from app.services.instance_lock import StorageLock
from app.services.janitor import StorageJanitor
from app.services.photos import PhotoService
from app.services.realtime import ConnectionManager, WebSocketGuard
from app.services.storage import LocalStorage


@dataclass(slots=True)
class Services:
    settings: Settings
    db: Database
    storage: LocalStorage
    janitor: StorageJanitor
    realtime: ConnectionManager
    ws_guard: WebSocketGuard
    events: EventService
    photos: PhotoService
    lock: StorageLock

    async def startup(self) -> None:
        # One process per storage directory; refuse to start before touching any file.
        self.lock.acquire()
        try:
            database_is_new = await anyio.to_thread.run_sync(self.db.initialize)
            await self.janitor.resume()
            await self.janitor.housekeeping(database_is_new=database_is_new)
        except BaseException:
            self.lock.release()
            raise

    async def shutdown(self) -> None:
        try:
            await self.realtime.close_all()
            await self.janitor.stop()
        finally:
            self.lock.release()


def build_services(settings: Settings, storage: LocalStorage, db: Database, janitor: StorageJanitor) -> Services:
    realtime = ConnectionManager.from_settings(settings)
    events = EventService(settings=settings, db=db, storage=storage, janitor=janitor, realtime=realtime)
    photos = PhotoService(
        settings=settings,
        db=db,
        storage=storage,
        janitor=janitor,
        realtime=realtime,
        events=events,
        mime_detector=MimeDetector(settings.libmagic),
    )
    return Services(
        settings=settings,
        db=db,
        storage=storage,
        janitor=janitor,
        realtime=realtime,
        ws_guard=WebSocketGuard(settings),
        events=events,
        photos=photos,
        lock=StorageLock(settings.storage_dir),
    )
