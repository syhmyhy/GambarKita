"""Round-2 regression tests: image pipeline, admission control, storage janitor and media mount.

Covers the fixes for:

1. ``image_processing``: hand-parsed WebP dimensions (no Pillow before the pixel
   check), per-format pixel caps, MPO accepted as JPEG, ICC profiles never copied
   (pixels converted to sRGB), 16-bit colour-key transparency, stored-size cap
   with lossy fallback.
2. ``admission.ProcessingBudget``: pixel-weighted admission, job cap, queue timeout.
3. ``photos``: persist survives request cancellation, readable size limits,
   ``photo.deleted`` published before file removal.
4. ``janitor`` / ``storage``: tombstones, retry loop, startup orphan sweep,
   no link traversal.
5. ``events``: ``event.deleted`` + close before file removal.
10. media mount: trailing slash, janitor tombstones, private caching.

Modules introduced by the fixes (``admission``, ``janitor``) are imported inside
the tests so that each test fails on its own against the pre-fix code.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import random
import secrets
import shutil
import struct
import pathlib
import subprocess
import sys
import sqlite3
import contextlib
import threading
import time
import zipfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.datastructures import UploadFile
from starlette.websockets import WebSocketDisconnect

from app.core.errors import ImageTooLargeError, UnsafePathError
from app.services import image_processing
from app.services import storage as storage_module
from app.services.image_processing import JPEG, PNG, WEBP, ImagePolicy, inspect_image, sniff_image_format
from app.services.storage import PHOTOS_DIR, THUMBNAILS_DIR, LocalStorage
from tests.conftest import ORIGIN, auth, image_bytes

KIB = 1024
STORED_CAP = 64 * KIB  # smallest max_stored_bytes the settings accept
LOSSLESS_CAP = {"max_lossless_image_pixels": 10_000, "max_image_pixels": 40_000}
EVENT_ID = "abcdefghijkl"
OTHER_EVENT_ID = "0123456789ab"
UNKNOWN_EVENT_ID = "zzzzzzzzzzzz"
MEDIA_CACHE_CONTROL = "private, max-age=31536000, immutable"
SWOP_PROFILE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "spool" / "drivers" / "color" / "RSWOP.icm"
WS_TIMEOUT = 5.0

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows file-locking semantics")


@pytest.fixture(autouse=True)
def _restore_pillow_bomb_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app() sets Image.MAX_IMAGE_PIXELS process-wide; put back whatever was there."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)


@pytest.fixture
def fast_unlink_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """LocalStorage retries a locked delete 3x with 0.1/0.2/0.3 s pauses; shrink them."""
    monkeypatch.setattr(storage_module, "_DELETE_RETRY_DELAY", 0.01)


# --- Generic helpers ----------------------------------------------------------------------


def encode(image: Image.Image, fmt: str, **save_kwargs: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def noise(side: int, mode: str = "RGB", seed: int = 0) -> Image.Image:
    rng = random.Random(seed)
    return Image.frombytes(mode, (side, side), rng.randbytes(side * side * len(mode)))


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def raw_png(width: int, height: int, *, bit_depth: int, color_type: int, scanlines: bytes, extra_chunks: tuple = ()) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + b"".join(png_chunk(kind, payload) for kind, payload in extra_chunks)
        + png_chunk(b"IDAT", zlib.compress(scanlines, 9))
        + png_chunk(b"IEND", b"")
    )


def png_chunk_types(data: bytes) -> list[bytes]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    kinds, pos = [], 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kinds.append(data[pos + 4 : pos + 8])
        pos += 12 + length
    return kinds


def jpeg_segments(data: bytes) -> list[tuple[int, bytes]]:
    """(marker, payload) for every header segment up to and including SOS."""
    assert data[:2] == b"\xff\xd8"
    segments, pos = [], 2
    while pos < len(data):
        assert data[pos] == 0xFF, f"lost JPEG marker sync at {pos}"
        marker = data[pos + 1]
        if marker == 0xDA:
            segments.append((marker, b""))
            break
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        segments.append((marker, data[pos + 4 : pos + 2 + length]))
        pos += 2 + length
    return segments


def webp_chunk_types(data: bytes) -> list[bytes]:
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    kinds, pos = [], 12
    while pos + 8 <= len(data):
        kind = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        kinds.append(kind)
        pos += 8 + size + (size & 1)
    return kinds


def assert_no_icc_container_data(data: bytes) -> None:
    """The encoded file carries no ICC profile at the container level."""
    fmt = sniff_image_format(data[:64])
    if fmt is JPEG:
        for marker, payload in jpeg_segments(data):
            assert not (marker == 0xE2 and payload.startswith(b"ICC_PROFILE\x00")), "APP2 ICC_PROFILE present"
    elif fmt is PNG:
        assert b"iCCP" not in png_chunk_types(data)
    else:
        assert fmt is WEBP
        kinds = webp_chunk_types(data)
        assert b"ICCP" not in kinds
        if kinds[0] == b"VP8X":
            assert not data[20] & 0x20, "VP8X ICC flag set"
    assert decode(data).info.get("icc_profile") is None


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code, error
    return error


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# --- API helpers ----------------------------------------------------------------------------


def new_event(client: TestClient, name: str = "Regression Event") -> dict[str, Any]:
    response = client.post("/api/events", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def post_photo(
    client: TestClient,
    event_id: str,
    data: bytes | None = None,
    *,
    filename: str = "photo.jpg",
    content_type: str = "image/jpeg",
) -> Any:
    payload = image_bytes() if data is None else data
    return client.post(f"/api/events/{event_id}/photos", files={"file": (filename, payload, content_type)})


def events_root(client: TestClient) -> Path:
    return Path(client.app.state.settings.storage_dir) / "events"


def media_path(client: TestClient, url: str) -> Path:
    """Map a /media/events/... URL onto the file on disk."""
    prefix = "/media/events/"
    assert url.startswith(prefix)
    return events_root(client).joinpath(*url[len(prefix) :].split("/"))


def services_of(client: TestClient) -> Any:
    return client.app.state.services


# --- WebSocket helpers ------------------------------------------------------------------------


def ws_connect(client: TestClient, event_id: str) -> Any:
    return client.websocket_connect(f"/ws/events/{event_id}", headers={"origin": ORIGIN})


def ws_recv(ws: Any, timeout: float = WS_TIMEOUT) -> dict[str, Any]:
    """Receive one JSON message, failing instead of hanging; raises WebSocketDisconnect on close."""

    async def receive() -> dict[str, Any]:
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    message = ws.portal.call(receive)
    if message["type"] == "websocket.close":
        raise WebSocketDisconnect(code=message.get("code", 1000), reason=message.get("reason", ""))
    assert message["type"] == "websocket.send", message
    return json.loads(message["text"])


def ws_expect_close(ws: Any) -> int:
    with pytest.raises(WebSocketDisconnect) as info:
        unexpected = ws_recv(ws)
        pytest.fail(f"expected the socket to close, got {unexpected!r}")
    return info.value.code


# --- Filesystem link helpers ------------------------------------------------------------------


def make_dir_link(link: Path, target: Path, kind: str) -> None:
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


@contextmanager
def held_open(*paths: Path) -> Iterator[None]:
    """On Windows an open handle (opened without FILE_SHARE_DELETE) blocks deletion."""
    handles = [open(path, "rb") for path in paths]  # noqa: SIM115
    try:
        yield
    finally:
        for handle in handles:
            handle.close()


def photo_name(extension: str = "jpg") -> str:
    return f"{secrets.token_hex(16)}.{extension}"


# =============================================================================================
# 1. WebP header parsing
# =============================================================================================


def _animated_webp(size: tuple[int, int] = (150, 151)) -> bytes:
    frames = [Image.new("RGB", size, (255, 0, 0)), Image.new("RGB", size, (0, 0, 255))]
    return encode(frames[0], "WEBP", save_all=True, append_images=frames[1:], duration=100, loop=0)


WEBP_VARIANTS: dict[str, tuple[bytes, bytes]] = {}


def _webp_variants() -> dict[str, tuple[bytes, bytes]]:
    """name -> (expected leading chunk, file bytes); built lazily and cached."""
    if not WEBP_VARIANTS:
        from PIL import ImageCms  # noqa: PLC0415

        srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        WEBP_VARIANTS.update(
            {
                "vp8-lossy-odd": (b"VP8 ", encode(Image.new("RGB", (123, 45), (1, 2, 3)), "WEBP", quality=80)),
                "vp8-lossy-wide": (b"VP8 ", encode(Image.new("RGB", (4097, 3), (9, 9, 9)), "WEBP", quality=50)),
                "vp8l-lossless": (b"VP8L", encode(Image.new("RGB", (123, 45), (1, 2, 3)), "WEBP", lossless=True)),
                "vp8l-lossless-max-width": (
                    b"VP8L",
                    encode(Image.new("RGB", (16383, 1), (1, 2, 3)), "WEBP", lossless=True),
                ),
                "vp8l-lossless-tall": (b"VP8L", encode(Image.new("RGBA", (2, 9001), (1, 2, 3, 4)), "WEBP", lossless=True)),
                "vp8x-alpha": (b"VP8X", encode(Image.new("RGBA", (77, 99), (1, 2, 3, 100)), "WEBP")),
                "vp8x-animated": (b"VP8X", _animated_webp()),
                "vp8x-exif": (b"VP8X", encode(Image.new("RGB", (33, 17), (1, 2, 3)), "WEBP", exif=b"Exif\x00\x00II*\x00")),
                "vp8x-icc-lossless": (
                    b"VP8X",
                    encode(Image.new("RGB", (300, 7), (1, 2, 3)), "WEBP", lossless=True, icc_profile=srgb),
                ),
            }
        )
    return WEBP_VARIANTS


@pytest.mark.parametrize(
    "variant",
    [
        "vp8-lossy-odd",
        "vp8-lossy-wide",
        "vp8l-lossless",
        "vp8l-lossless-max-width",
        "vp8l-lossless-tall",
        "vp8x-alpha",
        "vp8x-animated",
        "vp8x-exif",
        "vp8x-icc-lossless",
    ],
)
def test_webp_dimensions_match_pillow_for_every_bitstream_kind(variant: str) -> None:
    expected_chunk, data = _webp_variants()[variant]
    assert data[12:16] == expected_chunk  # the fixture really exercises that header layout
    with Image.open(io.BytesIO(data)) as reference:
        expected_size = reference.size
    assert image_processing.webp_dimensions(data) == expected_size


def _corrupt(data: bytes, offset: int, value: bytes) -> bytes:
    return data[:offset] + value + data[offset + len(value) :]


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "riff-only",
        "random",
        "jpeg",
        "png",
        "vp8-bad-start-code",
        "vp8-truncated",
        "vp8l-bad-signature",
        "vp8l-truncated",
        "vp8x-truncated",
        "unknown-chunk",
    ],
)
def test_webp_dimensions_returns_none_for_garbage(case: str) -> None:
    variants = _webp_variants()
    lossy = variants["vp8-lossy-odd"][1]
    lossless = variants["vp8l-lossless"][1]
    extended = variants["vp8x-animated"][1]
    data = {
        "empty": b"",
        "riff-only": b"RIFF\x00\x00\x00\x00WEBP",
        "random": random.Random(7).randbytes(64),
        "jpeg": image_bytes("JPEG"),
        "png": image_bytes("PNG"),
        "vp8-bad-start-code": _corrupt(lossy, 23, b"\x00\x00\x00"),
        "vp8-truncated": lossy[:29],
        "vp8l-bad-signature": _corrupt(lossless, 20, b"\x00"),
        "vp8l-truncated": lossless[:24],
        "vp8x-truncated": extended[:29],
        "unknown-chunk": _corrupt(lossless, 12, b"VP9 "),
    }[case]
    assert image_processing.webp_dimensions(data) is None


def test_webp_with_unparseable_header_is_rejected_as_invalid_image_from_the_header(
    client, create_event, upload_photo, open_spy
) -> None:
    lossless = _webp_variants()["vp8l-lossless"][1]
    broken = _corrupt(lossless, 20, b"\x00")  # VP8L signature byte must be 0x2F
    assert sniff_image_format(broken[:64]) is WEBP
    event_id = create_event()["event"]["id"]
    response = upload_photo(event_id, broken, filename="x.webp", content_type="image/webp")
    assert_error(response, 422, "INVALID_IMAGE")
    assert open_spy.calls == [], "an unreadable WebP header is rejected without Pillow"


# =============================================================================================
# 1. Per-format pixel caps; WebP rejected before Pillow allocates anything
# =============================================================================================


def _forge_vp8x_canvas(data: bytes, width: int, height: int) -> bytes:
    assert data[12:16] == b"VP8X"
    return data[:24] + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little") + data[30:]


def _forged_vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    payload = b"\x2f" + bits.to_bytes(4, "little") + b"\x00" * 11
    chunk = b"VP8L" + len(payload).to_bytes(4, "little") + payload
    return b"RIFF" + (4 + len(chunk)).to_bytes(4, "little") + b"WEBP" + chunk


def _forged_vp8(width: int, height: int) -> bytes:
    payload = b"\x10\x02\x00" + b"\x9d\x01\x2a" + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00" * 8
    chunk = b"VP8 " + len(payload).to_bytes(4, "little") + payload
    return b"RIFF" + (4 + len(chunk)).to_bytes(4, "little") + b"WEBP" + chunk


class _OpenSpy:
    """Stand-in for PIL.Image.open that records every call and refuses to decode."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs.get("formats"))
        raise AssertionError("PIL.Image.open must not be called for this input")


