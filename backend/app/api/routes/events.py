from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from app.api.deps import EventIdDep, GuestKeyHashDep, HostTokenDep, ServicesDep, guest_key_error
from app.api.responses import error_responses
from app.core.rate_limit import CREATE_EVENT_LIMIT, MANAGE_LIMIT, READ_LIMIT, limiter
from app.presenters import host_url, present_event
from app.schemas import EventCreate, EventCreatedResponse, EventPublic, EventUpdate, GuestQuota

router = APIRouter(prefix="/events", tags=["events"])


@router.post(
    "",
    status_code=201,
    response_model=EventCreatedResponse,
    responses=error_responses(401, 422, 429),
    summary="Create an event",
)
@limiter.limit(CREATE_EVENT_LIMIT)
async def create_event(
    request: Request,
    payload: EventCreate,
    services: ServicesDep,
    creation_key: Annotated[str | None, Header(alias="X-Event-Creation-Key", max_length=256)] = None,
) -> EventCreatedResponse:
    """Create an event. The response carries the **one-time** host token."""
    services.events.check_creation_key(creation_key)
    event, host_token = await services.events.create(payload.name)
    return EventCreatedResponse(
        event=present_event(event, 0, services.settings),
        host_token=host_token,
        host_url=host_url(services.settings, event.id, host_token),
    )


@router.get("/{event_id}", response_model=EventPublic, responses=error_responses(404, 429), summary="Get an event")
@limiter.limit(READ_LIMIT)
async def get_event(request: Request, event_id: EventIdDep, services: ServicesDep) -> EventPublic:
    event = await services.events.get(event_id)
    photo_count = await services.db.count_photos(event_id)
    return present_event(event, photo_count, services.settings)


@router.get(
    "/{event_id}/guest-quota",
    response_model=GuestQuota,
    responses=error_responses(404, 422, 429),
    summary="How many photos this guest has shared and may still share",
)
@limiter.limit(READ_LIMIT)
async def get_guest_quota(
    request: Request, event_id: EventIdDep, guest_key_hash: GuestKeyHashDep, services: ServicesDep
) -> GuestQuota:
    """Identifies the guest by the ``X-Guest-Key`` header (required)."""
    if guest_key_hash is None:
        raise guest_key_error("Required.")
    used = await services.photos.guest_photo_count(event_id, guest_key_hash)
    limit = services.photos.max_photos_per_guest or None
    return GuestQuota(limit=limit, used=used, remaining=None if limit is None else max(0, limit - used))


@router.patch(
    "/{event_id}",
    response_model=EventPublic,
    responses=error_responses(401, 404, 422, 429),
    summary="Update an event (host only)",
)
@limiter.limit(MANAGE_LIMIT)
async def update_event(
    request: Request,
    event_id: EventIdDep,
    payload: EventUpdate,
    host_token: HostTokenDep,
    services: ServicesDep,
) -> EventPublic:
    event, photo_count = await services.events.update(
        event_id, host_token, name=payload.name, uploads_open=payload.uploads_open
    )
    return present_event(event, photo_count, services.settings)


@router.delete(
    "/{event_id}",
    status_code=204,
    responses=error_responses(401, 404, 429),
    summary="Delete an event and all its photos (host only)",
)
@limiter.limit(MANAGE_LIMIT)
async def delete_event(
    request: Request, event_id: EventIdDep, host_token: HostTokenDep, services: ServicesDep
) -> Response:
    await services.events.delete(event_id, host_token)
    return Response(status_code=204)


@router.post(
    "/{event_id}/host/rotate",
    status_code=200,
    response_model=EventCreatedResponse,
    responses=error_responses(401, 404, 429),
    summary="Replace a lost host token (server admin only)",
)
@limiter.limit(CREATE_EVENT_LIMIT)
async def rotate_host_token(
    request: Request,
    event_id: EventIdDep,
    services: ServicesDep,
    creation_key: Annotated[str | None, Header(alias="X-Event-Creation-Key", max_length=256)] = None,
) -> EventCreatedResponse:
    """Issue a **new** host token and link for an event whose host lost theirs.

    Requires the server's ``X-Event-Creation-Key``; the old token stops working
    immediately. Disabled (401) when the server has no creation key configured.
    """
    event, host_token = await services.events.rotate_host_token(event_id, creation_key)
    photo_count = await services.db.count_photos(event_id)
    return EventCreatedResponse(
        event=present_event(event, photo_count, services.settings),
        host_token=host_token,
        host_url=host_url(services.settings, event.id, host_token),
    )


@router.post(
    "/{event_id}/host/verify",
    status_code=204,
    responses=error_responses(401, 404, 429),
    summary="Check that a host token is valid",
)
@limiter.limit(MANAGE_LIMIT)
async def verify_host(
    request: Request, event_id: EventIdDep, host_token: HostTokenDep, services: ServicesDep
) -> Response:
    await services.events.authorize_host(event_id, host_token)
    return Response(status_code=204)
