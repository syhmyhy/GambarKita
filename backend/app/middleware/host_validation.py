"""Host header allow-list (replaces Starlette's TrustedHostMiddleware).

It sits inside the security-header and CORS middleware, so rejections use the
standard JSON error envelope and carry the baseline headers.
"""

import re

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import ErrorCode, error_response

# A DNS-style name (letters, digits, '-', '_', dots, optional trailing dot), an
# IPv4 address or a bracketed IPv6 literal, optionally followed by a port.
_HOST_HEADER_RE = re.compile(
    r"(?P<host>[A-Za-z0-9_](?:[A-Za-z0-9._-]*[A-Za-z0-9_])?\.?|\[[0-9A-Fa-f:.]+\])(?::[0-9]{0,5})?"
)


def _normalize(host: str) -> str:
    return host.lower().rstrip(".")


def _hostname(host_header: str) -> str | None:
    match = _HOST_HEADER_RE.fullmatch(host_header)
    return _normalize(match["host"]) if match else None


class HostValidationMiddleware:
    def __init__(self, app: ASGIApp, *, allowed_hosts: list[str]) -> None:
        self.app = app
        self.exact = {_normalize(host) for host in allowed_hosts if not host.startswith("*.")}
        self.suffixes = tuple(_normalize(host[1:]) for host in allowed_hosts if host.startswith("*."))

    def _allowed(self, host: str | None) -> bool:
        if host is None:
            return False
        return host in self.exact or (bool(self.suffixes) and host.endswith(self.suffixes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or self._allowed(
            _hostname(Headers(scope=scope).get("host", ""))
        ):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})  # handshake rejected (HTTP 403)
            return
        response = error_response(400, ErrorCode.BAD_REQUEST, "Invalid host header.")
        await response(scope, receive, send)
