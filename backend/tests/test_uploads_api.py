"""API tests for POST /api/events/{event_id}/photos (the guest upload endpoint).

Covers content validation (magic bytes win over names/Content-Type), hostile
filenames, metadata stripping end to end, decompression bombs, size limits at
every layer (file, multipart body, streamed body, JSON body), multipart form
hardening, event state checks and the structured error envelope.
"""

from __future__ import annotations

import functools
import io
import re
import struct
import zipfile
import zlib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from PIL import ExifTags, Image

from app.core.config import MULTIPART_OVERHEAD_BYTES
from app.services.storage import LocalStorage
from tests.conftest import auth, image_bytes

HEX32 = r"[0-9a-f]{32}"
MEDIA_URL_RE = re.compile(rf"/media/events/(?P<event>[a-z0-9]{{12}})/photos/(?P<id>{HEX32})\.(?P<ext>jpg|png|webp)")
THUMB_URL_RE = re.compile(rf"/media/events/(?P<event>[a-z0-9]{{12}})/thumbs/(?P<id>{HEX32})\.webp")
STORED_FILE_RE = re.compile(rf"events/[a-z0-9]{{12}}/(?:photos/{HEX32}\.(?:jpg|png|webp)|thumbs/{HEX32}\.webp)")
DB_FILE_RE = re.compile(r"db/gambarkita\.(?:sqlite3(?:-wal|-shm|-journal)?|lock)")
ERROR_KEYS = {"code", "message", "details", "request_id"}


@pytest.fixture(autouse=True)
def _restore_pillow_bomb_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app() mutates PIL.Image.MAX_IMAGE_PIXELS process-wide; undo it after each test."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)


# --- Helpers ------------------------------------------------------------------------------