@pytest.fixture
def open_spy(monkeypatch: pytest.MonkeyPatch) -> _OpenSpy:
    spy = _OpenSpy()
    monkeypatch.setattr(image_processing.Image, "open", spy)
    return spy


def _tiny_webp_over_cap(kind: str) -> tuple[bytes, tuple[int, int]]:
    """A small WebP file whose canvas exceeds LOSSLESS_CAP (10,000 px)."""
    if kind == "lossless":
        return encode(Image.new("RGB", (150, 150), (5, 5, 5)), "WEBP", lossless=True), (150, 150)
    if kind == "animated":
        return _animated_webp((150, 150)), (150, 150)
    if kind == "lossy":
        return encode(Image.new("RGB", (150, 150), (5, 5, 5)), "WEBP", quality=60), (150, 150)
    assert kind == "forged-vp8x-canvas"
    return _forge_vp8x_canvas(_animated_webp((16, 16)), 1000, 1000), (1000, 1000)


@pytest.mark.parametrize("kind", ["lossless", "animated", "lossy", "forged-vp8x-canvas"])
def test_webp_over_lossless_cap_is_422_and_never_opened_by_pillow(make_client, open_spy, kind) -> None:
    data, (width, height) = _tiny_webp_over_cap(kind)
    assert len(data) < 4 * KIB
    client = make_client(**LOSSLESS_CAP)
    event_id = new_event(client)["event"]["id"]

    response = post_photo(client, event_id, data, filename="x.webp", content_type="image/webp")

    error = assert_error(response, 422, "IMAGE_TOO_LARGE")
    assert error["details"]["width"] == width and error["details"]["height"] == height
    assert error["details"]["max_pixels"] == LOSSLESS_CAP["max_lossless_image_pixels"]
    assert open_spy.calls == [], "the WebP canvas must be checked from the header, before Pillow opens it"
    assert list((events_root(client) / event_id / PHOTOS_DIR).iterdir()) == []


@pytest.mark.parametrize(
    ("data", "declared"),
    [
        (_forged_vp8l(16384, 16384), (16384, 16384)),
        (_forged_vp8l(8000, 8000), (8000, 8000)),
        (_forged_vp8(16383, 16383), (16383, 16383)),
    ],
    ids=["vp8l-over-max-dimension", "vp8l-64mp", "vp8-268mp"],
)
def test_inspect_image_rejects_huge_declared_webp_canvas_without_pillow(open_spy, data, declared) -> None:
    assert len(data) < 64
    assert image_processing.webp_dimensions(data) == declared
    policy = ImagePolicy(
        max_pixels=50_000_000, max_dimension=16_383, max_stored_dimension=4096, thumbnail_size=640,
        max_lossless_pixels=24_000_000,
    )
    with pytest.raises(ImageTooLargeError) as info:
        inspect_image(data, policy)
    assert info.value.code == "IMAGE_TOO_LARGE" and info.value.status_code == 422
    assert open_spy.calls == []


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")  # 44,100 px > Pillow's 40,000 warn level
def test_png_over_lossless_cap_is_422_but_jpeg_with_same_pixels_is_accepted(make_client) -> None:
    client = make_client(**LOSSLESS_CAP)
    event_id = new_event(client)["event"]["id"]

    png = post_photo(client, event_id, image_bytes("PNG", size=(150, 150)), filename="x.png", content_type="image/png")
    error = assert_error(png, 422, "IMAGE_TOO_LARGE")
    assert error["details"]["max_pixels"] == 10_000
    assert (error["details"]["width"], error["details"]["height"]) == (150, 150)

    # Exactly at the lossless cap is fine, for PNG and WebP alike.
    at_cap = post_photo(client, event_id, image_bytes("PNG", size=(100, 100)), filename="x.png", content_type="image/png")
    assert at_cap.status_code == 201, at_cap.text
    webp_at_cap = encode(Image.new("RGB", (100, 100), (5, 90, 5)), "WEBP", lossless=True)
    webp = post_photo(client, event_id, webp_at_cap, filename="x.webp", content_type="image/webp")
    assert webp.status_code == 201, webp.text
    assert (webp.json()["width"], webp.json()["height"]) == (100, 100)

    jpeg = post_photo(client, event_id, image_bytes("JPEG", size=(150, 150)))
    assert jpeg.status_code == 201, jpeg.text
    assert (jpeg.json()["width"], jpeg.json()["height"]) == (150, 150)
    assert jpeg.json()["content_type"] == "image/jpeg"

    too_big_jpeg = post_photo(client, event_id, image_bytes("JPEG", size=(210, 210)))  # 44,100 > 40,000
    error = assert_error(too_big_jpeg, 422, "IMAGE_TOO_LARGE")
    assert error["details"]["max_pixels"] == 40_000


