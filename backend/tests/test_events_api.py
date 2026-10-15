"""Event management API: creation, lookup, host authorisation, updates, deletion,
photo listing / pagination and cross-event (IDOR) protection."""

import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import ORIGIN, auth

EVENT_ID_RE = re.compile(r"^[a-z0-9]{12}$")
REQUEST_ID_KEYS = {"code", "message", "details", "request_id"}
UNKNOWN_EVENT_ID = "zzzzzzzzzzzz"
UNKNOWN_PHOTO_ID = "f" * 32


# --- Helpers ----------------------------------------------------------------------------


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    """Assert the structured error envelope and return its ``error`` object."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == REQUEST_ID_KEYS
    assert error["code"] == code, error
    assert isinstance(error["message"], str) and error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    return error


def assert_details_do_not_echo(error: dict[str, Any], *secrets_: str) -> None:
    for detail in error["details"] or []:
        assert set(detail) <= {"location", "field", "message", "type"}, detail
        assert "input" not in detail
    text = repr(error)
    for value in secrets_:
        assert value not in text


def without_timestamp(resource: dict[str, Any]) -> dict[str, Any]:
    """Drop ``created_at`` (compared separately by test_created_at_is_identical_in_create_response_and_later_reads)."""
    return {key: value for key, value in resource.items() if key != "created_at"}


def same_instant(first: str, second: str) -> bool:
    return abs((datetime.fromisoformat(first) - datetime.fromisoformat(second)).total_seconds()) < 0.001


def fake_token() -> str:
    """A syntactically plausible host token that belongs to no event."""
    return secrets.token_urlsafe(32)


def storage_files(settings: Any) -> list[Path]:
    return [path for path in Path(settings.storage_dir).rglob("*") if path.is_file()]


def photo_rows(settings: Any, event_id: str) -> int:
    with closing(sqlite3.connect(settings.database_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM photos WHERE event_id = ?", (event_id,)).fetchone()[0]


def event_rows(settings: Any, event_id: str) -> int:
    with closing(sqlite3.connect(settings.database_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM events WHERE id = ?", (event_id,)).fetchone()[0]


def uploaded(upload_photo: Any, event_id: str, **kwargs: Any) -> dict[str, Any]:
    response = upload_photo(event_id, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def raw_get(client: Any, path: str) -> tuple[int, dict[str, Any]]:
    """GET ``path`` verbatim through the ASGI app (httpx would normalise dot-segments)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "state": {},
    }

    async def run() -> tuple[int, dict[str, Any]]:
        start: dict[str, Any] = {}
        chunks: list[bytes] = []
        sent = False

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await client.app(scope, receive, send)
        return start["status"], json.loads(b"".join(chunks))

    return client.portal.call(run)


# --- POST /api/events ---------------------------------------------------------------------


def test_create_event_returns_id_links_and_one_time_host_token(client):
    response = client.post("/api/events", json={"name": "Aisyah & Daniel's Wedding"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"event", "host_token", "host_url"}
    event = body["event"]
    event_id = event["id"]
    assert EVENT_ID_RE.fullmatch(event_id)
    assert event["name"] == "Aisyah & Daniel's Wedding"
    assert event["uploads_open"] is True
    assert event["photo_count"] == 0
    datetime.fromisoformat(event["created_at"])
    assert event["links"] == {
        "upload": f"{ORIGIN}/e/{event_id}",
        "wall": f"{ORIGIN}/e/{event_id}/wall",
    }
    token = body["host_token"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token)
    assert body["host_url"] == f"{ORIGIN}/e/{event_id}/host#token={token}"
    assert "#token=" + token in body["host_url"]


def test_create_event_links_follow_public_app_url_without_double_slash(make_client):
    client = make_client(public_app_url="https://photos.example.com/")

    body = client.post("/api/events", json={"name": "Links"}).json()

    event_id = body["event"]["id"]
    assert body["event"]["links"]["upload"] == f"https://photos.example.com/e/{event_id}"
    assert body["event"]["links"]["wall"] == f"https://photos.example.com/e/{event_id}/wall"
    assert body["host_url"] == f"https://photos.example.com/e/{event_id}/host#token={body['host_token']}"


def test_created_event_ids_and_host_tokens_are_unique(client, create_event):
    created = [create_event(f"Event {i}") for i in range(6)]

    assert len({item["event"]["id"] for item in created}) == 6
    assert len({item["host_token"] for item in created}) == 6


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("  Aisyah   &\t\n Daniel  ", "Aisyah & Daniel"),
        ("‮evil‬ party", "evil party"),  # RIGHT-TO-LEFT OVERRIDE / POP DIRECTIONAL FORMATTING
        ("Zero​Width﻿ Party⁦⁩", "ZeroWidth Party"),
        ("‎‏LRM RLM؜", "LRM RLM"),
        ("Line Break\x00Null\x07Bell", "Line Break Null Bell"),
        ("Café", "Café"),  # NFC normalisation
        ("Family \U0001f468‍\U0001f469‍\U0001f467", "Family \U0001f468‍\U0001f469‍\U0001f467"),
    ],
)
def test_create_event_cleans_name(client, raw_name, expected):
    response = client.post("/api/events", json={"name": raw_name})

    assert response.status_code == 201, response.text
    assert response.json()["event"]["name"] == expected
    event_id = response.json()["event"]["id"]
    assert client.get(f"/api/events/{event_id}").json()["name"] == expected


