from fastapi import APIRouter, Request

from app import __version__
from app.api.deps import ServicesDep
from app.api.responses import error_responses
from app.core.errors import AppError, ErrorCode
from app.core.rate_limit import READ_LIMIT, limiter
from app.schemas import (
    MAX_CAPTION_LENGTH,
    MAX_EVENT_NAME_LENGTH,
    MAX_UPLOADER_NAME_LENGTH,
    HealthResponse,
    PublicConfig,
)
from app.services.image_processing import ALLOWED_MIME_TYPES

router = APIRouter(tags=["system"])


class _Unavailable(AppError):
    status_code = 503
    code = ErrorCode.SERVICE_UNAVAILABLE
    default_message = "The service is temporarily unavailable."


@router.get("/health", response_model=HealthResponse, responses=error_responses(503))
async def health(services: ServicesDep) -> HealthResponse:
    try:
        healthy = await services.db.ping()
    except Exception as exc:
        raise _Unavailable() from exc
    if not healthy:
        raise _Unavailable()
    return HealthResponse(status="ok", version=__version__)


@router.get("/config", response_model=PublicConfig, responses=error_responses(429))
@limiter.limit(READ_LIMIT)
async def public_config(request: Request, services: ServicesDep) -> PublicConfig:
    """Limits the frontend mirrors for instant client-side validation."""
    settings = services.settings
    return PublicConfig(
        max_upload_bytes=settings.max_upload_bytes,
        allowed_mime_types=list(ALLOWED_MIME_TYPES),
        max_photos_per_event=settings.max_photos_per_event,
        max_photos_per_guest=settings.max_photos_per_guest or None,
        max_event_name_length=MAX_EVENT_NAME_LENGTH,
        max_uploader_name_length=MAX_UPLOADER_NAME_LENGTH,
        max_caption_length=MAX_CAPTION_LENGTH,
        event_creation_requires_key=settings.event_creation_key is not None,
    )
