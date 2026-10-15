"""Unit tests for the image validation / sanitisation pipeline (app.services.image_processing).

These call the pure functions directly (no HTTP). Every "accepted" case also checks
that the output is a clean re-encode: the right container, no metadata segments,
and a WebP thumbnail that fits the policy.

NOTE: python-magic must never be imported on this machine (it hard-crashes the
interpreter on Windows). Tests that exercise the libmagic code path inject a fake
module / cookie instead.
"""

from __future__ import annotations

import functools
import io
import struct
import sys
import types
import zipfile
import zlib
from typing import Any

import pytest
from PIL import ExifTags, Image, ImageCms, ImageDraw, PngImagePlugin

from app.core.errors import (
    ErrorCode,
    ImageTooLargeError,
    InvalidImageError,
    UnsupportedMediaTypeError,
)
from app.services import image_processing
from app.services.image_processing import (
    ALLOWED_MIME_TYPES,
    JPEG,
    PNG,
    WEBP,
    ImagePolicy,
    MimeDetector,
    ProcessedImage,
    describe_unsupported_format,
    process_image,
    sniff_image_format,
)

DEFAULT_POLICY = ImagePolicy(max_pixels=50_000_000, max_dimension=16_383, max_stored_dimension=4096, thumbnail_size=640)


@pytest.fixture(autouse=True)
def _pillow_bomb_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app() sets this process-wide; pin it to the default so test order never matters."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", DEFAULT_POLICY.max_pixels)


# --- Helpers ------------------------------------------------------------------------------


def encode(image: Image.Image, fmt: str, **save_kwargs: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def solid(fmt: str = "JPEG", size: tuple[int, int] = (64, 48), mode: str = "RGB", color: Any = (200, 40, 40), **kw: Any) -> bytes:
    return encode(Image.new(mode, size, color), fmt, **kw)


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def raw_png(width: int, height: int, *, bit_depth: int, color_type: int, scanlines: bytes, extra_chunks: tuple = ()) -> bytes:
    """Hand-built PNG (lets us declare any header we like)."""
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + b"".join(png_chunk(kind, payload) for kind, payload in extra_chunks)
        + png_chunk(b"IDAT", zlib.compress(scanlines, 9))
        + png_chunk(b"IEND", b"")
    )


@functools.lru_cache(maxsize=1)
def png_decompression_bomb() -> bytes:
    """A ~50 KB PNG that declares 20000x20000 1-bit pixels (400 megapixels, ~50 MB of real scanlines)."""
    side = 20_000
    row = 1 + side // 8  # filter byte + packed bits
    return raw_png(side, side, bit_depth=1, color_type=0, scanlines=bytes(row * side))


def png_chunk_types(data: bytes) -> list[bytes]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    kinds, pos = [], 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kinds.append(data[pos + 4 : pos + 8])
        pos += 12 + length
    return kinds


def jpeg_marker_segments(data: bytes) -> list[tuple[int, bytes]]:
    """(marker, payload) for every segment before the scan data, plus the SOS marker."""
    assert data[:2] == b"\xff\xd8"
    segments, pos = [], 2
    while pos < len(data):
        assert data[pos] == 0xFF, f"lost JPEG marker sync at {pos}"
        marker = data[pos + 1]
        if marker == 0xDA:  # start of scan: entropy-coded data follows
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


def assert_clean_output(result: ProcessedImage, policy: ImagePolicy = DEFAULT_POLICY) -> tuple[Image.Image, Image.Image]:
    """Shared post-conditions for every accepted image."""
    original = decode(result.original.data)
    thumbnail = decode(result.thumbnail.data)
    assert sniff_image_format(result.original.data[:64]) is result.format
    assert original.format == result.format.pil_format
    assert (result.original.width, result.original.height) == original.size
    # Thumbnail: always WebP, bounded by the policy, same aspect family as the stored copy.
    assert sniff_image_format(result.thumbnail.data[:64]) is WEBP
    assert thumbnail.format == "WEBP"
    assert (result.thumbnail.width, result.thumbnail.height) == thumbnail.size
    assert max(thumbnail.size) <= policy.thumbnail_size
    assert thumbnail.width <= original.width and thumbnail.height <= original.height
    # Never animated.
    assert not getattr(original, "is_animated", False)
    assert not getattr(thumbnail, "is_animated", False)
    # No metadata of any kind in the container.
    if result.format is JPEG:
        markers = {marker for marker, _ in jpeg_marker_segments(result.original.data)}
        assert 0xE1 not in markers, "APP1 (EXIF/XMP) must be stripped"
        assert 0xFE not in markers, "COM comment must be stripped"
    elif result.format is PNG:
        kinds = set(png_chunk_types(result.original.data))
        assert not kinds & {b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME", b"acTL", b"fcTL", b"fdAT"}
    else:
        assert not set(webp_chunk_types(result.original.data)) & {b"EXIF", b"XMP ", b"ANIM", b"ANMF"}
    assert not set(webp_chunk_types(result.thumbnail.data)) & {b"EXIF", b"XMP ", b"ANIM", b"ANMF"}
    return original, thumbnail


SRGB_ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def relabelled_icc(color_space: bytes) -> bytes:
    """The sRGB profile with its header colour-space field changed (e.g. to CMYK/GRAY)."""
    return SRGB_ICC[:16] + color_space + SRGB_ICC[20:]


# --- sniff_image_format ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", JPEG),
        (b"\xff\xd8\xff", JPEG),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", PNG),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", WEBP),
        (b"RIFF\x24\x00\x00\x00WEBPVP8L", WEBP),
        (b"RIFF\x24\x00\x00\x00WEBPVP8X", WEBP),
    ],
    ids=["jpeg-jfif", "jpeg-minimal", "png", "webp-lossy", "webp-lossless", "webp-extended"],
)
def test_sniff_recognises_allowed_signatures(header: bytes, expected: Any) -> None:
    assert sniff_image_format(header) is expected