def test_create_event_accepts_80_visible_characters_even_with_invisible_padding(client):
    name = "n" * 80

    plain = client.post("/api/events", json={"name": name})
    padded = client.post("/api/events", json={"name": "​" * 50 + name + "   ‮"})

    assert plain.status_code == 201, plain.text
    assert padded.status_code == 201, padded.text
    assert padded.json()["event"]["name"] == name


@pytest.mark.parametrize(
    ("payload", "marker"),
    [
        ({"name": ""}, None),
        ({"name": "   \t\n  "}, None),
        ({"name": "​‮﻿"}, None),
        ({"name": "Q" * 81}, "Q" * 81),
        ({"name": "W" * 400}, "W" * 400),
        ({}, None),
        ({"name": None}, None),
        ({"name": 12345}, None),
        ({"name": ["list"]}, None),
        ({"name": "Valid", "host_token_hash": "SENSITIVE-MARKER-9f2c"}, "SENSITIVE-MARKER-9f2c"),
        ({"name": "Valid", "uploads_open": False}, None),
    ],
    ids=[
        "empty",
        "whitespace-only",
        "invisible-only",
        "81-chars",
        "over-raw-max",
        "missing",
        "null",
        "number",
        "list",
        "extra-secret-field",
        "extra-known-field",
    ],
)
def test_create_event_rejects_invalid_payload(client, payload, marker):
    response = client.post("/api/events", json=payload)

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"], error
    if marker is not None:
        assert marker not in response.text
        assert_details_do_not_echo(error, marker)
    else:
        assert_details_do_not_echo(error)


def test_create_event_extra_field_is_reported_by_name(client):
    response = client.post("/api/events", json={"name": "ok", "is_admin": True})

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert {"location": "body", "field": "is_admin", "type": "extra_forbidden"}.items() <= error["details"][0].items()


@pytest.mark.parametrize("body", [b"not json", b'{"name": ', b"[]", b'"just a string"'])
def test_create_event_rejects_non_object_json(client, body):
    response = client.post("/api/events", content=body, headers={"Content-Type": "application/json"})

    assert_error(response, 422, "VALIDATION_ERROR")


def test_host_token_is_stored_only_as_sha256_hash(client, create_event):
    created = create_event("Secret Party")
    token = created["host_token"]
    settings = client.app.state.settings

    db_dir = settings.database_path.parent
    db_bytes = b"".join(path.read_bytes() for path in db_dir.iterdir() if path.is_file())
    assert settings.database_path.exists()
    assert token.encode() not in db_bytes
    assert hashlib.sha256(token.encode()).hexdigest().encode() in db_bytes
    # ...and nowhere else in the storage tree either.
    for path in storage_files(settings):
        assert token.encode() not in path.read_bytes(), path


# --- GET /api/events/{id} -------------------------------------------------------------------


def test_get_event_returns_public_view_without_secrets(client, create_event):
    created = create_event("Public View")
    event_id = created["event"]["id"]

    response = client.get(f"/api/events/{event_id}")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "name", "uploads_open", "photo_count", "created_at", "links"}
    assert without_timestamp(body) == without_timestamp(created["event"])
    assert same_instant(body["created_at"], created["event"]["created_at"])
    assert created["host_token"] not in response.text
    assert hashlib.sha256(created["host_token"].encode()).hexdigest() not in response.text


