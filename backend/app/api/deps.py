"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Header, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import EventNotFoundError, PhotoNotFoundError, UnauthorizedError, ValidationFailedError
from app.core.security import (
    guest_key_hash,
    is_plausible_token,
    is_valid_event_id,
    is_valid_guest_key,
    is_valid_photo_id,
)
from app.services.container import Services


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def _event_id(event_id: Annotated[str, Path(description="Event identifier")]) -> str:
    # Malformed ids (any length or charset) get the same 404 as unknown ones.
    if not is_valid_event_id(event_id):
        raise EventNotFoundError()
    return event_id


def _photo_id(photo_id: Annotated[str, Path(description="Photo identifier")]) -> str:
    if not is_valid_photo_id(photo_id):
        raise PhotoNotFoundError()
    return photo_id


EventIdDep = Annotated[str, Depends(_event_id)]
PhotoIdDep = Annotated[str, Depends(_photo_id)]

_bearer = HTTPBearer(auto_error=False, description="Host token returned when the event was created.")


def _host_token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str:
    if credentials is None or not is_plausible_token(credentials.credentials):
        raise UnauthorizedError()
    return credentials.credentials


HostTokenDep = Annotated[str, Depends(_host_token)]

GUEST_KEY_HEADER = "X-Guest-Key"


def guest_key_error(message: str) -> ValidationFailedError:
    return ValidationFailedError(details=[{"location": "header", "field": GUEST_KEY_HEADER, "message": message}])


def _guest_key_hash(
    event_id: EventIdDep,
    guest_key: Annotated[
        str | None,
        Header(
            alias=GUEST_KEY_HEADER,
            max_length=256,
            description="Random key this browser keeps for the event; counts photos per guest.",
        ),
    ] = None,
) -> str | None:
    """The hashed guest key, or ``None`` when the request carries none."""
    if guest_key is None:
        return None
    if not is_valid_guest_key(guest_key):
        raise guest_key_error("Must be 32-128 characters: letters, digits, '-' or '_'.")
    return guest_key_hash(event_id, guest_key)


GuestKeyHashDep = Annotated[str | None, Depends(_guest_key_hash)]