@pytest.mark.parametrize(
    "header",
    [
        b"",
        b"\xff\xd8",  # incomplete JPEG SOI
        b"\x89PNG\r\n\x1a",  # incomplete PNG signature
        b"\x89PNG\r\n\x1a\x0b",  # corrupted PNG signature
        b"RIFF\x24\x00\x00\x00WAVEfmt ",  # RIFF but audio
        b"RIFF\x24\x00\x00\x00WEBPABCD",  # WebP container, unknown bitstream chunk
        b"RIFF\x24\x00\x00\x00WEBP",  # too short to hold the first chunk tag
        b"GIF89a\x01\x00\x01\x00",
        b"BM\x3a\x00\x00\x00",
        b"<?php echo 1; ?>",
        b"\x00\x00\x00\x18ftypheic",
        b"x\xff\xd8\xff",  # signature not at offset 0
    ],
    ids=[
        "empty", "short-jpeg", "short-png", "bad-png", "riff-wave", "webp-unknown-chunk",
        "webp-truncated", "gif", "bmp", "php", "heic", "offset-jpeg",
    ],
)
def test_sniff_rejects_everything_else(header: bytes) -> None:
    assert sniff_image_format(header) is None


def test_sniff_ignores_extension_like_content() -> None:
    # A PNG signature wins even if the rest of the header "looks like" a JPEG.
    assert sniff_image_format(b"\x89PNG\r\n\x1a\n\xff\xd8\xff") is PNG


# --- describe_unsupported_format ----------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic", "HEIC"),
        (b"\x00\x00\x00\x18ftypheix\x00\x00\x00\x00", "HEIC"),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00", "HEIF"),
        (b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00", "AVIF"),
        (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00", "video"),
        (b"GIF87a\x01\x00", "GIF"),
        (b"GIF89a\x01\x00", "GIF"),
        (b"II*\x00\x08\x00\x00\x00", "TIFF"),
        (b"MM\x00*\x00\x00\x00\x08", "TIFF"),
        (b"BM\x3a\x00\x00\x00", "BMP"),
        (b"%PDF-1.7\n", "PDF"),
        (b"PK\x03\x04\x14\x00", "ZIP"),
        (b'<svg xmlns="http://www.w3.org/2000/svg">', "SVG/XML"),
        (b'\xef\xbb\xbf  <?xml version="1.0"?><svg/>', "SVG/XML"),
        (b"<!DOCTYPE html><html>", "HTML"),
        (b"\n\n<HTML><body>", "HTML"),
        (b"<script>alert(1)</script>", "HTML"),
        (b"<?php system($_GET['c']); ?>", "PHP"),
        (b"  <?PHP echo 1;", "PHP"),
        (b"MZ\x90\x00", None),
        (b"just some text", None),
        (b"", None),
    ],
)
def test_describe_unsupported_format_names_common_types(header: bytes, expected: str | None) -> None:
    assert describe_unsupported_format(header) == expected


# --- Accepted formats --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (solid("JPEG"), JPEG),
        (solid("PNG"), PNG),
        (solid("WEBP"), WEBP),
    ],
    ids=["jpeg", "png", "webp"],
)
def test_allowed_formats_are_reencoded_in_their_detected_format(data: bytes, expected: Any) -> None:
    result = process_image(data, DEFAULT_POLICY)
    assert result.format is expected
    assert result.format.mime in ALLOWED_MIME_TYPES
    original, _ = assert_clean_output(result)
    assert original.size == (64, 48)
    assert original.convert("RGB").getpixel((10, 10)) == pytest.approx((200, 40, 40), abs=8)


def test_output_is_a_fresh_encode_not_the_uploaded_bytes() -> None:
    # Uncompressed PNG in, so an untouched pass-through would be detectable.
    source = solid("PNG", compress_level=0)
    result = process_image(source, DEFAULT_POLICY)
    assert result.original.data != source
    assert len(result.original.data) < len(source)


# --- Rejections --------------------------------------------------------------------------


