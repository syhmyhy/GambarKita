"""WebSocket live-update channel: /ws/events/{event_id}.

TestClient's WebSocket ``receive`` blocks forever when nothing is sent, so every
receive here goes through ``recv`` (bounded by a timeout) and every test is
designed so the message it waits for is always sent. Absence of a message is
proven with ping/pong ordering: a single writer task per socket delivers queued
messages in order, so if the first thing received after a ping is the pong,
nothing else was queued before it.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.conftest import ORIGIN, auth, image_bytes

RECEIVE_TIMEOUT = 5.0
PING = {"type": "ping"}
PONG = {"type": "pong"}

CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_POLICY_VIOLATION = 1008
CLOSE_MESSAGE_TOO_BIG = 1009
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_EVENT_NOT_FOUND = 4404
CLOSE_EVENT_DELETED = 4410
CLOSE_RATE_LIMITED = 4429


# --- Helpers ----------------------------------------------------------------------------


def ws_url(event_id: str) -> str:
    return f"/ws/events/{event_id}"


def connect(client: TestClient, event_id: str, origin: str | None = ORIGIN) -> Any:
    """Open a WebSocket session (use as a context manager). ``origin=None`` omits the header."""
    headers = {} if origin is None else {"origin": origin}
    return client.websocket_connect(ws_url(event_id), headers=headers)


def _receive_raw(ws: Any, timeout: float) -> dict[str, Any]:
    receive_stream = getattr(ws, "_send_rx", None)
    if receive_stream is None:  # pragma: no cover - older/newer Starlette internals
        return ws.receive()

    async def receive_with_timeout() -> dict[str, Any]:
        with anyio.fail_after(timeout):
            return await receive_stream.receive()

    return ws.portal.call(receive_with_timeout)


def recv(ws: Any, timeout: float = RECEIVE_TIMEOUT) -> Any:
    """Like ``receive_json`` but fails (TimeoutError) instead of hanging forever.

    Raises ``WebSocketDisconnect`` when the server closed the connection.
    """
    message = _receive_raw(ws, timeout)
    if message["type"] == "websocket.close":
        raise WebSocketDisconnect(code=message.get("code", 1000), reason=message.get("reason", ""))
    assert message["type"] == "websocket.send", message
    assert message.get("text") is not None, f"expected a text frame, got {message!r}"
    return json.loads(message["text"])


def expect_close(ws: Any) -> int:
    """Assert that the next frame is a close frame and return its code."""
    with pytest.raises(WebSocketDisconnect) as info:
        unexpected = recv(ws)
        pytest.fail(f"expected the server to close the socket, received {unexpected!r}")
    return info.value.code


def expect_hello(ws: Any, event_id: str) -> dict[str, Any]:
    hello = recv(ws)
    assert hello["type"] == "hello"
    assert hello["event_id"] == event_id
    return hello


def assert_ping_pong(ws: Any) -> None:
    ws.send_json(PING)
    assert recv(ws) == PONG


@contextmanager
def live(client: TestClient, event_id: str, origin: str | None = ORIGIN) -> Iterator[Any]:
    """Connect and consume the hello message."""
    with connect(client, event_id, origin) as ws:
        expect_hello(ws, event_id)
        yield ws


def new_event(client: TestClient, name: str = "Live Event") -> dict[str, Any]:
    response = client.post("/api/events", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client: TestClient, event_id: str, data: bytes | None = None) -> dict[str, Any]:
    response = client.post(
        f"/api/events/{event_id}/photos",
        files={"file": ("photo.jpg", data if data is not None else image_bytes(), "image/jpeg")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def realtime(client: TestClient) -> Any:
    return client.app.state.services.realtime


# --- Protocol basics ------------------------------------------------------------------


def test_hello_carries_event_and_current_photo_count(client):
    created = new_event(client, "Hello Party")
    event_id = created["event"]["id"]
    upload(client, event_id)
    upload(client, event_id, image_bytes("PNG"))

    with connect(client, event_id) as ws:
        hello = recv(ws)

    assert hello["type"] == "hello"
    assert hello["event_id"] == event_id
    assert hello["data"]["id"] == event_id
    assert hello["data"]["name"] == "Hello Party"
    assert hello["data"]["photo_count"] == 2
    assert hello["data"] == client.get(f"/api/events/{event_id}").json()


def test_hello_for_empty_event_reports_zero_photos(client):
    event_id = new_event(client)["event"]["id"]
    with connect(client, event_id) as ws:
        assert expect_hello(ws, event_id)["data"]["photo_count"] == 0


def test_ping_gets_pong(client):
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id) as ws:
        assert_ping_pong(ws)
        assert_ping_pong(ws)


@pytest.mark.parametrize(
    "frame",
    [
        "not json at all",
        "{",
        '{"type": "subscribe"}',
        '{"type": "PING"}',
        '{"kind": "ping"}',
        '"ping"',
        '["ping"]',
        "null",
        "42",
        "",
    ],
    ids=[
        "garbage",
        "truncated-json",
        "other-type",
        "wrong-case",
        "no-type-key",
        "json-string",
        "json-list",
        "json-null",
        "json-number",
        "empty",
    ],
)
def test_non_ping_and_invalid_messages_are_ignored_and_connection_stays_open(client, frame):
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id) as ws:
        ws.send_text(frame)
        # If the frame had produced any reply or closed the socket, it would arrive before this pong.
        assert_ping_pong(ws)


# --- Broadcasts -----------------------------------------------------------------------


def test_upload_broadcasts_photo_created_to_all_subscribers_of_that_event_only(client):
    event_a = new_event(client, "A")["event"]["id"]
    event_b = new_event(client, "B")["event"]["id"]

    with live(client, event_a) as wall_1, live(client, event_a) as wall_2, live(client, event_b) as other:
        photo = upload(client, event_a)

        for ws in (wall_1, wall_2):
            message = recv(ws)
            assert message == {"type": "photo.created", "event_id": event_a, "data": photo}
            assert_ping_pong(ws)  # exactly one broadcast per upload

        # The broadcast was queued before the REST response returned, so had the other
        # event's subscriber been sent anything, it would arrive before this pong.
        assert_ping_pong(other)


def test_photo_created_payload_describes_the_stored_photo(client):
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id) as ws:
        photo = upload(client, event_id, image_bytes("WEBP"))
        message = recv(ws)

    assert message == {"type": "photo.created", "event_id": event_id, "data": photo}
    data = message["data"]
    assert data["content_type"] == "image/webp"
    assert data["url"] == f"/media/events/{event_id}/photos/{data['id']}.webp"
    assert data["thumbnail_url"] == f"/media/events/{event_id}/thumbs/{data['id']}.webp"
    assert client.get(data["url"]).status_code == 200
    listed = client.get(f"/api/events/{event_id}/photos").json()["items"]
    assert [item["id"] for item in listed] == [data["id"]]


def test_photo_created_payload_is_identical_to_rest_listing_item(client):
    """The protocol tells clients to merge the live feed with GET /photos; both must describe a photo identically."""
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id) as ws:
        upload(client, event_id)
        message = recv(ws)

    listed = client.get(f"/api/events/{event_id}/photos").json()["items"]
    assert len(listed) == 1
    live_item, rest_item = dict(message["data"]), dict(listed[0])
    live_created, rest_created = live_item.pop("created_at"), rest_item.pop("created_at")
    assert live_item == rest_item  # every other field already agrees
    assert live_created == rest_created  # e.g. "...:17.040358Z" (live) vs "...:17.040000Z" (listing)


def test_failed_upload_does_not_broadcast(client):
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id) as ws:
        response = client.post(
            f"/api/events/{event_id}/photos",
            files={"file": ("evil.jpg", b"<svg onload=alert(1)></svg>", "image/jpeg")},
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
        assert_ping_pong(ws)


def test_host_photo_delete_broadcasts_photo_deleted(client):
    created = new_event(client)
    event_id = created["event"]["id"]
    photo = upload(client, event_id)

    with live(client, event_id) as ws:
        response = client.delete(
            f"/api/events/{event_id}/photos/{photo['id']}", headers=auth(created["host_token"])
        )
        assert response.status_code == 204
        assert recv(ws) == {"type": "photo.deleted", "event_id": event_id, "data": {"id": photo["id"]}}
        assert_ping_pong(ws)


def test_failed_photo_delete_does_not_broadcast(client):
    created = new_event(client)
    event_id = created["event"]["id"]
    photo = upload(client, event_id)

    with live(client, event_id) as ws:
        response = client.delete(f"/api/events/{event_id}/photos/{photo['id']}", headers=auth("x" * 43))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
        assert_ping_pong(ws)


def test_event_patch_broadcasts_event_updated(client):
    created = new_event(client, "Before")
    event_id = created["event"]["id"]

    with live(client, event_id) as ws:
        response = client.patch(
            f"/api/events/{event_id}",
            json={"name": "After", "uploads_open": False},
            headers=auth(created["host_token"]),
        )
        assert response.status_code == 200, response.text
        message = recv(ws)

    assert message == {"type": "event.updated", "event_id": event_id, "data": response.json()}
    assert message["data"]["name"] == "After"
    assert message["data"]["uploads_open"] is False


def test_event_delete_broadcasts_event_deleted_then_closes_4410(client):
    created = new_event(client)
    event_id = created["event"]["id"]
    other_event = new_event(client, "Other")["event"]["id"]

    with live(client, event_id) as first, live(client, event_id) as second, live(client, other_event) as other:
        response = client.delete(f"/api/events/{event_id}", headers=auth(created["host_token"]))
        assert response.status_code == 204
        for ws in (first, second):
            assert recv(ws) == {"type": "event.deleted", "event_id": event_id}
            assert expect_close(ws) == CLOSE_EVENT_DELETED
        assert_ping_pong(other)  # subscribers of other events are untouched

    assert realtime(client).total_connections == 0
    # The event is gone for new subscribers too.
    with connect(client, event_id) as ws:
        assert expect_close(ws) == CLOSE_EVENT_NOT_FOUND


# --- Admission control ----------------------------------------------------------------


@pytest.mark.parametrize(
    "event_id",
    ["zzzzzzzzzzzz", "ABCDEFGHIJKL", "short", "abcdefghijklm", "abc.def.ghij", "abcdefghij-k", "%2e%2e"],
    ids=["unknown", "uppercase", "too-short", "too-long", "dots", "dash", "dot-dot"],
)
def test_unknown_or_malformed_event_is_accepted_then_closed_4404(client, event_id):
    with connect(client, event_id) as ws:
        assert expect_close(ws) == CLOSE_EVENT_NOT_FOUND
    assert realtime(client).total_connections == 0


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "null",
        "http://localhost:5174",
        "https://localhost:5173",
        "http://localhost:5173.evil.example",
        "http://evil.example/http://localhost:5173",
        "file://",
        "not a url",
    ],
)
def test_disallowed_origin_is_rejected_during_handshake(client, origin):
    event_id = new_event(client)["event"]["id"]
    with pytest.raises(WebSocketDisconnect) as info:
        with connect(client, event_id, origin=origin):
            pytest.fail("handshake should have been rejected")
    assert info.value.code == CLOSE_POLICY_VIOLATION
    assert realtime(client).total_connections == 0


@pytest.mark.parametrize("origin", [ORIGIN, "http://127.0.0.1:5173", "HTTP://LOCALHOST:5173"])
def test_allowed_origins_are_accepted(client, origin):
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id, origin=origin) as ws:
        assert_ping_pong(ws)


def test_missing_origin_is_allowed_by_default(client):
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id, origin=None) as ws:
        assert_ping_pong(ws)


def test_missing_origin_is_rejected_when_disallowed(make_client):
    client = make_client(ws_allow_missing_origin=False)
    event_id = new_event(client)["event"]["id"]

    with pytest.raises(WebSocketDisconnect) as info:
        with connect(client, event_id, origin=None):
            pytest.fail("handshake should have been rejected")
    assert info.value.code == CLOSE_POLICY_VIOLATION

    # A browser-style connection with an allowed Origin still works.
    with live(client, event_id) as ws:
        assert_ping_pong(ws)


def test_wildcard_cors_origin_allows_any_websocket_origin(make_client):
    client = make_client(cors_origins="*")
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id, origin="https://anywhere.example") as ws:
        assert_ping_pong(ws)


# --- Connection caps ------------------------------------------------------------------


def test_per_ip_connection_cap_closes_extra_connection_1013_and_frees_slot(make_client):
    client = make_client(ws_max_connections_per_ip=1)
    event_a = new_event(client, "A")["event"]["id"]
    event_b = new_event(client, "B")["event"]["id"]

    with live(client, event_a) as first:
        # The cap is per IP, so it applies across events.
        for event_id in (event_a, event_b):
            with connect(client, event_id) as extra:
                assert expect_close(extra) == CLOSE_TRY_AGAIN_LATER
        assert realtime(client).total_connections == 1
        assert_ping_pong(first)  # the original connection is unaffected

    assert realtime(client).total_connections == 0
    # No slot leak: once the first socket is gone a new one is admitted, repeatedly.
    for _ in range(2):
        with live(client, event_b) as ws:
            assert_ping_pong(ws)


def test_per_event_connection_cap_closes_extra_connection_1013_and_frees_slot(make_client):
    client = make_client(ws_max_connections_per_event=1)
    event_a = new_event(client, "A")["event"]["id"]
    event_b = new_event(client, "B")["event"]["id"]

    with live(client, event_a) as first:
        with connect(client, event_a) as extra:
            assert expect_close(extra) == CLOSE_TRY_AGAIN_LATER
        # Another event has its own budget.
        with live(client, event_b) as other:
            assert_ping_pong(other)
        assert realtime(client).connections_for_event(event_a) == 1
        assert_ping_pong(first)

    assert realtime(client).connections_for_event(event_a) == 0
    with live(client, event_a) as ws:
        assert_ping_pong(ws)


def test_total_connection_cap_closes_extra_connection_1013(make_client):
    client = make_client(ws_max_connections_total=2)
    event_a = new_event(client, "A")["event"]["id"]
    event_b = new_event(client, "B")["event"]["id"]

    with live(client, event_a), live(client, event_b):
        with connect(client, event_a) as extra:
            assert expect_close(extra) == CLOSE_TRY_AGAIN_LATER
    with live(client, event_a) as ws:
        assert_ping_pong(ws)


def test_connection_counters_return_to_zero_after_all_sockets_close(client):
    event_a = new_event(client, "A")["event"]["id"]
    event_b = new_event(client, "B")["event"]["id"]
    manager = realtime(client)
    assert manager.total_connections == 0

    with live(client, event_a), live(client, event_a), live(client, event_b):
        assert manager.total_connections == 3
        assert manager.connections_for_event(event_a) == 2
        assert manager.connections_for_event(event_b) == 1
        # Rejected connections never hold a slot.
        with connect(client, "zzzzzzzzzzzz") as rejected:
            assert expect_close(rejected) == CLOSE_EVENT_NOT_FOUND
        assert manager.total_connections == 3

    assert manager.total_connections == 0
    assert manager.connections_for_event(event_a) == 0
    assert manager.connections_for_event(event_b) == 0


def test_shutdown_close_all_closes_live_sockets_1001(client):
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id) as first, live(client, event_id) as second:
        client.portal.call(realtime(client).close_all)
        assert expect_close(first) == 1001
        assert expect_close(second) == 1001
    assert realtime(client).total_connections == 0


def test_server_initiated_closes_release_their_slots(make_client):
    client = make_client(ws_max_message_bytes=64, ws_max_connections_per_ip=1)
    event_id = new_event(client)["event"]["id"]

    for _ in range(3):
        with live(client, event_id) as ws:
            ws.send_bytes(b"binary")
            assert expect_close(ws) == CLOSE_UNSUPPORTED_DATA
        assert realtime(client).total_connections == 0


# --- Rate limits and message policing -------------------------------------------------


def test_connect_rate_limit_closes_third_attempt_4429(make_client):
    client = make_client(rate_limit_enabled=True, ws_connect_rate="2/minute")
    event_id = new_event(client)["event"]["id"]

    for _ in range(2):
        with live(client, event_id) as ws:
            assert_ping_pong(ws)

    with connect(client, event_id) as ws:
        assert expect_close(ws) == CLOSE_RATE_LIMITED
    assert realtime(client).total_connections == 0


def test_connect_rate_limit_counts_attempts_for_unknown_events(make_client):
    client = make_client(rate_limit_enabled=True, ws_connect_rate="2/minute")
    event_id = new_event(client)["event"]["id"]

    for _ in range(2):
        with connect(client, "zzzzzzzzzzzz") as ws:
            assert expect_close(ws) == CLOSE_EVENT_NOT_FOUND

    with connect(client, event_id) as ws:
        assert expect_close(ws) == CLOSE_RATE_LIMITED


def test_connect_rate_limit_is_not_applied_when_rate_limiting_disabled(make_client):
    client = make_client(rate_limit_enabled=False, ws_connect_rate="1/minute")
    event_id = new_event(client)["event"]["id"]
    for _ in range(3):
        with live(client, event_id) as ws:
            assert_ping_pong(ws)


def test_message_larger_than_limit_closes_1009(make_client):
    client = make_client(ws_max_message_bytes=64)
    event_id = new_event(client)["event"]["id"]

    with live(client, event_id) as ws:
        ws.send_text("x" * 65)
        assert expect_close(ws) == CLOSE_MESSAGE_TOO_BIG
    assert realtime(client).total_connections == 0


def test_message_exactly_at_limit_is_processed(make_client):
    client = make_client(ws_max_message_bytes=64)
    event_id = new_event(client)["event"]["id"]
    prefix, suffix = '{"type":"ping","pad":"', '"}'
    frame = prefix + "x" * (64 - len(prefix) - len(suffix)) + suffix
    assert len(frame.encode("utf-8")) == 64

    with live(client, event_id) as ws:
        ws.send_text(frame)
        assert recv(ws) == PONG


def test_message_limit_counts_utf8_bytes_not_characters(make_client):
    client = make_client(ws_max_message_bytes=64)
    event_id = new_event(client)["event"]["id"]
    frame = "é" * 40  # 40 characters, 80 bytes
    assert len(frame) < 64 < len(frame.encode("utf-8"))

    with live(client, event_id) as ws:
        ws.send_text(frame)
        assert expect_close(ws) == CLOSE_MESSAGE_TOO_BIG


def test_binary_frame_closes_1003(client):
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id) as ws:
        ws.send_bytes(json.dumps(PING).encode("utf-8"))
        assert expect_close(ws) == CLOSE_UNSUPPORTED_DATA
    assert realtime(client).total_connections == 0


def test_message_flood_closes_1008(make_client):
    client = make_client(rate_limit_enabled=True, ws_message_rate="3/minute")
    event_id = new_event(client)["event"]["id"]

    with live(client, event_id) as ws:
        for _ in range(3):
            assert_ping_pong(ws)
        ws.send_json(PING)
        assert expect_close(ws) == CLOSE_POLICY_VIOLATION
    assert realtime(client).total_connections == 0


def test_message_budget_is_not_applied_when_rate_limiting_disabled(make_client):
    client = make_client(rate_limit_enabled=False, ws_message_rate="1/minute")
    event_id = new_event(client)["event"]["id"]
    with live(client, event_id) as ws:
        for _ in range(5):
            assert_ping_pong(ws)


def test_ignored_messages_still_count_against_the_message_budget(make_client):
    client = make_client(rate_limit_enabled=True, ws_message_rate="3/minute")
    event_id = new_event(client)["event"]["id"]

    with live(client, event_id) as ws:
        ws.send_text("junk")
        ws.send_text('{"type": "other"}')
        assert_ping_pong(ws)
        ws.send_text("junk")
        assert expect_close(ws) == CLOSE_POLICY_VIOLATION


def test_message_budget_is_per_connection(make_client):
    client = make_client(rate_limit_enabled=True, ws_message_rate="2/minute")
    event_id = new_event(client)["event"]["id"]

    with live(client, event_id) as first, live(client, event_id) as second:
        for _ in range(2):
            assert_ping_pong(first)
        # Exhausting the first socket's budget does not affect the second.
        for _ in range(2):
            assert_ping_pong(second)
        first.send_json(PING)
        assert expect_close(first) == CLOSE_POLICY_VIOLATION