# =============================================================================================
# 1. MPO (JPEG + MPF index) is accepted and stored as a plain JPEG
# =============================================================================================


def test_mpo_upload_is_stored_as_plain_jpeg_without_mpf(client, create_event, upload_photo) -> None:
    primary = Image.new("RGB", (64, 48), (255, 0, 0))
    secondary = Image.new("RGB", (32, 24), (0, 255, 0))
    source = encode(primary, "MPO", save_all=True, append_images=[secondary])
    assert decode(source).format == "MPO"
    assert b"MPF\x00" in source and source.count(b"\xff\xd8") >= 2
    event_id = create_event()["event"]["id"]

    response = upload_photo(event_id, source, filename="IMG_0001.jpg", content_type="image/jpeg")

    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["content_type"] == "image/jpeg"
    assert photo["url"].endswith(".jpg")
    assert (photo["width"], photo["height"]) == (64, 48)
    stored = media_path(client, photo["url"]).read_bytes()
    markers = [marker for marker, _ in jpeg_segments(stored)]
    assert 0xE2 not in markers, "APP2 (MPF) must not be copied"
    assert b"MPF\x00" not in stored
    assert stored.count(b"\xff\xd8") == 1, "the secondary MPO image must not be appended"
    image = decode(stored)
    assert image.format == "JPEG" and image.size == (64, 48)
    red, green, blue = image.getpixel((32, 24))
    assert red > 200 and green < 60 and blue < 60
    served = client.get(photo["url"])
    assert served.status_code == 200 and served.content == stored


# =============================================================================================
# 1. ICC profiles are never copied into stored files
# =============================================================================================


def _zip_payload() -> tuple[bytes, bytes]:
    marker = b"gambarkita-icc-polyglot-" + secrets.token_hex(8).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("payload/readme.txt", marker)
    payload = buffer.getvalue()
    assert zipfile.is_zipfile(io.BytesIO(payload))
    return payload, marker


@pytest.mark.parametrize(
    ("fmt", "mime", "extension", "save_kwargs"),
    [
        ("JPEG", "image/jpeg", "jpg", {}),
        ("WEBP", "image/webp", "webp", {"lossless": True}),
        ("PNG", "image/png", "png", {}),
    ],
)
def test_zip_smuggled_as_icc_profile_never_reaches_disk(client, create_event, upload_photo, fmt, mime, extension, save_kwargs) -> None:
    payload, marker = _zip_payload()
    source = encode(Image.new("RGB", (40, 30), (10, 150, 60)), fmt, icc_profile=payload, **save_kwargs)
    assert decode(source).info.get("icc_profile") == payload  # the profile really is embedded
    event_id = create_event()["event"]["id"]

    response = upload_photo(event_id, source, filename=f"x.{extension}", content_type=mime)

    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["content_type"] == mime
    for url in (photo["url"], photo["thumbnail_url"]):
        stored = media_path(client, url).read_bytes()
        assert marker not in stored
        for signature in (b"PK\x03\x04", b"PK\x01\x02", b"PK\x05\x06"):
            assert signature not in stored, (url, signature)
        assert not zipfile.is_zipfile(io.BytesIO(stored))
        assert_no_icc_container_data(stored)
    # Nothing else in the event directory carries the payload either.
    for path in (events_root(client) / event_id).rglob("*"):
        if path.is_file():
            assert marker not in path.read_bytes(), path


def test_cmyk_jpeg_with_relabelled_profile_is_stored_as_rgb_without_profile(client, create_event, upload_photo) -> None:
    from PIL import ImageCms  # noqa: PLC0415

    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    cmyk_labelled = srgb[:16] + b"CMYK" + srgb[20:]
    source = encode(Image.new("CMYK", (30, 20), (0, 255, 255, 0)), "JPEG", icc_profile=cmyk_labelled)
    event_id = create_event()["event"]["id"]

    response = upload_photo(event_id, source)

    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["content_type"] == "image/jpeg"
    for url in (photo["url"], photo["thumbnail_url"]):
        stored = media_path(client, url).read_bytes()
        assert_no_icc_container_data(stored)
        assert decode(stored).mode in {"RGB", "RGBA"}


@pytest.mark.skipif(not SWOP_PROFILE.is_file(), reason="Windows RSWOP.icm CMYK profile not available")
def test_cmyk_jpeg_with_real_swop_profile_is_colour_converted_to_srgb(client, create_event, upload_photo) -> None:
    swop = SWOP_PROFILE.read_bytes()
    source = encode(Image.new("CMYK", (30, 20), (0, 255, 255, 0)), "JPEG", icc_profile=swop)
    event_id = create_event()["event"]["id"]

    response = upload_photo(event_id, source)

    assert response.status_code == 201, response.text
    photo = response.json()
    stored = media_path(client, photo["url"]).read_bytes()
    thumbnail = media_path(client, photo["thumbnail_url"]).read_bytes()
    assert_no_icc_container_data(stored)
    assert_no_icc_container_data(thumbnail)
    original = decode(stored)
    assert original.mode == "RGB"
    red, green, blue = original.getpixel((15, 10))
    # SWOP 0/100/100/0 is a slightly muted red (~229,50,43 in sRGB), not the naive (255,0,0).
    assert 205 <= red <= 250 and 25 <= green <= 75 and 20 <= blue <= 70, (red, green, blue)


# =============================================================================================
# 1. Stored-size cap with lossy fallback
# =============================================================================================

EXTENSION_FOR_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _assert_photos_within_cap(client: TestClient, event_id: str) -> None:
    for path in (events_root(client) / event_id / PHOTOS_DIR).iterdir():
        assert path.stat().st_size <= STORED_CAP, (path.name, path.stat().st_size)