def test_empty_file_is_invalid_image() -> None:
    with pytest.raises(InvalidImageError) as exc_info:
        process_image(b"", DEFAULT_POLICY)
    assert exc_info.value.code == ErrorCode.INVALID_IMAGE
    assert exc_info.value.status_code == 422


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("shell.php", "<?php system($_GET['c']); ?>")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("data", "detected"),
    [
        (b"<?php system($_GET['cmd']); ?>", "PHP"),
        (b"<!DOCTYPE html><html><body><script>alert(1)</script></body></html>", "HTML"),
        (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>', "SVG/XML"),
        (solid("GIF", mode="P", color=1), "GIF"),
        (b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF", "PDF"),
        (solid("BMP"), "BMP"),
        (solid("TIFF"), "TIFF"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64, "HEIC"),
        (_zip_bytes(), "ZIP"),
        (b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64, None),
        (b"hello world", None),
    ],
    ids=["php", "html", "svg", "gif", "pdf", "bmp", "tiff", "heic", "zip", "exe", "text"],
)
def test_unsupported_types_raise_415_with_detected_name(data: bytes, detected: str | None) -> None:
    with pytest.raises(UnsupportedMediaTypeError) as exc_info:
        process_image(data, DEFAULT_POLICY)
    error = exc_info.value
    assert error.status_code == 415
    assert error.code == ErrorCode.UNSUPPORTED_MEDIA_TYPE
    assert error.details == {"allowed_types": ["image/jpeg", "image/png", "image/webp"], "detected": detected}
    if detected:
        assert detected in error.message


def test_heic_rejection_message_is_helpful() -> None:
    with pytest.raises(UnsupportedMediaTypeError) as exc_info:
        process_image(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64, DEFAULT_POLICY)
    message = exc_info.value.message
    assert "HEIC" in message
    assert "JPEG" in message
    assert "Most Compatible" in message


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xd8\xff" + b"\x13\x37" * 400,
        b"\xff\xd8\xff<?php system($_GET['c']); ?>",
        b"\x89PNG\r\n\x1a\n" + b"\x13\x37" * 400,
        b"\x89PNG\r\n\x1a\n<script>alert(1)</script>",
        b"RIFF\x00\x10\x00\x00WEBPVP8 " + b"\x13\x37" * 400,
    ],
    ids=["jpeg-garbage", "jpeg-php", "png-garbage", "png-html", "webp-garbage"],
)
def test_valid_magic_bytes_followed_by_garbage_is_invalid_image(data: bytes) -> None:
    with pytest.raises(InvalidImageError) as exc_info:
        process_image(data, DEFAULT_POLICY)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == ErrorCode.INVALID_IMAGE


@functools.lru_cache(maxsize=None)
def _noisy(fmt: str) -> bytes:
    return encode(Image.effect_noise((160, 120), 64).convert("RGB"), fmt)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
@pytest.mark.parametrize("fraction", [0.3, 0.6, 0.95])
def test_truncated_images_are_invalid(fmt: str, fraction: float) -> None:
    data = _noisy(fmt)
    with pytest.raises(InvalidImageError):
        process_image(data[: int(len(data) * fraction)], DEFAULT_POLICY)


def test_png_missing_iend_is_invalid() -> None:
    data = _noisy("PNG")
    assert data.endswith(png_chunk(b"IEND", b""))
    with pytest.raises(InvalidImageError):
        process_image(data[:-12], DEFAULT_POLICY)


# --- Decompression bombs / dimension limits ------------------------------------------------


def test_png_decompression_bomb_is_rejected_before_decoding() -> None:
    bomb = png_decompression_bomb()
    assert len(bomb) < 200_000  # tiny on the wire, 400 megapixels declared
    with pytest.raises(ImageTooLargeError) as exc_info:
        process_image(bomb, DEFAULT_POLICY)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == ErrorCode.IMAGE_TOO_LARGE


def test_bomb_is_rejected_even_when_pillow_global_guard_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # The policy check must stand on its own, not rely on Pillow's process-wide limit.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", None)
    with pytest.raises(ImageTooLargeError) as exc_info:
        process_image(png_decompression_bomb(), DEFAULT_POLICY)
    assert exc_info.value.details == {
        "width": 20_000,
        "height": 20_000,
        "max_dimension": DEFAULT_POLICY.max_dimension,
        "max_pixels": DEFAULT_POLICY.max_pixels,
    }


def test_single_dimension_over_max_dimension_is_rejected() -> None:
    policy = ImagePolicy(max_pixels=50_000_000, max_dimension=100, max_stored_dimension=4096, thumbnail_size=64)
    with pytest.raises(ImageTooLargeError) as exc_info:
        process_image(solid("PNG", size=(101, 10)), policy)
    assert exc_info.value.details["width"] == 101
    assert exc_info.value.details["max_dimension"] == 100
    # The boundary itself is allowed.
    assert process_image(solid("PNG", size=(100, 10)), policy).original.width == 100


def test_pixel_count_over_max_pixels_is_rejected() -> None:
    policy = ImagePolicy(max_pixels=1000, max_dimension=16_383, max_stored_dimension=4096, thumbnail_size=64)
    with pytest.raises(ImageTooLargeError) as exc_info:
        process_image(solid("WEBP", size=(40, 30)), policy)  # 1200 px
    assert exc_info.value.details["max_pixels"] == 1000
    assert process_image(solid("WEBP", size=(40, 25)), policy).original.width == 40  # exactly 1000 px


