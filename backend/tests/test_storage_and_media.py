"""Local storage confinement (LocalStorage) and the hardened /media/events static mount."""

import asyncio
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

from app.core.errors import EventNotFoundError, UnsafePathError
from app.services.storage import PHOTOS_DIR, THUMBNAILS_DIR, LocalStorage
from tests.conftest import auth, image_bytes

EVENT_ID = "abcdefghijkl"
OTHER_EVENT_ID = "0123456789ab"
MEDIA_HEADERS = {
    "content-security-policy": "default-src 'none'; sandbox",
    "x-content-type-options": "nosniff",
    "cross-origin-resource-policy": "cross-origin",
    "cache-control": "private, max-age=31536000, immutable",
}
SQLITE_MAGIC = b"SQLite format 3"


def photo_name(extension: str = "jpg") -> str:
    return f"{secrets.token_hex(16)}.{extension}"


# --- Storage fixtures & helpers -----------------------------------------------------------


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    store = LocalStorage(tmp_path / "storage")
    store.ensure_layout()
    return store


def make_dir_link(link: Path, target: Path, kind: str) -> None:
    """Plant a directory symlink or (Windows) junction, skipping when the platform cannot."""
    if kind == "symlink":
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"directory symlinks unavailable here: {exc}")
        return
    if sys.platform != "win32":
        pytest.skip("directory junctions only exist on Windows")
    import _winapi  # noqa: PLC0415 - Windows-only module

    _winapi.CreateJunction(str(target), str(link))


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


LINK_KINDS = ["symlink", "junction"]


# --- LocalStorage.resolve -------------------------------------------------------------------


UNSAFE_SEGMENTS = [
    "..",
    ".",
    "../x",
    "..\\x",
    "a/b",
    "a\\b",
    "C:",
    "C:\\x",
    "C:x",
    "/etc/passwd",
    "\\\\server\\share",
    "//server/share",
    "",
    " ",
    ".hidden",
    "-dash",
    "_underscore",
    "nul",
    "NUL",
    "CON.txt",
    "con",
    "prn.jpg",
    "aux",
    "com1",
    "COM9.png",
    "lpt1",
    "a\x00b",
    "photo.jpg\x00",
    "\uff0e\uff0e",  # fullwidth ".."
    "\uff0e",
    "abc\uff0ejpg",
    "\u2024\u2024",  # one-dot leaders
    "\uff41\uff42\uff43",  # fullwidth "abc"
    "caf\u00e9",
    "a" * 65,
    "a" * 65 + ".jpg",
    "a.abcdefghi",  # extension longer than 8
    "a.b.c",
    "a.",
    "a ",
    "a\n",
    "photo.jpg:stream",
    "photo.jpg::$DATA",
    "a*",
    "a?",
    "%2e%2e",
]


@pytest.mark.parametrize("segment", UNSAFE_SEGMENTS, ids=repr)
def test_resolve_rejects_unsafe_segment(storage, segment):
    with pytest.raises(UnsafePathError):
        storage.resolve(segment)
    with pytest.raises(UnsafePathError):
        storage.resolve(EVENT_ID, PHOTOS_DIR, segment)


@pytest.mark.parametrize("segment", [None, 123, b"abcdefghijkl", Path("abcdefghijkl")], ids=repr)
def test_resolve_rejects_non_string_segments(storage, segment):
    with pytest.raises(UnsafePathError):
        storage.resolve(segment)


def test_resolve_rejects_empty_call(storage):
    with pytest.raises(UnsafePathError):
        storage.resolve()


def test_unsafe_path_error_is_a_structured_400():
    error = UnsafePathError()
    assert error.status_code == 400
    assert error.code == "BAD_REQUEST"


@pytest.mark.parametrize(
    "segments",
    [
        (EVENT_ID,),
        (OTHER_EVENT_ID,),
        (EVENT_ID, PHOTOS_DIR),
        (EVENT_ID, THUMBNAILS_DIR, "0123456789abcdef0123456789abcdef.webp"),
        ("a" * 64,),
        ("a" * 64 + ".abcdefgh",),
        ("A-b_c.WEBP",),
    ],
)
def test_resolve_accepts_valid_segments_inside_events_root(storage, segments):
    resolved = storage.resolve(*segments)
    assert resolved == storage.events_root.joinpath(*segments)
    assert resolved.is_relative_to(storage.events_root)
    assert resolved != storage.events_root