@pytest.mark.parametrize(
    ("case", "allowed_mimes"),
    [
        ("png-rgb-noise", {"image/jpeg", "image/webp"}),
        ("png-rgba-noise", {"image/webp"}),  # alpha must survive, so no JPEG
        ("webp-lossless-noise", {"image/webp"}),
        ("jpeg-noise", {"image/jpeg"}),
    ],
)
def test_stored_copy_over_size_cap_falls_back_to_lossy(make_client, case, allowed_mimes) -> None:
    source, filename, mime = {
        "png-rgb-noise": (encode(noise(160), "PNG"), "x.png", "image/png"),
        "png-rgba-noise": (encode(noise(160, "RGBA"), "PNG"), "x.png", "image/png"),
        "webp-lossless-noise": (encode(noise(300), "WEBP", lossless=True), "x.webp", "image/webp"),
        "jpeg-noise": (encode(noise(320), "JPEG", quality=95), "x.jpg", "image/jpeg"),
    }[case]
    # Sanity: the app's normal encode of this image (same format, usual settings) exceeds the cap,
    # so only the lossy fallback can store it.
    reference = decode(source)
    normal_format = {"image/png": "PNG", "image/webp": "WEBP", "image/jpeg": "JPEG"}[mime]
    normal_kwargs = {
        "JPEG": {"quality": 88, "optimize": True, "progressive": True},
        "PNG": {"compress_level": 6},
        "WEBP": {"quality": 85, "method": 4},
    }
    assert len(encode(reference, normal_format, **normal_kwargs[normal_format])) > STORED_CAP

    client = make_client(max_stored_bytes=STORED_CAP)
    event_id = new_event(client)["event"]["id"]
    response = post_photo(client, event_id, source, filename=filename, content_type=mime)

    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["content_type"] in allowed_mimes
    extension = EXTENSION_FOR_MIME[photo["content_type"]]
    assert photo["url"].endswith(f".{extension}")
    stored_path = media_path(client, photo["url"])
    stored = stored_path.read_bytes()
    assert len(stored) == photo["size_bytes"] <= STORED_CAP
    assert sniff_image_format(stored[:64]).mime == photo["content_type"]
    image = decode(stored)
    assert image.size == reference.size == (photo["width"], photo["height"])
    if case == "png-rgba-noise":
        assert image.mode == "RGBA"
    served = client.get(photo["url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == photo["content_type"]
    _assert_photos_within_cap(client, event_id)


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_stored_copy_that_cannot_fit_even_lossy_is_422(make_client, fmt) -> None:
    source = encode(noise(512), fmt, **({"lossless": True} if fmt == "WEBP" else {}))
    client = make_client(max_stored_bytes=STORED_CAP)
    event_id = new_event(client)["event"]["id"]
    extension, mime = ("png", "image/png") if fmt == "PNG" else ("webp", "image/webp")

    response = post_photo(client, event_id, source, filename=f"x.{extension}", content_type=mime)

    error = assert_error(response, 422, "IMAGE_TOO_LARGE")
    assert error["details"] == {"max_stored_bytes": STORED_CAP}
    for sub_dir in (PHOTOS_DIR, THUMBNAILS_DIR):
        assert list((events_root(client) / event_id / sub_dir).iterdir()) == []
    assert client.get(f"/api/events/{event_id}/photos").json()["items"] == []


# =============================================================================================
# 1. 16-bit colour-key (tRNS) transparency
# =============================================================================================

GREY_KEY = 1000  # 0x03E8


def _grey16_quadrants(value: Callable[[int, int], int], key: int) -> bytes:
    rows = b"".join(b"\x00" + b"".join(struct.pack(">H", value(x, y)) for x in range(32)) for y in range(32))
    return raw_png(32, 32, bit_depth=16, color_type=0, scanlines=rows, extra_chunks=((b"tRNS", struct.pack(">H", key)),))


def _rgb16_quadrants(value: Callable[[int, int], tuple[int, int, int]], key: tuple[int, int, int]) -> bytes:
    rows = b"".join(b"\x00" + b"".join(struct.pack(">HHH", *value(x, y)) for x in range(32)) for y in range(32))
    return raw_png(32, 32, bit_depth=16, color_type=2, scanlines=rows, extra_chunks=((b"tRNS", struct.pack(">HHH", *key)),))


def _alpha(image: Image.Image, xy: tuple[int, int]) -> int:
    return image.convert("RGBA").getpixel(xy)[3]


def _upload_and_decode(client: TestClient, event_id: str, source: bytes) -> tuple[dict[str, Any], Image.Image, Image.Image]:
    response = post_photo(client, event_id, source, filename="x.png", content_type="image/png")
    assert response.status_code == 201, response.text
    photo = response.json()
    original = decode(media_path(client, photo["url"]).read_bytes())
    thumbnail = decode(media_path(client, photo["thumbnail_url"]).read_bytes())
    return photo, original, thumbnail


def test_16bit_greyscale_colour_key_is_matched_at_full_precision(client, create_event) -> None:
    # top-left: the key itself; top-right: 0xE8E8 (8-bit value 232 == key & 0xFF);
    # bottom-right: 0x03FF (same high byte as the key); bottom-left: 0x8000.
    def value(x: int, y: int) -> int:
        if x < 16 and y < 16:
            return GREY_KEY
        if x >= 16 and y < 16:
            return 0xE8E8
        if x >= 16:
            return 0x03FF
        return 0x8000

    event_id = create_event()["event"]["id"]
    photo, original, thumbnail = _upload_and_decode(client, event_id, _grey16_quadrants(value, GREY_KEY))

    assert photo["content_type"] == "image/png"
    for image, tolerance in ((original, 0), (thumbnail, 8)):
        assert _alpha(image, (8, 8)) <= tolerance, "keyed pixels must be transparent"
        assert _alpha(image, (24, 8)) >= 255 - tolerance, "0xE8E8 is not the key"
        assert _alpha(image, (24, 24)) >= 255 - tolerance, "0x03FF is not the key"
        assert _alpha(image, (8, 24)) >= 255 - tolerance


def test_16bit_rgb_colour_key_makes_keyed_pixels_transparent(client, create_event) -> None:
    key = (0x1234, 0x5678, 0x9ABC)

    def value(x: int, y: int) -> tuple[int, int, int]:
        return key if x < 16 else (0x8000, 0x4000, 0x2000)

    event_id = create_event()["event"]["id"]
    _, original, thumbnail = _upload_and_decode(client, event_id, _rgb16_quadrants(value, key))

    for image, tolerance in ((original, 0), (thumbnail, 8)):
        assert _alpha(image, (8, 8)) <= tolerance
        assert _alpha(image, (8, 24)) <= tolerance
        assert _alpha(image, (24, 8)) >= 255 - tolerance
        assert _alpha(image, (24, 24)) >= 255 - tolerance


def test_16bit_rgb_colour_key_below_256_is_still_treated_as_16_bit(client, create_event) -> None:
    key = (0x0080, 0x0080, 0x0080)  # near-black in 16 bits; decodes to (0, 0, 0)

    def value(x: int, y: int) -> tuple[int, int, int]:
        return key if x < 16 else (0x8080, 0x8080, 0x8080)  # mid grey; decodes to (128, 128, 128)

    event_id = create_event()["event"]["id"]
    _, original, _ = _upload_and_decode(client, event_id, _rgb16_quadrants(value, key))

    assert _alpha(original, (24, 8)) == 255, "mid-grey pixels are not the key and must stay opaque"
    assert _alpha(original, (8, 8)) == 0, "the keyed near-black pixels must be transparent"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN LIMITATION (accepted): Pillow decodes 16-bit RGB to 8 bits, so 16-bit RGB tRNS keys "
        "are matched on the high bytes. Pixels that share the key's high bytes but differ in the low "
        "bytes become transparent, although the PNG spec compares full 16-bit samples. Exact matching "
        "would need a custom PNG decoder for a very rare input; the greyscale path is exact."
    ),
)
def test_16bit_rgb_colour_key_is_matched_at_full_precision(client, create_event) -> None:
    key = (0x1234, 0x5678, 0x9ABC)

    def value(x: int, y: int) -> tuple[int, int, int]:
        return key if x < 16 else (0x1299, 0x5611, 0x9A00)  # same high bytes, different samples

    event_id = create_event()["event"]["id"]
    _, original, _ = _upload_and_decode(client, event_id, _rgb16_quadrants(value, key))

    assert _alpha(original, (8, 8)) == 0
    assert _alpha(original, (24, 8)) == 255, "a different 16-bit colour must stay opaque"


# =============================================================================================
# 2. ProcessingBudget (unit)
# =============================================================================================


def _budget(max_pixels: int = 100, max_jobs: int = 4, queue_timeout: float = 5.0) -> Any:
    from app.services.admission import ProcessingBudget  # noqa: PLC0415 - module added by the fix

    return ProcessingBudget(max_pixels=max_pixels, max_jobs=max_jobs, queue_timeout=queue_timeout)


async def _until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    with anyio.fail_after(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def test_budget_admits_jobs_that_fit_and_releases_them() -> None:
    async def scenario() -> None:
        budget = _budget(max_pixels=100, max_jobs=4)
        async with budget.reserve(30):
            async with budget.reserve(70):  # exactly fills the budget
                assert (budget.pixels_in_use, budget.active_jobs) == (100, 2)
            assert (budget.pixels_in_use, budget.active_jobs) == (30, 1)
        assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)

    asyncio.run(scenario())


def test_budget_second_job_waits_until_first_releases_pixels() -> None:
    async def scenario() -> None:
        budget = _budget(max_pixels=100, max_jobs=4)
        release = asyncio.Event()
        admitted = asyncio.Event()

        async def first() -> None:
            async with budget.reserve(60):
                await release.wait()

        async def second() -> None:
            async with budget.reserve(60):
                admitted.set()
                assert budget.pixels_in_use == 60

        holder = asyncio.create_task(first())
        await _until(lambda: budget.active_jobs == 1)
        waiter = asyncio.create_task(second())
        await asyncio.sleep(0.1)
        assert not admitted.is_set(), "60 + 60 pixels exceed the budget of 100"
        assert (budget.pixels_in_use, budget.active_jobs) == (60, 1)
        release.set()
        await asyncio.wait_for(admitted.wait(), 2)
        await asyncio.gather(holder, waiter)
        assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)

    asyncio.run(scenario())


def test_budget_caps_concurrent_jobs_even_when_pixels_fit() -> None:
    async def scenario() -> None:
        budget = _budget(max_pixels=1_000, max_jobs=1)
        release = asyncio.Event()
        admitted = asyncio.Event()

        async def first() -> None:
            async with budget.reserve(1):
                await release.wait()

        async def second() -> None:
            async with budget.reserve(1):
                admitted.set()

        holder = asyncio.create_task(first())
        await _until(lambda: budget.active_jobs == 1)
        waiter = asyncio.create_task(second())
        await asyncio.sleep(0.1)
        assert not admitted.is_set()
        release.set()
        await asyncio.wait_for(admitted.wait(), 2)
        await asyncio.gather(holder, waiter)
        assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)

    asyncio.run(scenario())


