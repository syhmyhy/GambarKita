import re
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from starlette.datastructures import FormData, UploadFile

from app.api.deps import EventIdDep, GuestKeyHashDep, HostTokenDep, PhotoIdDep, ServicesDep
from app.api.responses import error_responses
from app.core.errors import UnsupportedMediaTypeError, ValidationFailedError
from app.core.rate_limit import MANAGE_LIMIT, READ_LIMIT, UPLOAD_LIMIT, limiter
from app.core.text import clean_text
from app.presenters import present_photo
from app.schemas import MAX_CAPTION_LENGTH, MAX_UPLOADER_NAME_LENGTH, PhotoPage, PhotoPublic

router = APIRouter(prefix="/events/{event_id}/photos", tags=["photos"])

# Used by BodySizeLimitMiddleware to grant uploads the larger body budget.
UPLOAD_PATH_RE = re.compile(r"/api/events/[^/]+/photos/?")

_FILE_FIELD = "file"
_TEXT_FIELDS = {"uploader_name": MAX_UPLOADER_NAME_LENGTH, "caption": MAX_CAPTION_LENGTH}
_MAX_TEXT_FIELD_BYTES = 4 * 1024

_UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [_FILE_FIELD],
                    "properties": {
                        _FILE_FIELD: {"type": "string", "format": "binary", "description": "JPEG, PNG or WebP"},
                        "uploader_name": {"type": "string", "maxLength": MAX_UPLOADER_NAME_LENGTH},
                        "caption": {"type": "string", "maxLength": MAX_CAPTION_LENGTH},
                    },
                }
            }
        },
    }
}


def _field_error(field: str, message: str) -> ValidationFailedError:
    return ValidationFailedError(details=[{"location": "form", "field": field, "message": message}])


def _validate_form_fields(form: FormData) -> None:
    for key in form.keys():
        if key != _FILE_FIELD and key not in _TEXT_FIELDS:
            raise _field_error(key, "Unexpected form field.")
        if len(form.getlist(key)) > 1:
            raise _field_error(key, "Field must be provided at most once.")


def _optional_text(form: FormData, field: str) -> str | None:
    value = form.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _field_error(field, "Must be text.")
    cleaned = clean_text(value)
    max_length = _TEXT_FIELDS[field]
    if len(cleaned) > max_length:
        raise _field_error(field, f"Must be at most {max_length} characters.")
    return cleaned or None


@router.get("", response_model=PhotoPage, responses=error_responses(404, 422, 429), summary="List photos")
@limiter.limit(READ_LIMIT)
async def list_photos(
    request: Request,
    event_id: EventIdDep,
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(pattern=r"^[0-9]{1,18}$")] = None,
) -> PhotoPage:
    """Newest first. Follow ``next_cursor`` to page backwards in time."""
    photos, next_cursor = await services.photos.list(event_id, limit=limit, cursor=cursor)
    return PhotoPage(items=[present_photo(photo) for photo in photos], next_cursor=next_cursor)


@router.post(
    "",
    status_code=201,
    response_model=PhotoPublic,
    openapi_extra=_UPLOAD_OPENAPI,
    responses=error_responses(400, 403, 404, 409, 413, 415, 422, 429, 503, 507),
    summary="Upload a photo",
)
@limiter.limit(UPLOAD_LIMIT)
async def upload_photo(
    request: Request, event_id: EventIdDep, guest_key_hash: GuestKeyHashDep, services: ServicesDep
) -> PhotoPublic:
    """Upload one image as ``multipart/form-data`` (field ``file``).

    The body is parsed manually (not with ``UploadFile = File()``) so the cheap
    event checks run first and the parser is capped at one file and a few small
    text fields.

    While the server limits photos per guest, photos sent with an ``X-Guest-Key``
    header count towards that guest's allowance. Without the limit the key is
    ignored and nothing about the guest is stored.
    """
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise UnsupportedMediaTypeError("Uploads must be sent as multipart/form-data.")
    # A missing key isn't refused: pages opened before the limit existed don't send
    # one, and refusing them would gain nothing, since any client can make up a key.
    if not services.photos.max_photos_per_guest:
        guest_key_hash = None

    event = await services.photos.ensure_can_upload(event_id, guest_key_hash)

    async with request.form(
        max_files=1, max_fields=len(_TEXT_FIELDS), max_part_size=_MAX_TEXT_FIELD_BYTES
    ) as form:
        _validate_form_fields(form)
        upload = form.get(_FILE_FIELD)
        if not isinstance(upload, UploadFile):
            raise _field_error(_FILE_FIELD, "A photo file is required.")
        photo = await services.photos.create_from_upload(
            event,
            upload,
            uploader_name=_optional_text(form, "uploader_name"),
            caption=_optional_text(form, "caption"),
            guest_key_hash=guest_key_hash,
        )
    return present_photo(photo)


@router.delete(
    "/{photo_id}",
    status_code=204,
    responses=error_responses(401, 404, 429),
    summary="Delete a photo (host only)",
)
@limiter.limit(MANAGE_LIMIT)
async def delete_photo(
    request: Request,
    event_id: EventIdDep,
    photo_id: PhotoIdDep,
    host_token: HostTokenDep,
    services: ServicesDep,
) -> Response:
    await services.photos.delete(event_id, photo_id, host_token)
    return Response(status_code=204)