def test_get_event_photo_count_tracks_uploads_and_deletions(client, create_event, upload_photo):
    created = create_event("Counting")
    event_id = created["event"]["id"]
    assert client.get(f"/api/events/{event_id}").json()["photo_count"] == 0

    first = uploaded(upload_photo, event_id)
    assert client.get(f"/api/events/{event_id}").json()["photo_count"] == 1
    uploaded(upload_photo, event_id)
    uploaded(upload_photo, event_id)
    assert client.get(f"/api/events/{event_id}").json()["photo_count"] == 3

    deleted = client.delete(f"/api/events/{event_id}/photos/{first['id']}", headers=auth(created["host_token"]))
    assert deleted.status_code == 204
    assert client.get(f"/api/events/{event_id}").json()["photo_count"] == 2


@pytest.mark.parametrize(
    "event_id",
    [
        "ABC",
        "ABCDEFGHIJKL",  # right length, upper case
        "abcdefghijk",  # 11 chars
        "abcdefghijklm",  # 13 chars
        "abc-defghijk",
        "abc_defghijk",
        "abc.defghijk",
        "%2e%2e",  # decodes to ".." server-side
        "%00bcdefghijk",
        "ábcdefghijkl",  # unicode letter
        "аbcdefghijkl",  # Cyrillic look-alike 'a'
        "ａbcdefghijkl",  # full-width 'a'
        "a" * 64,
        "a" * 65,
        UNKNOWN_EVENT_ID,  # well-formed but unknown
        "000000000000",
    ],
)
def test_get_event_with_malformed_or_unknown_id_is_404(client, event_id):
    response = client.get(f"/api/events/{event_id}")

    assert_error(response, 404, "EVENT_NOT_FOUND")


@pytest.mark.parametrize("path", ["/api/events/..%2Fx", "/api/events/abc%2F..%2Fzzzzzzzzzzzz"])
def test_encoded_slash_in_event_id_never_matches_an_event_route(client, create_event, path):
    create_event("Neighbour")

    response = client.get(path)

    assert response.status_code == 404
    assert response.json()["error"]["code"] in {"NOT_FOUND", "EVENT_NOT_FOUND"}


@pytest.mark.parametrize(
    ("path", "expected_codes"),
    [
        ("/api/events/..", {"EVENT_NOT_FOUND"}),
        ("/api/events/.", {"EVENT_NOT_FOUND"}),
        ("/api/events/../x", {"NOT_FOUND", "EVENT_NOT_FOUND"}),
        ("/api/events/../../storage/db/gambarkita.sqlite3", {"NOT_FOUND", "EVENT_NOT_FOUND"}),
        ("/api/events/../events", {"NOT_FOUND", "EVENT_NOT_FOUND"}),
    ],
)
def test_literal_dot_segments_in_event_path_are_404(client, create_event, path, expected_codes):
    create_event("Neighbour")

    status, body = raw_get(client, path)

    assert status == 404, body
    assert body["error"]["code"] in expected_codes


def test_created_at_is_identical_in_create_response_and_later_reads(client, create_event, upload_photo):
    """The same resource should expose one timestamp, whichever endpoint returns it.

    Several samples are compared so that a creation time that happens to fall on an
    exact millisecond cannot make this pass by chance.
    """
    mismatches = []
    for index in range(3):
        created = create_event(f"Timestamps {index}")
        event_id = created["event"]["id"]
        stored_event = client.get(f"/api/events/{event_id}").json()
        if stored_event["created_at"] != created["event"]["created_at"]:
            mismatches.append(("event", created["event"]["created_at"], stored_event["created_at"]))

        photo = uploaded(upload_photo, event_id)
        listed = client.get(f"/api/events/{event_id}/photos").json()["items"][0]
        if listed["created_at"] != photo["created_at"]:
            mismatches.append(("photo", photo["created_at"], listed["created_at"]))

    assert mismatches == []


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "", None),
        ("GET", "/photos", None),
        ("PATCH", "", {"name": "x"}),
        ("DELETE", "", None),
        ("POST", "/host/verify", None),
        ("DELETE", "/photos/" + UNKNOWN_PHOTO_ID, None),
    ],
)
@pytest.mark.parametrize("event_id", ["ABC", UNKNOWN_EVENT_ID])
def test_every_event_endpoint_returns_event_not_found(client, method, suffix, body, event_id):
    response = client.request(
        method, f"/api/events/{event_id}{suffix}", json=body, headers=auth(fake_token())
    )

    assert_error(response, 404, "EVENT_NOT_FOUND")


# --- PATCH /api/events/{id} -------------------------------------------------------------------