def test_budget_job_larger_than_whole_budget_runs_alone() -> None:
    async def scenario() -> None:
        budget = _budget(max_pixels=100, max_jobs=4, queue_timeout=5.0)
        # Idle: admitted immediately, weighted as the whole budget.
        async with asyncio.timeout(1):
            async with budget.reserve(10_000):
                assert (budget.pixels_in_use, budget.active_jobs) == (100, 1)
        assert budget.pixels_in_use == 0

        # Busy: the oversized job waits for the small one, then runs.
        release = asyncio.Event()
        big_admitted = asyncio.Event()
        small_admitted_while_big_runs = []

        async def small() -> None:
            async with budget.reserve(1):
                await release.wait()

        async def big() -> None:
            async with budget.reserve(10_000):
                big_admitted.set()
                await asyncio.sleep(0.05)

        holder = asyncio.create_task(small())
        await _until(lambda: budget.active_jobs == 1)
        big_task = asyncio.create_task(big())
        await asyncio.sleep(0.1)
        assert not big_admitted.is_set()
        release.set()
        await asyncio.wait_for(big_admitted.wait(), 2)

        async def late_small() -> None:
            async with budget.reserve(1):
                small_admitted_while_big_runs.append(big_admitted.is_set() and not big_task.done())

        await asyncio.gather(holder, big_task, late_small())
        assert small_admitted_while_big_runs == [False], "nothing may run next to the oversized job"
        assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)

    asyncio.run(scenario())


def test_budget_queue_timeout_raises_service_busy() -> None:
    from app.core.errors import ServiceBusyError  # noqa: PLC0415

    async def scenario() -> None:
        budget = _budget(max_pixels=100, max_jobs=1, queue_timeout=0.2)
        async with budget.reserve(1):
            started = time.monotonic()
            with pytest.raises(ServiceBusyError) as info:
                async with budget.reserve(1):
                    pytest.fail("must not be admitted")
            elapsed = time.monotonic() - started
            assert 0.15 <= elapsed < 2.0, elapsed
            assert (budget.pixels_in_use, budget.active_jobs) == (1, 1)
        assert info.value.status_code == 503
        assert info.value.code == "SERVICE_BUSY"
        assert info.value.headers == {"Retry-After": "5"}
        assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)

    asyncio.run(scenario())


def test_budget_counters_restored_after_exception_in_job() -> None:
    async def scenario() -> None:
        budget = _budget(max_pixels=100, max_jobs=1, queue_timeout=0.5)
        with pytest.raises(RuntimeError, match="decoder exploded"):
            async with budget.reserve(80):
                assert budget.active_jobs == 1
                raise RuntimeError("decoder exploded")
        assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)
        async with asyncio.timeout(0.3):  # immediately admissible again
            async with budget.reserve(100):
                pass

    asyncio.run(scenario())


def test_budget_cancelled_waiter_and_cancelled_holder_leave_counters_intact() -> None:
    async def scenario() -> None:
        budget = _budget(max_pixels=100, max_jobs=4, queue_timeout=5.0)
        release = asyncio.Event()
        third_admitted = asyncio.Event()

        async def holder() -> None:
            async with budget.reserve(100):
                await release.wait()

        async def waiter() -> None:
            async with budget.reserve(50):
                pytest.fail("the cancelled waiter must never be admitted")

        async def third() -> None:
            async with budget.reserve(50):
                third_admitted.set()

        holding = asyncio.create_task(holder())
        await _until(lambda: budget.active_jobs == 1)
        waiting = asyncio.create_task(waiter())
        queued = asyncio.create_task(third())
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert (budget.pixels_in_use, budget.active_jobs) == (100, 1)

        release.set()  # the remaining waiter must still be woken
        await asyncio.wait_for(third_admitted.wait(), 2)
        await asyncio.gather(holding, queued)
        assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)

        # A job cancelled while it holds a reservation releases it.
        running = asyncio.Event()

        async def long_job() -> None:
            async with budget.reserve(70):
                running.set()
                await asyncio.sleep(60)

        job = asyncio.create_task(long_job())
        await asyncio.wait_for(running.wait(), 2)
        assert (budget.pixels_in_use, budget.active_jobs) == (70, 1)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)
        async with asyncio.timeout(0.3):
            async with budget.reserve(100):
                pass

    asyncio.run(scenario())


# =============================================================================================
# 2./3. Admission control through the API
# =============================================================================================


@contextmanager
def held_reservation(client: TestClient, pixels: int) -> Iterator[Any]:
    """Occupy part of the app's processing budget from the test thread."""
    budget = services_of(client).photos._budget
    reservation = budget.reserve(pixels)
    client.portal.call(reservation.__aenter__)
    try:
        yield budget
    finally:
        client.portal.call(reservation.__aexit__, None, None, None)


def test_upload_is_503_service_busy_when_all_processing_slots_are_taken(make_client) -> None:
    client = make_client(image_processing_concurrency=1, image_processing_queue_timeout=0.3)
    event_id = new_event(client)["event"]["id"]

    with held_reservation(client, 1) as budget:
        started = time.monotonic()
        response = post_photo(client, event_id)
        elapsed = time.monotonic() - started
        assert_error(response, 503, "SERVICE_BUSY")
        assert response.headers["retry-after"] == "5"
        assert 0.25 <= elapsed < 5.0, elapsed
        assert (budget.pixels_in_use, budget.active_jobs) == (1, 1), "the rejected upload must not leak a slot"

    assert (budget.pixels_in_use, budget.active_jobs) == (0, 0)
    assert list((events_root(client) / event_id / PHOTOS_DIR).iterdir()) == []
    assert post_photo(client, event_id).status_code == 201


def test_admission_is_weighted_by_declared_pixels(make_client) -> None:
    client = make_client(
        image_processing_pixel_budget=1_000_000,
        image_processing_concurrency=4,
        image_processing_queue_timeout=0.3,
    )
    event_id = new_event(client)["event"]["id"]

    with held_reservation(client, 1_000_000 - 1_000):
        too_many_pixels = post_photo(client, event_id, image_bytes("JPEG", size=(64, 48)))  # 3,072 px
        assert_error(too_many_pixels, 503, "SERVICE_BUSY")
        fits = post_photo(client, event_id, image_bytes("JPEG", size=(20, 20)))  # 400 px
        assert fits.status_code == 201, fits.text


def test_queued_upload_is_admitted_when_capacity_frees_up(make_client) -> None:
    client = make_client(image_processing_concurrency=1, image_processing_queue_timeout=10)
    event_id = new_event(client)["event"]["id"]
    result: dict[str, Any] = {}

    with held_reservation(client, 1):
        worker = threading.Thread(target=lambda: result.update(response=post_photo(client, event_id)), daemon=True)
        worker.start()
        time.sleep(0.4)
        assert worker.is_alive() and "response" not in result, "the upload must queue while the slot is taken"

    worker.join(10)
    assert not worker.is_alive()
    assert result["response"].status_code == 201, result["response"].text


# =============================================================================================
# 3. Upload pipeline: persist survives cancellation, readable size limit
# =============================================================================================


def test_cancelled_upload_request_still_persists_files_and_row_and_announces_it(client, create_event) -> None:
    services = services_of(client)
    event_id = create_event()["event"]["id"]
    data = image_bytes("JPEG", size=(40, 30))
    original_insert = services.db.insert_photo
    state: dict[str, Any] = {}

    async def scenario() -> None:
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def gated_insert(photo: Any, **kwargs: Any) -> Any:
            state["photo_id"] = photo.id
            entered.set()
            await gate.wait()
            return await original_insert(photo, **kwargs)

        services.db.insert_photo = gated_insert
        try:
            event = await services.events.get(event_id)
            upload = UploadFile(io.BytesIO(data), size=len(data), filename="x.jpg")
            request = asyncio.create_task(
                services.photos.create_from_upload(event, upload, uploader_name=None, caption=None)
            )
            await asyncio.wait_for(entered.wait(), 5)
            request.cancel()  # client went away while the photo was being persisted
            try:
                await request
            except asyncio.CancelledError:
                state["cancelled"] = True
            gate.set()
            with anyio.move_on_after(5):
                while await services.db.count_photos(event_id) == 0:
                    await asyncio.sleep(0.01)
        finally:
            del services.db.insert_photo

    with ws_connect(client, event_id) as ws:
        assert ws_recv(ws)["type"] == "hello"
        client.portal.call(scenario)
        announced = ws_recv(ws)

    assert state.get("cancelled") is True
    items = client.get(f"/api/events/{event_id}/photos").json()["items"]
    assert [item["id"] for item in items] == [state["photo_id"]], "the committed photo must exist"
    photo = items[0]
    for url in (photo["url"], photo["thumbnail_url"]):
        assert media_path(client, url).is_file(), "a committed row must never lack its files"
        assert client.get(url).status_code == 200
    assert announced["type"] == "photo.created"
    assert announced["data"]["id"] == state["photo_id"]


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [(10 * 1024 * 1024, "10 MB"), (5 * 1024 * 1024 + 1, "5 MB"), (50_000, "48.8 KB"), (1536, "1.5 KB"), (1024, "1 KB"), (512, "512 bytes")],
)
def test_format_size(num_bytes: int, expected: str) -> None:
    from app.services.photos import format_size  # noqa: PLC0415

    assert format_size(num_bytes) == expected