def test_jpeg_with_forged_huge_dimensions_is_rejected() -> None:
    small = solid("JPEG", size=(16, 16))
    sof = small.index(b"\xff\xc0")
    forged = small[: sof + 5] + struct.pack(">HH", 60_000, 60_000) + small[sof + 9 :]
    with pytest.raises(ImageTooLargeError):
        process_image(forged, DEFAULT_POLICY)


# --- Metadata stripping ------------------------------------------------------------------


def _camera_exif(orientation: int) -> Image.Exif:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    exif[ExifTags.Base.Make] = "SecretCam"
    exif[ExifTags.Base.Model] = "SerialNo-12345"
    exif[ExifTags.IFD.GPSInfo] = {
        ExifTags.GPS.GPSLatitudeRef: "N",
        ExifTags.GPS.GPSLatitude: (3.0, 8.0, 21.0),
        ExifTags.GPS.GPSLongitudeRef: "E",
        ExifTags.GPS.GPSLongitude: (101.0, 41.0, 11.0),
    }
    return exif


def test_jpeg_exif_and_gps_are_stripped_and_orientation_applied() -> None:
    source = encode(Image.new("RGB", (80, 40), (10, 200, 10)), "JPEG", exif=_camera_exif(6).tobytes())
    assert decode(source).getexif().get_ifd(ExifTags.IFD.GPSInfo)  # sanity: GPS really present

    result = process_image(source, DEFAULT_POLICY)
    original, thumbnail = assert_clean_output(result)

    assert original.size == (40, 80)  # rotated: width/height swapped
    assert (result.original.width, result.original.height) == (40, 80)
    assert thumbnail.size == (40, 80)
    assert dict(original.getexif()) == {}
    assert original.getexif().get_ifd(ExifTags.IFD.GPSInfo) == {}
    assert "exif" not in original.info
    for blob in (result.original.data, result.thumbnail.data):
        assert b"Exif\x00\x00" not in blob
        assert b"SecretCam" not in blob
        assert b"SerialNo-12345" not in blob


def _marked_image(size: tuple[int, int] = (80, 40)) -> Image.Image:
    image = Image.new("RGB", size, (0, 0, 255))
    ImageDraw.Draw(image).rectangle((0, 0, 15, 15), fill=(255, 0, 0))
    return image


def _red_corners(image: Image.Image) -> list[str]:
    rgb = image.convert("RGB")
    w, h = rgb.size
    probes = {"tl": (4, 4), "tr": (w - 5, 4), "bl": (4, h - 5), "br": (w - 5, h - 5)}
    return [name for name, point in probes.items() if rgb.getpixel(point)[0] > 150]


@pytest.mark.parametrize(
    ("orientation", "size", "corner"),
    [
        (1, (80, 40), "tl"),
        (2, (80, 40), "tr"),
        (3, (80, 40), "br"),
        (4, (80, 40), "bl"),
        (5, (40, 80), "tl"),
        (6, (40, 80), "tr"),
        (7, (40, 80), "br"),
        (8, (40, 80), "bl"),
    ],
)
def test_every_exif_orientation_is_baked_into_the_pixels(orientation: int, size: tuple[int, int], corner: str) -> None:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    source = encode(_marked_image(), "JPEG", quality=95, exif=exif.tobytes())
    result = process_image(source, DEFAULT_POLICY)
    original, thumbnail = assert_clean_output(result)
    assert original.size == size
    assert _red_corners(original) == [corner]
    assert _red_corners(thumbnail) == [corner]


def test_jpeg_comment_is_removed() -> None:
    source = encode(Image.new("RGB", (32, 32)), "JPEG", comment=b"TOP-SECRET-COMMENT")
    assert decode(source).info.get("comment") == b"TOP-SECRET-COMMENT"
    result = process_image(source, DEFAULT_POLICY)
    assert_clean_output(result)
    assert b"TOP-SECRET-COMMENT" not in result.original.data
    assert "comment" not in decode(result.original.data).info


XMP_PACKET = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" photoshop:City="SECRET-CITY"/>'
    b"</rdf:RDF></x:xmpmeta>"
)


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_xmp_is_removed(fmt: str) -> None:
    source = encode(Image.new("RGB", (32, 32), (1, 2, 3)), fmt, xmp=XMP_PACKET)
    assert b"SECRET-CITY" in source
    result = process_image(source, DEFAULT_POLICY)
    assert_clean_output(result)
    assert b"SECRET-CITY" not in result.original.data
    assert b"SECRET-CITY" not in result.thumbnail.data
    assert "xmp" not in decode(result.original.data).info


