"""Application factory.

Run with ``python run.py`` (recommended: hardened uvicorn settings) or
``uvicorn app.main:create_app --factory``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from PIL import Image
from starlette.middleware.cors import CORSMiddleware

from app import __version__
from app.api import api_router, ws_router
from app.api.routes.photos import UPLOAD_PATH_RE
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import configure_rate_limiting
from app.media import MEDIA_MOUNT_PATH, SecureMediaFiles
from app.middleware.body_limit import BodySizeLimitMiddleware, RequestBodyTooLarge, body_too_large_handler
from app.middleware.errors import UnhandledErrorMiddleware
from app.middleware.host_validation import HostValidationMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.services.container import build_services
from app.services.database import Database
from app.services.janitor import StorageJanitor
from app.services.storage import LocalStorage

logger = get_logger("app")

JSON_BODY_LIMIT = 64 * 1024
DOCS_PREFIX = "/api/docs"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    # Pillow's own decompression-bomb guard (raises at 2x this value); the upload
    # pipeline additionally enforces the exact limit before decoding.
    Image.MAX_IMAGE_PIXELS = settings.max_image_pixels

    storage = LocalStorage(settings.storage_dir)
    storage.ensure_layout()
    db = Database(settings.database_path)
    janitor = StorageJanitor(storage, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = build_services(settings, storage, db, janitor)
        await services.startup()
        app.state.services = services
        logger.info("GambarKita API %s started (environment=%s)", __version__, settings.environment)
        if settings.environment == "production" and "*" in settings.cors_origins:
            logger.warning("GK_CORS_ORIGINS contains '*': restrict it to your frontend origin in production")
        if settings.forwarded_allow_ips and "*" in settings.forwarded_allow_ips.split(","):
            logger.warning(
                "GK_FORWARDED_ALLOW_IPS='*' trusts the client-controlled left-most X-Forwarded-For entry: "
                "anyone can spoof their IP and bypass rate limits. Set it to your proxy's IP/CIDR instead."
            )
        try:
            yield
        finally:
            await services.shutdown()

    docs = settings.docs_enabled
    app = FastAPI(
        title="GambarKita API",
        version=__version__,
        description="QR-code event photo sharing: create events, upload photos, watch the live wall.",
        lifespan=lifespan,
        docs_url=DOCS_PREFIX if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
        swagger_ui_oauth2_redirect_url=f"{DOCS_PREFIX}/oauth2-redirect" if docs else None,
    )
    app.state.settings = settings
    app.state.limiter = configure_rate_limiting(settings)

    register_exception_handlers(app)
    app.add_exception_handler(RequestBodyTooLarge, body_too_large_handler)
    app.include_router(api_router)
    app.include_router(ws_router)
    app.mount(
        MEDIA_MOUNT_PATH,
        SecureMediaFiles(directory=storage.events_root, is_blocked=janitor.is_blocked),
        name="media",
    )

    # Middleware: the last one added is the outermost. Request flow:
    # RequestContext -> SecurityHeaders -> CORS -> HostValidation -> BodySizeLimit -> UnhandledError -> app
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=JSON_BODY_LIMIT,
        upload_limit=settings.max_request_bytes,
        upload_path=UPLOAD_PATH_RE,
    )
    if "*" not in settings.trusted_hosts:
        app.add_middleware(HostValidationMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,  # auth uses a bearer token, never cookies
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Event-Creation-Key", "X-Guest-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.hsts_enabled, docs_prefix=DOCS_PREFIX if docs else None)
    app.add_middleware(RequestContextMiddleware)

    return app
