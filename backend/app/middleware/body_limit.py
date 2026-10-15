"""Request body size enforcement: the first line of defence against disk/memory exhaustion.

* A declared ``Content-Length`` above the limit is rejected before any body byte is read.
* Chunked or under-declared bodies are counted as they stream; the request is
  aborted with 413 as soon as the running total crosses the limit.

The multipart parser therefore never spools more than the limit to disk.
"""

import re

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ErrorCode, error_response
from app.middleware.paths import route_path

_CONTENT_LENGTH_RE = re.compile(r"[0-9]{1,19}")


class RequestBodyTooLarge(HTTPException):
    """Raised from ``receive``. As an HTTPException, FastAPI re-raises it unchanged
    even when it surfaces during its own body parsing; ``body_too_large_handler``
    renders it."""

    def __init__(self, limit: int) -> None:
        super().__init__(status_code=413, detail=f"The request body exceeds the {limit}-byte limit.")
        self.limit = limit


def too_large_response(limit: int) -> JSONResponse:
    return error_response(
        413,
        ErrorCode.PAYLOAD_TOO_LARGE,
        f"The request body is too large (limit: {limit} bytes).",
        {"max_bytes": limit},
        {"Connection": "close"},
    )


async def body_too_large_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestBodyTooLarge)
    return too_large_response(exc.limit)


class BodySizeLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        default_limit: int,
        upload_limit: int,
        upload_path: re.Pattern[str],
    ) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit
        self.upload_path = upload_path

    def _limit_for(self, scope: Scope) -> int:
        if scope["method"] == "POST" and self.upload_path.fullmatch(route_path(scope)):
            return self.upload_limit
        return self.default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope)
        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            if _CONTENT_LENGTH_RE.fullmatch(content_length) is None:
                response = error_response(400, ErrorCode.BAD_REQUEST, "Invalid Content-Length header.")
                await response(scope, receive, send)
                return
            if int(content_length) > limit:
                await too_large_response(limit)(scope, receive, send)
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge(limit)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestBodyTooLarge as exc:  # only if nothing inside rendered it
            if response_started:
                raise
            await too_large_response(exc.limit)(scope, receive, send)
