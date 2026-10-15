"""Public API request/response models."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.text import clean_text

MAX_EVENT_NAME_LENGTH = 80
MAX_UPLOADER_NAME_LENGTH = 40
MAX_CAPTION_LENGTH = 140


def clean_event_name(value: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        raise ValueError("Event name must not be empty.")
    if len(cleaned) > MAX_EVENT_NAME_LENGTH:
        raise ValueError(f"Event name must be at most {MAX_EVENT_NAME_LENGTH} characters.")
    return cleaned


# --- Requests ---------------------------------------------------------------------------


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=4 * MAX_EVENT_NAME_LENGTH, examples=["Aisyah & Daniel's Wedding"])

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return clean_event_name(value)


class EventUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=4 * MAX_EVENT_NAME_LENGTH)
    uploads_open: bool | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return None if value is None else clean_event_name(value)

    @model_validator(mode="after")
    def _require_change(self) -> "EventUpdate":
        if self.name is None and self.uploads_open is None:
            raise ValueError("Provide at least one of 'name' or 'uploads_open'.")
        return self


# --- Responses ------------------------------------------------------------------------


class EventLinks(BaseModel):
    upload: str = Field(description="Guest upload page. Encode this URL in the QR code.")
    wall: str = Field(description="Live photo wall.")


class EventPublic(BaseModel):
    id: str
    name: str
    uploads_open: bool
    photo_count: int
    created_at: datetime
    links: EventLinks


class EventCreatedResponse(BaseModel):
    event: EventPublic
    host_token: str = Field(
        description="Secret that authorises host actions. Shown once; store it safely (it is not recoverable)."
    )
    host_url: str = Field(description="Host dashboard link. The token travels in the URL fragment, never to servers.")


class PhotoPublic(BaseModel):
    id: str
    event_id: str
    url: str
    thumbnail_url: str
    content_type: str
    width: int
    height: int
    size_bytes: int
    uploader_name: str | None
    caption: str | None
    created_at: datetime


class PhotoPage(BaseModel):
    items: list[PhotoPublic]
    next_cursor: str | None = Field(description="Pass as ?cursor= to fetch older photos; null on the last page.")


class GuestQuota(BaseModel):
    limit: int | None = Field(description="Photos each guest may add to this event; null when there is no limit.")
    used: int = Field(description="Photos this guest has on the event now (photos the host deleted don't count).")
    remaining: int | None = Field(description="Photos this guest can still add; null when there is no limit.")


class PublicConfig(BaseModel):
    max_upload_bytes: int
    allowed_mime_types: list[str]
    max_photos_per_event: int
    max_photos_per_guest: int | None = Field(description="Photos each guest may add to an event; null: no limit.")
    max_event_name_length: int
    max_uploader_name_length: int
    max_caption_length: int
    event_creation_requires_key: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorInfo
