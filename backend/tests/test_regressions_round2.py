"""Regression tests for the second review round (fixes to the fixes)."""

import asyncio
import logging
import os
import stat
import threading
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.errors import ServiceBusyError
from app.services import photos as photos_module
from app.services.admission import ProcessingBudget
from app.services.storage import LocalStorage
from tests.conftest import ORIGIN, image_bytes

WS_HEADERS = {"origin": ORIGIN}
UNKNOWN_EVENT_ID = "zzzzzzzzzzzz"


def ws_url(event_id: str) -> str:
    return f"/ws/events/{event_id}"


def host_protocols(token: str) -> list[str]:
    return ["gambarkita.v1", f"gambarkita.host.{token}"]


def close_code(client: Any, url: str, **kwargs: Any) -> int | None:
    """Connect and return the close code, or None if the server sent hello."""
    try:
        # A copy: TestClient.websocket_connect writes the subprotocol header into the dict it is given.
        with client.websocket_connect(url, headers=dict(WS_HEADERS), **kwargs) as ws:
            message = ws.receive_json()
            assert message["type"] == "hello"
            return None
    except WebSocketDisconnect as exc:
        return exc.code


# --- ProcessingBudget: FIFO admission -------------------------------------------------------


def test_budget_admission_is_fifo_so_a_large_photo_is_not_starved() -> None:
    async def scenario() -> list[str]:
        budget = ProcessingBudget(max_pixels=100, max_jobs=10, queue_timeout=5)
        order: list[str] = []
        first = await budget.acquire(60)

        async def job(name: str, pixels: int) -> int:
            weight = await budget.acquire(pixels)
            order.append(name)
            return weight

        large = asyncio.create_task(job("large", 80))  # 60 + 80 > 100: must wait
        await asyncio.sleep(0)
        small = asyncio.create_task(job("small", 10))  # would fit (70) but must queue behind "large"
        await asyncio.sleep(0.05)
        assert order == [], "a later, smaller job must not jump the queue"
        budget.release(first)
        budget.release(await large)
        budget.release(await small)
        assert (budget.pixels_in_use, budget.active_jobs, budget.queued) == (0, 0, 0)
        return order

    assert asyncio.run(scenario()) == ["large", "small"]


def test_budget_timeout_of_the_queue_head_lets_the_next_waiter_in() -> None:
    async def scenario() -> None:
        budget = ProcessingBudget(max_pixels=100, max_jobs=10, queue_timeout=0.1)
        held = await budget.acquire(90)
        head = asyncio.create_task(budget.acquire(50))  # times out
        await asyncio.sleep(0)
        with pytest.raises(ServiceBusyError):
            await head
        assert budget.queued == 0
        budget.release(held)
        assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)

    asyncio.run(scenario())


def test_processing_reservation_is_held_until_the_decode_thread_finishes(make_client, monkeypatch) -> None:
    client = make_client()
    photos = client.app.state.services.photos
    started, release = threading.Event(), threading.Event()
    real_process = photos_module.process_image

    def slow_process(*args: Any, **kwargs: Any) -> Any:
        started.set()
        release.wait(10)
        return real_process(*args, **kwargs)

    monkeypatch.setattr(photos_module, "process_image", slow_process)

    async def scenario() -> tuple[int, int]:
        task = asyncio.ensure_future(photos._process(image_bytes()))
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()  # e.g. the client went away, or a shutdown timeout
        with pytest.raises(asyncio.CancelledError):
            await task
        during = photos._budget.active_jobs  # the thread is still decoding
        release.set()
        for _ in range(500):
            if photos._budget.active_jobs == 0:
                break
            await asyncio.sleep(0.01)
        return during, photos._budget.active_jobs

    during, after = client.portal.call(scenario)
    assert during == 1, "the reservation must outlive the cancelled request while the thread runs"
    assert after == 0


# --- WebSockets: host privileges are bounded ------------------------------------------------


def test_host_token_grants_only_the_reserved_slots(make_client) -> None:
    client = make_client(ws_max_connections_per_ip=1, ws_max_connections_per_event=5, ws_reserved_host_connections=1)
    created = client.post("/api/events", json={"name": "Wall"}).json()
    url = ws_url(created["event"]["id"])
    protocols = host_protocols(created["host_token"])

    with client.websocket_connect(url, headers=dict(WS_HEADERS), subprotocols=protocols) as first_host:
        assert first_host.receive_json()["type"] == "hello"
        # The only reserved slot is taken; a second host connection is an ordinary
        # viewer and is subject to the per-IP cap (1, already used by the first host).
        assert close_code(client, url, subprotocols=protocols) == 1013

    services = client.app.state.services
    assert services.realtime.total_connections == 0
    assert services.realtime._host_slots == {}


