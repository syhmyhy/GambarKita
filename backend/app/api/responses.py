"""OpenAPI documentation for the structured error envelope."""

from typing import Any

from app.schemas import ErrorResponse

_DESCRIPTIONS = {
    400: "Malformed request",
    401: "Missing or invalid host token / creation key",
    403: "Uploads are closed for this event",
    404: "Event or photo not found",
    409: "Event or per-guest photo limit reached",
    413: "File or request body too large",
    415: "Unsupported file type",
    422: "Validation failed or the image is invalid",
    429: "Rate limit exceeded",
    503: "Server busy",
    507: "Server out of storage",
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        code: {"model": ErrorResponse, "description": _DESCRIPTIONS.get(code, "Error")} for code in status_codes
    }