def test_png_text_itxt_ztxt_xmp_and_exif_chunks_are_removed() -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "SECRET-AUTHOR")
    info.add_text("Hidden", "SECRET-ZTXT", zip=True)
    info.add_itxt("Comment", "SECRET-ITXT", lang="en", tkey="c")
    info.add_itxt("XML:com.adobe.xmp", XMP_PACKET.decode())
    source = encode(Image.new("RGB", (40, 20)), "PNG", pnginfo=info, exif=_camera_exif(6).tobytes())
    assert {b"tEXt", b"zTXt", b"iTXt", b"eXIf"} <= set(png_chunk_types(source))

    result = process_image(source, DEFAULT_POLICY)
    original, _ = assert_clean_output(result)
    assert original.text == {}
    assert dict(original.getexif()) == {}
    assert original.size == (20, 40)  # PNG eXIf orientation honoured too
    assert b"SECRET" not in result.original.data


def test_webp_exif_is_removed_and_orientation_applied() -> None:
    source = encode(Image.new("RGB", (80, 40)), "WEBP", exif=_camera_exif(6).tobytes())
    assert b"EXIF" in webp_chunk_types(source)
    result = process_image(source, DEFAULT_POLICY)
    original, _ = assert_clean_output(result)
    assert original.size == (40, 80)
    assert "exif" not in original.info
    assert b"SecretCam" not in result.original.data


def test_malformed_exif_does_not_reject_an_otherwise_valid_photo() -> None:
    clean = encode(Image.new("RGB", (80, 40)), "JPEG")
    bogus_tiff = b"MM\x00\x2a\x00\x00\x00\x08\xff\xff" + b"\x00" * 10  # claims 65535 IFD entries
    app1 = b"\xff\xe1" + struct.pack(">H", 2 + 6 + len(bogus_tiff)) + b"Exif\x00\x00" + bogus_tiff
    source = clean[:2] + app1 + clean[2:]
    with pytest.warns(UserWarning):  # Pillow complains about the corrupt EXIF, but the photo is fine
        result = process_image(source, DEFAULT_POLICY)
    original, _ = assert_clean_output(result)
    assert original.size == (80, 40)


# --- ICC colour profiles -------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_icc_profile_is_applied_but_never_copied_to_outputs(fmt: str) -> None:
    # Pixels are converted to sRGB; the (attacker-controlled) profile bytes are not embedded.
    source = encode(Image.new("RGB", (30, 20), (40, 120, 200)), fmt, icc_profile=SRGB_ICC)
    result = process_image(source, DEFAULT_POLICY)
    original, thumbnail = assert_clean_output(result)
    assert original.info.get("icc_profile") is None
    assert thumbnail.info.get("icc_profile") is None
    red, green, blue = original.convert("RGB").getpixel((15, 10))
    assert abs(red - 40) <= 6 and abs(green - 120) <= 6 and abs(blue - 200) <= 6


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP", "PNG"])
def test_zip_payload_disguised_as_icc_profile_is_not_stored(fmt: str) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("evil/hello.txt", "polyglot")
    source = encode(Image.new("RGB", (30, 20), (1, 2, 3)), fmt, icc_profile=payload.getvalue())
    result = process_image(source, DEFAULT_POLICY)
    original, thumbnail = assert_clean_output(result)
    for data in (result.original.data, result.thumbnail.data):
        assert b"PK" not in data and b"evil/hello.txt" not in data
        assert not zipfile.is_zipfile(io.BytesIO(data))
    assert original.info.get("icc_profile") is None
    assert thumbnail.info.get("icc_profile") is None


def test_cmyk_jpeg_is_converted_to_rgb_and_its_profile_dropped() -> None:
    cmyk_icc = relabelled_icc(b"CMYK")
    source = encode(Image.new("CMYK", (30, 20), (0, 255, 255, 0)), "JPEG", icc_profile=cmyk_icc)
    assert decode(source).mode == "CMYK"
    result = process_image(source, DEFAULT_POLICY)
    original, thumbnail = assert_clean_output(result)
    assert original.mode == "RGB"
    assert original.info.get("icc_profile") is None
    assert thumbnail.info.get("icc_profile") is None
    red, green, blue = original.getpixel((15, 10))
    assert red > 200 and green < 60 and blue < 60  # C=0 M=100% Y=100% K=0 is red


def test_greyscale_jpeg_stays_greyscale_without_a_profile() -> None:
    grey_icc = relabelled_icc(b"GRAY")
    source = encode(Image.new("L", (30, 20), 90), "JPEG", icc_profile=grey_icc)
    result = process_image(source, DEFAULT_POLICY)
    original, thumbnail = assert_clean_output(result)
    assert original.mode == "L"
    assert original.info.get("icc_profile") is None
    assert thumbnail.mode == "RGB"
    assert thumbnail.info.get("icc_profile") is None


# --- Image modes -------------------------------------------------------------------------


def _palette_with_transparency() -> bytes:
    image = Image.new("P", (16, 16), 0)
    image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    image.paste(1, (8, 8, 16, 16))
    return encode(image, "PNG", transparency=0)


def _rgb_colour_key() -> bytes:
    image = Image.new("RGB", (16, 16), (10, 20, 30))
    image.paste((1, 2, 3), (0, 0, 8, 8))
    return encode(image, "PNG", transparency=(1, 2, 3))