def test_guests_exhausting_the_shared_attempt_budget_do_not_lock_out_the_host(make_client) -> None:
    client = make_client(rate_limit_enabled=True, ws_connect_rate="2/minute")
    created = client.post("/api/events", json={"name": "Wall"}).json()
    url = ws_url(created["event"]["id"])

    # Everyone at the venue shares one IP; guests burn the anonymous budget.
    assert close_code(client, ws_url(UNKNOWN_EVENT_ID)) == 4404
    assert close_code(client, ws_url(UNKNOWN_EVENT_ID)) == 4404
    assert close_code(client, url) == 4429

    assert close_code(client, url, subprotocols=host_protocols(created["host_token"])) is None
    # A wrong token gets no special treatment.
    assert close_code(client, url, subprotocols=host_protocols("x" * 43)) == 4429


# --- Logging & deployment safety --------------------------------------------------------------


def test_host_tokens_are_redacted_from_handshake_debug_logs(make_client, caplog) -> None:
    make_client()  # installs the redaction filters
    token = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcd"
    with caplog.at_level(logging.DEBUG, logger="uvicorn.error"):
        logging.getLogger("uvicorn.error").debug(
            "< %s: %s", "sec-websocket-protocol", f"gambarkita.v1, gambarkita.host.{token}"
        )
    messages = [record.getMessage() for record in caplog.records]
    assert messages and all(token not in message for message in messages)
    assert any("gambarkita.host.<redacted>" in message for message in messages)


def test_trusting_forwarded_headers_from_anyone_is_warned_about(make_client, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="gambarkita"):
        make_client(forwarded_allow_ips="*")
    assert any("GK_FORWARDED_ALLOW_IPS='*'" in record.getMessage() for record in caplog.records)


# --- Deletion robustness ----------------------------------------------------------------------


@pytest.fixture
def storage(tmp_path: Any) -> LocalStorage:
    storage = LocalStorage(tmp_path / "unit-storage")
    storage.ensure_layout()
    return storage


def test_read_only_photo_files_are_still_deleted(storage) -> None:
    event_id = "abcdefghijkl"
    storage.create_event_dirs(event_id)
    name, thumb = "a" * 32 + ".jpg", "a" * 32 + ".webp"
    storage.save_photo(event_id, name, b"x", thumb, b"y")
    for path in (storage.photo_path(event_id, name), storage.thumbnail_path(event_id, thumb)):
        os.chmod(path, stat.S_IREAD)

    assert storage.delete_photo(event_id, name, thumb) is True
    assert not storage.photo_path(event_id, name).exists()


def test_read_only_files_inside_an_event_are_deleted_with_it(storage) -> None:
    event_id = "abcdefghijkl"
    storage.create_event_dirs(event_id)
    storage.save_photo(event_id, "b" * 32 + ".jpg", b"x", "b" * 32 + ".webp", b"y")
    os.chmod(storage.photo_path(event_id, "b" * 32 + ".jpg"), stat.S_IREAD)

    assert storage.delete_event(event_id) is True
    assert not (storage.events_root / event_id).exists()


def test_a_regular_file_in_place_of_an_event_dir_is_removed(storage) -> None:
    (storage.events_root / "abcdefghijkl").write_bytes(b"not a directory")

    assert storage.delete_event("abcdefghijkl") is True
    assert not (storage.events_root / "abcdefghijkl").exists()


# --- Round 3 ------------------------------------------------------------------------------------


def test_host_slots_cannot_multiply_one_networks_connections(make_client) -> None:
    client = make_client(ws_max_connections_per_ip=1, ws_max_connections_per_event=10, ws_reserved_host_connections=1)
    events = [client.post("/api/events", json={"name": f"E{i}"}).json() for i in range(3)]

    def connect(created: dict[str, Any]) -> Any:
        return client.websocket_connect(
            ws_url(created["event"]["id"]), headers=dict(WS_HEADERS), subprotocols=host_protocols(created["host_token"])
        )

    with connect(events[0]) as first, connect(events[1]) as second:
        assert first.receive_json()["type"] == second.receive_json()["type"] == "hello"
        # per-IP cap (1) + one reserve (1) = 2: a third self-created event adds nothing.
        with connect(events[2]) as third, pytest.raises(WebSocketDisconnect) as closed:
            third.receive_json()
        assert closed.value.code == 1013
    assert client.app.state.services.realtime.total_connections == 0