def test_update_event_without_authorization_is_401_with_bearer_challenge(client, create_event):
    event_id = create_event("Protected")["event"]["id"]

    response = client.patch(f"/api/events/{event_id}", json={"name": "Hijacked"})

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.get(f"/api/events/{event_id}").json()["name"] == "Protected"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer short",
        "Bearer " + "a" * 200,
        "Bearer " + "!" * 43,
        "Bearer",
        "Basic dXNlcjpwYXNzd29yZA==",
        "Token " + "a" * 43,
        "",
    ],
    ids=["too-short", "too-long", "bad-charset", "no-credentials", "basic-scheme", "other-scheme", "empty"],
)
def test_update_event_with_malformed_authorization_is_401(client, create_event, authorization):
    event_id = create_event("Protected")["event"]["id"]

    response = client.patch(
        f"/api/events/{event_id}", json={"name": "Hijacked"}, headers={"Authorization": authorization}
    )

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.get(f"/api/events/{event_id}").json()["name"] == "Protected"


def test_update_event_with_wrong_token_is_401(client, create_event):
    event_id = create_event("Protected")["event"]["id"]

    response = client.patch(f"/api/events/{event_id}", json={"uploads_open": False}, headers=auth(fake_token()))

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.get(f"/api/events/{event_id}").json()["uploads_open"] is True


def test_update_event_with_another_events_token_is_401(client, create_event):
    victim = create_event("Victim")
    attacker = create_event("Attacker")
    victim_id = victim["event"]["id"]

    response = client.patch(
        f"/api/events/{victim_id}",
        json={"name": "Pwned", "uploads_open": False},
        headers=auth(attacker["host_token"]),
    )

    assert_error(response, 401, "UNAUTHORIZED")
    assert without_timestamp(client.get(f"/api/events/{victim_id}").json()) == without_timestamp(victim["event"])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": None, "uploads_open": None},
        {"name": "   "},
        {"name": "x" * 81},
        {"uploads_open": "maybe"},
        {"uploads_open": True, "host_token_hash": "x"},
    ],
    ids=["empty", "all-null", "blank-name", "long-name", "non-bool", "extra-field"],
)
def test_update_event_with_invalid_body_is_422(client, create_event, payload):
    created = create_event("Original")
    event_id = created["event"]["id"]

    response = client.patch(f"/api/events/{event_id}", json=payload, headers=auth(created["host_token"]))

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert_details_do_not_echo(error)
    assert without_timestamp(client.get(f"/api/events/{event_id}").json()) == without_timestamp(created["event"])