def _grey_colour_key() -> bytes:
    rows = b"".join(b"\x00" + bytes(7 if x < 8 and y < 8 else 200 for x in range(16)) for y in range(16))
    return raw_png(16, 16, bit_depth=8, color_type=0, scanlines=rows, extra_chunks=((b"tRNS", struct.pack(">H", 7)),))


def _animated_webp() -> bytes:
    frames = [Image.new("RGB", (40, 30), color) for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    return encode(frames[0], "WEBP", save_all=True, append_images=frames[1:], duration=100, lossless=True)


def _animated_png() -> bytes:
    frames = [Image.new("RGB", (40, 30), color) for color in ((255, 0, 0), (0, 255, 0))]
    return encode(frames[0], "PNG", save_all=True, append_images=frames[1:])


MODE_CASES = {
    "png-rgba": (lambda: solid("PNG", mode="RGBA", color=(10, 20, 30, 128)), PNG, {"RGBA"}),
    "png-la": (lambda: solid("PNG", mode="LA", color=(100, 50)), PNG, {"LA"}),
    "png-p-transparency": (_palette_with_transparency, PNG, {"RGBA"}),
    "png-rgb-colour-key": (_rgb_colour_key, PNG, {"RGBA"}),
    "png-grey-colour-key": (_grey_colour_key, PNG, {"RGBA", "LA"}),
    "png-p-opaque": (lambda: solid("PNG", mode="P", color=3), PNG, {"RGB"}),
    "png-16bit-grey": (lambda: solid("PNG", mode="I;16", color=0x8000), PNG, {"L"}),
    "png-1bit": (lambda: solid("PNG", mode="1", color=1), PNG, {"L", "RGB"}),
    "png-grey": (lambda: solid("PNG", mode="L", color=77), PNG, {"L"}),
    "png-animated": (_animated_png, PNG, {"RGB"}),
    "jpeg-grey": (lambda: solid("JPEG", mode="L", color=77), JPEG, {"L"}),
    "jpeg-progressive": (lambda: solid("JPEG", progressive=True), JPEG, {"RGB"}),
    "jpeg-cmyk": (lambda: solid("JPEG", mode="CMYK", color=(0, 0, 0, 0)), JPEG, {"RGB"}),
    "jpeg-subsampling-444": (lambda: solid("JPEG", subsampling=0, quality=100), JPEG, {"RGB"}),
    "webp-lossy": (lambda: solid("WEBP"), WEBP, {"RGB"}),
    "webp-lossless-alpha": (lambda: solid("WEBP", mode="RGBA", color=(1, 2, 3, 4), lossless=True), WEBP, {"RGBA"}),
    "webp-animated": (_animated_webp, WEBP, {"RGB"}),
}


@pytest.mark.parametrize("case", sorted(MODE_CASES))
def test_image_modes_are_accepted_and_normalised(case: str) -> None:
    factory, expected_format, expected_modes = MODE_CASES[case]
    policy = ImagePolicy(max_pixels=50_000_000, max_dimension=16_383, max_stored_dimension=4096, thumbnail_size=64)
    result = process_image(factory(), policy)
    assert result.format is expected_format
    original, thumbnail = assert_clean_output(result, policy)
    assert original.mode in expected_modes
    assert thumbnail.mode in {"RGB", "RGBA"}


def _alpha(image: Image.Image, point: tuple[int, int]) -> int:
    return image.convert("RGBA").getpixel(point)[3]


@pytest.mark.parametrize("case", ["png-p-transparency", "png-rgb-colour-key", "png-grey-colour-key"])
def test_palette_and_colour_key_transparency_survive_as_real_alpha(case: str) -> None:
    factory, _, _ = MODE_CASES[case]
    result = process_image(factory(), DEFAULT_POLICY)
    original, thumbnail = assert_clean_output(result)
    assert "transparency" not in original.info  # baked into an alpha channel instead
    assert _alpha(original, (2, 2)) == 0
    assert _alpha(original, (12, 12)) == 255
    assert _alpha(thumbnail, (2, 2)) == 0
    assert _alpha(thumbnail, (12, 12)) == 255


def test_rgba_alpha_values_survive() -> None:
    result = process_image(solid("PNG", size=(8, 8), mode="RGBA", color=(10, 20, 30, 128)), DEFAULT_POLICY)
    original, thumbnail = assert_clean_output(result)
    assert original.getpixel((4, 4)) == (10, 20, 30, 128)
    assert abs(_alpha(thumbnail, (4, 4)) - 128) <= 2


def test_16bit_greyscale_is_scaled_not_clipped() -> None:
    result = process_image(solid("PNG", size=(8, 8), mode="I;16", color=0x8000), DEFAULT_POLICY)
    original, _ = assert_clean_output(result)
    assert original.mode == "L"
    assert original.getpixel((4, 4)) == 128  # 0x8000 / 256, not saturated to 255


def _grey16_with_colour_key(key: int = 1000) -> bytes:
    """16x16 16-bit greyscale PNG with a tRNS key.

    top-left 8x8  = the key (must become transparent)
    bottom-right  = 0xE8E8, whose 8-bit value 232 equals ``key & 0xFF`` (must stay opaque)
    elsewhere     = 0x8000 (opaque)
    Built by hand: Pillow's own I;16 paste() does not write exact 16-bit values.
    """

    def value(x: int, y: int) -> int:
        if x < 8 and y < 8:
            return key
        if x >= 8 and y >= 8:
            return 0xE8E8
        return 0x8000

    rows = b"".join(b"\x00" + b"".join(struct.pack(">H", value(x, y)) for x in range(16)) for y in range(16))
    return raw_png(16, 16, bit_depth=16, color_type=0, scanlines=rows, extra_chunks=((b"tRNS", struct.pack(">H", key)),))


def test_16bit_greyscale_colour_key_transparency_survives() -> None:
    source = _grey16_with_colour_key(1000)
    decoded = decode(source)
    assert decoded.mode == "I;16" and decoded.info.get("transparency") == 1000
    assert decoded.getpixel((2, 2)) == 1000 and decoded.getpixel((12, 12)) == 0xE8E8
    result = process_image(source, DEFAULT_POLICY)
    original = decode(result.original.data)
    assert _alpha(original, (12, 2)) == 255  # background
    assert _alpha(original, (2, 2)) == 0  # the colour-keyed pixels must be transparent
    assert _alpha(original, (12, 12)) == 255  # a different 16-bit value must not become transparent


def test_cmyk_and_progressive_jpegs_are_reencoded_as_baseline_safe_rgb() -> None:
    result = process_image(solid("JPEG", mode="CMYK", color=(0, 0, 0, 0), progressive=True), DEFAULT_POLICY)
    original, _ = assert_clean_output(result)
    assert original.mode == "RGB"
    red, green, blue = original.getpixel((10, 10))
    assert min(red, green, blue) > 230  # no ink = white (not inverted)


@pytest.mark.parametrize("factory", [_animated_webp, _animated_png], ids=["webp", "apng"])
def test_animated_images_keep_only_the_first_frame(factory: Any) -> None:
    source = factory()
    assert decode(source).n_frames > 1
    result = process_image(source, DEFAULT_POLICY)
    original, _ = assert_clean_output(result)
    assert getattr(original, "n_frames", 1) == 1
    red, green, blue = original.convert("RGB").getpixel((5, 5))
    assert red > 200 and green < 60 and blue < 60  # first frame is red


# --- Resizing ----------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_max_stored_dimension_downscales_long_edge(fmt: str) -> None:
    policy = ImagePolicy(max_pixels=50_000_000, max_dimension=16_383, max_stored_dimension=256, thumbnail_size=64)
    result = process_image(solid(fmt, size=(1000, 500)), policy)
    original, thumbnail = assert_clean_output(result, policy)
    assert original.size == (256, 128)
    assert thumbnail.size == (64, 32)


def test_large_jpeg_uses_reduced_decode_and_still_hits_exact_size() -> None:
    policy = ImagePolicy(max_pixels=50_000_000, max_dimension=16_383, max_stored_dimension=256, thumbnail_size=64)
    result = process_image(solid("JPEG", size=(2000, 1000)), policy)
    original, _ = assert_clean_output(result, policy)
    assert original.size == (256, 128)


def test_max_stored_dimension_zero_keeps_original_size() -> None:
    policy = ImagePolicy(max_pixels=50_000_000, max_dimension=16_383, max_stored_dimension=0, thumbnail_size=64)
    result = process_image(solid("PNG", size=(1000, 500)), policy)
    original, thumbnail = assert_clean_output(result, policy)
    assert original.size == (1000, 500)
    assert thumbnail.size == (64, 32)


def test_small_images_are_never_upscaled() -> None:
    result = process_image(solid("PNG", size=(10, 5)), DEFAULT_POLICY)
    original, thumbnail = assert_clean_output(result)
    assert original.size == (10, 5)
    assert thumbnail.size == (10, 5)


def test_extreme_aspect_ratio_does_not_collapse_to_zero() -> None:
    policy = ImagePolicy(max_pixels=50_000_000, max_dimension=16_383, max_stored_dimension=256, thumbnail_size=64)
    result = process_image(solid("PNG", size=(4000, 1)), policy)
    original, thumbnail = assert_clean_output(result, policy)
    assert original.size == (256, 1)
    assert thumbnail.size == (64, 1)


# --- Polyglots ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [b"<script>alert(1)</script>", b"<?php system($_GET['c']); ?>", _zip_bytes()],
    ids=["html", "php", "zip"],
)
def test_jpeg_polyglot_payload_is_discarded(payload: bytes) -> None:
    source = solid("JPEG", size=(30, 20)) + payload
    result = process_image(source, DEFAULT_POLICY)
    assert result.format is JPEG
    assert_clean_output(result)
    for blob in (result.original.data, result.thumbnail.data):
        assert payload not in blob
        assert b"<script" not in blob and b"<?php" not in blob and b"PK\x03\x04" not in blob
    assert result.original.data.endswith(b"\xff\xd9")  # nothing trails the EOI marker