def test_events_root_requires_ensure_layout(tmp_path):
    store = LocalStorage(tmp_path / "storage")
    with pytest.raises(RuntimeError):
        store.events_root  # noqa: B018 - property access is the behaviour under test
    root = store.ensure_layout()
    assert root == (tmp_path / "storage" / "events").resolve()
    assert store.ensure_layout() == root  # idempotent


@pytest.mark.parametrize(
    "event_id",
    ["", "abc", "ABCDEFGHIJKL", "abcdefghijk.", "abcdefghijk_", "abcdefghijk-", "abcdefghijklm", "photos",
     "../abcdefghi", "abcdefghijk\x00", "abcdefghij.k", "abcdefghijk\uff41"],
    ids=repr,
)
def test_event_dir_rejects_invalid_event_ids(storage, event_id):
    with pytest.raises(UnsafePathError):
        storage.event_dir(event_id)
    with pytest.raises(UnsafePathError):
        storage.photo_path(event_id, photo_name())
    with pytest.raises(UnsafePathError):
        storage.thumbnail_path(event_id, photo_name("webp"))
    with pytest.raises(UnsafePathError):
        storage.create_event_dirs(event_id)
    with pytest.raises(UnsafePathError):
        storage.delete_event(event_id)


def test_event_dir_accepts_valid_event_id(storage):
    assert storage.event_dir(EVENT_ID) == storage.events_root / EVENT_ID


@pytest.mark.parametrize("filename", ["../x.jpg", "..", "a/b.jpg", "a\\b.jpg", "nul.jpg", "x.jpg\x00"], ids=repr)
def test_photo_and_thumbnail_paths_reject_unsafe_filenames(storage, filename):
    with pytest.raises(UnsafePathError):
        storage.photo_path(EVENT_ID, filename)
    with pytest.raises(UnsafePathError):
        storage.thumbnail_path(EVENT_ID, filename)


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_resolve_refuses_link_escaping_events_root(storage, tmp_path, kind):
    outside = tmp_path / "outside"
    (outside / PHOTOS_DIR).mkdir(parents=True)
    make_dir_link(storage.events_root / EVENT_ID, outside, kind)

    for segments in [(EVENT_ID,), (EVENT_ID, PHOTOS_DIR), (EVENT_ID, PHOTOS_DIR, photo_name())]:
        with pytest.raises(UnsafePathError):
            storage.resolve(*segments)
    with pytest.raises(UnsafePathError):
        storage.event_dir(EVENT_ID)


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_save_photo_refuses_to_write_through_link_escaping_root(storage, tmp_path, kind):
    outside = tmp_path / "outside"
    for sub_dir in (PHOTOS_DIR, THUMBNAILS_DIR):
        (outside / sub_dir).mkdir(parents=True)
    make_dir_link(storage.events_root / EVENT_ID, outside, kind)

    with pytest.raises(UnsafePathError):
        storage.save_photo(EVENT_ID, photo_name(), b"photo", photo_name("webp"), b"thumb")
    assert list(outside.rglob("*.*")) == []


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_delete_event_through_link_never_touches_target_outside_root(storage, tmp_path, kind):
    """Refusing (UnsafePathError) or removing just the link are both safe; deleting the target is not."""
    outside = tmp_path / "outside"
    outside.mkdir()
    precious = outside / "precious.txt"
    precious.write_text("keep me")
    link = storage.events_root / EVENT_ID
    make_dir_link(link, outside, kind)

    try:
        storage.delete_event(EVENT_ID)
    except UnsafePathError:
        pass

    assert precious.read_text() == "keep me"


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_delete_event_on_linked_event_dir_removes_only_the_link(storage, kind):
    storage.create_event_dirs(OTHER_EVENT_ID)
    victim_photo = storage.photo_path(OTHER_EVENT_ID, photo_name())
    victim_photo.write_bytes(b"other event's photo")
    link = storage.events_root / EVENT_ID
    make_dir_link(link, storage.events_root / OTHER_EVENT_ID, kind)

    storage.delete_event(EVENT_ID)

    assert victim_photo.read_bytes() == b"other event's photo"
    assert not is_link(link) and not link.exists()