def test_update_event_renames_with_cleaning(client, create_event, upload_photo):
    created = create_event("Old Name")
    event_id = created["event"]["id"]
    uploaded(upload_photo, event_id)

    response = client.patch(
        f"/api/events/{event_id}",
        json={"name": "  New‮   Name​ "},
        headers=auth(created["host_token"]),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "New Name"
    assert body["id"] == event_id
    assert body["uploads_open"] is True
    assert body["photo_count"] == 1
    assert body["links"] == created["event"]["links"]
    assert "host_token" not in body
    assert client.get(f"/api/events/{event_id}").json() == body


def test_update_event_closing_uploads_blocks_new_photos_until_reopened(client, create_event, upload_photo):
    created = create_event("Closable")
    event_id = created["event"]["id"]
    headers = auth(created["host_token"])

    closed = client.patch(f"/api/events/{event_id}", json={"uploads_open": False}, headers=headers)
    assert closed.status_code == 200, closed.text
    assert closed.json()["uploads_open"] is False
    assert closed.json()["name"] == "Closable"

    assert_error(upload_photo(event_id), 403, "UPLOADS_CLOSED")
    assert client.get(f"/api/events/{event_id}").json()["photo_count"] == 0

    reopened = client.patch(f"/api/events/{event_id}", json={"uploads_open": True}, headers=headers)
    assert reopened.json()["uploads_open"] is True
    assert upload_photo(event_id).status_code == 201


def test_update_event_can_change_name_and_uploads_together(client, create_event):
    created = create_event("Both")
    event_id = created["event"]["id"]

    response = client.patch(
        f"/api/events/{event_id}",
        json={"name": "Both Changed", "uploads_open": False},
        headers=auth(created["host_token"]),
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Both Changed"
    assert response.json()["uploads_open"] is False


# --- DELETE /api/events/{id} -------------------------------------------------------------------


def test_delete_event_removes_event_photos_files_and_rows(client, create_event, upload_photo):
    created = create_event("Doomed")
    event_id = created["event"]["id"]
    photos = [uploaded(upload_photo, event_id) for _ in range(2)]
    settings = client.app.state.settings
    event_dir = Path(settings.storage_dir) / "events" / event_id
    assert event_dir.is_dir()
    assert photo_rows(settings, event_id) == 2
    for photo in photos:
        assert client.get(photo["url"]).status_code == 200
        assert client.get(photo["thumbnail_url"]).status_code == 200

    response = client.delete(f"/api/events/{event_id}", headers=auth(created["host_token"]))

    assert response.status_code == 204
    assert response.content == b""
    assert_error(client.get(f"/api/events/{event_id}"), 404, "EVENT_NOT_FOUND")
    assert_error(client.get(f"/api/events/{event_id}/photos"), 404, "EVENT_NOT_FOUND")
    for photo in photos:
        assert client.get(photo["url"]).status_code == 404
        assert client.get(photo["thumbnail_url"]).status_code == 404
    assert not event_dir.exists()
    assert photo_rows(settings, event_id) == 0
    assert event_rows(settings, event_id) == 0


def test_delete_event_is_not_repeatable_and_token_stops_working(client, create_event):
    created = create_event("Once")
    event_id = created["event"]["id"]
    headers = auth(created["host_token"])

    assert client.delete(f"/api/events/{event_id}", headers=headers).status_code == 204

    assert_error(client.delete(f"/api/events/{event_id}", headers=headers), 404, "EVENT_NOT_FOUND")
    assert_error(client.post(f"/api/events/{event_id}/host/verify", headers=headers), 404, "EVENT_NOT_FOUND")
    assert_error(
        client.patch(f"/api/events/{event_id}", json={"name": "Zombie"}, headers=headers), 404, "EVENT_NOT_FOUND"
    )


def test_uploading_to_a_deleted_event_is_404(client, create_event, upload_photo):
    created = create_event("Gone")
    event_id = created["event"]["id"]
    client.delete(f"/api/events/{event_id}", headers=auth(created["host_token"]))

    assert_error(upload_photo(event_id), 404, "EVENT_NOT_FOUND")
    assert not (Path(client.app.state.settings.storage_dir) / "events" / event_id).exists()


@pytest.mark.parametrize("token_kind", ["missing", "wrong", "other-event"])
def test_delete_event_requires_the_events_own_token(client, create_event, upload_photo, token_kind):
    victim = create_event("Victim")
    attacker = create_event("Attacker")
    victim_id = victim["event"]["id"]
    photo = uploaded(upload_photo, victim_id)
    headers = {
        "missing": {},
        "wrong": auth(fake_token()),
        "other-event": auth(attacker["host_token"]),
    }[token_kind]

    response = client.delete(f"/api/events/{victim_id}", headers=headers)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.get(f"/api/events/{victim_id}").json()["photo_count"] == 1
    assert client.get(photo["url"]).status_code == 200


def test_deleting_one_event_leaves_other_events_intact(client, create_event, upload_photo):
    doomed = create_event("Doomed")
    survivor = create_event("Survivor")
    survivor_id = survivor["event"]["id"]
    uploaded(upload_photo, doomed["event"]["id"])
    kept = uploaded(upload_photo, survivor_id)

    assert client.delete(f"/api/events/{doomed['event']['id']}", headers=auth(doomed["host_token"])).status_code == 204

    assert client.get(f"/api/events/{survivor_id}").json()["photo_count"] == 1
    assert client.get(kept["url"]).status_code == 200
    assert [item["id"] for item in client.get(f"/api/events/{survivor_id}/photos").json()["items"]] == [kept["id"]]


# --- POST /api/events/{id}/host/verify ----------------------------------------------------------


def test_verify_host_accepts_valid_token(client, create_event):
    created = create_event("Verify")

    response = client.post(
        f"/api/events/{created['event']['id']}/host/verify", headers=auth(created["host_token"])
    )

    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize("token_kind", ["missing", "malformed", "wrong", "other-event"])
def test_verify_host_rejects_invalid_token(client, create_event, token_kind):
    created = create_event("Verify")
    other = create_event("Other")
    headers = {
        "missing": {},
        "malformed": {"Authorization": "Bearer nope"},
        "wrong": auth(fake_token()),
        "other-event": auth(other["host_token"]),
    }[token_kind]

    response = client.post(f"/api/events/{created['event']['id']}/host/verify", headers=headers)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"


# --- Event creation key ----------------------------------------------------------------------


CREATION_KEY = "s3cret-key-123"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Event-Creation-Key": ""},
        {"X-Event-Creation-Key": "wrong-key"},
        {"X-Event-Creation-Key": CREATION_KEY.upper()},
        {"X-Event-Creation-Key": CREATION_KEY[:-1]},
        {"X-Event-Creation-Key": CREATION_KEY + "4"},
        {"Authorization": f"Bearer {CREATION_KEY}"},
    ],
    ids=["missing", "empty", "wrong", "wrong-case", "prefix", "suffix", "wrong-header"],
)
def test_creation_key_is_required_when_configured(make_client, headers):
    client = make_client(event_creation_key=CREATION_KEY)

    response = client.post("/api/events", json={"name": "Keyed"}, headers=headers)

    error = assert_error(response, 401, "UNAUTHORIZED")
    assert error["details"] == {"header": "X-Event-Creation-Key"}
    assert CREATION_KEY not in response.text