def test_file_too_large_message_uses_a_human_readable_limit(make_client) -> None:
    client = make_client(max_upload_bytes=50_000)
    event_id = new_event(client)["event"]["id"]
    response = post_photo(client, event_id, b"\xff\xd8\xff" + os.urandom(60_000))
    error = assert_error(response, 413, "FILE_TOO_LARGE")
    assert error["message"] == "Photos must be 48.8 KB or smaller."
    assert error["details"] == {"max_bytes": 50_000}


# =============================================================================================
# 3./5. Notify walls before touching files
# =============================================================================================


def test_photo_delete_publishes_before_removing_files(client, create_event, upload_photo, monkeypatch) -> None:
    services = services_of(client)
    created = create_event()
    event_id = created["event"]["id"]
    photo = upload_photo(event_id).json()
    order: list[tuple[str, str]] = []
    publish, delete_photo = services.realtime.publish, services.storage.delete_photo

    def recording_publish(target: str, message: Any) -> Any:
        order.append(("publish", message["type"]))
        return publish(target, message)

    def recording_delete(*args: Any) -> Any:
        order.append(("storage", "delete_photo"))
        return delete_photo(*args)

    monkeypatch.setattr(services.realtime, "publish", recording_publish)
    monkeypatch.setattr(services.storage, "delete_photo", recording_delete)

    response = client.delete(f"/api/events/{event_id}/photos/{photo['id']}", headers=auth(created["host_token"]))

    assert response.status_code == 204
    assert order == [("publish", "photo.deleted"), ("storage", "delete_photo")]


def test_event_delete_publishes_and_closes_walls_before_removing_files(client, create_event, upload_photo, monkeypatch) -> None:
    services = services_of(client)
    created = create_event()
    event_id = created["event"]["id"]
    upload_photo(event_id)
    order: list[str] = []
    publish, close_event, delete_event = (
        services.realtime.publish,
        services.realtime.close_event,
        services.storage.delete_event,
    )

    def recording_publish(target: str, message: Any) -> Any:
        order.append(f"publish:{message['type']}")
        return publish(target, message)

    def recording_close(*args: Any) -> Any:
        order.append("close_event")
        return close_event(*args)

    def recording_delete(*args: Any) -> Any:
        order.append("storage:delete_event")
        return delete_event(*args)

    monkeypatch.setattr(services.realtime, "publish", recording_publish)
    monkeypatch.setattr(services.realtime, "close_event", recording_close)
    monkeypatch.setattr(services.storage, "delete_event", recording_delete)

    response = client.delete(f"/api/events/{event_id}", headers=auth(created["host_token"]))

    assert response.status_code == 204
    assert order == ["publish:event.deleted", "close_event", "storage:delete_event"]


# =============================================================================================
# 4. Storage: no link traversal
# =============================================================================================


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    store = LocalStorage(tmp_path / "unit-storage")
    store.ensure_layout()
    return store


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_resolve_refuses_link_that_stays_inside_the_events_root(storage, kind) -> None:
    storage.create_event_dirs(OTHER_EVENT_ID)
    make_dir_link(storage.events_root / EVENT_ID, storage.events_root / OTHER_EVENT_ID, kind)
    name = photo_name()

    for segments in [(EVENT_ID,), (EVENT_ID, PHOTOS_DIR), (EVENT_ID, PHOTOS_DIR, name)]:
        with pytest.raises(UnsafePathError):
            storage.resolve(*segments)
    with pytest.raises(UnsafePathError):
        storage.save_photo(EVENT_ID, name, b"photo", photo_name("webp"), b"thumb")
    assert list((storage.events_root / OTHER_EVENT_ID / PHOTOS_DIR).iterdir()) == []
    # The link target itself is an ordinary path.
    assert storage.resolve(OTHER_EVENT_ID, PHOTOS_DIR, name) == storage.events_root / OTHER_EVENT_ID / PHOTOS_DIR / name


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_resolve_refuses_link_planted_at_a_media_sub_directory(storage, kind) -> None:
    storage.create_event_dirs(OTHER_EVENT_ID)
    storage.create_event_dirs(EVENT_ID)
    (storage.events_root / EVENT_ID / PHOTOS_DIR).rmdir()
    make_dir_link(storage.events_root / EVENT_ID / PHOTOS_DIR, storage.events_root / OTHER_EVENT_ID / PHOTOS_DIR, kind)

    assert storage.resolve(EVENT_ID) == storage.events_root / EVENT_ID
    assert storage.resolve(EVENT_ID, THUMBNAILS_DIR) == storage.events_root / EVENT_ID / THUMBNAILS_DIR
    with pytest.raises(UnsafePathError):
        storage.resolve(EVENT_ID, PHOTOS_DIR, photo_name())
    with pytest.raises(UnsafePathError):
        storage.photo_path(EVENT_ID, photo_name())


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_api_delete_of_event_whose_dir_was_replaced_by_a_link_removes_only_the_link(client, create_event, upload_photo, kind) -> None:
    victim = create_event("Victim")
    victim_id = victim["event"]["id"]
    victim_photo = upload_photo(victim_id).json()
    target = create_event("Target")
    target_id = target["event"]["id"]
    target_dir = events_root(client) / target_id
    shutil.rmtree(target_dir)  # only the empty photos/thumbs dirs
    make_dir_link(target_dir, events_root(client) / victim_id, kind)

    response = client.delete(f"/api/events/{target_id}", headers=auth(target["host_token"]))

    assert response.status_code == 204
    assert not is_link(target_dir) and not target_dir.exists()
    for url in (victim_photo["url"], victim_photo["thumbnail_url"]):
        assert media_path(client, url).is_file(), "the other event's files must survive"
        assert client.get(url).status_code == 200
    assert services_of(client).janitor.pending == 0


# =============================================================================================
# 4./10. Janitor tombstones: locked files are hidden at once and removed later
# =============================================================================================


def _photo_files(client: TestClient, photo: dict[str, Any]) -> list[Path]:
    return [media_path(client, photo["url"]), media_path(client, photo["thumbnail_url"])]


def _filenames(photo: dict[str, Any]) -> list[str]:
    return [photo["url"].rsplit("/", 1)[1], photo["thumbnail_url"].rsplit("/", 1)[1]]


def test_locked_photo_delete_is_tombstoned_then_retried(client, create_event, upload_photo, monkeypatch) -> None:
    services = services_of(client)
    janitor = services.janitor
    monkeypatch.setattr(janitor, "_retry_interval", 0.05)
    created = create_event()
    event_id = created["event"]["id"]
    doomed = upload_photo(event_id).json()
    kept = upload_photo(event_id).json()
    etag = client.get(doomed["url"]).headers["etag"]
    attempts: list[tuple[str, ...]] = []

    def locked_delete_photo(*args: str) -> bool:
        attempts.append(args)
        return False  # what LocalStorage reports while Windows holds the files open

    services.storage.delete_photo = locked_delete_photo
    try:
        with ws_connect(client, event_id) as ws:
            assert ws_recv(ws)["type"] == "hello"
            response = client.delete(f"/api/events/{event_id}/photos/{doomed['id']}", headers=auth(created["host_token"]))
            assert response.status_code == 204
            assert ws_recv(ws) == {"type": "photo.deleted", "event_id": event_id, "data": {"id": doomed["id"]}}

        assert all(path.is_file() for path in _photo_files(client, doomed)), "simulated lock keeps the files"
        for url in (doomed["url"], doomed["thumbnail_url"]):
            assert_error(client.get(url), 404, "NOT_FOUND")
            assert client.head(url).status_code == 404
        conditional = client.get(doomed["url"], headers={"If-None-Match": etag})
        assert_error(conditional, 404, "NOT_FOUND")  # never a 304 for a deleted photo
        assert janitor.pending == 1
        assert all(janitor.is_blocked(event_id, name) for name in _filenames(doomed))
        assert client.get(kept["url"]).status_code == 200
        assert wait_until(lambda: len(attempts) >= 3), "the janitor keeps retrying"
        assert janitor.pending == 1
    finally:
        del services.storage.delete_photo  # the lock goes away

    assert wait_until(lambda: janitor.pending == 0)
    assert not any(path.exists() for path in _photo_files(client, doomed))
    assert not any(janitor.is_blocked(event_id, name) for name in _filenames(doomed))
    for url in (doomed["url"], doomed["thumbnail_url"]):
        assert_error(client.get(url), 404, "NOT_FOUND")
    assert client.get(kept["url"]).status_code == 200
    assert all(path.is_file() for path in _photo_files(client, kept))


@windows_only
def test_really_locked_photo_file_is_tombstoned_then_removed(client, create_event, upload_photo, monkeypatch, fast_unlink_retries) -> None:
    janitor = services_of(client).janitor
    monkeypatch.setattr(janitor, "_retry_interval", 0.05)
    created = create_event()
    event_id = created["event"]["id"]
    photo = upload_photo(event_id).json()
    original, thumbnail = _photo_files(client, photo)

    with held_open(original):  # e.g. an in-flight download on Windows
        response = client.delete(f"/api/events/{event_id}/photos/{photo['id']}", headers=auth(created["host_token"]))
        assert response.status_code == 204
        assert original.is_file(), "Windows refuses to delete an open file"
        assert not thumbnail.exists()
        assert_error(client.get(photo["url"]), 404, "NOT_FOUND")
        assert_error(client.get(photo["thumbnail_url"]), 404, "NOT_FOUND")
        assert janitor.pending == 1
        time.sleep(0.2)
        assert original.is_file() and janitor.pending == 1

    assert wait_until(lambda: janitor.pending == 0)
    assert not original.exists()
    assert not any(janitor.is_blocked(event_id, name) for name in _filenames(photo))