# --- LocalStorage operations --------------------------------------------------------------


def test_create_event_dirs_builds_layout(storage):
    storage.create_event_dirs(EVENT_ID)
    assert (storage.events_root / EVENT_ID / PHOTOS_DIR).is_dir()
    assert (storage.events_root / EVENT_ID / THUMBNAILS_DIR).is_dir()
    storage.create_event_dirs(EVENT_ID)  # idempotent


def test_save_photo_writes_both_files(storage):
    storage.create_event_dirs(EVENT_ID)
    name, thumb = photo_name(), photo_name("webp")
    storage.save_photo(EVENT_ID, name, b"photo-bytes", thumb, b"thumb-bytes")
    assert storage.photo_path(EVENT_ID, name).read_bytes() == b"photo-bytes"
    assert storage.thumbnail_path(EVENT_ID, thumb).read_bytes() == b"thumb-bytes"


def test_save_photo_never_overwrites_existing_photo(storage):
    storage.create_event_dirs(EVENT_ID)
    name, thumb = photo_name(), photo_name("webp")
    storage.save_photo(EVENT_ID, name, b"original", thumb, b"original-thumb")

    other_thumb = photo_name("webp")
    with pytest.raises(FileExistsError):
        storage.save_photo(EVENT_ID, name, b"attacker", other_thumb, b"attacker-thumb")

    assert storage.photo_path(EVENT_ID, name).read_bytes() == b"original"
    assert storage.thumbnail_path(EVENT_ID, thumb).read_bytes() == b"original-thumb"
    assert not storage.thumbnail_path(EVENT_ID, other_thumb).exists()


def test_save_photo_thumbnail_collision_rolls_back_new_photo(storage):
    storage.create_event_dirs(EVENT_ID)
    thumb = photo_name("webp")
    storage.save_photo(EVENT_ID, photo_name(), b"first", thumb, b"first-thumb")

    new_name = photo_name()
    with pytest.raises(FileExistsError):
        storage.save_photo(EVENT_ID, new_name, b"second", thumb, b"second-thumb")

    assert not storage.photo_path(EVENT_ID, new_name).exists()
    assert storage.thumbnail_path(EVENT_ID, thumb).read_bytes() == b"first-thumb"


def test_save_photo_into_deleted_event_raises_event_not_found(storage):
    storage.create_event_dirs(EVENT_ID)
    storage.delete_event(EVENT_ID)
    with pytest.raises(EventNotFoundError):
        storage.save_photo(EVENT_ID, photo_name(), b"x", photo_name("webp"), b"y")
    assert not (storage.events_root / EVENT_ID).exists()


def test_save_photo_into_never_created_event_raises_event_not_found(storage):
    with pytest.raises(EventNotFoundError):
        storage.save_photo(EVENT_ID, photo_name(), b"x", photo_name("webp"), b"y")
    assert not (storage.events_root / EVENT_ID).exists()


def test_save_photo_rejects_unsafe_names(storage):
    storage.create_event_dirs(EVENT_ID)
    with pytest.raises(UnsafePathError):
        storage.save_photo(EVENT_ID, "../../escape.jpg", b"x", photo_name("webp"), b"y")
    with pytest.raises(UnsafePathError):
        storage.save_photo(EVENT_ID, photo_name(), b"x", "..\\escape.webp", b"y")
    assert list((storage.events_root / EVENT_ID).rglob("*.*")) == []


def test_delete_photo_is_idempotent(storage):
    storage.create_event_dirs(EVENT_ID)
    name, thumb = photo_name(), photo_name("webp")
    storage.save_photo(EVENT_ID, name, b"p", thumb, b"t")

    storage.delete_photo(EVENT_ID, name, thumb)
    storage.delete_photo(EVENT_ID, name, thumb)

    assert not storage.photo_path(EVENT_ID, name).exists()
    assert not storage.thumbnail_path(EVENT_ID, thumb).exists()
    # Deleting a photo that never existed, or from an event without directories, is a no-op too.
    storage.delete_photo(OTHER_EVENT_ID, photo_name(), photo_name("webp"))