def test_creation_key_correct_value_creates_event(make_client):
    client = make_client(event_creation_key=CREATION_KEY)

    response = client.post("/api/events", json={"name": "Keyed"}, headers={"X-Event-Creation-Key": CREATION_KEY})

    assert response.status_code == 201, response.text
    assert CREATION_KEY not in response.text
    assert client.get("/api/config").json()["event_creation_requires_key"] is True


def test_overlong_creation_key_is_rejected_without_echo(make_client):
    client = make_client(event_creation_key=CREATION_KEY)
    overlong = "K" * 300

    response = client.post("/api/events", json={"name": "Keyed"}, headers={"X-Event-Creation-Key": overlong})

    assert response.status_code in {401, 422}
    assert response.json()["error"]["code"] in {"UNAUTHORIZED", "VALIDATION_ERROR"}
    assert overlong not in response.text
    assert CREATION_KEY not in response.text


def test_there_is_no_endpoint_to_enumerate_events(client, create_event):
    create_event("Private")

    assert_error(client.get("/api/events"), 405, "METHOD_NOT_ALLOWED")


def test_creation_key_is_not_required_by_default(client):
    assert client.get("/api/config").json()["event_creation_requires_key"] is False
    assert client.post("/api/events", json={"name": "Open"}).status_code == 201


# --- Host token rotation (lost-link recovery) ------------------------------------------------


KEY_HEADER = {"X-Event-Creation-Key": CREATION_KEY}


def test_rotate_host_token_issues_a_new_working_token_and_revokes_the_old_one(make_client, upload_photo):
    client = make_client(event_creation_key=CREATION_KEY)
    created = client.post("/api/events", json={"name": "Lost Link"}, headers=KEY_HEADER).json()
    event_id = created["event"]["id"]
    old_token = created["host_token"]
    assert upload_photo(event_id, on=client).status_code == 201

    response = client.post(f"/api/events/{event_id}/host/rotate", headers=KEY_HEADER)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"event", "host_token", "host_url"}
    new_token = body["host_token"]
    assert new_token != old_token
    assert body["host_url"] == created["host_url"].replace(old_token, new_token)
    assert body["host_url"].endswith(f"#token={new_token}")
    assert body["event"]["id"] == event_id
    assert body["event"]["name"] == "Lost Link"
    assert body["event"]["photo_count"] == 1
    assert CREATION_KEY not in response.text

    # The new token works everywhere a host token is accepted; the old one is dead.
    assert client.post(f"/api/events/{event_id}/host/verify", headers=auth(new_token)).status_code == 204
    assert_error(client.post(f"/api/events/{event_id}/host/verify", headers=auth(old_token)), 401, "UNAUTHORIZED")
    assert_error(client.patch(f"/api/events/{event_id}", json={"name": "Nope"}, headers=auth(old_token)), 401, "UNAUTHORIZED")
    renamed = client.patch(f"/api/events/{event_id}", json={"name": "Found"}, headers=auth(new_token))
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Found"


