"""Map internal records onto public API / WebSocket payloads."""

from app.core.config import Settings
from app.media import MEDIA_MOUNT_PATH
from app.models import EventRecord, PhotoRecord
from app.schemas import EventLinks, EventPublic, PhotoPublic
from app.services.storage import PHOTOS_DIR, THUMBNAILS_DIR


def event_links(settings: Settings, event_id: str) -> EventLinks:
    base = settings.public_app_url
    return EventLinks(upload=f"{base}/e/{event_id}", wall=f"{base}/e/{event_id}/wall")


def host_url(settings: Settings, event_id: str, host_token: str) -> str:
    # The token sits in the fragment so it is never sent to any server or logged.
    return f"{settings.public_app_url}/e/{event_id}/host#token={host_token}"


def present_event(event: EventRecord, photo_count: int, settings: Settings) -> EventPublic:
    return EventPublic(
        id=event.id,
        name=event.name,
        uploads_open=event.uploads_open,
        photo_count=photo_count,
        created_at=event.created_at,
        links=event_links(settings, event.id),
    )


def present_photo(photo: PhotoRecord) -> PhotoPublic:
    base = f"{MEDIA_MOUNT_PATH}/{photo.event_id}"
    return PhotoPublic(
        id=photo.id,
        event_id=photo.event_id,
        url=f"{base}/{PHOTOS_DIR}/{photo.filename}",
        thumbnail_url=f"{base}/{THUMBNAILS_DIR}/{photo.thumbnail_filename}",
        content_type=photo.content_type,
        width=photo.width,
        height=photo.height,
        size_bytes=photo.size_bytes,
        uploader_name=photo.uploader_name,
        caption=photo.caption,
        created_at=photo.created_at,
    )
