"""HTTP rate limiting (slowapi) keyed on the client network.

slowapi evaluates decorators at import time, so the limiter is a module-level
singleton and the limit strings are resolved lazily from the active settings.
Limits are kept in process memory, which matches the single-process deployment
model required by the in-memory WebSocket broadcaster.

* ``key_style="endpoint"``: a limit applies per route across *all* events.
  The default "url" style would key on the concrete path, giving every event
  id its own budget and an unbounded number of keys.
* ``sliding-window-counter``: expired counters are deleted. The moving-window
  memory storage keeps one key per identifier forever.
* IPv6 clients are grouped by /64, since a single host usually controls a
  whole /64.

Behind a reverse proxy, run uvicorn with ``--forwarded-allow-ips`` (see run.py)
so that ``request.client`` is the real client address rather than the proxy's.
Never trust ``X-Forwarded-For`` directly: clients can forge it to dodge limits.
"""

import ipaddress
from collections.abc import Callable

from slowapi import Limiter
from starlette.requests import HTTPConnection

from app.core.config import Settings, get_settings


def client_ip(request: HTTPConnection) -> str:
    """Rate-limit identity of the peer: the IPv4 address or the IPv6 /64."""
    # slowapi only passes the request to key functions whose parameter is named "request".
    host = request.client.host if request.client and request.client.host else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host or "unknown"
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{address}/64", strict=False))
    return str(address)


limiter = Limiter(
    key_func=client_ip,
    key_style="endpoint",
    strategy="sliding-window-counter",
    storage_uri="memory://",
    headers_enabled=False,
)

_active_settings: Settings | None = None


def configure_rate_limiting(settings: Settings) -> Limiter:
    global _active_settings
    _active_settings = settings
    limiter.enabled = settings.rate_limit_enabled
    # slowapi also reads RATELIMIT_* from the environment and ./.env without casting,
    # so e.g. RATELIMIT_HEADERS_ENABLED=false would turn header injection *on* and
    # break every endpoint that returns a model. The app sends its own Retry-After.
    limiter._headers_enabled = False
    return limiter


def _limit_from_settings(attribute: str) -> Callable[[], str]:
    def resolve() -> str:
        return getattr(_active_settings or get_settings(), attribute)

    return resolve


CREATE_EVENT_LIMIT = _limit_from_settings("rate_limit_create_event")
UPLOAD_LIMIT = _limit_from_settings("rate_limit_upload")
READ_LIMIT = _limit_from_settings("rate_limit_read")
MANAGE_LIMIT = _limit_from_settings("rate_limit_manage")