def test_rotate_host_token_stores_only_the_hash_of_the_new_token(make_client):
    client = make_client(event_creation_key=CREATION_KEY)
    event_id = client.post("/api/events", json={"name": "Hashed"}, headers=KEY_HEADER).json()["event"]["id"]

    new_token = client.post(f"/api/events/{event_id}/host/rotate", headers=KEY_HEADER).json()["host_token"]

    with closing(sqlite3.connect(client.app.state.settings.database_path)) as conn:
        stored = conn.execute("SELECT host_token_hash FROM events WHERE id = ?", (event_id,)).fetchone()[0]
    assert stored == hashlib.sha256(new_token.encode()).hexdigest()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Event-Creation-Key": ""},
        {"X-Event-Creation-Key": "wrong-key"},
        {"X-Event-Creation-Key": CREATION_KEY[:-1]},
        {"Authorization": f"Bearer {CREATION_KEY}"},
    ],
    ids=["missing", "empty", "wrong", "prefix", "wrong-header"],
)
def test_rotate_host_token_requires_the_creation_key(make_client, headers):
    client = make_client(event_creation_key=CREATION_KEY)
    created = client.post("/api/events", json={"name": "Guarded"}, headers=KEY_HEADER).json()
    event_id, token = created["event"]["id"], created["host_token"]

    response = client.post(f"/api/events/{event_id}/host/rotate", headers=headers)

    error = assert_error(response, 401, "UNAUTHORIZED")
    assert error["details"] == {"header": "X-Event-Creation-Key"}
    assert CREATION_KEY not in response.text
    # A failed attempt must not touch the existing token.
    assert client.post(f"/api/events/{event_id}/host/verify", headers=auth(token)).status_code == 204


def test_rotate_host_token_with_a_host_token_is_not_enough(make_client):
    """The host's own token cannot rotate itself: the endpoint is for the server admin only."""
    client = make_client(event_creation_key=CREATION_KEY)
    created = client.post("/api/events", json={"name": "Guarded"}, headers=KEY_HEADER).json()
    event_id, token = created["event"]["id"], created["host_token"]

    assert_error(client.post(f"/api/events/{event_id}/host/rotate", headers=auth(token)), 401, "UNAUTHORIZED")
    assert client.post(f"/api/events/{event_id}/host/verify", headers=auth(token)).status_code == 204


def test_rotate_host_token_is_disabled_without_a_configured_creation_key(client, create_event):
    """Without a server key there is nothing to authenticate the admin with, so nobody may rotate."""
    created = create_event("Open Server")
    event_id, token = created["event"]["id"], created["host_token"]

    for headers in ({}, {"X-Event-Creation-Key": "anything"}, auth(token)):
        error = assert_error(client.post(f"/api/events/{event_id}/host/rotate", headers=headers), 401, "UNAUTHORIZED")
        assert error["details"] == {"header": "X-Event-Creation-Key"}
    assert client.post(f"/api/events/{event_id}/host/verify", headers=auth(token)).status_code == 204


def test_rotate_host_token_for_unknown_event_is_404(make_client):
    client = make_client(event_creation_key=CREATION_KEY)

    assert_error(client.post("/api/events/AAAAAAAAAAAA/host/rotate", headers=KEY_HEADER), 404, "EVENT_NOT_FOUND")


# --- Photo listing & pagination -----------------------------------------------------------------


def test_list_photos_is_newest_first_with_public_fields(client, create_event, upload_photo):
    event_id = create_event("Gallery")["event"]["id"]
    photos = [
        uploaded(upload_photo, event_id, fields={"caption": f"Photo {i}", "uploader_name": "Guest"}) for i in range(3)
    ]

    response = client.get(f"/api/events/{event_id}/photos")

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert [item["id"] for item in body["items"]] == [photo["id"] for photo in reversed(photos)]
    assert without_timestamp(body["items"][0]) == without_timestamp(photos[-1])
    assert [item["caption"] for item in body["items"]] == ["Photo 2", "Photo 1", "Photo 0"]
    for item in body["items"]:
        assert item["event_id"] == event_id
        assert item["url"].startswith(f"/media/events/{event_id}/photos/")
        assert item["thumbnail_url"].startswith(f"/media/events/{event_id}/thumbs/")


