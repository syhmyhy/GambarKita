"""Regression tests for the second review round (HTTP and WebSocket layer).

* Fix 6  - rate limits are scoped per endpoint (not per event URL), use expiring
  sliding-window counters, and key IPv6 clients by /64; slowapi's header flag is pinned off.
* Fix 7  - venue-sized defaults and host-reserved WebSocket slots / subprotocols.
* Fix 8  - a WebSocket attached to an event deleted during the handshake is closed 4410.
* Fix 9  - root_path-aware middleware, the streamed 413, ClientDisconnect handling and
  the JSON-envelope Host allow-list.
* Fix 11 - ids of any length are 404, millisecond ``created_at``, config validation, run.py.
"""

import json
import logging
import os
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from limits.strategies import SlidingWindowCounterRateLimiter
from packaging.specifiers import SpecifierSet
from PIL import Image
from pydantic import ValidationError
from starlette.requests import HTTPConnection
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.core.rate_limit import client_ip, limiter
from app.main import create_app
from tests.conftest import ORIGIN, auth, image_bytes

BACKEND_DIR = Path(__file__).resolve().parents[1]
JSON_BODY_LIMIT = 64 * 1024
MULTIPART_OVERHEAD = 64 * 1024
ERROR_KEYS = {"code", "message", "details", "request_id"}

WS_V1 = "gambarkita.v1"
WS_HOST_PREFIX = "gambarkita.host."
RECEIVE_TIMEOUT = 5.0
CLOSE_POLICY_VIOLATION = 1008
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_EVENT_NOT_FOUND = 4404
CLOSE_EVENT_DELETED = 4410
CLOSE_RATE_LIMITED = 4429


# --- HTTP helpers -----------------------------------------------------------------------


class RawResponse:
    """Minimal response object for requests driven straight through the ASGI app."""

    def __init__(self, start: dict[str, Any], body: bytes) -> None:
        self.status_code: int = start["status"]
        merged: dict[str, str] = {}
        for name, value in start.get("headers", []):
            key = name.decode("latin-1").lower()
            text = value.decode("latin-1")
            merged[key] = f"{merged[key]}, {text}" if key in merged else text
        self.headers = merged
        self.content = body

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.content)


