from fastapi import APIRouter

from app.api.routes import events, photos, realtime, system

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(events.router)
api_router.include_router(photos.router)

ws_router = realtime.router

__all__ = ["api_router", "ws_router"]