def test_png_polyglot_with_payload_in_text_chunk_and_trailer_is_discarded() -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("x", "<?php system($_GET['c']); ?>")
    source = encode(Image.new("RGB", (4, 4)), "PNG", pnginfo=info) + b"<script>alert(1)</script>"
    result = process_image(source, DEFAULT_POLICY)
    assert_clean_output(result)
    assert b"<?php" not in result.original.data
    assert b"<script" not in result.original.data
    assert png_chunk_types(result.original.data)[-1] == b"IEND"


# --- Real-world JPEG variants --------------------------------------------------------------


def test_jpeg_with_mpf_secondary_image_is_accepted_as_jpeg() -> None:
    main = Image.new("RGB", (64, 48), (255, 0, 0))
    preview = Image.new("RGB", (32, 24), (0, 255, 0))
    source = encode(main, "MPO", save_all=True, append_images=[preview])
    assert sniff_image_format(source[:64]) is JPEG  # it *is* a JPEG file (FFD8FF + APP2 "MPF")
    result = process_image(source, DEFAULT_POLICY)
    assert result.format is JPEG
    original, _ = assert_clean_output(result)
    assert original.size == (64, 48)


# --- MimeDetector (libmagic cross-check) ---------------------------------------------------


def test_mime_detector_off_is_unavailable_and_detects_nothing() -> None:
    detector = MimeDetector("off")
    assert detector.available is False
    assert detector.detect(solid("JPEG")) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific safety behaviour")
