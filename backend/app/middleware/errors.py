"""Last-resort handler for unexpected exceptions.

It sits inside the CORS middleware, unlike Starlette's ServerErrorMiddleware,
so that 500 responses still carry CORS headers and the frontend can read the
structured error body. Stack traces are logged, never returned to the client.
"""

from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ErrorCode, error_response
from app.core.logging import get_logger

logger = get_logger("errors")


class UnhandledErrorMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except ClientDisconnect:
            # e.g. a guest walking out of Wi-Fi range mid-upload: nobody to answer.
            logger.info("Client disconnected during %s %s", scope.get("method"), scope.get("path"))
        except Exception:
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if response_started:
                raise
            response = error_response(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")
            await response(scope, receive, send)
