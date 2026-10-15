"""Baseline security headers for every HTTP response (never overrides ones already set)."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.middleware.paths import route_path

_BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
# The API only returns JSON and images, so the strictest policy applies.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool, docs_prefix: str | None) -> None:
        self.app = app
        self.hsts = hsts
        self.docs_prefix = docs_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = route_path(scope)
        # Swagger UI needs its CDN scripts, so the interactive docs skip the strict CSP.
        is_docs = self.docs_prefix is not None and (
            path.startswith(self.docs_prefix) or path == "/api/openapi.json"
        )
        is_api = path.startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", []))
                headers = MutableHeaders(scope=message)
                for name, value in _BASE_HEADERS.items():
                    headers.setdefault(name, value)
                if not is_docs:
                    headers.setdefault("Content-Security-Policy", _API_CSP)
                if is_api:
                    # Responses can carry secrets (e.g. the one-time host token).
                    headers.setdefault("Cache-Control", "no-store")
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", _HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