def asgi_call(
    client: TestClient,
    method: str,
    path: str,
    *,
    headers: list[tuple[str, str]] | None = None,
    chunks: list[bytes] | None = None,
    root_path: str = "",
    disconnect_after_body: bool = False,
    client_addr: tuple[str, int] = ("testclient", 50000),
) -> tuple[RawResponse | None, list[dict[str, Any]]]:
    """Send a hand-built request through a started TestClient's app.

    ``chunks`` are delivered as separate ``http.request`` messages (a streamed body).
    With ``disconnect_after_body`` the last chunk still says ``more_body=True`` and the
    next ``receive()`` reports ``http.disconnect`` (the client vanished mid-upload).
    Returns the response (``None`` if the app sent none) and every message sent.
    """
    raw_headers = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers or []]
    if not any(name == b"host" for name, _ in raw_headers):
        raw_headers.insert(0, (b"host", b"testserver"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "root_path": root_path,
        "query_string": b"",
        "headers": raw_headers,
        "client": client_addr,
        "server": ("testserver", 80),
        "state": {},
    }
    pending = list(chunks if chunks is not None else [b""])
    messages: list[dict[str, Any]] = []

    async def run() -> None:
        async def receive() -> dict[str, Any]:
            if pending:
                chunk = pending.pop(0)
                return {"type": "http.request", "body": chunk, "more_body": bool(pending) or disconnect_after_body}
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await client.app(scope, receive, send)

    assert client.portal is not None, "the TestClient must be started"
    client.portal.call(run)
    starts = [message for message in messages if message["type"] == "http.response.start"]
    if not starts:
        return None, messages
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return RawResponse(starts[0], body), messages


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    """Assert the structured error envelope and return its ``error`` object."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == ERROR_KEYS, error
    assert error["code"] == code, error
    assert isinstance(error["message"], str) and error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    return error


def assert_security_headers(headers: Any) -> None:
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert "default-src 'none'" in headers.get("content-security-policy", "")
    assert headers.get("cache-control") == "no-store"


def new_event(client: TestClient, name: str = "Regression", **headers: str) -> dict[str, Any]:
    response = client.post("/api/events", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def post_photo(client: TestClient, event_id: str, data: bytes | None = None, **kwargs: Any) -> Any:
    payload = image_bytes() if data is None else data
    return client.post(
        f"/api/events/{event_id}/photos", files={"file": ("photo.jpg", payload, "image/jpeg")}, **kwargs
    )


def noisy_png(size: tuple[int, int]) -> bytes:
    """An incompressible PNG (bigger than the 64 KiB JSON body budget at 200x200)."""
    buffer = BytesIO()
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(buffer, format="PNG")
    return buffer.getvalue()


def multipart(data: bytes, *, filename: str = "photo.png", content_type: str = "image/png") -> tuple[bytes, str]:
    boundary = "gkRegressionBoundary7MA4YWxkTrZu0gW"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def split(data: bytes, size: int) -> list[bytes]:
    return [data[offset : offset + size] for offset in range(0, len(data), size)]


def services_of(client: TestClient) -> Any:
    return client.app.state.services


def valid_unknown_event_ids(count: int, prefix: str = "q") -> list[str]:
    """Well-formed (12 x [a-z0-9]) but unknown event ids, so requests reach the limiter."""
    return [f"{prefix}{index:011d}" for index in range(count)]


# --- WebSocket helpers ----------------------------------------------------------------------


def _receive_raw(ws: Any, timeout: float) -> dict[str, Any]:
    receive_stream = getattr(ws, "_send_rx", None)
    if receive_stream is None:  # pragma: no cover - other Starlette internals
        return ws.receive()

    async def receive_with_timeout() -> dict[str, Any]:
        with anyio.fail_after(timeout):
            return await receive_stream.receive()

    return ws.portal.call(receive_with_timeout)


def recv(ws: Any, timeout: float = RECEIVE_TIMEOUT) -> Any:
    """Like ``receive_json`` but bounded; raises ``WebSocketDisconnect`` on a close frame."""
    message = _receive_raw(ws, timeout)
    if message["type"] == "websocket.close":
        raise WebSocketDisconnect(code=message.get("code", 1000), reason=message.get("reason", ""))
    assert message["type"] == "websocket.send", message
    assert message.get("text") is not None, message
    return json.loads(message["text"])


def expect_close(ws: Any) -> int:
    with pytest.raises(WebSocketDisconnect) as info:
        unexpected = recv(ws)
        pytest.fail(f"expected the server to close the socket, received {unexpected!r}")
    return info.value.code


def expect_hello(ws: Any, event_id: str) -> dict[str, Any]:
    hello = recv(ws)
    assert hello["type"] == "hello", hello
    assert hello["event_id"] == event_id
    return hello


def assert_ping_pong(ws: Any) -> None:
    ws.send_json({"type": "ping"})
    assert recv(ws) == {"type": "pong"}


def ws_connect(
    client: TestClient,
    event_id: str,
    *,
    subprotocols: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    merged = {"origin": ORIGIN, **(headers or {})}
    return client.websocket_connect(f"/ws/events/{event_id}", subprotocols=subprotocols, headers=merged)


def host_protocols(token: str) -> list[str]:
    return [WS_V1, f"{WS_HOST_PREFIX}{token}"]


@contextmanager
def live(client: TestClient, event_id: str, **kwargs: Any) -> Iterator[Any]:
    with ws_connect(client, event_id, **kwargs) as ws:
        expect_hello(ws, event_id)
        yield ws


# =============================================================================================
# Fix 6: rate limits per endpoint across events, expiring keys, IPv6 /64 identity
# =============================================================================================
# Small limits use day-long windows: "limits" floors the sliding-window-counter weight, so a
# window boundary falling mid-test could otherwise let one extra request through.


def test_upload_rate_limit_is_one_budget_across_all_events(make_client):
    client = make_client(rate_limit_enabled=True, rate_limit_upload="2/day")
    event_a = new_event(client, "A")["event"]["id"]
    event_b = new_event(client, "B")["event"]["id"]

    assert post_photo(client, event_a).status_code == 201
    assert post_photo(client, event_a).status_code == 201
    # A fresh event id must not come with a fresh budget.
    third = post_photo(client, event_b)

    error = assert_error(third, 429, "RATE_LIMITED")
    assert error["details"] == {"limit": "2 per 1 day"}
    assert client.get(f"/api/events/{event_b}").json()["photo_count"] == 0


def test_read_rate_limit_cannot_be_dodged_by_cycling_event_ids(make_client):
    client = make_client(rate_limit_enabled=True, rate_limit_read="3/day")
    ids = valid_unknown_event_ids(6)

    for event_id in ids[:3]:
        assert_error(client.get(f"/api/events/{event_id}/photos"), 404, "EVENT_NOT_FOUND")
    for event_id in ids[3:]:
        assert_error(client.get(f"/api/events/{event_id}/photos"), 429, "RATE_LIMITED")

    # Scope is the endpoint, not the whole API: another read endpoint has its own budget.
    assert_error(client.get(f"/api/events/{ids[0]}"), 404, "EVENT_NOT_FOUND")


def test_limiter_storage_holds_expiring_per_endpoint_keys_not_one_per_event(make_client):
    client = make_client(rate_limit_enabled=True, rate_limit_read="1000/minute")
    ids = valid_unknown_event_ids(30, prefix="k")

    for event_id in ids:
        assert_error(client.get(f"/api/events/{event_id}/photos"), 404, "EVENT_NOT_FOUND")
        assert_error(client.get(f"/api/events/{event_id}"), 404, "EVENT_NOT_FOUND")

    storage = limiter.limiter.storage
    keys = list(storage.storage)
    # Moving-window storage keeps one never-expiring entry list per key.
    assert not storage.events, list(storage.events)[:5]
    assert not any(event_id in key for key in keys for event_id in ids), keys[:5]
    # One counter per endpoint (two around a window boundary), whatever the event id.
    assert 1 <= sum("list_photos" in key for key in keys) <= 2, keys
    assert 1 <= sum("get_event" in key for key in keys) <= 2, keys
    assert len(keys) <= 4, keys
    # Every counter expires.
    assert keys and all(key in storage.expirations for key in keys), (keys, storage.expirations)


def test_websocket_connect_limiter_uses_expiring_sliding_window_counters(make_client):
    client = make_client(rate_limit_enabled=True, ws_connect_rate="50/minute")
    event_id = new_event(client)["event"]["id"]

    for unknown in valid_unknown_event_ids(5, prefix="w"):
        with ws_connect(client, unknown) as ws:
            assert expect_close(ws) == CLOSE_EVENT_NOT_FOUND
    with live(client, event_id) as ws:
        assert_ping_pong(ws)

    rate_limiter = services_of(client).ws_guard._limiter
    assert isinstance(rate_limiter, SlidingWindowCounterRateLimiter)
    storage = rate_limiter.storage
    keys = list(storage.storage)
    assert not storage.events
    assert 1 <= len(keys) <= 2, keys
    assert all(key in storage.expirations for key in keys)


def _connection(host: str | None) -> HTTPConnection:
    scope: dict[str, Any] = {"type": "http", "headers": [], "path": "/", "query_string": b""}
    if host is not None:
        scope["client"] = (host, 40000)
    return HTTPConnection(scope)


def test_client_ip_groups_ipv6_by_64_and_keeps_ipv4():
    assert client_ip(_connection("203.0.113.9")) == "203.0.113.9"
    assert client_ip(_connection("203.0.113.9")) != client_ip(_connection("203.0.113.10"))

    same_a = client_ip(_connection("2001:db8:1:2::1"))
    same_b = client_ip(_connection("2001:db8:1:2:aaaa:bbbb:cccc:dddd"))
    same_c = client_ip(_connection("2001:0db8:0001:0002:0000:0000:0000:0002"))
    other = client_ip(_connection("2001:db8:1:3::1"))

    assert same_a == same_b == same_c
    assert same_a != other
    # The interface identifier is not part of the identity.
    assert "aaaa" not in same_b and "dddd" not in same_b


def test_client_ip_unwraps_ipv4_mapped_ipv6():
    assert client_ip(_connection("::ffff:1.2.3.4")) == "1.2.3.4"
    assert client_ip(_connection("::ffff:1.2.3.4")) == client_ip(_connection("1.2.3.4"))


def test_client_ip_without_client_address_is_unknown():
    assert client_ip(_connection(None)) == "unknown"
    assert client_ip(_connection("")) == "unknown"


def test_ipv6_clients_in_the_same_64_share_the_create_event_budget(settings_factory):
    app = create_app(settings_factory(rate_limit_enabled=True, rate_limit_create_event="1/day"))
    body = b'{"name": "IPv6 guest"}'
    json_headers = [("content-type", "application/json"), ("content-length", str(len(body)))]

    with TestClient(app, client=("2001:db8:1:2::1", 1234)) as client:
        assert client.post("/api/events", json={"name": "first"}).status_code == 201

        # Another address in the same /64 (e.g. a privacy-extension address) shares the budget.
        neighbour, _ = asgi_call(
            client, "POST", "/api/events", headers=json_headers, chunks=[body], client_addr=("2001:db8:1:2::2", 5678)
        )
        assert neighbour is not None
        assert_error(neighbour, 429, "RATE_LIMITED")

        # A different /64 is a different client.
        stranger, _ = asgi_call(
            client, "POST", "/api/events", headers=json_headers, chunks=[body], client_addr=("2001:db8:1:3::1", 5678)
        )
        assert stranger is not None and stranger.status_code == 201, stranger and stranger.text

        # An IPv4-mapped address counts as the IPv4 client it wraps.
        mapped, _ = asgi_call(
            client, "POST", "/api/events", headers=json_headers, chunks=[body], client_addr=("::ffff:198.51.100.7", 1)
        )
        assert mapped is not None and mapped.status_code == 201, mapped and mapped.text
        plain, _ = asgi_call(
            client, "POST", "/api/events", headers=json_headers, chunks=[body], client_addr=("198.51.100.7", 2)
        )
        assert plain is not None
        assert_error(plain, 429, "RATE_LIMITED")


def test_truthy_string_headers_enabled_flag_is_pinned_off(make_client, monkeypatch):
    # slowapi copies RATELIMIT_HEADERS_ENABLED from the environment / ./.env uncast,
    # so "false" arrives as a truthy string and header injection breaks model responses.
    monkeypatch.setattr(limiter, "_headers_enabled", "false")
    client = make_client(rate_limit_enabled=True)

    assert limiter._headers_enabled is False
    created = client.post("/api/events", json={"name": "Headers pinned"})
    assert created.status_code == 201, created.text
    event_id = created.json()["event"]["id"]
    token = created.json()["host_token"]

    responses = {
        "get_event": client.get(f"/api/events/{event_id}"),
        "list_photos": client.get(f"/api/events/{event_id}/photos"),
        "config": client.get("/api/config"),
        "upload": post_photo(client, event_id),
        "patch": client.patch(f"/api/events/{event_id}", json={"name": "Renamed"}, headers=auth(token)),
    }
    expected = {"get_event": 200, "list_photos": 200, "config": 200, "upload": 201, "patch": 200}
    assert {name: r.status_code for name, r in responses.items()} == expected, {
        name: r.text[:200] for name, r in responses.items()
    }
    assert "x-ratelimit-limit" not in responses["get_event"].headers


# =============================================================================================
# Fix 7: venue-sized defaults, subprotocol selection and host-reserved WebSocket slots
# =============================================================================================


def test_defaults_are_sized_for_a_venue_behind_one_nat():
    settings = Settings(_env_file=None)

    assert settings.rate_limit_upload == "300/minute"
    assert settings.rate_limit_read == "1200/minute"
    assert settings.rate_limit_manage == "120/minute"
    assert settings.ws_connect_rate == "300/minute"
    assert settings.ws_max_connections_per_ip == 500
    assert settings.ws_reserved_host_connections == 20


def test_websocket_selects_v1_subprotocol_when_offered(client):
    event_id = new_event(client)["event"]["id"]

    with ws_connect(client, event_id, subprotocols=[WS_V1]) as ws:
        assert ws.accepted_subprotocol == WS_V1
        expect_hello(ws, event_id)
        assert_ping_pong(ws)
    with ws_connect(client, event_id, subprotocols=["chat.example", WS_V1]) as ws:
        assert ws.accepted_subprotocol == WS_V1
        expect_hello(ws, event_id)
    # A host protocol offer never gets echoed back (it carries the token).
    token = new_event(client, "Hosted")
    with ws_connect(client, token["event"]["id"], subprotocols=host_protocols(token["host_token"])) as ws:
        assert ws.accepted_subprotocol == WS_V1
        expect_hello(ws, token["event"]["id"])


def test_websocket_rejection_paths_also_select_v1_subprotocol(make_client):
    client = make_client(
        rate_limit_enabled=True,
        ws_connect_rate="3/day",
        ws_max_connections_per_event=1,
        ws_reserved_host_connections=0,
    )
    event_id = new_event(client)["event"]["id"]

    with ws_connect(client, event_id, subprotocols=[WS_V1]) as first:  # attempt 1: admitted
        assert first.accepted_subprotocol == WS_V1
        expect_hello(first, event_id)

        with ws_connect(client, event_id, subprotocols=[WS_V1]) as full:  # attempt 2: event full
            assert full.accepted_subprotocol == WS_V1
            assert expect_close(full) == CLOSE_TRY_AGAIN_LATER

        with ws_connect(client, "zzzzzzzzzzzz", subprotocols=[WS_V1]) as unknown:  # attempt 3
            assert unknown.accepted_subprotocol == WS_V1
            assert expect_close(unknown) == CLOSE_EVENT_NOT_FOUND

        with ws_connect(client, event_id, subprotocols=[WS_V1]) as limited:  # attempt 4: rate limited
            assert limited.accepted_subprotocol == WS_V1
            assert expect_close(limited) == CLOSE_RATE_LIMITED

    assert services_of(client).realtime.total_connections == 0


def test_host_uses_reserved_slot_when_event_is_full_of_viewers(make_client):
    client = make_client(ws_max_connections_per_event=1, ws_reserved_host_connections=1)
    created = new_event(client)
    event_id, token = created["event"]["id"], created["host_token"]
    manager = services_of(client).realtime

    with live(client, event_id, subprotocols=[WS_V1]) as viewer:
        with ws_connect(client, event_id, subprotocols=[WS_V1]) as anonymous:
            assert expect_close(anonymous) == CLOSE_TRY_AGAIN_LATER

        with ws_connect(client, event_id, subprotocols=host_protocols(token)) as host:
            assert host.accepted_subprotocol == WS_V1
            expect_hello(host, event_id)
            assert_ping_pong(host)
            assert manager.connections_for_event(event_id) == 2

            # The reserved pool is finite: a second host display does not fit.
            with ws_connect(client, event_id, subprotocols=host_protocols(token)) as second_host:
                assert expect_close(second_host) == CLOSE_TRY_AGAIN_LATER
        assert_ping_pong(viewer)

    assert manager.connections_for_event(event_id) == 0
    assert manager.total_connections == 0


def test_invalid_host_token_does_not_unlock_reserved_slots(make_client):
    client = make_client(ws_max_connections_per_event=1, ws_reserved_host_connections=1)
    created = new_event(client, "Mine")
    other = new_event(client, "Someone else's")
    event_id, token = created["event"]["id"], created["host_token"]

    bad_offers = [
        host_protocols("A" * len(token)),  # plausible shape, wrong secret
        host_protocols(other["host_token"]),  # valid token of another event
        host_protocols("short"),  # implausible token
        [WS_V1, f"{WS_HOST_PREFIX}"],  # empty token
    ]
    with live(client, event_id):
        for offer in bad_offers:
            with ws_connect(client, event_id, subprotocols=offer) as ws:
                assert ws.accepted_subprotocol == WS_V1
                assert expect_close(ws) == CLOSE_TRY_AGAIN_LATER, offer
        # The genuine token still gets in, so the rejections above were about the token.
        with ws_connect(client, event_id, subprotocols=host_protocols(token)) as host:
            expect_hello(host, event_id)

    assert services_of(client).realtime.total_connections == 0


def test_host_connection_is_exempt_from_the_per_ip_cap(make_client):
    client = make_client(ws_max_connections_per_ip=1)
    created = new_event(client)
    event_id, token = created["event"]["id"], created["host_token"]
    manager = services_of(client).realtime

    with live(client, event_id, subprotocols=[WS_V1]):
        with ws_connect(client, event_id, subprotocols=[WS_V1]) as anonymous:
            assert expect_close(anonymous) == CLOSE_TRY_AGAIN_LATER
        with ws_connect(client, event_id, subprotocols=host_protocols(token)) as host:
            assert host.accepted_subprotocol == WS_V1
            expect_hello(host, event_id)
            assert_ping_pong(host)
            assert manager.total_connections == 2

    assert manager.total_connections == 0


# =============================================================================================
# Fix 8: an event deleted during the WebSocket handshake
# =============================================================================================


def test_websocket_closes_4410_when_event_vanishes_after_register(client, monkeypatch):
    event_id = new_event(client)["event"]["id"]
    services = services_of(client)

    async def deleted_meanwhile(event_id: str) -> None:
        return None

    monkeypatch.setattr(services.db, "get_event_summary", deleted_meanwhile)

    with ws_connect(client, event_id, subprotocols=[WS_V1]) as ws:
        assert ws.accepted_subprotocol == WS_V1
        assert expect_close(ws) == CLOSE_EVENT_DELETED

    assert services.realtime.total_connections == 0
    assert services.realtime.connections_for_event(event_id) == 0


def test_websocket_closes_4410_when_deletion_commits_between_lookup_and_register(client, monkeypatch):
    created = new_event(client)
    event_id = created["event"]["id"]
    services = services_of(client)
    original_get = services.events.get

    async def get_then_host_deletes(requested_id: str) -> Any:
        event = await original_get(requested_id)
        # The host's DELETE commits now: its broadcast found no subscriber yet.
        assert await services.db.delete_event(requested_id)
        return event

    monkeypatch.setattr(services.events, "get", get_then_host_deletes)

    with ws_connect(client, event_id) as ws:
        assert expect_close(ws) == CLOSE_EVENT_DELETED

    monkeypatch.undo()
    assert services.realtime.total_connections == 0
    assert services.realtime.connections_for_event(event_id) == 0
    assert_error(client.get(f"/api/events/{event_id}"), 404, "EVENT_NOT_FOUND")


# =============================================================================================
# Fix 9: middleware (root_path, 413 handler, ClientDisconnect, Host allow-list)
# =============================================================================================


def test_upload_under_root_path_gets_upload_budget_and_api_headers(client):
    event_id = new_event(client)["event"]["id"]
    body, content_type = multipart(noisy_png((200, 200)))
    assert JSON_BODY_LIMIT < len(body) < client.app.state.settings.max_request_bytes

    response, _ = asgi_call(
        client,
        "POST",
        f"/gk/api/events/{event_id}/photos",
        root_path="/gk",
        headers=[("content-type", content_type), ("content-length", str(len(body)))],
        chunks=[body],
    )

    assert response is not None
    assert response.status_code == 201, response.text
    assert response.headers.get("cache-control") == "no-store"
    photo = response.json()

    listing, _ = asgi_call(client, "GET", f"/gk/api/events/{event_id}/photos", root_path="/gk")
    assert listing is not None and listing.status_code == 200, listing and listing.text
    assert listing.headers.get("cache-control") == "no-store"
    assert [item["id"] for item in listing.json()["items"]] == [photo["id"]]

    event, _ = asgi_call(client, "GET", f"/gk/api/events/{event_id}", root_path="/gk")
    assert event is not None and event.status_code == 200
    assert_security_headers(event.headers)

    missing, _ = asgi_call(client, "GET", "/gk/api/events/zzzzzzzzzzzz", root_path="/gk")
    assert missing is not None
    assert_error(missing, 404, "EVENT_NOT_FOUND")
    assert_security_headers(missing.headers)

    # Non-upload routes under the root path keep the small JSON budget.
    big_json = b'{"name": "' + b"x" * (JSON_BODY_LIMIT + 10) + b'"}'
    too_big, _ = asgi_call(
        client,
        "POST",
        "/gk/api/events",
        root_path="/gk",
        headers=[("content-type", "application/json"), ("content-length", str(len(big_json)))],
        chunks=[big_json],
    )
    assert too_big is not None
    assert assert_error(too_big, 413, "PAYLOAD_TOO_LARGE")["details"] == {"max_bytes": JSON_BODY_LIMIT}


def test_streamed_json_overflow_413_matches_declared_length_413(client):
    big_json = b'{"name": "' + b"x" * (JSON_BODY_LIMIT + 1024) + b'"}'
    base_headers = [("content-type", "application/json"), ("origin", ORIGIN)]

    declared, _ = asgi_call(
        client, "POST", "/api/events", headers=[*base_headers, ("content-length", str(len(big_json)))], chunks=[big_json]
    )
    streamed, _ = asgi_call(client, "POST", "/api/events", headers=base_headers, chunks=split(big_json, 8192))

    for response in (declared, streamed):
        assert response is not None
        error = assert_error(response, 413, "PAYLOAD_TOO_LARGE")
        assert error["details"] == {"max_bytes": JSON_BODY_LIMIT}
        assert response.headers.get("connection") == "close"
        assert response.headers.get("access-control-allow-origin") == ORIGIN
        assert_security_headers(response.headers)
    assert client.get("/api/health").status_code == 200


def test_streamed_upload_overflow_413_has_details_and_connection_close(make_client):
    client = make_client(max_upload_bytes=1024)
    event_id = new_event(client)["event"]["id"]
    limit = 1024 + MULTIPART_OVERHEAD
    body, content_type = multipart(os.urandom(limit + 4096), filename="big.jpg", content_type="image/jpeg")

    response, _ = asgi_call(
        client,
        "POST",
        f"/api/events/{event_id}/photos",
        headers=[("content-type", content_type), ("origin", ORIGIN)],
        chunks=split(body, 16 * 1024),
    )

    assert response is not None
    error = assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    assert error["details"] == {"max_bytes": limit}
    assert response.headers.get("connection") == "close"
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert client.get(f"/api/events/{event_id}").json()["photo_count"] == 0


def test_client_disconnect_mid_upload_is_not_an_error(client, caplog):
    event_id = new_event(client)["event"]["id"]
    body, content_type = multipart(noisy_png((200, 200)))
    caplog.set_level(logging.INFO)
    caplog.clear()

    response, messages = asgi_call(
        client,
        "POST",
        f"/api/events/{event_id}/photos",
        headers=[("content-type", content_type), ("content-length", str(len(body)))],
        chunks=[body[: len(body) // 3]],
        disconnect_after_body=True,
    )

    errors = [r for r in caplog.records if r.name.startswith("gambarkita") and r.levelno >= logging.ERROR]
    assert errors == [], [r.getMessage() for r in errors]
    assert response is None or response.status_code < 500, response.text
    assert not any(m["type"] == "http.response.start" and m["status"] >= 500 for m in messages)
    assert any(r.name == "gambarkita.errors" and r.levelno == logging.INFO for r in caplog.records)
    assert client.get(f"/api/events/{event_id}").json()["photo_count"] == 0


def test_untrusted_host_gets_json_envelope_with_security_and_cors_headers(make_client):
    client = make_client(trusted_hosts=["api.example.com"])

    for response in (
        client.get("/api/health", headers={"Host": "evil.example", "Origin": ORIGIN}),
        client.post("/api/events", json={"name": "x"}, headers={"Host": "evil.example:8000", "Origin": ORIGIN}),
    ):
        assert_error(response, 400, "BAD_REQUEST")
        assert_security_headers(response.headers)
        assert response.headers.get("access-control-allow-origin") == ORIGIN

    ok = client.get("/api/health", headers={"Host": "API.Example.COM:8443"})
    assert ok.status_code == 200, ok.text


def test_untrusted_host_websocket_is_rejected_during_handshake(make_client):
    client = make_client(trusted_hosts=["api.example.com"])
    event_id = new_event(client, Host="api.example.com")["event"]["id"]

    with pytest.raises(WebSocketDisconnect) as info:
        with ws_connect(client, event_id, subprotocols=[WS_V1], headers={"host": "evil.example"}):
            pytest.fail("handshake should have been rejected")
    assert not isinstance(info.value, WebSocketDenialResponse)
    assert info.value.code == CLOSE_POLICY_VIOLATION
    assert services_of(client).realtime.total_connections == 0

    with live(client, event_id, subprotocols=[WS_V1], headers={"host": "api.example.com"}) as ws:
        assert_ping_pong(ws)


def test_wildcard_trusted_host_matches_subdomains_only(make_client):
    client = make_client(trusted_hosts=["*.example.com"])

    for host in ("a.example.com", "deep.sub.example.com:8000", "A.EXAMPLE.COM"):
        response = client.get("/api/health", headers={"Host": host})
        assert response.status_code == 200, (host, response.text)
    for host in ("example.com", "notexample.com", "example.com.evil.test", "evil.test"):
        assert_error(client.get("/api/health", headers={"Host": host}), 400, "BAD_REQUEST")


def test_ipv6_literal_trusted_host_with_port(make_client):
    client = make_client(trusted_hosts=["[::1]"])

    for host in ("[::1]:8000", "[::1]"):
        response, _ = asgi_call(client, "GET", "/api/health", headers=[("host", host)])
        assert response is not None and response.status_code == 200, (host, response and response.text)
    for host in ("[::2]:8000", "::1", "[::10]"):
        response, _ = asgi_call(client, "GET", "/api/health", headers=[("host", host)])
        assert response is not None
        assert_error(response, 400, "BAD_REQUEST")


def test_malformed_host_headers_are_rejected(make_client):
    exact = make_client(trusted_hosts=["api.example.com"])
    wildcard = make_client(trusted_hosts=["*.example.com"])

    cases = [
        (exact, "api.example.com:80@evil.example"),
        (exact, "api.example.com:notaport"),
        (wildcard, "evil.test/.example.com"),
        (wildcard, "evil.test?.example.com"),
    ]
    for client, host in cases:
        response, _ = asgi_call(client, "GET", "/api/health", headers=[("host", host)])
        assert response is not None
        assert_error(response, 400, "BAD_REQUEST")


def test_star_anywhere_in_trusted_hosts_allows_any_host(make_client):
    client = make_client(trusted_hosts="*,api.example.com")

    assert client.get("/api/health", headers={"Host": "api.example.com"}).status_code == 200
    response = client.get("/api/health", headers={"Host": "other.example"})
    assert response.status_code == 200, response.text


# =============================================================================================
# Fix 11: ids of any length, millisecond timestamps, configuration, run.py
# =============================================================================================

LONG_IDS = [
    "a" * 65,
    "abcdefghijkl" * 20,
    "z" * 5000,
    "ü" * 65,
    "Mixed-Case_and.dots~" * 5,
]


@pytest.mark.parametrize("bad_id", LONG_IDS, ids=["65", "240", "5000", "unicode-65", "charset-100"])
def test_event_ids_of_any_length_or_charset_are_404(client, bad_id):
    created = new_event(client)
    token = created["host_token"]
    base = f"/api/events/{bad_id}"

    responses = {
        "get": client.get(base),
        "photos": client.get(f"{base}/photos"),
        "upload": post_photo(client, bad_id),
        "patch": client.patch(base, json={"name": "x"}, headers=auth(token)),
        "delete": client.delete(base, headers=auth(token)),
        "verify": client.post(f"{base}/host/verify", headers=auth(token)),
        "delete_photo": client.delete(f"{base}/photos/{'f' * 32}", headers=auth(token)),
    }
    for name, response in responses.items():
        assert response.status_code == 404, (name, response.status_code, response.text[:200])
        assert_error(response, 404, "EVENT_NOT_FOUND")

    with ws_connect(client, bad_id) as ws:
        assert expect_close(ws) == CLOSE_EVENT_NOT_FOUND


@pytest.mark.parametrize("bad_id", ["f" * 65, "0123456789abcdef" * 300, "é" * 70], ids=["65", "4800", "unicode"])
def test_photo_ids_of_any_length_or_charset_are_404(client, bad_id):
    created = new_event(client)
    event_id, token = created["event"]["id"], created["host_token"]

    response = client.delete(f"/api/events/{event_id}/photos/{bad_id}", headers=auth(token))

    assert_error(response, 404, "PHOTO_NOT_FOUND")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_created_at_is_identical_in_responses_websocket_and_later_reads(client):
    created = new_event(client, "Timestamps")
    event_id = created["event"]["id"]
    event_created_at = created["event"]["created_at"]

    assert client.get(f"/api/events/{event_id}").json()["created_at"] == event_created_at

    uploads: list[dict[str, Any]] = []
    pushed: list[dict[str, Any]] = []
    with ws_connect(client, event_id) as ws:
        assert expect_hello(ws, event_id)["data"]["created_at"] == event_created_at
        for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
            response = post_photo(client, event_id, image_bytes(color=color))
            assert response.status_code == 201, response.text
            uploads.append(response.json())
            message = recv(ws)
            assert message["type"] == "photo.created"
            pushed.append(message["data"])

    listed = {item["id"]: item for item in client.get(f"/api/events/{event_id}/photos").json()["items"]}
    for uploaded, message in zip(uploads, pushed, strict=True):
        assert message["id"] == uploaded["id"]
        assert message["created_at"] == uploaded["created_at"]
        assert listed[uploaded["id"]]["created_at"] == uploaded["created_at"]
        assert message == listed[uploaded["id"]] == uploaded

    for stamp in [event_created_at, *(upload["created_at"] for upload in uploads)]:
        assert _parse_timestamp(stamp).microsecond % 1000 == 0, stamp


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_blank_event_creation_key_means_no_key_required(make_client, monkeypatch, blank):
    monkeypatch.setenv("GK_EVENT_CREATION_KEY", blank)
    assert Settings(_env_file=None).event_creation_key is None
    client = make_client()

    assert client.get("/api/config").json()["event_creation_requires_key"] is False
    response = client.post("/api/events", json={"name": "Open creation"})
    assert response.status_code == 201, response.text

    explicit = make_client(event_creation_key=blank)
    assert explicit.post("/api/events", json={"name": "Also open"}).status_code == 201


def test_short_event_creation_key_is_rejected(make_client):
    for weak in ("short", "x" * 11):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, event_creation_key=weak)

    client = make_client(event_creation_key="x" * 12)
    assert_error(client.post("/api/events", json={"name": "Keyed"}), 401, "UNAUTHORIZED")
    keyed = client.post("/api/events", json={"name": "Keyed"}, headers={"X-Event-Creation-Key": "x" * 12})
    assert keyed.status_code == 201, keyed.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_stored_bytes", 64 * 1024 - 1),
        ("max_stored_bytes", 100 * 1024 * 1024 + 1),
        ("image_processing_pixel_budget", 999_999),
        ("image_processing_concurrency", 0),
        ("image_processing_concurrency", 65),
        ("image_processing_queue_timeout", 0),
        ("max_lossless_image_pixels", 0),
        ("ws_reserved_host_connections", -1),
    ],
)
def test_new_settings_reject_out_of_range_values(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_new_settings_accept_boundary_values_and_env_overrides(monkeypatch):
    settings = Settings(
        _env_file=None,
        max_stored_bytes=64 * 1024,
        image_processing_pixel_budget=1_000_000,
        image_processing_concurrency=64,
        ws_reserved_host_connections=0,
    )
    assert settings.max_stored_bytes == 64 * 1024
    assert settings.image_processing_pixel_budget == 1_000_000
    assert settings.image_processing_concurrency == 64
    assert settings.ws_reserved_host_connections == 0

    monkeypatch.setenv("GK_MAX_STORED_BYTES", str(100 * 1024 * 1024))
    monkeypatch.setenv("GK_IMAGE_PROCESSING_PIXEL_BUDGET", "5000000")
    from_env = Settings(_env_file=None)
    assert from_env.max_stored_bytes == 100 * 1024 * 1024
    assert from_env.image_processing_pixel_budget == 5_000_000


def test_run_main_bounds_graceful_shutdown(monkeypatch, settings_factory):
    import uvicorn

    import run

    captured: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(run, "get_settings", lambda: settings_factory())
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: captured.append((args, kwargs)))

    run.main()

    assert len(captured) == 1
    args, kwargs = captured[0]
    assert args == ("app.main:create_app",)
    assert kwargs["factory"] is True
    assert kwargs["workers"] == 1
    timeout = kwargs.get("timeout_graceful_shutdown")
    assert isinstance(timeout, int | float) and 0 < timeout <= 60, timeout


def test_project_requires_python_312():
    project = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    spec = SpecifierSet(project["requires-python"])

    assert "3.12.0" in spec
    assert "3.11.9" not in spec