def test_locked_event_delete_is_tombstoned_then_retried(client, create_event, upload_photo, monkeypatch) -> None:
    services = services_of(client)
    janitor = services.janitor
    monkeypatch.setattr(janitor, "_retry_interval", 0.05)
    created = create_event()
    event_id = created["event"]["id"]
    photos = [upload_photo(event_id).json() for _ in range(2)]
    other = create_event("Other")
    other_photo = upload_photo(other["event"]["id"]).json()
    event_dir = events_root(client) / event_id

    services.storage.delete_event = lambda *args: False
    try:
        with ws_connect(client, event_id) as ws:
            assert ws_recv(ws)["type"] == "hello"
            response = client.delete(f"/api/events/{event_id}", headers=auth(created["host_token"]))
            assert response.status_code == 204
            assert ws_recv(ws) == {"type": "event.deleted", "event_id": event_id}
            assert ws_expect_close(ws) == 4410

        assert event_dir.is_dir()
        for photo in photos:
            assert all(path.is_file() for path in _photo_files(client, photo))
            for url in (photo["url"], photo["thumbnail_url"]):
                assert_error(client.get(url), 404, "NOT_FOUND")
            assert all(janitor.is_blocked(event_id, name) for name in _filenames(photo))
        assert janitor.pending == 1
        time.sleep(0.2)  # several retries while still "locked"
        assert event_dir.is_dir() and janitor.pending == 1
        assert client.get(other_photo["url"]).status_code == 200
    finally:
        del services.storage.delete_event

    assert wait_until(lambda: janitor.pending == 0)
    assert not event_dir.exists()
    for photo in photos:
        assert not any(janitor.is_blocked(event_id, name) for name in _filenames(photo))
    assert client.get(other_photo["url"]).status_code == 200


@windows_only
def test_really_locked_file_in_deleted_event_hides_all_its_media_until_removed(
    client, create_event, upload_photo, monkeypatch, fast_unlink_retries
) -> None:
    janitor = services_of(client).janitor
    monkeypatch.setattr(janitor, "_retry_interval", 0.05)
    created = create_event()
    event_id = created["event"]["id"]
    locked_photo, other_photo = (upload_photo(event_id).json() for _ in range(2))
    event_dir = events_root(client) / event_id
    locked_file = media_path(client, locked_photo["url"])

    with held_open(locked_file):
        response = client.delete(f"/api/events/{event_id}", headers=auth(created["host_token"]))
        assert response.status_code == 204
        assert locked_file.is_file() and event_dir.is_dir()
        for photo in (locked_photo, other_photo):
            for url in (photo["url"], photo["thumbnail_url"]):
                assert_error(client.get(url), 404, "NOT_FOUND")
        assert janitor.pending == 1

    assert wait_until(lambda: janitor.pending == 0)
    assert not event_dir.exists()
    assert not janitor.is_blocked(event_id, _filenames(locked_photo)[0])


# =============================================================================================
# 4. Startup orphan sweep
# =============================================================================================


def _journal_interrupted_upload(client: TestClient, event_id: str) -> list[Path]:
    """Leave the on-disk state of a process that died between writing a photo's
    files and committing its row: a journaled intent plus both files."""
    from app.models import PhotoRecord, utcnow  # noqa: PLC0415

    services = services_of(client)
    photo_id = secrets.token_hex(16)
    photo = PhotoRecord(
        id=photo_id, event_id=event_id, filename=f"{photo_id}.jpg", thumbnail_filename=f"{photo_id}.webp",
        content_type="image/jpeg", width=64, height=48, size_bytes=1, uploader_name=None, caption=None,
        created_at=utcnow(),
    )
    client.portal.call(services.db.begin_upload, photo)
    services.storage.save_photo(event_id, photo.filename, image_bytes(), photo.thumbnail_filename, image_bytes("WEBP"))
    return [
        services.storage.photo_path(event_id, photo.filename),
        services.storage.thumbnail_path(event_id, photo.thumbnail_filename),
    ]


def _db_rows(client: TestClient, table: str) -> list[tuple[Any, ...]]:
    database = services_of(client).settings.database_path
    with contextlib.closing(sqlite3.connect(database)) as conn:
        return conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - fixed table names


def _age(paths: list[Path], seconds: float = 2 * 24 * 3600) -> None:
    old = time.time() - seconds
    for path in paths:
        os.utime(path, (old, old))


def test_housekeeping_completes_durable_deletions_and_never_guesses_from_disk(make_client, tmp_path, monkeypatch) -> None:
    storage_dir = tmp_path / "sweep-storage"
    first = make_client(storage_dir=storage_dir)
    kept_event = new_event(first, "Kept")
    kept_id = kept_event["event"]["id"]
    empty_id = new_event(first, "Empty")["event"]["id"]
    kept = post_photo(first, kept_id).json()
    deferred = post_photo(first, kept_id).json()

    # A deletion that could not remove its files before shutdown (e.g. locked on Windows).
    monkeypatch.setattr(services_of(first).storage, "delete_photo", lambda *args: False)
    response = first.delete(f"/api/events/{kept_id}/photos/{deferred['id']}", headers=auth(kept_event["host_token"]))
    assert response.status_code == 204
    deferred_files = _photo_files(first, deferred)
    assert all(path.is_file() for path in deferred_files)
    assert len(_db_rows(first, "pending_deletions")) == 1

    root = events_root(first)
    unjournaled = root / kept_id / PHOTOS_DIR / photo_name("jpg")  # no DB row, no intent: never guessed
    unjournaled.write_bytes(image_bytes())
    unknown_dir = root / UNKNOWN_EVENT_ID
    (unknown_dir / PHOTOS_DIR).mkdir(parents=True)
    unknown_file = unknown_dir / PHOTOS_DIR / photo_name()
    unknown_file.write_bytes(image_bytes())
    _age([unjournaled, unknown_file, *deferred_files])
    first.__exit__(None, None, None)  # shut the first app down (the fixture's second exit is a no-op)

    second = make_client(storage_dir=storage_dir)

    assert not any(path.exists() for path in deferred_files), "durable pending deletion completed on restart"
    assert _db_rows(second, "pending_deletions") == []
    assert unjournaled.exists(), "files are never deleted just because the database does not know them"
    assert unknown_file.exists(), "directories without a record are kept (they may belong to a restored DB)"
    assert_error(second.get(f"/media/events/{UNKNOWN_EVENT_ID}/{PHOTOS_DIR}/{unknown_file.name}"), 404, "NOT_FOUND")
    for url in (kept["url"], kept["thumbnail_url"]):
        assert second.get(url).status_code == 200
    for sub_dir in (PHOTOS_DIR, THUMBNAILS_DIR):
        assert (root / empty_id / sub_dir).is_dir(), "an event without photos keeps its directories"
    assert post_photo(second, empty_id).status_code == 201
    assert services_of(second).janitor.pending == 0
    listing = second.get(f"/api/events/{kept_id}/photos").json()["items"]
    assert [item["id"] for item in listing] == [kept["id"]]


def test_upload_intents_are_journaled_and_cleared_on_commit(client, create_event, upload_photo) -> None:
    event_id = create_event()["event"]["id"]
    assert upload_photo(event_id).status_code == 201
    assert _db_rows(client, "pending_uploads") == [], "the intent is removed in the insert transaction"


def test_files_of_an_interrupted_upload_are_removed_at_startup(make_client, tmp_path) -> None:
    storage_dir = tmp_path / "interrupted"
    first = make_client(storage_dir=storage_dir)
    event_id = new_event(first)["event"]["id"]
    kept = post_photo(first, event_id).json()
    written = _journal_interrupted_upload(first, event_id)
    assert all(path.is_file() for path in written)
    first.__exit__(None, None, None)

    second = make_client(storage_dir=storage_dir)

    assert not any(path.exists() for path in written)
    assert _db_rows(second, "pending_uploads") == []
    assert second.get(kept["url"]).status_code == 200