def test_delete_event_removes_whole_tree_and_is_idempotent(storage):
    storage.create_event_dirs(EVENT_ID)
    storage.create_event_dirs(OTHER_EVENT_ID)
    for _ in range(3):
        storage.save_photo(EVENT_ID, photo_name(), b"p", photo_name("webp"), b"t")
    keep = photo_name()
    storage.save_photo(OTHER_EVENT_ID, keep, b"other", photo_name("webp"), b"t")

    storage.delete_event(EVENT_ID)
    storage.delete_event(EVENT_ID)

    assert not (storage.events_root / EVENT_ID).exists()
    assert storage.events_root.is_dir()
    assert storage.photo_path(OTHER_EVENT_ID, keep).read_bytes() == b"other"


def test_free_bytes_reports_disk_space(storage):
    assert storage.free_bytes() > 0


# --- Media serving: helpers -----------------------------------------------------------------


def events_dir(client: Any) -> Path:
    return Path(client.app.state.settings.storage_dir) / "events"


def db_file(client: Any) -> Path:
    return Path(client.app.state.settings.database_path)


def assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["request_id"]


def raw_request(app: Any, path: str, *, method: str = "GET", raw_path: bytes | None = None) -> tuple[int, dict, bytes]:
    """Drive the ASGI app with a hand-built scope, bypassing httpx's URL normalisation."""
    if raw_path is None:
        raw_path = path.encode("utf-8", "surrogateescape")

    async def run() -> tuple[int, dict, bytes]:
        response_done = anyio.Event()
        request_sent = False
        start: dict[str, Any] = {}
        chunks: list[bytes] = []

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await response_done.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    response_done.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": raw_path,
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"testserver"), (b"user-agent", b"raw-asgi-test")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "state": {},
        }
        with anyio.fail_after(10):
            await app(scope, receive, send)
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in start["headers"]}
        return start["status"], headers, b"".join(chunks)

    return asyncio.run(run())


@pytest.fixture
def photo(client, create_event, upload_photo) -> dict[str, Any]:
    created = create_event("Media")
    response = upload_photo(created["event"]["id"])
    assert response.status_code == 201, response.text
    return {**response.json(), "host_token": created["host_token"]}


# --- Media serving: happy path ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "mime", "extension", "magic"),
    [
        ("JPEG", "image/jpeg", "jpg", b"\xff\xd8\xff"),
        ("PNG", "image/png", "png", b"\x89PNG\r\n\x1a\n"),
        ("WEBP", "image/webp", "webp", b"RIFF"),
    ],
)
def test_uploaded_photo_and_thumbnail_are_served_with_hardened_headers(
    client, create_event, upload_photo, fmt, mime, extension, magic
):
    event_id = create_event()["event"]["id"]
    response = upload_photo(event_id, image_bytes(fmt), filename=f"x.{extension}", content_type=mime)
    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["content_type"] == mime
    assert photo["url"] == f"/media/events/{event_id}/photos/{photo['id']}.{extension}"

    for url, expected_mime, expected_magic in [
        (photo["url"], mime, magic),
        (photo["thumbnail_url"], "image/webp", b"RIFF"),
    ]:
        served = client.get(url)
        assert served.status_code == 200
        assert served.headers["content-type"] == expected_mime
        for name, value in MEDIA_HEADERS.items():
            assert served.headers[name] == value, name
        assert served.headers["content-disposition"] == "inline"
        assert served.content.startswith(expected_magic)
        on_disk = events_dir(client) / event_id / url.split(f"/{event_id}/", 1)[1]
        assert served.content == on_disk.read_bytes()


def test_media_head_request_returns_headers_without_body(client, photo):
    get = client.get(photo["url"])
    head = client.head(photo["url"])
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"] == "image/jpeg"
    assert head.headers["content-length"] == str(len(get.content))
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["content-security-policy"] == MEDIA_HEADERS["content-security-policy"]


def test_media_if_none_match_returns_304(client, photo):
    first = client.get(photo["url"])
    etag = first.headers["etag"]

    for header in (etag, f"W/{etag}", f'"nope", {etag}', "*"):
        cached = client.get(photo["url"], headers={"If-None-Match": header})
        assert cached.status_code == 304, header
        assert cached.content == b""
        assert cached.headers["etag"] == etag
        assert cached.headers["cache-control"] == MEDIA_HEADERS["cache-control"]

    stale = client.get(photo["url"], headers={"If-None-Match": '"some-other-etag"'})
    assert stale.status_code == 200
    assert stale.content == first.content


