"""Image validation and sanitisation.

The upload pipeline never trusts the client-supplied filename, extension or
Content-Type. Every file goes through these layers:

1. **Magic-byte sniffing**: the leading bytes must be a JPEG, PNG or WebP signature.
2. **Optional libmagic cross-check** (POSIX only, see ``MimeDetector``).
3. **Cheap inspection** (``inspect_image``): dimensions are read from the file
   header and checked against the pixel limits before any pixel buffer is
   allocated. WebP headers are parsed by hand because Pillow's WebP decoder
   allocates full canvases when the file is opened.
4. **Restricted decode**: Pillow may use only the plugin for the sniffed format
   (``formats=[...]``). The whole image must decode.
5. **Re-encode** (``process_image``): the pixels are written to a fresh file,
   converted to sRGB. **No source bytes are copied**, including EXIF/GPS, XMP,
   comments, ICC profiles and trailing or polyglot payloads. The stored copy is
   size-capped, so small but highly compressible inputs cannot turn into huge
   files on disk.

These are pure, CPU-bound functions. Call them from a worker thread.
"""

from __future__ import annotations

import io
import sys
import threading
from dataclasses import dataclass

from PIL import Image, ImageCms, ImageMath, ImageOps

from app.core.errors import (
    AppError,
    ImageTooLargeError,
    InvalidImageError,
    UnsupportedMediaTypeError,
)
from app.core.logging import get_logger

logger = get_logger("images")


@dataclass(frozen=True, slots=True)
class ImageFormat:
    key: str
    pil_format: str
    mime: str
    extension: str


JPEG = ImageFormat("jpeg", "JPEG", "image/jpeg", "jpg")
PNG = ImageFormat("png", "PNG", "image/png", "png")
WEBP = ImageFormat("webp", "WEBP", "image/webp", "webp")

ALLOWED_FORMATS: tuple[ImageFormat, ...] = (JPEG, PNG, WEBP)
ALLOWED_MIME_TYPES: tuple[str, ...] = tuple(fmt.mime for fmt in ALLOWED_FORMATS)
MEDIA_TYPES_BY_EXTENSION: dict[str, str] = {fmt.extension: fmt.mime for fmt in ALLOWED_FORMATS}
THUMBNAIL_FORMAT = WEBP

HEADER_BYTES = 64

# Pillow reports JPEGs that carry an MPF index of several images (camera
# previews, HDR gain maps) as "MPO". They are ordinary JPEGs to every browser;
# only the primary image is decoded and re-encoded.
_PIL_FORMATS_ACCEPTED = {"JPEG": {"JPEG", "MPO"}, "PNG": {"PNG"}, "WEBP": {"WEBP"}}

_JPEG_QUALITY = 88
_WEBP_QUALITY = 85
_FALLBACK_QUALITY = 72
_THUMBNAIL_QUALITY = 80
_MAX_ICC_BYTES = 1024 * 1024


# --- Format detection -----------------------------------------------------------------