def encode(image: Image.Image, fmt: str, **save_kwargs: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    """Assert the structured error envelope and return its ``error`` object."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == ERROR_KEYS
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    return error


def multipart(parts: Iterable[tuple[bytes, bytes | None, bytes | None, bytes]], boundary: str = "gkBoundary7MA4YWxk") -> tuple[bytes, str]:
    """Hand-built multipart body so hostile filenames reach the server byte-for-byte.

    Each part is ``(name, filename or None, content_type or None, data)``.
    """
    out = b""
    for name, filename, content_type, data in parts:
        out += b"--" + boundary.encode() + b"\r\n"
        disposition = b'Content-Disposition: form-data; name="' + name + b'"'
        if filename is not None:
            disposition += b'; filename="' + filename + b'"'
        out += disposition + b"\r\n"
        if content_type:
            out += b"Content-Type: " + content_type + b"\r\n"
        out += b"\r\n" + data + b"\r\n"
    out += b"--" + boundary.encode() + b"--\r\n"
    return out, f"multipart/form-data; boundary={boundary}"


def post_raw(client: TestClient, event_id: str, parts: Iterable[tuple[bytes, bytes | None, bytes | None, bytes]]) -> Any:
    body, content_type = multipart(parts)
    return client.post(f"/api/events/{event_id}/photos", content=body, headers={"content-type": content_type})


def storage_dir(client: TestClient) -> Path:
    return client.app.state.settings.storage_dir


def stored_files(client: TestClient) -> list[str]:
    root = storage_dir(client)
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if not p.is_dir())


def media_files(client: TestClient) -> list[str]:
    return [path for path in stored_files(client) if path.startswith("events/")]


def assert_storage_is_safe(client: TestClient, tmp_path: Path) -> None:
    """Everything on disk is either the database or a server-named photo/thumbnail."""
    root = storage_dir(client)
    for path in tmp_path.rglob("*"):
        if path.is_dir():
            continue
        assert path.is_relative_to(root), f"file written outside storage: {path}"
        rel = path.relative_to(root).as_posix()
        assert STORED_FILE_RE.fullmatch(rel) or DB_FILE_RE.fullmatch(rel), f"unexpected stored file: {rel!r}"
        assert not path.is_symlink()
    # Every photo has exactly one thumbnail with the same id, and vice versa.
    media = media_files(client)
    photo_ids = {Path(p).stem for p in media if "/photos/" in p}
    thumb_ids = {Path(p).stem for p in media if "/thumbs/" in p}
    assert photo_ids == thumb_ids


def list_photo_ids(client: TestClient, event_id: str) -> list[str]:
    response = client.get(f"/api/events/{event_id}/photos")
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


@functools.lru_cache(maxsize=1)
def png_decompression_bomb() -> bytes:
    """~50 KB on the wire; declares 20000x20000 1-bit pixels (400 MP, ~50 MB of real scanlines)."""
    side = 20_000
    scanlines = bytes((1 + side // 8) * side)
    ihdr = struct.pack(">IIBBBBB", side, side, 1, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(scanlines, 9))
        + png_chunk(b"IEND", b"")
    )


def zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("shell.php", "<?php system($_GET['c']); ?>")
    return buffer.getvalue()


@pytest.fixture
def event(create_event: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    return create_event("Upload Tests")


@pytest.fixture
def event_id(event: dict[str, Any]) -> str:
    return event["event"]["id"]


# --- Happy path: allowed formats ------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "mime", "ext", "magic"),
    [
        ("JPEG", "image/jpeg", "jpg", b"\xff\xd8\xff"),
        ("PNG", "image/png", "png", b"\x89PNG\r\n\x1a\n"),
        ("WEBP", "image/webp", "webp", b"RIFF"),
    ],
)
def test_allowed_formats_are_stored_and_served(client, upload_photo, event_id, tmp_path, fmt, mime, ext, magic):
    response = upload_photo(event_id, image_bytes(fmt), filename=f"holiday.{ext}", content_type=mime)
    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["content_type"] == mime
    assert (photo["width"], photo["height"]) == (64, 48)
    assert photo["event_id"] == event_id
    match = MEDIA_URL_RE.fullmatch(photo["url"])
    assert match and match["ext"] == ext and match["id"] == photo["id"] and match["event"] == event_id
    assert THUMB_URL_RE.fullmatch(photo["thumbnail_url"])

    served = client.get(photo["url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == mime
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.content.startswith(magic)
    assert photo["size_bytes"] == len(served.content)

    assert list_photo_ids(client, event_id) == [photo["id"]]
    assert_storage_is_safe(client, tmp_path)


@pytest.mark.parametrize(
    ("fmt", "claimed_name", "claimed_type", "expected_ext", "expected_mime"),
    [
        ("PNG", "x.jpg", "image/jpeg", "png", "image/png"),
        ("JPEG", "x.png", "image/png", "jpg", "image/jpeg"),
        ("WEBP", "x.gif", "image/gif", "webp", "image/webp"),
        ("JPEG", "shell.php", "application/x-httpd-php", "jpg", "image/jpeg"),
        ("PNG", "no-extension", "application/octet-stream", "png", "image/png"),
        ("WEBP", "page.html", "text/html", "webp", "image/webp"),
    ],
)
def test_stored_extension_and_type_come_from_detected_format(
    client, upload_photo, event_id, fmt, claimed_name, claimed_type, expected_ext, expected_mime
):
    response = upload_photo(event_id, image_bytes(fmt), filename=claimed_name, content_type=claimed_type)
    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["content_type"] == expected_mime
    assert MEDIA_URL_RE.fullmatch(photo["url"])["ext"] == expected_ext
    served = client.get(photo["url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == expected_mime
    assert [p for p in media_files(client) if "/photos/" in p] == [
        f"events/{event_id}/photos/{photo['id']}.{expected_ext}"
    ]


def test_thumbnail_is_webp_and_bounded(make_client, create_event, upload_photo):
    client = make_client(thumbnail_size=64)
    event_id = create_event(on=client)["event"]["id"]
    response = upload_photo(event_id, image_bytes("PNG", size=(300, 120)), on=client)
    assert response.status_code == 201, response.text
    thumb = client.get(response.json()["thumbnail_url"])
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"
    image = decode(thumb.content)
    assert image.format == "WEBP"
    assert image.size == (64, 26)


# --- Hostile filenames ---------------------------------------------------------------------


HOSTILE_FILENAMES = {
    "unix-traversal": b"../../evil.php",
    "windows-traversal": b"..\\..\\x.jpg",
    "double-extension": b"photo.php.jpg",
    "nul-byte": b"a.jpg\x00.php",
    "unicode-rlo": "\u00fcn\u00efc\u00f8d\u00e9\u202egpj.php".encode(),
    "windows-device": b"CON.jpg",
    "ntfs-stream": b"x.jpg::$DATA",
    "absolute-unix": b"/etc/passwd",
    "absolute-windows": b"C:\\Windows\\win.ini",
    "unc-path": b"\\\\server\\share\\x.jpg",
    "header-injection": b"x.jpg\r\nContent-Type: text/html",
    "dot-dot-only": b"..",
    "htaccess": b".htaccess",
    "trailing-dot-space": b"shell.php. ",
}


@pytest.mark.parametrize("label", sorted(HOSTILE_FILENAMES))
def test_hostile_client_filenames_never_reach_disk(client, event_id, tmp_path, label):
    filename = HOSTILE_FILENAMES[label]
    response = post_raw(client, event_id, [(b"file", filename, b"image/jpeg", image_bytes())])
    assert response.status_code == 201, response.text
    photo = response.json()
    assert MEDIA_URL_RE.fullmatch(photo["url"])
    assert THUMB_URL_RE.fullmatch(photo["thumbnail_url"])
    assert "filename" not in photo  # the client name is never echoed
    assert_storage_is_safe(client, tmp_path)
    assert media_files(client) == sorted(
        [f"events/{event_id}/photos/{photo['id']}.jpg", f"events/{event_id}/thumbs/{photo['id']}.webp"]
    )


def test_hostile_filename_via_standard_client_encoding(client, upload_photo, event_id, tmp_path):
    response = upload_photo(event_id, filename="../../../../evil.php", content_type="application/x-php")
    assert response.status_code == 201, response.text
    assert MEDIA_URL_RE.fullmatch(response.json()["url"])["ext"] == "jpg"
    assert_storage_is_safe(client, tmp_path)


def test_every_upload_gets_a_fresh_random_name(client, upload_photo, event_id, tmp_path):
    ids = set()
    for _ in range(5):
        response = upload_photo(event_id, filename="same-name.jpg")
        assert response.status_code == 201, response.text
        ids.add(response.json()["id"])
    assert len(ids) == 5  # identical client names never overwrite each other
    assert len(media_files(client)) == 10
    assert_storage_is_safe(client, tmp_path)


def test_absurdly_long_filename_is_handled_without_server_error(client, event_id, tmp_path):
    response = post_raw(client, event_id, [(b"file", b"A" * 5000 + b".jpg", b"image/jpeg", image_bytes())])
    assert response.status_code in {201, 400}, response.text
    if response.status_code == 400:
        assert_error(response, 400, "BAD_REQUEST")
    assert_storage_is_safe(client, tmp_path)


# --- Rejected content types -----------------------------------------------------------------


HEIC_BYTES = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 200

REJECTED_CONTENT = {
    "php": (b"<?php system($_GET['cmd']); ?>", "PHP"),
    "html": (b"<!DOCTYPE html><html><body><script>alert(document.cookie)</script></body></html>", "HTML"),
    "svg": (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>', "SVG/XML"),
    "svg-xml-prolog": (b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>', "SVG/XML"),
    "gif": (image_bytes("GIF", mode="P", color=1), "GIF"),
    "pdf": (b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF", "PDF"),
    "bmp": (image_bytes("BMP"), "BMP"),
    "tiff": (image_bytes("TIFF"), "TIFF"),
    "heic": (HEIC_BYTES, "HEIC"),
    "avif": (b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00" + b"\x00" * 64, "AVIF"),
    "zip": (zip_bytes(), "ZIP"),
    "windows-exe": (b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64, None),
    "plain-text": (b"definitely not an image", None),
}


@pytest.mark.parametrize("label", sorted(REJECTED_CONTENT))
def test_non_image_content_is_rejected_with_415_despite_image_name_and_type(client, upload_photo, event_id, tmp_path, label):
    data, detected = REJECTED_CONTENT[label]
    response = upload_photo(event_id, data, filename="photo.jpg", content_type="image/jpeg")
    error = assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")
    assert error["details"] == {"allowed_types": ["image/jpeg", "image/png", "image/webp"], "detected": detected}
    assert media_files(client) == []
    assert list_photo_ids(client, event_id) == []
    assert_storage_is_safe(client, tmp_path)


def test_heic_rejection_explains_how_to_fix_it(upload_photo, event_id):
    response = upload_photo(event_id, HEIC_BYTES, filename="IMG_0001.HEIC", content_type="image/heic")
    error = assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")
    assert "HEIC" in error["message"]
    assert "JPEG" in error["message"]
    assert error["details"]["detected"] == "HEIC"


@functools.lru_cache(maxsize=None)
def noisy(fmt: str) -> bytes:
    return encode(Image.effect_noise((120, 90), 64).convert("RGB"), fmt)


INVALID_IMAGES = {
    "empty": b"",
    "jpeg-magic-garbage": b"\xff\xd8\xff" + b"\x13\x37" * 500,
    "jpeg-magic-php": b"\xff\xd8\xff<?php system($_GET['c']); ?>",
    "png-magic-garbage": b"\x89PNG\r\n\x1a\n" + b"\x13\x37" * 500,
    "webp-magic-garbage": b"RIFF\x00\x10\x00\x00WEBPVP8 " + b"\x13\x37" * 500,
    "truncated-jpeg": noisy("JPEG")[: len(noisy("JPEG")) // 2],
    "truncated-png": noisy("PNG")[: len(noisy("PNG")) // 2],
    "truncated-webp": noisy("WEBP")[: len(noisy("WEBP")) // 2],
}


@pytest.mark.parametrize("label", sorted(INVALID_IMAGES))
def test_undecodable_files_are_rejected_with_422_invalid_image(client, upload_photo, event_id, tmp_path, label):
    response = upload_photo(event_id, INVALID_IMAGES[label])
    assert_error(response, 422, "INVALID_IMAGE")
    assert media_files(client) == []
    assert list_photo_ids(client, event_id) == []
    assert_storage_is_safe(client, tmp_path)


# --- Polyglots & metadata ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [b"<script>alert(1)</script>", b"<?php system($_GET['c']); ?>", zip_bytes()],
    ids=["html", "php", "zip"],
)
def test_polyglot_payload_is_not_stored_or_served(client, upload_photo, event_id, payload):
    response = upload_photo(event_id, image_bytes("JPEG") + payload)
    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["content_type"] == "image/jpeg"
    served = client.get(photo["url"]).content
    on_disk = (storage_dir(client) / "events" / event_id / "photos" / f"{photo['id']}.jpg").read_bytes()
    assert served == on_disk
    for blob in (served, client.get(photo["thumbnail_url"]).content):
        assert payload not in blob
        assert b"<script" not in blob and b"<?php" not in blob and b"PK\x03\x04" not in blob
    assert served.endswith(b"\xff\xd9")


def test_gps_exif_is_stripped_and_orientation_applied_end_to_end(client, upload_photo, event_id):
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    exif[ExifTags.Base.Make] = "SecretCam"
    exif[ExifTags.IFD.GPSInfo] = {
        ExifTags.GPS.GPSLatitudeRef: "N",
        ExifTags.GPS.GPSLatitude: (3.0, 8.0, 21.0),
        ExifTags.GPS.GPSLongitudeRef: "E",
        ExifTags.GPS.GPSLongitude: (101.0, 41.0, 11.0),
    }
    source = encode(Image.new("RGB", (80, 40), (10, 200, 10)), "JPEG", exif=exif.tobytes(), comment=b"SECRET-COMMENT")
    response = upload_photo(event_id, source)
    assert response.status_code == 201, response.text
    photo = response.json()
    assert (photo["width"], photo["height"]) == (40, 80)

    for url in (photo["url"], photo["thumbnail_url"]):
        blob = client.get(url).content
        image = decode(blob)
        assert image.size == (40, 80)
        assert dict(image.getexif()) == {}
        assert image.getexif().get_ifd(ExifTags.IFD.GPSInfo) == {}
        assert b"Exif\x00\x00" not in blob
        assert b"SecretCam" not in blob
        assert b"SECRET-COMMENT" not in blob


def test_camera_jpeg_with_mpf_secondary_image_is_accepted(client, upload_photo, event_id):
    main = Image.new("RGB", (64, 48), (255, 0, 0))
    preview = Image.new("RGB", (32, 24), (0, 255, 0))
    source = encode(main, "MPO", save_all=True, append_images=[preview])
    response = upload_photo(event_id, source, filename="DSC_0001.JPG", content_type="image/jpeg")
    assert response.status_code == 201, response.text
    assert response.json()["content_type"] == "image/jpeg"


# --- Image modes -----------------------------------------------------------------------------


def _animated_webp() -> bytes:
    frames = [Image.new("RGB", (100, 80), color) for color in ((255, 0, 0), (0, 255, 0))]
    return encode(frames[0], "WEBP", save_all=True, append_images=frames[1:], duration=100)


def _palette_png_with_transparency() -> bytes:
    image = Image.new("P", (100, 80), 0)
    image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    image.paste(1, (50, 0, 100, 80))
    return encode(image, "PNG", transparency=0)


def _rgb_png_with_colour_key() -> bytes:
    image = Image.new("RGB", (100, 80), (10, 20, 30))
    image.paste((1, 2, 3), (0, 0, 50, 80))
    return encode(image, "PNG", transparency=(1, 2, 3))


MODE_UPLOADS = {
    "png-rgba": (lambda: image_bytes("PNG", size=(100, 80), mode="RGBA", color=(1, 2, 3, 100)), "image/png"),
    "png-la": (lambda: image_bytes("PNG", size=(100, 80), mode="LA", color=(100, 50)), "image/png"),
    "png-p-transparency": (_palette_png_with_transparency, "image/png"),
    "png-rgb-colour-key": (_rgb_png_with_colour_key, "image/png"),
    "png-16bit": (lambda: image_bytes("PNG", size=(100, 80), mode="I;16", color=0x8000), "image/png"),
    "png-1bit": (lambda: image_bytes("PNG", size=(100, 80), mode="1", color=1), "image/png"),
    "jpeg-grey": (lambda: image_bytes("JPEG", size=(100, 80), mode="L", color=77), "image/jpeg"),
    "jpeg-progressive": (lambda: image_bytes("JPEG", size=(100, 80), progressive=True), "image/jpeg"),
    "jpeg-cmyk": (lambda: image_bytes("JPEG", size=(100, 80), mode="CMYK", color=(0, 255, 255, 0)), "image/jpeg"),
    "webp-animated": (_animated_webp, "image/webp"),
    "webp-lossless": (lambda: image_bytes("WEBP", size=(100, 80), mode="RGBA", color=(1, 2, 3, 4), lossless=True), "image/webp"),
}


@pytest.mark.parametrize("label", sorted(MODE_UPLOADS))
def test_image_modes_are_accepted_with_valid_output(make_client, create_event, upload_photo, label):
    client = make_client(thumbnail_size=64)
    event_id = create_event(on=client)["event"]["id"]
    factory, mime = MODE_UPLOADS[label]
    response = upload_photo(event_id, factory(), filename="x.bin", content_type="application/octet-stream", on=client)
    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["content_type"] == mime
    assert (photo["width"], photo["height"]) == (100, 80)

    stored = decode(client.get(photo["url"]).content)
    assert stored.size == (100, 80)
    assert not getattr(stored, "is_animated", False)
    thumb = decode(client.get(photo["thumbnail_url"]).content)
    assert thumb.format == "WEBP"
    assert max(thumb.size) <= 64


@pytest.mark.parametrize("label", ["png-p-transparency", "png-rgb-colour-key"])
def test_transparency_survives_in_stored_png(client, upload_photo, event_id, label):
    factory, _ = MODE_UPLOADS[label]
    response = upload_photo(event_id, factory(), filename="x.png", content_type="image/png")
    assert response.status_code == 201, response.text
    stored = decode(client.get(response.json()["url"]).content).convert("RGBA")
    assert stored.getpixel((10, 40))[3] == 0  # the transparent half
    assert stored.getpixel((90, 40))[3] == 255  # the opaque half


# --- Decompression bombs & dimensions ------------------------------------------------------------


def test_png_decompression_bomb_is_rejected(client, upload_photo, event_id, tmp_path):
    bomb = png_decompression_bomb()
    assert len(bomb) < 200_000
    response = upload_photo(event_id, bomb, filename="bomb.png", content_type="image/png")
    assert_error(response, 422, "IMAGE_TOO_LARGE")
    assert media_files(client) == []
    assert_storage_is_safe(client, tmp_path)


def test_image_wider_than_max_dimension_is_rejected(make_client, create_event, upload_photo):
    client = make_client(max_image_dimension=100)
    event_id = create_event(on=client)["event"]["id"]
    response = upload_photo(event_id, image_bytes("PNG", size=(200, 10)), on=client)
    error = assert_error(response, 422, "IMAGE_TOO_LARGE")
    assert error["details"]["width"] == 200
    assert error["details"]["max_dimension"] == 100
    ok = upload_photo(event_id, image_bytes("PNG", size=(100, 10)), on=client)
    assert ok.status_code == 201, ok.text


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_image_over_max_pixels_is_rejected_with_details(make_client, create_event, upload_photo):
    client = make_client(max_image_pixels=1000)
    event_id = create_event(on=client)["event"]["id"]
    # 1600 px: over the app's exact limit, under Pillow's own 2x guard -> the app's check reports details.
    response = upload_photo(event_id, image_bytes("PNG", size=(40, 40)), on=client)
    error = assert_error(response, 422, "IMAGE_TOO_LARGE")
    assert error["details"]["max_pixels"] == 1000
    # 3072 px: also trips Pillow's guard; still the same structured error.
    assert_error(upload_photo(event_id, image_bytes("JPEG", size=(64, 48)), on=client), 422, "IMAGE_TOO_LARGE")
    assert media_files(client) == []


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_max_stored_dimension_downscales_the_display_copy(make_client, create_event, upload_photo, fmt):
    client = make_client(max_stored_dimension=256, thumbnail_size=64)
    event_id = create_event(on=client)["event"]["id"]
    response = upload_photo(event_id, image_bytes(fmt, size=(1000, 500)), on=client)
    assert response.status_code == 201, response.text
    photo = response.json()
    assert (photo["width"], photo["height"]) == (256, 128)
    assert decode(client.get(photo["url"]).content).size == (256, 128)
    assert decode(client.get(photo["thumbnail_url"]).content).size == (64, 32)


# --- Size limits ---------------------------------------------------------------------------


SMALL_LIMIT = 50_000


def _padded_jpeg(total_size: int) -> bytes:
    """A valid JPEG padded with trailing zeros to exactly ``total_size`` bytes."""
    data = image_bytes("JPEG")
    return data + bytes(total_size - len(data))


def test_file_over_max_upload_bytes_is_rejected_with_413(make_client, create_event, upload_photo, tmp_path):
    client = make_client(max_upload_bytes=SMALL_LIMIT)
    event_id = create_event(on=client)["event"]["id"]
    response = upload_photo(event_id, _padded_jpeg(SMALL_LIMIT + 1), on=client)
    error = assert_error(response, 413, "FILE_TOO_LARGE")
    assert error["details"] == {"max_bytes": SMALL_LIMIT}
    assert media_files(client) == []
    assert list_photo_ids(client, event_id) == []
    assert_storage_is_safe(client, tmp_path)


def test_file_exactly_at_max_upload_bytes_is_accepted(make_client, create_event, upload_photo):
    client = make_client(max_upload_bytes=SMALL_LIMIT)
    event_id = create_event(on=client)["event"]["id"]
    response = upload_photo(event_id, _padded_jpeg(SMALL_LIMIT), on=client)
    assert response.status_code == 201, response.text
    assert response.json()["size_bytes"] < SMALL_LIMIT  # padding is not stored


def test_file_too_large_message_states_the_real_limit(make_client, create_event, upload_photo):
    client = make_client(max_upload_bytes=SMALL_LIMIT)
    event_id = create_event(on=client)["event"]["id"]
    error = assert_error(upload_photo(event_id, _padded_jpeg(SMALL_LIMIT + 1), on=client), 413, "FILE_TOO_LARGE")
    assert not re.search(r"(?<![\d.])0 MB", error["message"]), error["message"]


def test_declared_content_length_over_limit_is_rejected_before_reading(make_client, create_event):
    client = make_client(max_upload_bytes=SMALL_LIMIT)
    event_id = create_event(on=client)["event"]["id"]
    limit = SMALL_LIMIT + MULTIPART_OVERHEAD_BYTES
    response = client.post(
        f"/api/events/{event_id}/photos",
        content=b"tiny",  # never read: the header alone is enough to refuse
        headers={"content-type": "multipart/form-data; boundary=x", "content-length": str(limit + 1)},
    )
    error = assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    assert error["details"] == {"max_bytes": limit}
    assert response.headers.get("connection") == "close"


def test_real_body_over_request_limit_is_rejected(make_client, create_event, tmp_path):
    client = make_client(max_upload_bytes=SMALL_LIMIT)
    event_id = create_event(on=client)["event"]["id"]
    body, content_type = multipart([(b"file", b"big.jpg", b"image/jpeg", _padded_jpeg(200_000))])
    response = client.post(f"/api/events/{event_id}/photos", content=body, headers={"content-type": content_type})
    assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    assert media_files(client) == []
    assert_storage_is_safe(client, tmp_path)


def test_invalid_content_length_header_is_rejected(client, event_id):
    response = client.post(
        f"/api/events/{event_id}/photos",
        content=b"x",
        headers={"content-type": "multipart/form-data; boundary=x", "content-length": "12abc"},
    )
    assert_error(response, 400, "BAD_REQUEST")


def test_chunked_body_without_content_length_is_cut_off(make_client, create_event, tmp_path):
    client = make_client(max_upload_bytes=SMALL_LIMIT)
    event_id = create_event(on=client)["event"]["id"]
    body, content_type = multipart([(b"file", b"big.jpg", b"image/jpeg", _padded_jpeg(300_000))])

    def chunks() -> Iterable[bytes]:
        for start in range(0, len(body), 16_384):
            yield body[start : start + 16_384]

    response = client.post(f"/api/events/{event_id}/photos", content=chunks(), headers={"content-type": content_type})
    assert "content-length" not in response.request.headers
    assert response.request.headers.get("transfer-encoding") == "chunked"
    assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    assert media_files(client) == []
    assert_storage_is_safe(client, tmp_path)


def test_chunked_overflow_413_matches_declared_length_413(make_client, create_event):
    client = make_client(max_upload_bytes=SMALL_LIMIT)
    event_id = create_event(on=client)["event"]["id"]
    limit = SMALL_LIMIT + MULTIPART_OVERHEAD_BYTES
    body, content_type = multipart([(b"file", b"big.jpg", b"image/jpeg", _padded_jpeg(300_000))])

    def chunks() -> Iterable[bytes]:
        for start in range(0, len(body), 16_384):
            yield body[start : start + 16_384]

    response = client.post(f"/api/events/{event_id}/photos", content=chunks(), headers={"content-type": content_type})
    error = assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    # Same contract as the declared Content-Length path (see test_declared_content_length_over_limit_...).
    assert error["details"] == {"max_bytes": limit}
    assert response.headers.get("connection") == "close"


def _asgi_streamed_post(client: TestClient, path: str, chunks: list[bytes], content_type: str) -> dict[str, Any]:
    """Drive the ASGI app directly with a genuinely incremental body; count chunks consumed."""
    state: dict[str, Any] = {"consumed": 0, "status": None, "headers": {}, "body": b""}

    async def run() -> None:
        finished = anyio.Event()

        async def receive() -> dict[str, Any]:
            index = state["consumed"]
            if index < len(chunks):
                state["consumed"] += 1
                return {"type": "http.request", "body": chunks[index], "more_body": index + 1 < len(chunks)}
            await finished.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["headers"] = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                state["body"] += message.get("body", b"")
                if not message.get("more_body", False):
                    finished.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"testserver"), (b"content-type", content_type.encode())],
            "client": ("203.0.113.9", 50000),
            "server": ("testserver", 80),
            "state": {},
            "extensions": {},
        }
        with anyio.fail_after(15):
            await client.app(scope, receive, send)

    client.portal.call(run)
    return state


def test_streamed_body_stops_being_read_once_the_limit_is_crossed(make_client, create_event, tmp_path):
    client = make_client(max_upload_bytes=SMALL_LIMIT)
    event_id = create_event(on=client)["event"]["id"]
    limit = SMALL_LIMIT + MULTIPART_OVERHEAD_BYTES
    body, content_type = multipart([(b"file", b"big.jpg", b"image/jpeg", _padded_jpeg(2 * 1024 * 1024))])
    chunk_size = 16_384
    chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]

    result = _asgi_streamed_post(client, f"/api/events/{event_id}/photos", chunks, content_type)

    assert result["status"] == 413
    assert b"PAYLOAD_TOO_LARGE" in result["body"]
    # The server must stop pulling the body right after the limit, not drain 2 MiB.
    assert result["consumed"] <= limit // chunk_size + 2 < len(chunks)
    assert media_files(client) == []
    assert_storage_is_safe(client, tmp_path)


def test_json_body_over_64k_to_create_event_is_rejected(client):
    response = client.post("/api/events", json={"name": "x" * 70_000})
    error = assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    assert error["details"] == {"max_bytes": 64 * 1024}


def test_upload_budget_only_applies_to_the_upload_route(make_client, create_event):
    # max_upload_bytes is large, but other routes keep the small JSON limit.
    client = make_client(max_upload_bytes=5 * 1024 * 1024)
    event = create_event(on=client)
    response = client.patch(
        f"/api/events/{event['event']['id']}",
        json={"name": "y" * 100_000},
        headers=auth(event["host_token"]),
    )
    assert_error(response, 413, "PAYLOAD_TOO_LARGE")


# --- Multipart form hardening ---------------------------------------------------------------


def test_missing_file_field_is_422(client, event_id):
    response = post_raw(client, event_id, [(b"caption", None, None, b"no photo attached")])
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"][0]["field"] == "file"


def test_file_sent_as_plain_text_field_is_422(client, event_id):
    response = post_raw(client, event_id, [(b"file", None, None, b"\xff\xd8\xff not really a file")])
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"][0]["field"] == "file"


def test_two_files_in_one_request_are_rejected(client, event_id):
    response = post_raw(
        client,
        event_id,
        [(b"file", b"a.jpg", b"image/jpeg", image_bytes()), (b"file", b"b.jpg", b"image/jpeg", image_bytes())],
    )
    assert_error(response, 400, "BAD_REQUEST")
    assert media_files(client) == []


def test_second_file_under_another_field_name_is_rejected(client, event_id):
    response = post_raw(
        client,
        event_id,
        [(b"file", b"a.jpg", b"image/jpeg", image_bytes()), (b"extra", b"b.jpg", b"image/jpeg", image_bytes())],
    )
    assert_error(response, 400, "BAD_REQUEST")
    assert media_files(client) == []


def test_unknown_form_field_is_422(upload_photo, event_id, client):
    response = upload_photo(event_id, fields={"is_admin": "true"})
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"] == [{"location": "form", "field": "is_admin", "message": "Unexpected form field."}]
    assert media_files(client) == []


def test_duplicated_text_field_is_422(client, event_id):
    response = post_raw(
        client,
        event_id,
        [(b"file", b"a.jpg", b"image/jpeg", image_bytes()), (b"caption", None, None, b"one"), (b"caption", None, None, b"two")],
    )
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"][0]["field"] == "caption"


def test_too_many_text_fields_are_rejected(client, event_id):
    response = post_raw(
        client,
        event_id,
        [(b"file", b"a.jpg", b"image/jpeg", image_bytes())]
        + [(b"caption", None, None, b"x")] * 3,
    )
    assert_error(response, 400, "BAD_REQUEST")


@pytest.mark.parametrize(("field", "limit"), [("caption", 140), ("uploader_name", 40)])
def test_text_field_length_limits(upload_photo, event_id, field, limit):
    too_long = upload_photo(event_id, fields={field: "x" * (limit + 1)})
    error = assert_error(too_long, 422, "VALIDATION_ERROR")
    assert error["details"] == [{"location": "form", "field": field, "message": f"Must be at most {limit} characters."}]
    at_limit = upload_photo(event_id, fields={field: "x" * limit})
    assert at_limit.status_code == 201, at_limit.text
    assert at_limit.json()[field] == "x" * limit


def test_oversized_text_part_is_rejected_by_the_parser(upload_photo, event_id):
    response = upload_photo(event_id, fields={"caption": "x" * 5000})
    assert_error(response, 400, "BAD_REQUEST")


@pytest.mark.parametrize(
    ("content_type", "kwargs"),
    [
        ("application/json", {"json": {"file": "aGVsbG8="}}),
        ("application/x-www-form-urlencoded", {"data": {"file": "hello"}}),
        ("image/jpeg", {"content": image_bytes(), "headers": {"content-type": "image/jpeg"}}),
        ("text/plain", {"content": b"hello", "headers": {"content-type": "text/plain"}}),
    ],
)
def test_non_multipart_request_is_415(client, event_id, content_type, kwargs):
    response = client.post(f"/api/events/{event_id}/photos", **kwargs)
    assert response.request.headers["content-type"].startswith(content_type)
    error = assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")
    assert "multipart/form-data" in error["message"]


def test_multipart_without_boundary_is_400(client, event_id):
    response = client.post(
        f"/api/events/{event_id}/photos", content=b"garbage", headers={"content-type": "multipart/form-data"}
    )
    assert_error(response, 400, "BAD_REQUEST")


def test_caption_and_name_are_sanitised(upload_photo, event_id):
    response = upload_photo(
        event_id,
        fields={
            "caption": "Hi\u202eThere\x00\x1bfriend\r\n\u200b!",
            "uploader_name": "  Bob\t\u2066Smith\u2069  ",
        },
    )
    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["caption"] == "HiThere friend !"
    assert photo["uploader_name"] == "Bob Smith"
    for value in (photo["caption"], photo["uploader_name"]):
        assert not any(ch in value for ch in "\u202e\u2066\u2069\u200b\x00\x1b\r\n\t")


def test_blank_or_invisible_text_fields_become_null(upload_photo, event_id):
    response = upload_photo(event_id, fields={"caption": " \u200b\u202e \t", "uploader_name": ""})
    assert response.status_code == 201, response.text
    assert response.json()["caption"] is None
    assert response.json()["uploader_name"] is None


def test_caption_length_is_measured_after_cleaning(upload_photo, event_id):
    # 140 visible chars padded with removable controls still fits.
    caption = "\u202e" * 50 + "y" * 140 + "\u200b" * 50
    response = upload_photo(event_id, fields={"caption": caption})
    assert response.status_code == 201, response.text
    assert response.json()["caption"] == "y" * 140


# --- Event state --------------------------------------------------------------------------


def test_uploads_closed_by_host_returns_403(client, upload_photo, event):
    event_id = event["event"]["id"]
    closed = client.patch(f"/api/events/{event_id}", json={"uploads_open": False}, headers=auth(event["host_token"]))
    assert closed.status_code == 200, closed.text
    assert closed.json()["uploads_open"] is False

    assert_error(upload_photo(event_id), 403, "UPLOADS_CLOSED")
    assert media_files(client) == []

    reopened = client.patch(f"/api/events/{event_id}", json={"uploads_open": True}, headers=auth(event["host_token"]))
    assert reopened.status_code == 200
    assert upload_photo(event_id).status_code == 201


def test_event_checks_run_before_the_body_is_parsed(client, event):
    event_id = event["event"]["id"]
    client.patch(f"/api/events/{event_id}", json={"uploads_open": False}, headers=auth(event["host_token"]))
    response = client.post(
        f"/api/events/{event_id}/photos",
        content=b"this is not valid multipart at all",
        headers={"content-type": "multipart/form-data; boundary=zzz"},
    )
    assert_error(response, 403, "UPLOADS_CLOSED")


def test_event_photo_limit_returns_409(make_client, create_event, upload_photo):
    client = make_client(max_photos_per_event=1)
    event_id = create_event(on=client)["event"]["id"]
    first = upload_photo(event_id, on=client)
    assert first.status_code == 201, first.text
    assert_error(upload_photo(event_id, on=client), 409, "EVENT_PHOTO_LIMIT")
    assert list_photo_ids(client, event_id) == [first.json()["id"]]
    assert len(media_files(client)) == 2  # one photo + one thumbnail


def test_low_disk_threshold_returns_507(make_client, create_event, upload_photo):
    client = make_client(min_free_disk_bytes=10**18)
    event_id = create_event(on=client)["event"]["id"]
    assert_error(upload_photo(event_id, on=client), 507, "INSUFFICIENT_STORAGE")
    assert media_files(client) == []


def test_nearly_full_disk_returns_507(client, upload_photo, event_id, monkeypatch):
    # Even with min_free_disk_bytes=0, the server keeps room for two max-size uploads.
    monkeypatch.setattr(LocalStorage, "free_bytes", lambda self: 10 * 1024 * 1024)
    assert_error(upload_photo(event_id), 507, "INSUFFICIENT_STORAGE")
    assert media_files(client) == []


@pytest.mark.parametrize("bad_id", ["abcdefghijkl", "ABCDEFGHIJKL", "short", "zzzzzzzzzzzzz", "db", "events"])
def test_unknown_or_malformed_event_returns_404(client, upload_photo, bad_id, tmp_path):
    response = upload_photo(bad_id)
    assert_error(response, 404, "EVENT_NOT_FOUND")
    assert_storage_is_safe(client, tmp_path)


@pytest.mark.parametrize("bad_id", ["..%2F..%2Fdb", "%2E%2E", "..%5C..%5Cdb"])
def test_encoded_traversal_in_event_id_is_404(client, upload_photo, bad_id, tmp_path):
    response = upload_photo(bad_id)
    # Depending on how the decoded path splits, either no route matches or the id is refused.
    error = assert_error(response, 404, response.json()["error"]["code"])
    assert error["code"] in {"NOT_FOUND", "EVENT_NOT_FOUND"}
    assert media_files(client) == []
    assert_storage_is_safe(client, tmp_path)


def test_upload_to_deleted_event_returns_404(client, upload_photo, event):
    event_id = event["event"]["id"]
    deleted = client.delete(f"/api/events/{event_id}", headers=auth(event["host_token"]))
    assert deleted.status_code == 204
    assert_error(upload_photo(event_id), 404, "EVENT_NOT_FOUND")
    assert media_files(client) == []


# --- Error envelope & request ids ---------------------------------------------------------------


def test_error_envelope_shape_and_request_id_header(upload_photo, event_id):
    response = upload_photo(event_id, b"<?php echo 1; ?>")
    error = assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")
    assert re.fullmatch(r"[0-9a-f]{32}", error["request_id"])
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_client_supplied_request_id_is_echoed(client, event_id):
    response = client.post(
        f"/api/events/{event_id}/photos",
        files={"file": ("x.jpg", b"not an image", "image/jpeg")},
        headers={"X-Request-ID": "frontend-req-0001"},
    )
    error = assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")
    assert error["request_id"] == "frontend-req-0001"


@pytest.mark.parametrize("bad_request_id", ["short", "has spaces in it!", "x" * 65, "evil\u202eid-123456"])
def test_unsafe_client_request_id_is_replaced(client, event_id, bad_request_id):
    response = client.post(
        f"/api/events/{event_id}/photos",
        files={"file": ("x.jpg", b"not an image", "image/jpeg")},
        headers={"X-Request-ID": bad_request_id.encode("utf-8")},
    )
    error = assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")
    assert re.fullmatch(r"[0-9a-f]{32}", error["request_id"])


def test_middleware_level_errors_carry_the_request_id_too(make_client, create_event):
    client = make_client(max_upload_bytes=SMALL_LIMIT)
    event_id = create_event(on=client)["event"]["id"]
    response = client.post(
        f"/api/events/{event_id}/photos",
        content=b"x",
        headers={
            "content-type": "multipart/form-data; boundary=x",
            "content-length": str(10**9),
            "X-Request-ID": "trace-413-abcdef",
        },
    )
    error = assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    assert error["request_id"] == "trace-413-abcdef"


def test_successful_upload_also_carries_request_id(upload_photo, event_id):
    response = upload_photo(event_id)
    assert response.status_code == 201
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])
