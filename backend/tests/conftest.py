"""Shared fixtures. Every test gets an isolated storage directory and a fresh app."""

import io
import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import Settings
from app.core.rate_limit import limiter
from app.main import create_app

ORIGIN = "http://localhost:5173"


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Ignore any GK_* variables from the developer's shell and reset rate-limit counters."""
    for key in list(os.environ):
        if key.startswith("GK_"):
            monkeypatch.delenv(key, raising=False)
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def settings_factory(tmp_path: Any) -> Callable[..., Settings]:
    def make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "environment": "test",
            "storage_dir": tmp_path / "storage",
            "min_free_disk_bytes": 0,
            "rate_limit_enabled": False,
            "libmagic": "off",
            "public_app_url": ORIGIN,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return make


@pytest.fixture
def make_client(settings_factory: Callable[..., Settings]) -> Iterator[Callable[..., TestClient]]:
    """Build a started TestClient for an app with the given setting overrides."""
    clients: list[TestClient] = []

    def make(**overrides: Any) -> TestClient:
        client = TestClient(create_app(settings_factory(**overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in reversed(clients):
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


def image_bytes(
    fmt: str = "JPEG",
    size: tuple[int, int] = (64, 48),
    mode: str = "RGB",
    color: Any = (200, 40, 40),
    **save_kwargs: Any,
) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


@pytest.fixture
def create_event(client: TestClient) -> Callable[..., dict[str, Any]]:
    """POST /api/events and return the JSON body ({event, host_token, host_url})."""

    def make(name: str = "Test Event", *, on: TestClient | None = None) -> dict[str, Any]:
        response = (on or client).post("/api/events", json={"name": name})
        assert response.status_code == 201, response.text
        return response.json()

    return make


@pytest.fixture
def upload_photo(client: TestClient) -> Callable[..., Any]:
    """POST a multipart upload; returns the httpx response."""

    def do(
        event_id: str,
        data: bytes | None = None,
        *,
        filename: str = "photo.jpg",
        content_type: str = "image/jpeg",
        fields: dict[str, str] | None = None,
        on: TestClient | None = None,
    ) -> Any:
        payload = image_bytes() if data is None else data
        return (on or client).post(
            f"/api/events/{event_id}/photos",
            files={"file": (filename, payload, content_type)},
            data=fields or {},
        )

    return do


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