def test_deleted_photo_and_event_media_are_no_longer_served(client, create_event, upload_photo):
    created = create_event()
    event_id = created["event"]["id"]
    kept = upload_photo(event_id).json()
    removed = upload_photo(event_id).json()

    response = client.delete(f"/api/events/{event_id}/photos/{removed['id']}", headers=auth(created["host_token"]))
    assert response.status_code == 204
    for url in (removed["url"], removed["thumbnail_url"]):
        assert_error(client.get(url), 404, "NOT_FOUND")
    assert client.get(kept["url"]).status_code == 200

    assert client.delete(f"/api/events/{event_id}", headers=auth(created["host_token"])).status_code == 204
    for url in (kept["url"], kept["thumbnail_url"]):
        assert_error(client.get(url), 404, "NOT_FOUND")


# --- Media serving: strict allow-list -----------------------------------------------------------


def test_manually_placed_file_matching_the_pattern_is_served(client, photo):
    """Control for the tests below: a file that fits the pattern is served even if the app didn't write it."""
    event_id = photo["event_id"]
    name = photo_name()
    (events_dir(client) / event_id / PHOTOS_DIR / name).write_bytes(image_bytes())
    served = client.get(f"/media/events/{event_id}/photos/{name}")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize(
    "relative",
    [
        "photos/notes.txt",
        "photos/{hex}.tmp",
        "secret.jpg",
        "photos/{hex}.JPG",
        "photos/{hex}.jpeg",
        "photos/{hex}.svg",
        "photos/{hex}.html",
        "photos/{hex}.jpg.bak",
        "photos/{HEX}.jpg",
        "photos/{short}.jpg",
        "photos/{hex}0.jpg",
        "photos/.{hex}.jpg",
        "other/{hex}.jpg",
        "PHOTOS/{hex}.png",
        "thumbs/{hex}.gif",
    ],
)
def test_files_outside_the_strict_pattern_are_not_served(client, photo, relative):
    event_id = photo["event_id"]
    hex_id = secrets.token_hex(16)
    relative = relative.format(hex=hex_id, HEX=hex_id.upper(), short=hex_id[:31])
    target = events_dir(client) / event_id / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes() if relative.endswith((".jpg", ".png", ".JPG")) else b"<svg onload=alert(1)>")

    response = client.get(f"/media/events/{event_id}/{relative}")
    assert_error(response, 404, "NOT_FOUND")
    assert b"<svg" not in response.content


def test_directory_named_like_a_photo_is_not_served(client, photo):
    event_id = photo["event_id"]
    name = photo_name()
    (events_dir(client) / event_id / PHOTOS_DIR / name).mkdir()
    assert_error(client.get(f"/media/events/{event_id}/photos/{name}"), 404, "NOT_FOUND")


@pytest.mark.parametrize(
    "url_template",
    [
        "/media/events/",
        "/media/events/{event}",
        "/media/events/{event}/",
        "/media/events/{event}/photos",
        "/media/events/{event}/photos/",
        "/media/events/{EVENT}/photos/{file}",
        "/media/events/{event}/thumbs/{file}",  # photo name under the wrong directory
        "/media/events/zzzzzzzzzzzz/photos/{file}",  # right file name, wrong event
    ],
)
def test_non_file_and_mismatched_media_urls_404(client, photo, url_template):
    event_id = photo["event_id"]
    file_name = photo["url"].rsplit("/", 1)[1]
    url = url_template.format(event=event_id, EVENT=event_id.upper(), file=file_name)
    assert_error(client.get(url), 404, "NOT_FOUND")


@pytest.mark.parametrize(
    "url",
    [
        "/media/events/../db/gambarkita.sqlite3",
        "/media/events/%2e%2e/db/gambarkita.sqlite3",
        "/media/events/%2E%2E/%2E%2E/storage/db/gambarkita.sqlite3",
        "/media/events/..%2fdb%2fgambarkita.sqlite3",
        "/media/events/..%5cdb%5cgambarkita.sqlite3",
        "/media/db/gambarkita.sqlite3",
        "/media/gambarkita.sqlite3",
        "/db/gambarkita.sqlite3",
        "/storage/db/gambarkita.sqlite3",
        "/gambarkita.sqlite3",
        "/api/../storage/db/gambarkita.sqlite3",
    ],
)
def test_sqlite_database_is_unreachable_over_http(client, photo, url):
    assert db_file(client).is_file()
    response = client.get(url)
    assert response.status_code == 404
    assert SQLITE_MAGIC not in response.content


