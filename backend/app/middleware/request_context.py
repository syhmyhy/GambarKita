"""Assigns a correlation id to every request (``X-Request-ID``) for logs and error bodies."""

import re
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

# Accept a caller-supplied id only if it is short and log-safe.
_INCOMING_ID_RE = re.compile(r"[A-Za-z0-9._-]{8,64}")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id")
        request_id = incoming if incoming and _INCOMING_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", []))
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id if scope["type"] == "http" else send)
        finally:
            request_id_var.reset(token)