@pytest.mark.parametrize(
    ("allowed", "host_header"),
    [
        ("photos.example.com", "photos.example.com."),  # trailing-dot FQDN
        ("photos.example.com.", "photos.example.com"),
        ("gambarkita_api", "gambarkita_api:8000"),  # docker-compose service names
        ("*.example.com", "api_v2.example.com"),
    ],
)
def test_valid_host_headers_are_accepted(make_client, allowed: str, host_header: str) -> None:
    client = make_client(trusted_hosts=[allowed])
    assert client.get("/api/health", headers={"host": host_header}).status_code == 200


def test_host_tokens_are_redacted_from_asgi_trace_logs_and_tracebacks(make_client, caplog) -> None:
    make_client()
    token = "ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210_-abcd"
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("uvicorn.asgi").debug("ASGI scope: %s", {"subprotocols": [f"gambarkita.host.{token}"]})
        try:
            raise ValueError(f"invalid Sec-WebSocket-Protocol header: gambarkita.v1 gambarkita.host.{token}")
        except ValueError:
            logging.getLogger("uvicorn.error").exception("handshake failed")
    formatter = logging.Formatter()
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert caplog.records and token not in rendered
    assert rendered.count("gambarkita.host.<redacted>") >= 2


def test_host_token_checks_are_rate_limited_before_touching_the_database(make_client, monkeypatch) -> None:
    client = make_client(rate_limit_enabled=True, ws_connect_rate="2/minute")
    db = client.app.state.services.db
    reads = 0
    real_get_event = db.get_event

    async def counting_get_event(event_id: str) -> Any:
        nonlocal reads
        reads += 1
        return await real_get_event(event_id)

    monkeypatch.setattr(db, "get_event", counting_get_event)
    junk = host_protocols("j" * 43)

    assert close_code(client, ws_url(UNKNOWN_EVENT_ID), subprotocols=junk) == 4404
    assert reads == 1, "a failed lookup is not repeated"
    codes = [close_code(client, ws_url(UNKNOWN_EVENT_ID), subprotocols=junk) for _ in range(10)]
    assert set(codes[1:]) == {4429}
    assert reads <= 2, f"token checks must be budgeted before the database is read ({reads} reads)"


def test_cancelled_persist_never_deletes_files_of_a_row_that_commits(make_client, monkeypatch) -> None:
    import time  # noqa: PLC0415

    import anyio  # noqa: PLC0415

    from app.models import PhotoRecord, utcnow  # noqa: PLC0415
    from app.services.image_processing import process_image  # noqa: PLC0415

    client = make_client()
    services = client.app.state.services
    event_id = client.post("/api/events", json={"name": "Race"}).json()["event"]["id"]
    photos = services.photos
    processed = process_image(image_bytes(), photos._policy)
    photo = PhotoRecord(
        id="c" * 32, event_id=event_id, filename="c" * 32 + ".jpg", thumbnail_filename="c" * 32 + ".webp",
        content_type="image/jpeg", width=64, height=48, size_bytes=len(processed.original.data),
        uploader_name=None, caption=None, created_at=utcnow(),
    )
    photo_file = services.storage.photo_path(event_id, photo.filename)

    async def slow_run(func: Any, *args: Any) -> Any:  # every DB call lingers in its worker thread
        def slow() -> Any:
            time.sleep(0.2)
            return func(*args)

        return await anyio.to_thread.run_sync(slow)

    monkeypatch.setattr(services.db, "_run", slow_run)

    async def scenario() -> None:
        task = asyncio.ensure_future(photos._persist(photo, processed))
        while not photo_file.exists():  # files written: the insert is now running
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.5)  # the insert thread commits after the cancellation

    client.portal.call(scenario)
    monkeypatch.undo()

    listing = client.get(f"/api/events/{event_id}/photos").json()["items"]
    assert [item["id"] for item in listing] == [photo.id], "the row committed"
    assert photo_file.exists(), "its files must still be there"
    assert client.get(f"/media/events/{event_id}/photos/{photo.filename}").status_code == 200


def test_created_at_order_matches_insertion_order(make_client) -> None:
    """Timestamps are taken inside the insert transaction, so newest-first by time
    and newest-first by pagination order (seq) always agree."""
    client = make_client()
    event_id = client.post("/api/events", json={"name": "Order"}).json()["event"]["id"]
    for _ in range(5):
        response = client.post(
            f"/api/events/{event_id}/photos", files={"file": ("p.jpg", image_bytes(), "image/jpeg")}
        )
        assert response.status_code == 201
    items = client.get(f"/api/events/{event_id}/photos").json()["items"]
    stamps = [item["created_at"] for item in items]
    assert stamps == sorted(stamps, reverse=True)