def sniff_image_format(header: bytes) -> ImageFormat | None:
    """Identify an allowed image format from its leading bytes."""
    if header.startswith(b"\xff\xd8\xff"):
        return JPEG
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if (
        len(header) >= 16
        and header[:4] == b"RIFF"
        and header[8:12] == b"WEBP"
        and header[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
    ):
        return WEBP
    return None


_ISO_BMFF_BRANDS = {
    b"heic": "HEIC", b"heix": "HEIC", b"hevc": "HEIC", b"hevx": "HEIC",
    b"heim": "HEIC", b"heis": "HEIC", b"mif1": "HEIF", b"msf1": "HEIF",
    b"avif": "AVIF", b"avis": "AVIF",
}


def describe_unsupported_format(header: bytes) -> str | None:
    """Best-effort name for a rejected file type, used to give a helpful error."""
    if header[4:8] == b"ftyp":
        return _ISO_BMFF_BRANDS.get(header[8:12], "video")
    if header[:6] in {b"GIF87a", b"GIF89a"}:
        return "GIF"
    if header[:4] in {b"II*\x00", b"MM\x00*"}:
        return "TIFF"
    if header[:2] == b"BM":
        return "BMP"
    if header[:4] == b"%PDF":
        return "PDF"
    if header[:4] == b"PK\x03\x04":
        return "ZIP"
    text = header.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith((b"<svg", b"<?xml")):
        return "SVG/XML"
    if text.startswith((b"<!doctype", b"<html", b"<script")):
        return "HTML"
    if text.startswith(b"<?php"):
        return "PHP"
    return None


def _unsupported_error(detected: str | None) -> UnsupportedMediaTypeError:
    if detected in {"HEIC", "HEIF"}:
        message = (
            "HEIC/HEIF photos are not supported. Please upload a JPEG, PNG or WebP image "
            "(on iPhone: Settings > Camera > Formats > Most Compatible)."
        )
    elif detected:
        message = f"{detected} files are not supported. Only JPEG, PNG and WebP images are accepted."
    else:
        message = "Unrecognised file type. Only JPEG, PNG and WebP images are accepted."
    return UnsupportedMediaTypeError(
        message, details={"allowed_types": list(ALLOWED_MIME_TYPES), "detected": detected}
    )


def webp_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read the canvas size from a WebP header without decoding anything."""
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


class MimeDetector:
    """Optional libmagic second opinion.

    In ``auto`` mode python-magic is never imported on Windows: its loader
    probes DLLs on ``PATH`` and can load MSYS/Cygwin builds (for example from
    Git for Windows), which hard-crashes the interpreter instead of raising.
    The built-in signature check and the restricted Pillow decode are
    authoritative either way.
    """

    def __init__(self, mode: str) -> None:
        self._magic = None
        self._lock = threading.Lock()
        if mode == "off" or (mode == "auto" and sys.platform == "win32"):
            return
        try:
            import magic  # noqa: PLC0415 - optional dependency, imported lazily on purpose

            self._magic = magic.Magic(mime=True)
        except Exception as exc:  # ImportError when libmagic is missing, OSError on load failure
            if mode == "on":
                raise RuntimeError("GK_LIBMAGIC=on but libmagic could not be loaded") from exc
            logger.warning("libmagic unavailable (%s); relying on built-in signature checks", exc)

    @property
    def available(self) -> bool:
        return self._magic is not None

    def detect(self, data: bytes) -> str | None:
        if self._magic is None:
            return None
        with self._lock:  # a libmagic cookie is not thread-safe
            return self._magic.from_buffer(data[:8192])


# --- Policy & results -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImagePolicy:
    max_pixels: int
    max_dimension: int
    max_stored_dimension: int
    thumbnail_size: int
    # PNG and WebP always decode at full resolution, and a tiny lossless file can
    # declare an enormous canvas, so they get a lower pixel budget than JPEG.
    # ``None`` uses ``max_pixels``.
    max_lossless_pixels: int | None = None
    # Upper bound for the stored display copy. Re-encoding can inflate
    # highly compressible inputs.
    max_stored_bytes: int = 10 * 1024 * 1024

    def pixel_limit(self, fmt: ImageFormat) -> int:
        if fmt is JPEG or self.max_lossless_pixels is None:
            return self.max_pixels
        return min(self.max_pixels, self.max_lossless_pixels)


@dataclass(frozen=True, slots=True)
class ImageInfo:
    format: ImageFormat
    width: int
    height: int

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class EncodedImage:
    data: bytes
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    format: ImageFormat  # format of the *stored* copy (lossy fallback may change it)
    original: EncodedImage
    thumbnail: EncodedImage


# --- Inspection ---------------------------------------------------------------------------


def inspect_image(data: bytes, policy: ImagePolicy, mime_detector: MimeDetector | None = None) -> ImageInfo:
    """Identify the format and dimensions without allocating pixel buffers."""
    if not data:
        raise InvalidImageError("The uploaded file is empty.")

    header = data[:HEADER_BYTES]
    fmt = sniff_image_format(header)
    if fmt is None:
        raise _unsupported_error(describe_unsupported_format(header))

    if mime_detector is not None and mime_detector.available:
        detected_mime = mime_detector.detect(data)
        if detected_mime != fmt.mime:
            logger.info("libmagic mismatch: signature=%s libmagic=%s", fmt.mime, detected_mime)
            raise _unsupported_error(None)

    try:
        if fmt is WEBP:
            size = webp_dimensions(data)
            if size is None:
                raise InvalidImageError()
            width, height = size
            _check_dimensions(fmt, width, height, policy)
            # Full validation happens in process_image, under the processing budget.
            return ImageInfo(fmt, width, height)

        with Image.open(io.BytesIO(data), formats=[fmt.pil_format]) as probe:
            if probe.format not in _PIL_FORMATS_ACCEPTED[fmt.pil_format]:
                raise _unsupported_error(None)
            width, height = probe.size
            _check_dimensions(fmt, width, height, policy)
            probe.verify()
        return ImageInfo(fmt, width, height)
    except AppError:
        raise
    except (Image.DecompressionBombError, MemoryError) as exc:
        raise ImageTooLargeError() from exc
    except Exception as exc:  # noqa: BLE001 - any parser failure means "not a valid image"
        logger.info("Image inspection failed: %s: %s", type(exc).__name__, exc)
        raise InvalidImageError() from exc


def _check_dimensions(fmt: ImageFormat, width: int, height: int, policy: ImagePolicy) -> None:
    if width < 1 or height < 1:
        raise InvalidImageError()
    max_pixels = policy.pixel_limit(fmt)
    if width > policy.max_dimension or height > policy.max_dimension or width * height > max_pixels:
        raise ImageTooLargeError(
            details={"width": width, "height": height, "max_dimension": policy.max_dimension, "max_pixels": max_pixels}
        )


# --- Processing -----------------------------------------------------------------------


def process_image(
    data: bytes,
    policy: ImagePolicy,
    mime_detector: MimeDetector | None = None,
    info: ImageInfo | None = None,
) -> ProcessedImage:
    """Validate ``data`` as an allowed image and return sanitised, re-encoded copies."""
    if info is None:
        info = inspect_image(data, policy, mime_detector)
    try:
        return _reencode(data, info, policy)
    except AppError:
        raise
    except (Image.DecompressionBombError, MemoryError) as exc:
        raise ImageTooLargeError() from exc
    except Exception as exc:  # noqa: BLE001 - any decoder failure means "not a valid image"
        logger.info("Image decode failed: %s: %s", type(exc).__name__, exc)
        raise InvalidImageError() from exc


def _reencode(data: bytes, info: ImageInfo, policy: ImagePolicy) -> ProcessedImage:
    fmt = info.format
    with Image.open(io.BytesIO(data), formats=[fmt.pil_format]) as source:
        if source.format not in _PIL_FORMATS_ACCEPTED[fmt.pil_format]:
            raise _unsupported_error(None)
        if source.size != (info.width, info.height):
            raise InvalidImageError()
        source.load()
        icc_profile = source.info.get("icc_profile")
        _narrow_16bit_colour_key(source, data, fmt)
        _apply_orientation(source)
        image = _normalize_bit_depth(source)
        if policy.max_stored_dimension and max(image.size) > policy.max_stored_dimension:
            image.thumbnail((policy.max_stored_dimension, policy.max_stored_dimension), Image.Resampling.LANCZOS)
        image = _to_srgb(image, icc_profile)

        stored_format, original = _encode_stored(image, fmt, policy)

        thumbnail = image.copy()
        thumbnail.thumbnail((policy.thumbnail_size, policy.thumbnail_size), Image.Resampling.LANCZOS)
        thumbnail = _to_output_mode(thumbnail, THUMBNAIL_FORMAT.pil_format)
        thumb = _encode(thumbnail, THUMBNAIL_FORMAT.pil_format, quality=_THUMBNAIL_QUALITY)

    # Defensive: the encoder output must itself pass the signature check.
    if sniff_image_format(original.data[:HEADER_BYTES]) is not stored_format:
        raise InvalidImageError()
    return ProcessedImage(format=stored_format, original=original, thumbnail=thumb)


def _apply_orientation(image: Image.Image) -> None:
    """Rotate/flip ``image`` in place according to its EXIF Orientation tag."""
    try:
        ImageOps.exif_transpose(image, in_place=True)
    except Exception as exc:  # noqa: BLE001 - malformed EXIF must not reject an otherwise valid photo
        logger.info("Ignoring unreadable EXIF orientation: %s", exc)


def _normalize_bit_depth(image: Image.Image) -> Image.Image:
    """Map 16/32-bit greyscale modes (I;16, I, F) to 8-bit, keeping tRNS transparency exact."""
    if not (image.mode.startswith("I") or image.mode == "F"):
        return image
    key = image.info.get("transparency")
    wide = image.convert("I") if image.mode.startswith("I;16") else image
    grey = wide.point(lambda value: value / 256).convert("L")
    if isinstance(key, int):
        # Build the alpha mask at full precision before the 8-bit reduction.
        alpha = ImageMath.lambda_eval(lambda args: (args["im"] != key) * 255, im=wide).convert("L")
        return Image.merge("LA", (grey, alpha))
    return grey


def _narrow_16bit_colour_key(image: Image.Image, data: bytes, fmt: ImageFormat) -> None:
    """Map a 16-bit RGB PNG colour key onto the 8-bit pixels Pillow decodes.

    Pillow keeps the high byte of each 16-bit sample but the full 16-bit tRNS
    key. The key is narrowed the same way, so matching happens on the high
    bytes. (Exact 16-bit matching would need a custom decoder; the input is rare.)
    """
    key = image.info.get("transparency")
    bit_depth = data[24] if fmt is PNG and len(data) > 24 else 8  # IHDR bit depth
    if bit_depth == 16 and image.mode == "RGB" and isinstance(key, tuple):
        image.info["transparency"] = tuple(channel >> 8 for channel in key)


def _to_srgb(image: Image.Image, icc_profile: bytes | None) -> Image.Image:
    """Convert pixels from their embedded ICC profile to sRGB.

    The profile itself is never copied to the output: it is attacker-controlled
    bytes that could smuggle a payload (e.g. a ZIP polyglot) into stored files.
    Unparseable or mismatched profiles are ignored; the pixels stay as they are.
    """
    if not icc_profile or len(icc_profile) > _MAX_ICC_BYTES:
        return image
    if image.mode in {"P", "PA"}:
        image = image.convert("RGBA" if _has_alpha(image) else "RGB")
    if image.mode not in {"RGB", "RGBA", "CMYK"}:
        return image
    try:
        source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
        converted = ImageCms.profileToProfile(
            image,
            source_profile,
            ImageCms.createProfile("sRGB"),
            outputMode="RGBA" if image.mode == "RGBA" else "RGB",
        )
    except Exception as exc:  # noqa: BLE001 - a bad profile only costs colour accuracy
        logger.info("Ignoring unusable ICC profile: %s", exc)
        return image
    return converted if converted is not None else image


_OUTPUT_MODES = {
    "JPEG": {"RGB", "L"},
    "PNG": {"RGB", "RGBA", "L", "LA"},
    "WEBP": {"RGB", "RGBA"},
}


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA", "PA", "RGBa", "La"} or "transparency" in image.info


def _to_output_mode(image: Image.Image, pil_format: str) -> Image.Image:
    if "transparency" in image.info and image.mode not in {"RGBA", "LA"}:
        # Palette / colour-key transparency lives in ``info``, which _encode clears:
        # bake it into a real alpha channel first.
        image = image.convert("RGBA")
    if image.mode in _OUTPUT_MODES[pil_format]:
        return image
    if pil_format == "JPEG":
        if _has_alpha(image):
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.getchannel("A"))
            return flattened
        return image.convert("RGB")
    return image.convert("RGBA" if _has_alpha(image) else "RGB")


def _encode_stored(image: Image.Image, fmt: ImageFormat, policy: ImagePolicy) -> tuple[ImageFormat, EncodedImage]:
    """Encode the display copy, falling back to a lossy format if it exceeds the size cap."""
    encoded = _encode(_to_output_mode(image, fmt.pil_format), fmt.pil_format)
    if len(encoded.data) <= policy.max_stored_bytes:
        return fmt, encoded

    fallback = WEBP if fmt is WEBP or (fmt is PNG and _has_alpha(image)) else JPEG
    encoded = _encode(_to_output_mode(image, fallback.pil_format), fallback.pil_format, quality=_FALLBACK_QUALITY)
    if len(encoded.data) <= policy.max_stored_bytes:
        logger.info("Stored %s copy exceeded %d bytes; saved as lossy %s", fmt.key, policy.max_stored_bytes, fallback.key)
        return fallback, encoded
    raise ImageTooLargeError(
        "This image is too detailed to store. Please upload a smaller or more compressed photo.",
        details={"max_stored_bytes": policy.max_stored_bytes},
    )


def _encode(image: Image.Image, pil_format: str, *, quality: int | None = None) -> EncodedImage:
    # ``info`` is the only channel through which source metadata (EXIF, XMP,
    # comments, ICC, text chunks, ...) could reach the encoder, so clear it.
    # Transparency has already been baked into an alpha channel by _to_output_mode.
    image.info = {}
    params: dict[str, object] = {}
    if pil_format == "JPEG":
        params.update(quality=quality or _JPEG_QUALITY, optimize=True, progressive=True)
    elif pil_format == "WEBP":
        params.update(quality=quality or _WEBP_QUALITY, method=4)
    elif pil_format == "PNG":
        params.update(compress_level=6)
    buffer = io.BytesIO()
    image.save(buffer, format=pil_format, **params)
    return EncodedImage(data=buffer.getvalue(), width=image.width, height=image.height)
