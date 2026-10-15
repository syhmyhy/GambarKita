"""Identifier and secret generation / verification.

Every identifier that ends up in a URL or a filesystem path is generated
server-side from a CSPRNG and validated against a strict ASCII pattern before use.
"""

import hashlib
import hmac
import re
import secrets
import string

EVENT_ID_LENGTH = 12
_EVENT_ID_ALPHABET = string.ascii_lowercase + string.digits  # ~62 bits of entropy at 12 chars

_EVENT_ID_RE = re.compile(r"[a-z0-9]{12}")
_PHOTO_ID_RE = re.compile(r"[a-f0-9]{32}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")


def new_event_id() -> str:
    return "".join(secrets.choice(_EVENT_ID_ALPHABET) for _ in range(EVENT_ID_LENGTH))


def new_photo_id() -> str:
    return secrets.token_hex(16)


def new_host_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # Host tokens carry 256 bits of entropy, so a fast hash is sufficient; the
    # plaintext token is never stored.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def guest_key_hash(event_id: str, guest_key: str) -> str:
    """What the database stores for a guest key: hashed and scoped to the event,
    so stored rows can't link one browser's photos across events or reveal the key."""
    return hash_token(f"{event_id}:{guest_key}")


def verify_token(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash)


def constant_time_equals(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def is_valid_event_id(value: object) -> bool:
    return isinstance(value, str) and _EVENT_ID_RE.fullmatch(value) is not None


def is_valid_photo_id(value: object) -> bool:
    return isinstance(value, str) and _PHOTO_ID_RE.fullmatch(value) is not None


def is_plausible_token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN_RE.fullmatch(value) is not None


def is_valid_guest_key(value: object) -> bool:
    # Same shape as host tokens: the browser makes one from 32 random bytes (base64url).
    return is_plausible_token(value)