# --- Media serving: raw non-canonical paths (ASGI level) ------------------------------------------


def test_raw_asgi_harness_serves_canonical_path(client, photo):
    status, headers, body = raw_request(client.app, photo["url"])
    assert status == 200
    assert headers["content-type"] == "image/jpeg"
    assert body == client.get(photo["url"]).content


@pytest.mark.parametrize(
    "template",
    [
        "/media/events/{event}/./photos/{file}",
        "/media/events/./{event}/photos/{file}",
        "/media/events/{event}/photos/./{file}",
        "/media/events/{event}/photos/../photos/{file}",
        "/media/events/{event}/thumbs/../photos/{file}",
        "/media/events/zzzzzzzzzzzz/../{event}/photos/{file}",
        "/media/events/{event}//photos/{file}",
        "/media/events//{event}/photos/{file}",
        "/media/events/{event}/photos//{file}",
        "/media/events/{event}/photos\\{file}",
        "/media/events/{event}\\photos\\{file}",
        "/media/events/{event}/photos/{file}\x00",
        "/media/events/{event}/photos/{file}\x00.png",
        "/media/events/{event}/photos/{file}.",
        "/media/events/{event}/photos/{file} ",
        "/media/events/{event}/photos/{file}::$DATA",
    ],
)
def test_raw_non_canonical_media_paths_404_even_when_they_resolve_to_a_real_file(client, photo, template):
    event_id = photo["event_id"]
    file_name = photo["url"].rsplit("/", 1)[1]
    path = template.format(event=event_id, file=file_name)

    status, headers, body = raw_request(client.app, path)

    assert status == 404, path
    assert headers["content-type"].startswith("application/json")
    assert b"NOT_FOUND" in body
    assert not body.startswith(b"\xff\xd8\xff")


def test_raw_media_path_with_trailing_slash_404(client, photo):
    status, _, body = raw_request(client.app, photo["url"] + "/")
    assert status == 404
    assert not body.startswith(b"\xff\xd8\xff")


@pytest.mark.parametrize(
    "template",
    [
        "/media/events/../db/gambarkita.sqlite3",
        "/media/events/..\\db\\gambarkita.sqlite3",
        "/media/events/{event}/../../db/gambarkita.sqlite3",
        "/media/events/{event}/photos/../../../db/gambarkita.sqlite3",
        "/media/events/./../db/gambarkita.sqlite3",
        "/media/events/..//db//gambarkita.sqlite3",
        "/media/events/{db_abs}",
        "/media/events/{db_abs_backslash}",
    ],
)
def test_raw_traversal_to_sqlite_database_is_refused(client, photo, template):
    database = db_file(client)
    assert database.is_file()
    path = template.format(
        event=photo["event_id"],
        db_abs=database.resolve().as_posix(),
        db_abs_backslash=str(database.resolve()),
    )

    status, _, body = raw_request(client.app, path)

    assert status == 404, path
    assert SQLITE_MAGIC not in body


def test_raw_percent_encoded_traversal_to_database_is_refused(client, photo):
    status, _, body = raw_request(
        client.app,
        "/media/events/../db/gambarkita.sqlite3",
        raw_path=b"/media/events/%2e%2e/db/gambarkita.sqlite3",
    )
    assert status == 404
    assert SQLITE_MAGIC not in body


# --- Media serving: methods --------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_methods_on_media_url_are_405(client, photo, method):
    response = client.request(method, photo["url"], content=b"overwrite attempt")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")

    # The stored file is untouched.
    served = client.get(photo["url"])
    assert served.status_code == 200
    assert served.content.startswith(b"\xff\xd8\xff")


def test_raw_post_to_media_url_is_405(client, photo):
    status, headers, body = raw_request(client.app, photo["url"], method="POST")
    assert status == 405
    assert headers["content-type"].startswith("application/json")
    assert b"METHOD_NOT_ALLOWED" in body