def test_list_photos_of_empty_event(client, create_event):
    event_id = create_event("Empty")["event"]["id"]

    response = client.get(f"/api/events/{event_id}/photos")

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_list_photos_cursor_pagination_returns_every_photo_exactly_once(client, create_event, upload_photo):
    event_id = create_event("Paged")["event"]["id"]
    uploaded_ids = [uploaded(upload_photo, event_id)["id"] for _ in range(5)]

    pages = []
    cursor = None
    for _ in range(10):  # guard against an infinite loop
        params = {"limit": 2} if cursor is None else {"limit": 2, "cursor": cursor}
        response = client.get(f"/api/events/{event_id}/photos", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        pages.append([item["id"] for item in page["items"]])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert re.fullmatch(r"[0-9]{1,18}", cursor)

    assert [len(page) for page in pages] == [2, 2, 1]
    seen = [photo_id for page in pages for photo_id in page]
    assert seen == list(reversed(uploaded_ids))
    assert len(set(seen)) == 5


def test_list_photos_exact_multiple_of_limit_ends_with_null_cursor(client, create_event, upload_photo):
    event_id = create_event("Even")["event"]["id"]
    for _ in range(2):
        uploaded(upload_photo, event_id)

    page = client.get(f"/api/events/{event_id}/photos", params={"limit": 2}).json()

    assert len(page["items"]) == 2
    assert page["next_cursor"] is None


def test_list_photos_is_scoped_to_the_event(client, create_event, upload_photo):
    first = create_event("First")["event"]["id"]
    second = create_event("Second")["event"]["id"]
    in_first = uploaded(upload_photo, first)["id"]
    in_second = uploaded(upload_photo, second)["id"]

    first_ids = [item["id"] for item in client.get(f"/api/events/{first}/photos").json()["items"]]
    second_ids = [item["id"] for item in client.get(f"/api/events/{second}/photos").json()["items"]]

    assert first_ids == [in_first]
    assert second_ids == [in_second]


@pytest.mark.parametrize(
    "params",
    [
        {"cursor": "abc"},
        {"cursor": "-1"},
        {"cursor": ""},
        {"cursor": "1.5"},
        {"cursor": "1" * 19},
        {"cursor": "12 "},
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"limit": "abc"},
        {"limit": "2.5"},
    ],
    ids=lambda params: "-".join(f"{key}={value!r}" for key, value in params.items()),
)
def test_list_photos_rejects_invalid_query(client, create_event, params):
    event_id = create_event("Query")["event"]["id"]

    response = client.get(f"/api/events/{event_id}/photos", params=params)

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"][0]["location"] == "query"
    assert error["details"][0]["field"] in {"cursor", "limit"}
    assert_details_do_not_echo(error)


@pytest.mark.parametrize("limit", [1, 100])
def test_list_photos_accepts_limit_bounds(client, create_event, limit):
    event_id = create_event("Bounds")["event"]["id"]

    response = client.get(f"/api/events/{event_id}/photos", params={"limit": limit})

    assert response.status_code == 200


# --- Cross-event authorisation (IDOR) ----------------------------------------------------------------


def test_photo_cannot_be_deleted_through_another_events_url(client, create_event, upload_photo):
    event_a = create_event("Event A")
    event_b = create_event("Event B")
    a_id, b_id = event_a["event"]["id"], event_b["event"]["id"]
    photo = uploaded(upload_photo, a_id)

    # B's host, B's URL, A's photo id: the photo lookup is scoped to event B.
    with_b_token = client.delete(f"/api/events/{b_id}/photos/{photo['id']}", headers=auth(event_b["host_token"]))
    assert_error(with_b_token, 404, "PHOTO_NOT_FOUND")

    # A's token is not valid for B's URL.
    with_a_token = client.delete(f"/api/events/{b_id}/photos/{photo['id']}", headers=auth(event_a["host_token"]))
    assert_error(with_a_token, 401, "UNAUTHORIZED")

    # B's token on A's URL.
    b_token_on_a = client.delete(f"/api/events/{a_id}/photos/{photo['id']}", headers=auth(event_b["host_token"]))
    assert_error(b_token_on_a, 401, "UNAUTHORIZED")

    assert client.get(photo["url"]).status_code == 200
    assert [item["id"] for item in client.get(f"/api/events/{a_id}/photos").json()["items"]] == [photo["id"]]
    assert client.get(f"/api/events/{a_id}").json()["photo_count"] == 1

    # The rightful host can still delete it.
    own = client.delete(f"/api/events/{a_id}/photos/{photo['id']}", headers=auth(event_a["host_token"]))
    assert own.status_code == 204
    assert client.get(photo["url"]).status_code == 404


@pytest.mark.parametrize("photo_id", ["not-a-photo", "F" * 32, "f" * 31, "g" * 32, UNKNOWN_PHOTO_ID])
def test_delete_photo_with_malformed_or_unknown_id_is_404(client, create_event, photo_id):
    created = create_event("Photos")

    response = client.delete(
        f"/api/events/{created['event']['id']}/photos/{photo_id}", headers=auth(created["host_token"])
    )

    assert_error(response, 404, "PHOTO_NOT_FOUND")


def test_delete_photo_requires_host_token(client, create_event, upload_photo):
    created = create_event("Photos")
    event_id = created["event"]["id"]
    photo = uploaded(upload_photo, event_id)

    response = client.delete(f"/api/events/{event_id}/photos/{photo['id']}")

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.get(photo["url"]).status_code == 200
