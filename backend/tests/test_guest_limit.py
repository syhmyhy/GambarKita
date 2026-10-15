"""Per-guest photo limit (GK_MAX_PHOTOS_PER_GUEST).

A guest is the random key their browser keeps for the event, sent as
``X-Guest-Key``. While the limit is on, the server stores only a hash scoped to
the event, checks the limit before the body is read, and re-checks it inside the
insert transaction. While it is off, nothing about the guest is stored.
"""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.security import guest_key_hash, hash_token
from app.main import create_app
from app.models import PhotoRecord, utcnow
from app.services.database import InsertPhotoResult
from app.services.storage import LocalStorage
from tests.conftest import ORIGIN, auth, image_bytes

HEADER = "X-Guest-Key"


def new_key() -> str:
    return secrets.token_urlsafe(32)  # what the browser sends: 43 base64url characters


def upload(client: TestClient, event_id: str, key: str | None) -> Any:
    return client.post(
        f"/api/events/{event_id}/photos",
        files={"file": ("photo.jpg", image_bytes(), "image/jpeg")},
        headers={HEADER: key} if key is not None else {},
    )


def new_event(client: TestClient, name: str = "Guest Limit") -> dict[str, Any]:
    response = client.post("/api/events", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def quota(client: TestClient, event_id: str, key: str | None) -> Any:
    return client.get(f"/api/events/{event_id}/guest-quota", headers={HEADER: key} if key is not None else {})


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    return error


def media_files(client: TestClient) -> list[str]:
    root = client.app.state.settings.storage_dir / "events"
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def photo_ids(client: TestClient, event_id: str) -> list[str]:
    return [item["id"] for item in client.get(f"/api/events/{event_id}/photos").json()["items"]]


def stored_guest_hashes(client: TestClient) -> list[str | None]:
    conn = sqlite3.connect(client.app.state.settings.database_path)
    try:
        return [row[0] for row in conn.execute("SELECT guest_key_hash FROM photos ORDER BY seq")]
    finally:
        conn.close()


@pytest.fixture
def limited(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client(max_photos_per_guest=2)


# --- Limit off (the default) ------------------------------------------------------------


def test_no_limit_by_default_and_no_key_needed(client):
    event_id = new_event(client)["event"]["id"]
    for _ in range(3):
        assert upload(client, event_id, None).status_code == 201
    assert client.get("/api/config").json()["max_photos_per_guest"] is None


def test_without_a_limit_nothing_about_the_guest_is_stored(client):
    event_id = new_event(client)["event"]["id"]
    key = new_key()
    assert upload(client, event_id, key).status_code == 201
    assert upload(client, event_id, None).status_code == 201
    assert stored_guest_hashes(client) == [None, None]


def test_the_limit_counts_from_when_it_is_switched_on(settings_factory):
    key = new_key()
    with TestClient(create_app(settings_factory())) as client:
        event_id = new_event(client)["event"]["id"]
        for _ in range(2):
            assert upload(client, event_id, key).status_code == 201

    with TestClient(create_app(settings_factory(max_photos_per_guest=2))) as client:
        for _ in range(2):
            assert upload(client, event_id, key).status_code == 201
        assert_error(upload(client, event_id, key), 409, "GUEST_PHOTO_LIMIT")


def test_switching_the_limit_off_stops_counting_and_storing(settings_factory):
    key = new_key()
    with TestClient(create_app(settings_factory(max_photos_per_guest=1))) as client:
        event_id = new_event(client)["event"]["id"]
        assert upload(client, event_id, key).status_code == 201
        assert_error(upload(client, event_id, key), 409, "GUEST_PHOTO_LIMIT")

    with TestClient(create_app(settings_factory())) as client:
        assert upload(client, event_id, key).status_code == 201
        assert stored_guest_hashes(client) == [guest_key_hash(event_id, key), None]


# --- Limit on ---------------------------------------------------------------------------


def test_guest_at_the_limit_gets_409_and_nothing_is_stored(limited):
    event_id = new_event(limited)["event"]["id"]
    key = new_key()
    first, second = upload(limited, event_id, key), upload(limited, event_id, key)
    assert first.status_code == second.status_code == 201

    error = assert_error(upload(limited, event_id, key), 409, "GUEST_PHOTO_LIMIT")
    assert error["details"] == {"max_photos_per_guest": 2}
    assert error["message"] == "Each guest can share up to 2 photos at this event."
    assert photo_ids(limited, event_id) == [second.json()["id"], first.json()["id"]]
    assert len(media_files(limited)) == 4  # two photos + two thumbnails


def test_message_is_singular_for_a_limit_of_one(make_client):
    client = make_client(max_photos_per_guest=1)
    event_id = new_event(client)["event"]["id"]
    key = new_key()
    assert upload(client, event_id, key).status_code == 201
    error = assert_error(upload(client, event_id, key), 409, "GUEST_PHOTO_LIMIT")
    assert error["message"] == "Each guest can share up to 1 photo at this event."


def test_other_guests_keep_their_own_allowance(limited):
    event_id = new_event(limited)["event"]["id"]
    full, other = new_key(), new_key()
    for _ in range(2):
        assert upload(limited, event_id, full).status_code == 201
    assert_error(upload(limited, event_id, full), 409, "GUEST_PHOTO_LIMIT")
    for _ in range(2):
        assert upload(limited, event_id, other).status_code == 201


def test_the_allowance_is_per_event(limited):
    key = new_key()
    first_event = new_event(limited, "One")["event"]["id"]
    second_event = new_event(limited, "Two")["event"]["id"]
    for _ in range(2):
        assert upload(limited, first_event, key).status_code == 201
    assert_error(upload(limited, first_event, key), 409, "GUEST_PHOTO_LIMIT")
    assert upload(limited, second_event, key).status_code == 201


def test_event_limit_is_reported_before_the_guest_limit(make_client):
    client = make_client(max_photos_per_event=2, max_photos_per_guest=2)
    event_id = new_event(client)["event"]["id"]
    key = new_key()
    for _ in range(2):
        assert upload(client, event_id, key).status_code == 201
    assert_error(upload(client, event_id, key), 409, "EVENT_PHOTO_LIMIT")


def test_host_deleting_a_photo_frees_a_slot(limited):
    created = new_event(limited)
    event_id = created["event"]["id"]
    key = new_key()
    first = upload(limited, event_id, key).json()
    assert upload(limited, event_id, key).status_code == 201
    assert_error(upload(limited, event_id, key), 409, "GUEST_PHOTO_LIMIT")

    deleted = limited.delete(f"/api/events/{event_id}/photos/{first['id']}", headers=auth(created["host_token"]))
    assert deleted.status_code == 204
    assert upload(limited, event_id, key).status_code == 201


def test_uploads_without_a_key_are_accepted_but_not_counted(limited):
    """Pages opened before the limit existed send no key: they keep working."""
    event_id = new_event(limited)["event"]["id"]
    for _ in range(3):
        assert upload(limited, event_id, None).status_code == 201
    assert stored_guest_hashes(limited) == [None, None, None]
    key = new_key()
    for _ in range(2):
        assert upload(limited, event_id, key).status_code == 201
    assert_error(upload(limited, event_id, key), 409, "GUEST_PHOTO_LIMIT")


@pytest.mark.parametrize("limit", [0, 2])
@pytest.mark.parametrize(
    "bad_key",
    [
        "short",
        "a" * 31,
        "a" * 129,
        "a" * 300,  # over the header's max_length
        "a" * 40 + "+/=",
        "a" * 40 + " b",
        "a" * 40 + "é",
    ],
)
def test_malformed_keys_are_refused_with_or_without_a_limit(make_client, limit, bad_key):
    client = make_client(max_photos_per_guest=limit)
    event_id = new_event(client)["event"]["id"]
    response = client.post(
        f"/api/events/{event_id}/photos",
        files={"file": ("photo.jpg", image_bytes(), "image/jpeg")},
        headers={HEADER: bad_key.encode("utf-8")},
    )
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"][0]["location"] == "header"
    assert bad_key not in str(error)  # rejected input is never echoed back
    assert media_files(client) == []


def test_limit_is_checked_before_the_body_is_parsed(limited):
    event_id = new_event(limited)["event"]["id"]
    key = new_key()
    for _ in range(2):
        assert upload(limited, event_id, key).status_code == 201
    response = limited.post(
        f"/api/events/{event_id}/photos",
        content=b"this is not valid multipart at all",
        headers={"content-type": "multipart/form-data; boundary=zzz", HEADER: key},
    )
    assert_error(response, 409, "GUEST_PHOTO_LIMIT")


def test_insert_rechecks_the_limit_when_the_pre_check_was_passed(limited, monkeypatch):
    """Two uploads racing past the cheap pre-check: the insert transaction decides,
    and the loser's files are removed."""
    event_id = new_event(limited)["event"]["id"]
    key = new_key()
    for _ in range(2):
        assert upload(limited, event_id, key).status_code == 201
    db = limited.app.state.services.db

    async def stale_count(event_id: str, guest_key_hash: str) -> int:
        return 0

    monkeypatch.setattr(db, "count_guest_photos", stale_count)
    assert_error(upload(limited, event_id, key), 409, "GUEST_PHOTO_LIMIT")
    assert len(media_files(limited)) == 4
    assert len(photo_ids(limited, event_id)) == 2


def test_concurrent_inserts_never_exceed_the_limit(client):
    event_id = new_event(client)["event"]["id"]
    db = client.app.state.services.db
    guest = guest_key_hash(event_id, new_key())

    def record(index: int) -> PhotoRecord:
        photo_id = f"{index:032x}"
        return PhotoRecord(
            id=photo_id,
            event_id=event_id,
            filename=f"{photo_id}.jpg",
            thumbnail_filename=f"{photo_id}.webp",
            content_type="image/jpeg",
            width=1,
            height=1,
            size_bytes=1,
            uploader_name=None,
            caption=None,
            created_at=utcnow(),
            guest_key_hash=guest,
        )

    async def race() -> list[InsertPhotoResult]:
        results = await asyncio.gather(
            *(db.insert_photo(record(i), max_photos=1000, max_photos_per_guest=3) for i in range(12))
        )
        return [result for result, _ in results]

    results = client.portal.call(race)
    assert results.count(InsertPhotoResult.OK) == 3
    assert results.count(InsertPhotoResult.GUEST_LIMIT_REACHED) == 9


# --- Quota endpoint ---------------------------------------------------------------------


def test_quota_counts_down_as_the_guest_shares(limited):
    event_id = new_event(limited)["event"]["id"]
    key = new_key()
    assert quota(limited, event_id, key).json() == {"limit": 2, "used": 0, "remaining": 2}
    upload(limited, event_id, key)
    assert quota(limited, event_id, key).json() == {"limit": 2, "used": 1, "remaining": 1}
    upload(limited, event_id, key)
    assert quota(limited, event_id, key).json() == {"limit": 2, "used": 2, "remaining": 0}
    assert quota(limited, event_id, new_key()).json() == {"limit": 2, "used": 0, "remaining": 2}


def test_remaining_never_goes_negative_after_the_limit_is_lowered(settings_factory):
    key = new_key()
    with TestClient(create_app(settings_factory(max_photos_per_guest=3))) as client:
        event_id = new_event(client)["event"]["id"]
        for _ in range(3):
            assert upload(client, event_id, key).status_code == 201
    with TestClient(create_app(settings_factory(max_photos_per_guest=1))) as client:
        assert quota(client, event_id, key).json() == {"limit": 1, "used": 3, "remaining": 0}


def test_quota_without_a_limit(client):
    event_id = new_event(client)["event"]["id"]
    key = new_key()
    upload(client, event_id, key)
    assert quota(client, event_id, key).json() == {"limit": None, "used": 0, "remaining": None}


def test_quota_needs_a_valid_key_and_a_real_event(limited):
    event_id = new_event(limited)["event"]["id"]
    assert_error(quota(limited, event_id, None), 422, "VALIDATION_ERROR")
    assert_error(quota(limited, event_id, "nope"), 422, "VALIDATION_ERROR")
    assert_error(quota(limited, "zzzzzzzzzzzz", new_key()), 404, "EVENT_NOT_FOUND")
    assert_error(quota(limited, "BAD-ID", new_key()), 404, "EVENT_NOT_FOUND")


def test_public_config_reports_the_guest_limit(make_client):
    assert make_client(max_photos_per_guest=15).get("/api/config").json()["max_photos_per_guest"] == 15


# --- Privacy ----------------------------------------------------------------------------


def test_the_key_never_leaves_the_server_and_is_stored_hashed_per_event(limited):
    event_id = new_event(limited)["event"]["id"]
    key = new_key()
    photo = upload(limited, event_id, key).json()
    listing = limited.get(f"/api/events/{event_id}/photos").text
    expected_hash = guest_key_hash(event_id, key)

    for text in (str(photo), listing):
        assert key not in text
        assert expected_hash not in text
    assert "guest" not in str(photo)

    assert stored_guest_hashes(limited) == [expected_hash]
    assert expected_hash != hash_token(key), "scoped to the event, not the bare key"
    db_dir = limited.app.state.settings.database_path.parent
    db_bytes = b"".join(path.read_bytes() for path in db_dir.glob("gambarkita.sqlite3*"))
    assert key.encode() not in db_bytes


def test_live_wall_announcement_carries_no_guest_data(limited):
    event_id = new_event(limited)["event"]["id"]
    key = new_key()
    with limited.websocket_connect(
        f"/ws/events/{event_id}", headers={"origin": ORIGIN}, subprotocols=["gambarkita.v1"]
    ) as ws:
        assert ws.receive_json()["type"] == "hello"
        assert upload(limited, event_id, key).status_code == 201
        message = ws.receive_json()
    assert message["type"] == "photo.created"
    assert key not in str(message)
    assert guest_key_hash(event_id, key) not in str(message)


def test_cors_preflight_allows_the_guest_key_header(client):
    response = client.options(
        "/api/events/abcdefghijkl/photos",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-guest-key",
        },
    )
    assert response.status_code == 200
    assert "x-guest-key" in response.headers["access-control-allow-headers"].lower()


# --- Existing databases -----------------------------------------------------------------

# The schema before guest keys existed.
_OLD_SCHEMA = """
CREATE TABLE events (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, host_token_hash TEXT NOT NULL,
    uploads_open INTEGER NOT NULL DEFAULT 1 CHECK (uploads_open IN (0, 1)), created_at TEXT NOT NULL
);
CREATE TABLE photos (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    filename TEXT NOT NULL, thumbnail_filename TEXT NOT NULL, content_type TEXT NOT NULL,
    width INTEGER NOT NULL, height INTEGER NOT NULL, size_bytes INTEGER NOT NULL,
    uploader_name TEXT, caption TEXT, created_at TEXT NOT NULL
);
CREATE INDEX ix_photos_event_seq ON photos (event_id, seq);
"""


def test_a_database_from_before_the_limit_is_upgraded_on_startup(settings_factory):
    settings = settings_factory(max_photos_per_guest=1)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    event_id, old_photo = "oldevent0001", "e" * 32
    created = utcnow().isoformat(timespec="milliseconds")
    conn = sqlite3.connect(settings.database_path)
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO events (id, name, host_token_hash, uploads_open, created_at) VALUES (?, ?, ?, 1, ?)",
            (event_id, "Before", hash_token("t" * 43), created),
        )
        conn.execute(
            "INSERT INTO photos (id, event_id, filename, thumbnail_filename, content_type, width, height, "
            "size_bytes, uploader_name, caption, created_at) VALUES (?, ?, ?, ?, 'image/jpeg', 1, 1, 1, NULL, NULL, ?)",
            (old_photo, event_id, f"{old_photo}.jpg", f"{old_photo}.webp", created),
        )
        conn.commit()
    finally:
        conn.close()
    storage = LocalStorage(settings.storage_dir)
    storage.ensure_layout()
    storage.create_event_dirs(event_id)

    for _ in range(2):  # the upgrade is idempotent
        with TestClient(create_app(settings)) as client:
            assert photo_ids(client, event_id) == [old_photo]
    with TestClient(create_app(settings)) as client:
        key = new_key()
        assert upload(client, event_id, key).status_code == 201
        assert_error(upload(client, event_id, key), 409, "GUEST_PHOTO_LIMIT")
        assert stored_guest_hashes(client) == [None, guest_key_hash(event_id, key)]

    conn = sqlite3.connect(settings.database_path)
    try:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(photos)")}
    finally:
        conn.close()
    assert "ix_photos_event_guest" in indexes