def test_mime_detector_auto_never_loads_python_magic_on_windows() -> None:
    assert "magic" not in sys.modules
    detector = MimeDetector("auto")
    assert detector.available is False
    assert "magic" not in sys.modules


class _FakeCookie:
    """Stands in for a python-magic ``Magic`` instance."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.seen_lengths: list[int] = []

    def from_buffer(self, data: bytes) -> str:
        self.seen_lengths.append(len(data))
        return self.answer


def _detector_with(answer: str) -> tuple[MimeDetector, _FakeCookie]:
    detector = MimeDetector("off")
    cookie = _FakeCookie(answer)
    detector._magic = cookie  # inject: never load the real libmagic in tests
    return detector, cookie


def test_libmagic_mismatch_is_rejected_as_unsupported() -> None:
    detector, cookie = _detector_with("text/x-php")
    assert detector.available is True
    with pytest.raises(UnsupportedMediaTypeError) as exc_info:
        process_image(solid("JPEG"), DEFAULT_POLICY, detector)
    assert exc_info.value.status_code == 415
    assert cookie.seen_lengths  # the cross-check actually ran


def test_libmagic_agreement_is_accepted_and_only_a_bounded_prefix_is_inspected() -> None:
    detector, cookie = _detector_with("image/png")
    big_png = encode(Image.effect_noise((200, 200), 80).convert("RGB"), "PNG")
    assert len(big_png) > 8192
    result = process_image(big_png, DEFAULT_POLICY, detector)
    assert result.format is PNG
    assert cookie.seen_lengths == [8192]


def test_libmagic_is_not_consulted_for_files_with_no_allowed_signature() -> None:
    detector, cookie = _detector_with("image/jpeg")  # a lying detector cannot rescue a GIF
    with pytest.raises(UnsupportedMediaTypeError):
        process_image(solid("GIF", mode="P", color=1), DEFAULT_POLICY, detector)
    assert cookie.seen_lengths == []


@pytest.fixture
def fake_magic_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a stub ``magic`` module so ``import magic`` can never reach python-magic."""
    module = types.ModuleType("magic")
    monkeypatch.setitem(sys.modules, "magic", module)
    assert sys.modules["magic"] is module
    return module


def test_mime_detector_on_fails_loudly_when_libmagic_cannot_load(fake_magic_module: types.ModuleType) -> None:
    def broken_magic(**kwargs: Any) -> Any:
        raise OSError("libmagic not found")

    fake_magic_module.Magic = broken_magic
    with pytest.raises(RuntimeError, match="libmagic"):
        MimeDetector("on")


def test_mime_detector_auto_degrades_gracefully_off_windows(
    fake_magic_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_magic(**kwargs: Any) -> Any:
        raise OSError("libmagic not found")

    fake_magic_module.Magic = broken_magic
    monkeypatch.setattr(image_processing, "sys", types.SimpleNamespace(platform="linux"))
    assert MimeDetector("auto").available is False


def test_mime_detector_auto_uses_libmagic_off_windows(
    fake_magic_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie = _FakeCookie("image/webp")
    calls: list[dict[str, Any]] = []

    def make_magic(**kwargs: Any) -> _FakeCookie:
        calls.append(kwargs)
        return cookie

    fake_magic_module.Magic = make_magic
    monkeypatch.setattr(image_processing, "sys", types.SimpleNamespace(platform="linux"))
    detector = MimeDetector("auto")
    assert detector.available is True
    assert calls == [{"mime": True}]
    assert process_image(solid("WEBP"), DEFAULT_POLICY, detector).format is WEBP