def test_restoring_an_older_database_never_deletes_newer_photos(make_client, tmp_path) -> None:
    storage_dir = tmp_path / "restore"
    first = make_client(storage_dir=storage_dir)
    event_id = new_event(first)["event"]["id"]
    post_photo(first, event_id)
    database = services_of(first).settings.database_path
    first.__exit__(None, None, None)
    backup = tmp_path / "backup.sqlite3"
    shutil.copy2(database, backup)

    second = make_client(storage_dir=storage_dir)
    newer = post_photo(second, event_id).json()
    newer_files = _photo_files(second, newer)
    second.__exit__(None, None, None)
    for suffix in ("-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    shutil.copy2(backup, database)  # the operator restores last night's backup
    _age(newer_files)

    make_client(storage_dir=storage_dir)

    assert all(path.is_file() for path in newer_files), "photos committed after the backup must survive"


def test_case_changed_file_names_are_never_deleted(make_client, tmp_path) -> None:
    storage_dir = tmp_path / "case"
    first = make_client(storage_dir=storage_dir)
    event_id = new_event(first)["event"]["id"]
    photo = post_photo(first, event_id).json()
    original = media_path(first, photo["url"])
    first.__exit__(None, None, None)
    renamed = original.with_name(original.name.upper())  # a sync/restore tool changed the case
    original.rename(renamed)
    _age([renamed])

    make_client(storage_dir=storage_dir)

    assert renamed.exists()


@windows_only
def test_locked_files_of_an_interrupted_upload_do_not_block_startup(make_client, tmp_path, monkeypatch, fast_unlink_retries) -> None:
    storage_dir = tmp_path / "locked-stale"
    first = make_client(storage_dir=storage_dir)
    event_id = new_event(first)["event"]["id"]
    stale_file = _journal_interrupted_upload(first, event_id)[0]
    first.__exit__(None, None, None)

    with held_open(stale_file):
        second = make_client(storage_dir=storage_dir)
        assert second.get("/api/health").status_code == 200
        assert stale_file.is_file()
        assert len(_db_rows(second, "pending_uploads")) == 1, "kept for the next startup"
    second.__exit__(None, None, None)

    third = make_client(storage_dir=storage_dir)
    assert not stale_file.exists()
    assert _db_rows(third, "pending_uploads") == []


def test_missing_database_never_deletes_or_serves_existing_media(make_client, tmp_path) -> None:
    storage_dir = tmp_path / "lost-db"
    first = make_client(storage_dir=storage_dir)
    event_id = new_event(first)["event"]["id"]
    photo = post_photo(first, event_id).json()
    files = _photo_files(first, photo)
    database = services_of(first).settings.database_path
    first.__exit__(None, None, None)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    _age(files)

    second = make_client(storage_dir=storage_dir)

    assert all(path.is_file() for path in files), "photos survive the loss of the database"
    # Nobody can moderate them any more (the event and host token are gone), so they are not served.
    assert_error(second.get(photo["url"]), 404, "NOT_FOUND")
    assert second.get("/api/health").status_code == 200


def test_housekeeping_errors_never_prevent_startup(make_client, tmp_path, monkeypatch) -> None:
    from app.services.storage import LocalStorage  # noqa: PLC0415

    def broken_scan(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(LocalStorage, "scan_event_dirs", broken_scan)
    storage_dir = tmp_path / "broken-sweep"
    make_client(storage_dir=storage_dir).__exit__(None, None, None)

    client = make_client(storage_dir=storage_dir)

    assert client.get("/api/health").status_code == 200


@windows_only
def test_a_junction_loop_in_place_of_an_event_never_blocks_startup(make_client, tmp_path, monkeypatch) -> None:
    storage_dir = tmp_path / "loop"
    first = make_client(storage_dir=storage_dir)
    created = new_event(first)
    event_id = created["event"]["id"]
    photo = post_photo(first, event_id).json()
    monkeypatch.setattr(services_of(first).storage, "delete_photo", lambda *args: False)
    first.delete(f"/api/events/{event_id}/photos/{photo['id']}", headers=auth(created["host_token"]))
    first.__exit__(None, None, None)
    event_dir = storage_dir / "events" / event_id
    shutil.rmtree(event_dir)
    other = tmp_path / "loop-partner"
    other.mkdir()
    make_dir_link(event_dir, other, "junction")  # event dir -> partner
    other.rmdir()
    make_dir_link(other, event_dir, "junction")  # partner -> event dir: a loop

    client = make_client(storage_dir=storage_dir)

    assert client.get("/api/health").status_code == 200


def test_second_process_cannot_use_the_same_storage(make_client, tmp_path) -> None:
    storage_dir = tmp_path / "locked-storage"
    make_client(storage_dir=storage_dir)  # this process now owns the storage
    code = (
        "import sys\n"
        "from app.services.instance_lock import StorageLock, StorageInUseError\n"
        "try:\n"
        "    StorageLock(sys.argv[1]).acquire()\n"
        "    print('ACQUIRED')\n"
        "except StorageInUseError:\n"
        "    print('REFUSED')\n"
    )
    backend = pathlib.Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", code, str(storage_dir)], capture_output=True, text=True, cwd=backend, timeout=60
    )
    assert result.stdout.strip() == "REFUSED", result.stderr


def test_startup_scan_does_not_follow_a_symlinked_media_dir(storage, tmp_path) -> None:
    outside = tmp_path / "outside-symlink"
    outside.mkdir()
    precious = [outside / "precious.txt", outside / photo_name()]
    for path in precious:
        path.write_bytes(b"keep me")
    storage.create_event_dirs(EVENT_ID)
    (storage.events_root / EVENT_ID / PHOTOS_DIR).rmdir()
    make_dir_link(storage.events_root / EVENT_ID / PHOTOS_DIR, outside, "symlink")

    report = storage.scan_event_dirs({EVENT_ID})

    assert report.unknown_events == []
    assert all(path.read_bytes() == b"keep me" for path in precious)


def test_startup_sweep_does_not_follow_a_junction_planted_at_a_media_dir(make_client, tmp_path) -> None:
    storage_dir = tmp_path / "junction-sweep"
    first = make_client(storage_dir=storage_dir)
    event_id = new_event(first)["event"]["id"]
    first.__exit__(None, None, None)
    outside = tmp_path / "outside-junction"
    outside.mkdir()
    precious = [outside / "precious.txt", outside / photo_name()]
    for path in precious:
        path.write_bytes(b"keep me")
    photos_dir = storage_dir / "events" / event_id / PHOTOS_DIR
    photos_dir.rmdir()
    make_dir_link(photos_dir, outside, "junction")

    make_client(storage_dir=storage_dir)  # startup sweep

    assert all(path.exists() for path in precious), "files outside the storage root were deleted"


def test_startup_sweep_does_not_follow_a_junctioned_event_dir_into_another_event(make_client, tmp_path) -> None:
    storage_dir = tmp_path / "junction-event-sweep"
    first = make_client(storage_dir=storage_dir)
    victim_id = new_event(first, "Victim")["event"]["id"]
    victim_photo = post_photo(first, victim_id).json()
    decoy_id = new_event(first, "Decoy")["event"]["id"]
    first.__exit__(None, None, None)
    root = storage_dir / "events"
    shutil.rmtree(root / decoy_id)
    make_dir_link(root / decoy_id, root / victim_id, "junction")

    second = make_client(storage_dir=storage_dir)  # startup sweep

    for url in (victim_photo["url"], victim_photo["thumbnail_url"]):
        assert media_path(second, url).is_file(), "the victim event's photo was deleted through the junction"


# =============================================================================================
# 10. Media mount
# =============================================================================================


def _raw_get(app: Any, path: str) -> tuple[int, dict[str, str], bytes]:
    """Drive the ASGI app with a hand-built scope (httpx would normalise the path)."""

    async def run() -> tuple[int, dict[str, str], bytes]:
        done = anyio.Event()
        sent = False
        start: dict[str, Any] = {}
        chunks: list[bytes] = []

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await done.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    done.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "state": {},
        }
        with anyio.fail_after(10):
            await app(scope, receive, send)
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in start["headers"]}
        return start["status"], headers, b"".join(chunks)

    return asyncio.run(run())


def test_media_url_with_trailing_slash_is_404(client, create_event, upload_photo) -> None:
    photo = upload_photo(create_event()["event"]["id"]).json()
    for url in (photo["url"], photo["thumbnail_url"]):
        status, _, body = _raw_get(client.app, url)
        assert status == 200 and body == media_path(client, url).read_bytes()  # control
        status, headers, body = _raw_get(client.app, url + "/")
        assert status == 404, url
        assert headers["content-type"].startswith("application/json")
        assert json.loads(body)["error"]["code"] == "NOT_FOUND"


def test_media_responses_are_privately_cacheable_only(client, create_event, upload_photo) -> None:
    photo = upload_photo(create_event()["event"]["id"]).json()
    for url in (photo["url"], photo["thumbnail_url"]):
        served = client.get(url)
        assert served.status_code == 200
        assert served.headers["cache-control"] == MEDIA_CACHE_CONTROL
        assert "public" not in served.headers["cache-control"]
        head = client.head(url)
        assert head.headers["cache-control"] == MEDIA_CACHE_CONTROL
        revalidated = client.get(url, headers={"If-None-Match": served.headers["etag"]})
        assert revalidated.status_code == 304
        assert revalidated.headers["cache-control"] == MEDIA_CACHE_CONTROL
