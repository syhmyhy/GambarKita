"""Application error hierarchy and the structured JSON error envelope.

Every error response has the same shape::

    {"error": {"code": "FILE_TOO_LARGE", "message": "...", "details": ..., "request_id": "..."}}
"""

import math
import time
from enum import StrEnum
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var

logger = get_logger("errors")


class ErrorCode(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    EVENT_NOT_FOUND = "EVENT_NOT_FOUND"
    PHOTO_NOT_FOUND = "PHOTO_NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    UPLOADS_CLOSED = "UPLOADS_CLOSED"
    EVENT_PHOTO_LIMIT = "EVENT_PHOTO_LIMIT"
    GUEST_PHOTO_LIMIT = "GUEST_PHOTO_LIMIT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    INVALID_IMAGE = "INVALID_IMAGE"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    INSUFFICIENT_STORAGE = "INSUFFICIENT_STORAGE"
    SERVICE_BUSY = "SERVICE_BUSY"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    HTTP_ERROR = "HTTP_ERROR"


class AppError(Exception):
    """Base class for errors that map onto a structured JSON response."""

    status_code: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details
        self.headers = headers
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = 400
    code = ErrorCode.BAD_REQUEST
    default_message = "The request could not be processed."


class UnsafePathError(BadRequestError):
    default_message = "Invalid storage path."


class ValidationFailedError(AppError):
    status_code = 422
    code = ErrorCode.VALIDATION_ERROR
    default_message = "The request is invalid."


class UnauthorizedError(AppError):
    status_code = 401
    code = ErrorCode.UNAUTHORIZED
    default_message = "A valid host token is required."

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        super().__init__(message, details=details, headers={"WWW-Authenticate": "Bearer"})


class NotFoundError(AppError):
    status_code = 404
    code = ErrorCode.NOT_FOUND
    default_message = "The requested resource was not found."


class EventNotFoundError(NotFoundError):
    code = ErrorCode.EVENT_NOT_FOUND
    default_message = "Event not found."


class PhotoNotFoundError(NotFoundError):
    code = ErrorCode.PHOTO_NOT_FOUND
    default_message = "Photo not found."


class UploadsClosedError(AppError):
    status_code = 403
    code = ErrorCode.UPLOADS_CLOSED
    default_message = "This event is no longer accepting uploads."


class EventPhotoLimitError(AppError):
    status_code = 409
    code = ErrorCode.EVENT_PHOTO_LIMIT
    default_message = "This event has reached its photo limit."


class GuestPhotoLimitError(AppError):
    status_code = 409
    code = ErrorCode.GUEST_PHOTO_LIMIT
    default_message = "You have reached the photo limit for each guest at this event."

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"Each guest can share up to {limit} photo{'' if limit == 1 else 's'} at this event.",
            details={"max_photos_per_guest": limit},
        )



class FileTooLargeError(AppError):
    status_code = 413
    code = ErrorCode.FILE_TOO_LARGE
    default_message = "The file is too large."


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
    default_message = "Only JPEG, PNG and WebP images are accepted."


class InvalidImageError(AppError):
    status_code = 422
    code = ErrorCode.INVALID_IMAGE
    default_message = "The file could not be decoded as a valid image."


class ImageTooLargeError(AppError):
    status_code = 422
    code = ErrorCode.IMAGE_TOO_LARGE
    default_message = "The image dimensions exceed the allowed maximum."


class InsufficientStorageError(AppError):
    status_code = 507
    code = ErrorCode.INSUFFICIENT_STORAGE
    default_message = "The server is out of storage space. Please try again later."


class ServiceBusyError(AppError):
    status_code = 503
    code = ErrorCode.SERVICE_BUSY
    default_message = "The server is busy processing other photos. Please try again shortly."

    def __init__(self, message: str | None = None, *, retry_after: int = 5) -> None:
        super().__init__(message, headers={"Retry-After": str(retry_after)})


# --- Response helpers ---------------------------------------------------------------


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "error": {
            "code": str(code),
            "message": message,
            "details": details,
            "request_id": request_id_var.get(),
        }
    }


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_body(code, message, details), headers=headers)


_HTTP_ERROR_CODES: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}

_FRIENDLY_MESSAGES: dict[int, str] = {
    404: "The requested resource was not found.",
    405: "This HTTP method is not allowed for this resource.",
}


def _http_message(status_code: int, detail: Any) -> str:
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        phrase = "HTTP error"
    if status_code < 500 and isinstance(detail, str) and detail and detail != phrase:
        return detail
    return _FRIENDLY_MESSAGES.get(status_code, phrase)


def _retry_after_seconds(request: Request, exc: RateLimitExceeded) -> int:
    try:
        limit_item, args = request.state.view_rate_limit
        stats = request.app.state.limiter.limiter.get_window_stats(limit_item, *args)
        return max(1, math.ceil(stats.reset_time - time.time()))
    except Exception:  # noqa: BLE001 - best effort header, fall back to the window size
        try:
            return int(exc.limit.limit.get_expiry())
        except Exception:  # noqa: BLE001
            return 60


# --- Handlers -------------------------------------------------------------------------


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        logger.warning("%s: %s", exc.code, exc.message)
    return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ())]
        details.append(
            {
                # Never echo the rejected input back: it may be large or sensitive.
                "location": location[0] if location else None,
                "field": ".".join(location[1:]) or None,
                "message": error.get("msg", "Invalid value."),
                "type": error.get("type"),
            }
        )
    return error_response(422, ErrorCode.VALIDATION_ERROR, "The request is invalid.", details)


async def _rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitExceeded)
    return error_response(
        429,
        ErrorCode.RATE_LIMITED,
        "Too many requests. Please slow down and try again shortly.",
        {"limit": str(exc.detail)},
        {"Retry-After": str(_retry_after_seconds(request, exc))},
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_ERROR_CODES.get(exc.status_code, ErrorCode.HTTP_ERROR)
    return error_response(exc.status_code, code, _http_message(exc.status_code, exc.detail), None, exc.headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
