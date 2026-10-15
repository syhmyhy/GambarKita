"""HTTP hardening layer: security headers, CORS, request ids, trusted hosts, body-size
limits, rate limiting, API docs exposure, health/config and settings validation."""

import io
import json
import os
import re
import secrets
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from app.core.config import Settings
from tests.conftest import ORIGIN, auth, image_bytes

EVIL_ORIGIN = "https://evil.example"
GENERATED_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ERROR_KEYS = {"code", "message", "details", "request_id"}
JSON_BODY_LIMIT = 64 * 1024


# --- Helpers ----------------------------------------------------------------------------


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    """Assert the structured error envelope and return its ``error`` object."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == ERROR_KEYS
    assert error["code"] == code, error
    assert isinstance(error["message"], str) and error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    return error


def assert_security_headers(headers: Any, *, api: bool = True) -> None:
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("referrer-policy") == "no-referrer"
    csp = headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp, csp
    assert "frame-ancestors 'none'" in csp, csp
    if api:
        assert headers.get("cache-control") == "no-store"


class RawResponse:
    """Minimal response object for requests driven straight through the ASGI app."""

    def __init__(self, status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
        self.status_code = status
        merged: dict[str, str] = {}
        for name, value in headers:
            key = name.decode("latin-1").lower()
            merged[key] = f"{merged[key]}, {value.decode('latin-1')}" if key in merged else value.decode("latin-1")
        self.headers = merged
        self.content = body

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.content)


def asgi_request(
    client: Any,
    method: str,
    path: str,
    *,
    headers: list[tuple[str, str]] | None = None,
    body: bytes = b"",
    client_addr: tuple[str, int] = ("testclient", 50000),
) -> RawResponse:
    """Send a hand-built request through the app, bypassing httpx header/URL normalisation."""
    raw_headers = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers or []]
    if not any(name == b"host" for name, _ in raw_headers):
        raw_headers.insert(0, (b"host", b"testserver"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "root_path": "",
        "query_string": b"",
        "headers": raw_headers,
        "client": client_addr,
        "server": ("testserver", 80),
        "state": {},
    }

    async def run() -> RawResponse:
        start: dict[str, Any] = {}
        chunks: list[bytes] = []
        sent = False

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await client.app(scope, receive, send)
        return RawResponse(start["status"], list(start.get("headers", [])), b"".join(chunks))

    return client.portal.call(run)


def new_event(client: Any, name: str = "Hardening", **headers: str) -> dict[str, Any]:
    response = client.post("/api/events", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def post_photo(client: Any, event_id: str, data: bytes | None = None, **kwargs: Any) -> Any:
    payload = image_bytes() if data is None else data
    return client.post(
        f"/api/events/{event_id}/photos", files={"file": ("photo.jpg", payload, "image/jpeg")}, **kwargs
    )


def noisy_png(size: tuple[int, int]) -> bytes:
    """An incompressible PNG, useful for exceeding byte thresholds with a valid image."""
    buffer = io.BytesIO()
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(buffer, format="PNG")
    return buffer.getvalue()


def oversized_json() -> bytes:
    return b'{"name": "' + b"x" * (JSON_BODY_LIMIT + 1024) + b'"}'


# --- Security headers ----------------------------------------------------------------------


def _req_health(client, created):
    return client.get("/api/health")


def _req_config(client, created):
    return client.get("/api/config")


def _req_get_event(client, created):
    return client.get(f"/api/events/{created['event']['id']}")


def _req_create(client, created):
    return client.post("/api/events", json={"name": "Headers"})


def _req_verify(client, created):
    return client.post(f"/api/events/{created['event']['id']}/host/verify", headers=auth(created["host_token"]))


def _req_unauthorized(client, created):
    return client.patch(f"/api/events/{created['event']['id']}", json={"name": "x"})


def _req_unknown_event(client, created):
    return client.get("/api/events/zzzzzzzzzzzz")


def _req_unknown_route(client, created):
    return client.get("/api/definitely-not-a-route")


def _req_method_not_allowed(client, created):
    return client.put("/api/events", json={"name": "x"})


def _req_too_large(client, created):
    return client.post("/api/events", content=oversized_json(), headers={"Content-Type": "application/json"})


def _req_validation(client, created):
    return client.post("/api/events", json={})


def _req_unsupported_upload(client, created):
    return client.post(f"/api/events/{created['event']['id']}/photos", json={"file": "x"})


SECURITY_HEADER_CASES = [
    pytest.param(_req_health, 200, None, id="200-health"),
    pytest.param(_req_config, 200, None, id="200-config"),
    pytest.param(_req_get_event, 200, None, id="200-event"),
    pytest.param(_req_create, 201, None, id="201-create"),
    pytest.param(_req_verify, 204, None, id="204-verify"),
    pytest.param(_req_unauthorized, 401, "UNAUTHORIZED", id="401"),
    pytest.param(_req_unknown_event, 404, "EVENT_NOT_FOUND", id="404-event"),
    pytest.param(_req_unknown_route, 404, "NOT_FOUND", id="404-route"),
    pytest.param(_req_method_not_allowed, 405, "METHOD_NOT_ALLOWED", id="405"),
    pytest.param(_req_too_large, 413, "PAYLOAD_TOO_LARGE", id="413"),
    pytest.param(_req_unsupported_upload, 415, "UNSUPPORTED_MEDIA_TYPE", id="415"),
    pytest.param(_req_validation, 422, "VALIDATION_ERROR", id="422"),
]


@pytest.mark.parametrize(("make_request", "status", "code"), SECURITY_HEADER_CASES)
def test_security_headers_on_api_responses(client, create_event, make_request, status, code):
    created = create_event("Headers")

    response = make_request(client, created)

    if code is None:
        assert response.status_code == status, response.text
    else:
        assert_error(response, status, code)
    assert_security_headers(response.headers)
    assert GENERATED_ID_RE.fullmatch(response.headers["x-request-id"])


@pytest.mark.parametrize(("make_request", "status", "code"), SECURITY_HEADER_CASES)
def test_cors_headers_on_api_responses_for_allowed_origin(client, create_event, make_request, status, code):
    created = create_event("Cors")
    client.headers["Origin"] = ORIGIN
    try:
        response = make_request(client, created)
    finally:
        del client.headers["Origin"]

    assert response.status_code == status, response.text
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert "access-control-allow-credentials" not in response.headers


def test_unhandled_exception_becomes_structured_500_without_leaking_details(client, create_event, monkeypatch):
    event_id = create_event("Boom")["event"]["id"]

    async def explode(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("secret-internal-detail at C:\\app\\services\\database.py")

    monkeypatch.setattr(client.app.state.services.db, "count_photos", explode)

    response = client.get(f"/api/events/{event_id}", headers={"Origin": ORIGIN, "X-Request-ID": "trace-500-abcdef"})

    error = assert_error(response, 500, "INTERNAL_ERROR")
    assert error["details"] is None
    assert error["request_id"] == "trace-500-abcdef"
    for leaked in ("secret-internal-detail", "Traceback", "RuntimeError", "database.py"):
        assert leaked not in response.text
    assert_security_headers(response.headers)
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert response.headers["content-type"].startswith("application/json")


def test_health_failure_is_structured_503(client, monkeypatch):
    async def broken_ping() -> bool:
        raise OSError("disk I/O error on C:\\secret\\path")

    monkeypatch.setattr(client.app.state.services.db, "ping", broken_ping)

    response = client.get("/api/health")

    assert_error(response, 503, "SERVICE_UNAVAILABLE")
    assert "secret" not in response.text
    assert_security_headers(response.headers)


def test_security_headers_on_non_api_404(client):
    response = client.get("/definitely-not-here")

    assert_error(response, 404, "NOT_FOUND")
    assert_security_headers(response.headers, api=False)


def test_security_headers_do_not_clobber_media_caching(client, create_event, upload_photo):
    event_id = create_event("Media")["event"]["id"]
    photo = upload_photo(event_id).json()

    response = client.get(photo["url"])

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "no-store" not in response.headers["cache-control"]
    assert "sandbox" in response.headers["content-security-policy"]


def test_hsts_only_when_enabled(make_client):
    plain = make_client()
    assert "strict-transport-security" not in plain.get("/api/health").headers

    hardened = make_client(hsts_enabled=True)
    header = hardened.get("/api/health").headers.get("strict-transport-security", "")
    assert "max-age=31536000" in header
    assert "includeSubDomains" in header


# --- CORS ------------------------------------------------------------------------------------


def preflight(client: Any, origin: str, method: str = "POST", path: str = "/api/events", headers: str = "content-type"):
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


def test_cors_preflight_from_frontend_origin_is_allowed(client):
    response = preflight(client, ORIGIN, headers="content-type, x-event-creation-key, x-request-id")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    for header in ("content-type", "x-event-creation-key", "x-request-id", "authorization"):
        assert header in allowed_headers
    assert "access-control-allow-credentials" not in response.headers
    assert_security_headers(response.headers)


def test_cors_preflight_for_host_actions_with_authorization(client):
    response = preflight(client, ORIGIN, method="PATCH", path="/api/events/abcdefghijkl", headers="authorization")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "DELETE" in response.headers["access-control-allow-methods"]


@pytest.mark.parametrize("origin", [EVIL_ORIGIN, "null", "http://localhost:5174", "https://localhost:5173"])
def test_cors_preflight_from_other_origins_is_refused(client, origin):
    response = preflight(client, origin)

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_for_disallowed_method_is_refused(client):
    response = preflight(client, ORIGIN, method="PUT")

    assert response.status_code == 400


def test_simple_request_from_disallowed_origin_gets_no_cors_headers(client):
    response = client.get("/api/health", headers={"Origin": EVIL_ORIGIN})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_simple_request_from_allowed_origin_exposes_request_id_and_retry_after(client):
    response = client.get("/api/health", headers={"Origin": ORIGIN})

    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "origin" in response.headers["vary"].lower()
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-request-id" in exposed
    assert "retry-after" in exposed


def test_public_app_url_origin_is_allowed_automatically(make_client):
    client = make_client(public_app_url="https://photos.example.com", cors_origins=["http://localhost:5173"])

    assert client.app.state.settings.allowed_origins == ["http://localhost:5173", "https://photos.example.com"]
    for origin in ("https://photos.example.com", "http://localhost:5173"):
        response = preflight(client, origin)
        assert response.status_code == 200, origin
        assert response.headers["access-control-allow-origin"] == origin
    refused = preflight(client, EVIL_ORIGIN)
    assert "access-control-allow-origin" not in refused.headers
    simple = client.get("/api/health", headers={"Origin": "https://photos.example.com"})
    assert simple.headers["access-control-allow-origin"] == "https://photos.example.com"


def test_cors_rejected_origin_list_does_not_include_unlisted_default(make_client):
    client = make_client(public_app_url="https://photos.example.com", cors_origins=["https://admin.example.com"])

    assert preflight(client, "https://admin.example.com").status_code == 200
    assert preflight(client, "https://photos.example.com").status_code == 200
    assert "access-control-allow-origin" not in preflight(client, ORIGIN).headers


# --- API docs exposure -----------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json", "/api/docs/oauth2-redirect"])
def test_docs_are_hidden_in_production(make_client, path):
    client = make_client(environment="production")

    response = client.get(path)

    assert_error(response, 404, "NOT_FOUND")
    assert_security_headers(response.headers)


def test_docs_are_available_in_development(make_client):
    client = make_client(environment="development")

    docs = client.get("/api/docs")
    schema = client.get("/api/openapi.json")

    assert docs.status_code == 200
    assert "text/html" in docs.headers["content-type"]
    # Swagger UI loads its assets, so the docs must not get the strict API CSP...
    assert "default-src 'none'" not in docs.headers.get("content-security-policy", "")
    # ...but still get the other protections.
    assert docs.headers["x-content-type-options"] == "nosniff"
    assert docs.headers["x-frame-options"] == "DENY"
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "GambarKita API"
    assert "/api/events" in schema.json()["paths"]


def test_docs_can_be_toggled_explicitly(make_client):
    assert make_client(environment="production", enable_docs=True).get("/api/openapi.json").status_code == 200
    assert make_client(environment="development", enable_docs=False).get("/api/openapi.json").status_code == 404


# --- X-Request-ID ------------------------------------------------------------------------------


@pytest.mark.parametrize("request_id", ["req-12345678", "abc.DEF_123-xyz", "A" * 64, "01HZX5V3K9Q7J8M2N4P6R8T0W"])
def test_safe_request_id_is_echoed_in_header_and_error_body(client, request_id):
    response = client.get("/api/events/zzzzzzzzzzzz", headers={"X-Request-ID": request_id})

    error = assert_error(response, 404, "EVENT_NOT_FOUND")
    assert response.headers["x-request-id"] == request_id
    assert error["request_id"] == request_id


@pytest.mark.parametrize(
    "request_id",
    [
        "a\r\nb",
        "abcdefgh\r\nSet-Cookie: pwned=1",
        "<script>alert(1)</script>",
        "x" * 200,
        "A" * 65,
        "short",
        "has spaces in it",
        "../../etc/passwd",
        "\u00fcn\u00efcode-id-123",
        "",
    ],
    ids=["crlf", "header-injection", "html", "200-chars", "65-chars", "too-short", "spaces", "traversal", "latin1", "empty"],
)
def test_unsafe_request_id_is_replaced_with_generated_id(client, request_id):
    response = asgi_request(client, "GET", "/api/events/zzzzzzzzzzzz", headers=[("X-Request-ID", request_id)])

    assert response.status_code == 404
    generated = response.headers["x-request-id"]
    assert GENERATED_ID_RE.fullmatch(generated), generated
    assert response.json()["error"]["request_id"] == generated
    assert "set-cookie" not in response.headers
    if request_id:
        assert request_id not in response.text


def test_generated_request_ids_are_unique(client):
    ids = {client.get("/api/health").headers["x-request-id"] for _ in range(5)}

    assert len(ids) == 5
    assert all(GENERATED_ID_RE.fullmatch(value) for value in ids)


# --- Trusted hosts ----------------------------------------------------------------------------


def test_trusted_hosts_rejects_unknown_host_header(make_client):
    client = make_client(trusted_hosts=["api.example.com"])

    rejected = client.get("/api/health", headers={"Host": "evil.example"})
    accepted = client.get("/api/health", headers={"Host": "api.example.com"})

    assert rejected.status_code == 400
    assert "ok" not in rejected.text
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ok"


def test_trusted_hosts_applies_to_every_route(make_client):
    client = make_client(trusted_hosts=["api.example.com"])

    assert client.post("/api/events", json={"name": "x"}, headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/config", headers={"Host": "evil.example:8000"}).status_code == 400


def test_untrusted_host_rejection_uses_error_envelope_and_security_headers(make_client):
    client = make_client(trusted_hosts=["api.example.com"])

    response = client.get("/api/health", headers={"Host": "evil.example"})

    assert_error(response, 400, "BAD_REQUEST")
    assert_security_headers(response.headers)


# --- Body size limits -------------------------------------------------------------------------


def test_json_body_over_limit_is_rejected_from_content_length(client):
    response = client.post(
        "/api/events", content=oversized_json(), headers={"Content-Type": "application/json", "Origin": ORIGIN}
    )

    error = assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    assert error["details"] == {"max_bytes": JSON_BODY_LIMIT}
    assert response.headers.get("connection") == "close"
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert_security_headers(response.headers)


def test_streamed_json_body_over_limit_is_rejected_without_content_length(client):
    def chunks():
        yield b'{"name": "'
        for _ in range(10):
            yield b"x" * 10_000
        yield b'"}'

    response = client.post("/api/events", content=chunks(), headers={"Content-Type": "application/json"})

    assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    assert client.get("/api/health").status_code == 200


def test_patch_body_over_limit_is_rejected_before_authorization(client, create_event):
    event_id = create_event("Big Patch")["event"]["id"]

    response = client.patch(
        f"/api/events/{event_id}", content=oversized_json(), headers={"Content-Type": "application/json"}
    )

    assert_error(response, 413, "PAYLOAD_TOO_LARGE")


@pytest.mark.parametrize("content_length", ["abc", "-1", "1e3", "+10", " 10", "9" * 25])
def test_invalid_content_length_is_400(client, content_length):
    response = asgi_request(
        client,
        "POST",
        "/api/events",
        headers=[("Content-Type", "application/json"), ("Content-Length", content_length), ("Origin", ORIGIN)],
        body=b'{"name": "x"}',
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert_security_headers(response.headers)


def test_upload_route_gets_the_larger_body_budget(client, create_event):
    event_id = create_event("Budget")["event"]["id"]
    big_png = noisy_png((200, 200))
    assert len(big_png) > JSON_BODY_LIMIT

    response = client.post(f"/api/events/{event_id}/photos", files={"file": ("big.png", big_png, "image/png")})

    assert response.status_code == 201, response.text


def test_upload_body_over_request_limit_is_rejected_by_middleware(make_client):
    client = make_client(max_upload_bytes=1024)
    event_id = new_event(client)["event"]["id"]
    body = os.urandom(1024 + JSON_BODY_LIMIT + 4096)

    response = client.post(
        f"/api/events/{event_id}/photos",
        files={"file": ("big.jpg", body, "image/jpeg")},
        headers={"Origin": ORIGIN},
    )

    error = assert_error(response, 413, "PAYLOAD_TOO_LARGE")
    assert error["details"] == {"max_bytes": 1024 + JSON_BODY_LIMIT}
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert client.get(f"/api/events/{event_id}").json()["photo_count"] == 0


# --- Rate limiting ------------------------------------------------------------------------------


def assert_rate_limited(response: Any, limit_text: str) -> None:
    error = assert_error(response, 429, "RATE_LIMITED")
    assert error["details"] == {"limit": limit_text}
    retry_after = response.headers["retry-after"]
    assert re.fullmatch(r"[0-9]+", retry_after), retry_after
    assert 1 <= int(retry_after) <= 60
    assert_security_headers(response.headers)


def test_create_event_rate_limit(make_client):
    client = make_client(rate_limit_enabled=True, rate_limit_create_event="2/minute")
    headers = {"Origin": ORIGIN}

    for _ in range(2):
        assert client.post("/api/events", json={"name": "ok"}, headers=headers).status_code == 201
    response = client.post("/api/events", json={"name": "third"}, headers=headers)

    assert_rate_limited(response, "2 per 1 minute")
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "retry-after" in response.headers["access-control-expose-headers"].lower()


def test_rate_limits_are_per_endpoint(make_client):
    client = make_client(rate_limit_enabled=True, rate_limit_create_event="1/minute", rate_limit_read="2/minute")
    event_id = new_event(client)["event"]["id"]
    assert_rate_limited(client.post("/api/events", json={"name": "blocked"}), "1 per 1 minute")

    # Creation is exhausted but reads have their own budget...
    for _ in range(2):
        assert client.get(f"/api/events/{event_id}").status_code == 200
    assert_rate_limited(client.get(f"/api/events/{event_id}"), "2 per 1 minute")

    # ...and each read endpoint is counted separately.
    assert client.get(f"/api/events/{event_id}/photos").status_code == 200
    assert client.get("/api/config").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_upload_rate_limit(make_client):
    client = make_client(rate_limit_enabled=True, rate_limit_upload="2/minute")
    event_id = new_event(client)["event"]["id"]

    for _ in range(2):
        assert post_photo(client, event_id).status_code == 201
    response = post_photo(client, event_id, headers={"Origin": ORIGIN})

    assert_rate_limited(response, "2 per 1 minute")
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert client.get(f"/api/events/{event_id}").json()["photo_count"] == 2


def test_read_rate_limit_on_photo_listing(make_client):
    client = make_client(rate_limit_enabled=True, rate_limit_read="3/minute")
    event_id = new_event(client)["event"]["id"]

    for _ in range(3):
        assert client.get(f"/api/events/{event_id}/photos").status_code == 200

    assert_rate_limited(client.get(f"/api/events/{event_id}/photos"), "3 per 1 minute")


def test_manage_rate_limit_counts_wrong_token_guesses(make_client):
    client = make_client(rate_limit_enabled=True, rate_limit_manage="3/minute")
    created = new_event(client)
    verify_url = f"/api/events/{created['event']['id']}/host/verify"

    for _ in range(3):
        assert_error(client.post(verify_url, headers=auth(secrets.token_urlsafe(32))), 401, "UNAUTHORIZED")

    # Brute-forcing the host token is throttled: even the right token is refused now.
    assert_rate_limited(client.post(verify_url, headers=auth(created["host_token"])), "3 per 1 minute")


def test_creation_key_guesses_are_rate_limited(make_client):
    client = make_client(
        rate_limit_enabled=True, rate_limit_create_event="2/minute", event_creation_key="s3cret-key-123"
    )

    for guess in ("guess-1", "guess-2"):
        response = client.post("/api/events", json={"name": "x"}, headers={"X-Event-Creation-Key": guess})
        assert_error(response, 401, "UNAUTHORIZED")

    response = client.post("/api/events", json={"name": "x"}, headers={"X-Event-Creation-Key": "s3cret-key-123"})
    assert_rate_limited(response, "2 per 1 minute")


def test_rate_limit_is_per_client_ip_and_ignores_forwarded_headers(make_client):
    client = make_client(rate_limit_enabled=True, rate_limit_create_event="1/minute")
    new_event(client)

    spoofed = client.post(
        "/api/events",
        json={"name": "spoof"},
        headers={"X-Forwarded-For": "203.0.113.9", "X-Real-IP": "203.0.113.9", "Forwarded": "for=203.0.113.9"},
    )
    assert_rate_limited(spoofed, "1 per 1 minute")

    body = b'{"name": "other client"}'
    other_client = asgi_request(
        client,
        "POST",
        "/api/events",
        headers=[("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
        body=body,
        client_addr=("198.51.100.7", 40000),
    )
    assert other_client.status_code == 201, other_client.text


def test_rate_limiting_can_be_disabled(make_client):
    client = make_client(rate_limit_enabled=False, rate_limit_create_event="1/minute")

    for _ in range(3):
        assert client.post("/api/events", json={"name": "unlimited"}).status_code == 201


# --- Health & public config ------------------------------------------------------------------


def test_health_reports_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]


def test_public_config_defaults(client):
    body = client.get("/api/config").json()

    assert body == {
        "max_upload_bytes": 10 * 1024 * 1024,
        "allowed_mime_types": ["image/jpeg", "image/png", "image/webp"],
        "max_photos_per_event": 2000,
        "max_photos_per_guest": None,
        "max_event_name_length": 80,
        "max_uploader_name_length": 40,
        "max_caption_length": 140,
        "event_creation_requires_key": False,
    }


def test_public_config_reflects_settings_overrides(make_client):
    client = make_client(max_upload_bytes=2 * 1024 * 1024, max_photos_per_event=7, event_creation_key="k" * 20)

    body = client.get("/api/config").json()

    assert body["max_upload_bytes"] == 2 * 1024 * 1024
    assert body["max_photos_per_event"] == 7
    assert body["event_creation_requires_key"] is True
    assert "k" * 20 not in json.dumps(body)


# --- Settings validation --------------------------------------------------------------------------


def make_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rate_limit_create_event", "lots"),
        ("rate_limit_upload", "10/fortnight"),
        ("rate_limit_read", ""),
        ("rate_limit_manage", "per minute"),
        ("ws_connect_rate", "abc"),
        ("ws_message_rate", "x/second"),
    ],
)
def test_settings_reject_invalid_rate_limit_strings(field, value):
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_settings_accept_multi_rate_limit_strings():
    settings = make_settings(rate_limit_upload="10/minute;100/hour", rate_limit_read="5 per second")

    assert settings.rate_limit_upload == "10/minute;100/hour"


@pytest.mark.parametrize(
    "origin",
    [
        "localhost:5173",
        "http://a.com/path",
        "http://a.com/?x=1",
        "http://a.com/#frag",
        "http://user:pw@a.com",
        "ftp://a.com",
        "javascript:alert(1)",
        "http://",
        "a.com",
    ],
)
def test_settings_reject_invalid_cors_origins(origin):
    with pytest.raises(ValidationError):
        make_settings(cors_origins=[origin])


def test_settings_normalise_cors_origins():
    settings = make_settings(cors_origins=["HTTPS://Photos.Example.COM/", " http://localhost:5173 "])

    assert settings.cors_origins == ["https://photos.example.com", "http://localhost:5173"]


@pytest.mark.parametrize(
    "url",
    ["https://photos.example.com/?x=1", "https://photos.example.com/#top", "photos.example.com", "ftp://a.com", ""],
)
def test_settings_reject_invalid_public_app_url(url):
    with pytest.raises(ValidationError):
        make_settings(public_app_url=url)


def test_settings_strip_trailing_slash_from_public_app_url():
    settings = make_settings(public_app_url="https://photos.example.com/app/", cors_origins=[])

    assert settings.public_app_url == "https://photos.example.com/app"
    assert settings.allowed_origins == ["https://photos.example.com"]


def test_settings_do_not_duplicate_public_app_origin():
    settings = make_settings(public_app_url="http://localhost:5173", cors_origins=["http://localhost:5173"])

    assert settings.allowed_origins == ["http://localhost:5173"]


def test_settings_parse_comma_separated_environment_variables(monkeypatch):
    monkeypatch.setenv("GK_CORS_ORIGINS", "http://a.com, http://b.com")
    monkeypatch.setenv("GK_TRUSTED_HOSTS", "api.example.com , www.example.com,")
    monkeypatch.setenv("GK_RATE_LIMIT_UPLOAD", "5/minute")
    monkeypatch.setenv("GK_RATE_LIMIT_ENABLED", "false")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://a.com", "http://b.com"]
    assert settings.trusted_hosts == ["api.example.com", "www.example.com"]
    assert settings.rate_limit_upload == "5/minute"
    assert settings.rate_limit_enabled is False


def test_empty_creation_key_is_rejected_or_treated_as_unset(monkeypatch):
    monkeypatch.setenv("GK_EVENT_CREATION_KEY", "")

    try:
        settings = Settings(_env_file=None)
    except ValidationError:
        return  # failing fast on the misconfiguration is acceptable
    assert settings.event_creation_key is None


def test_settings_reject_invalid_origin_from_environment(monkeypatch):
    monkeypatch.setenv("GK_CORS_ORIGINS", "http://a.com, localhost:5173")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("field", "value"),
    [("max_upload_bytes", 10), ("max_upload_bytes", 101 * 1024 * 1024), ("port", 0), ("environment", "staging")],
)
def test_settings_reject_out_of_range_values(field, value):
    with pytest.raises(ValidationError):
        make_settings(**{field: value})
