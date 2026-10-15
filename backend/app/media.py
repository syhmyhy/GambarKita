"""Hardened static-file mount for uploaded photos.

Built on Starlette's ``StaticFiles``, which already resolves symlinks and
refuses paths outside its directory. On top of that this mount:

* serves only paths matching ``<event_id>/(photos|thumbs)/<photo_id>.<jpg|png|webp>``
  and rejects any non-canonical request path (``..``, ``.``, empty segments,
  a trailing slash, backslashes, NUL);
* refuses media the janitor has tombstoned (deleted, but the file is still
  locked on disk);
* sets the Content-Type from a fixed extension map (never platform MIME guessing);
* adds ``nosniff``, a sandboxing CSP and long-lived caching (file names are
  random and never reused). Caching is ``private``, so a shared cache or CDN
  never keeps serving a photo a host has deleted.
"""

import os
import re
from collections.abc import Callable

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import Scope

from app.services.image_processing import MEDIA_TYPES_BY_EXTENSION

MEDIA_MOUNT_PATH = "/media/events"

_MEDIA_PATH_RE = re.compile(
    r"(?P<event_id>[a-z0-9]{12})/(?:photos|thumbs)/(?P<filename>[a-f0-9]{32}\.(?:jpg|png|webp))"
)

_MEDIA_HEADERS = {
    "Cache-Control": "private, max-age=31536000, immutable",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": "inline",
}


def _is_canonical(request_path: str) -> bool:
    if "\\" in request_path or "\x00" in request_path or "//" in request_path or request_path.endswith("/"):
        return False
    return not any(segment in {".", ".."} for segment in request_path.split("/"))


class SecureMediaFiles(StaticFiles):
    def __init__(
        self,
        *,
        directory: str | os.PathLike[str],
        is_blocked: Callable[[str, str], bool] | None = None,
    ) -> None:
        super().__init__(directory=directory, html=False, check_dir=True, follow_symlink=False)
        self._is_blocked = is_blocked

    async def get_response(self, path: str, scope: Scope) -> Response:
        match = _MEDIA_PATH_RE.fullmatch(path.replace(os.sep, "/"))
        if match is None or not _is_canonical(scope["path"]):
            raise HTTPException(status_code=404)
        if self._is_blocked is not None and self._is_blocked(match["event_id"], match["filename"]):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        extension = os.fspath(full_path).rsplit(".", 1)[-1].lower()
        media_type = MEDIA_TYPES_BY_EXTENSION.get(extension)
        if media_type is None:
            raise HTTPException(status_code=404)
        response = FileResponse(
            full_path,
            status_code=status_code,
            stat_result=stat_result,
            media_type=media_type,
            headers=dict(_MEDIA_HEADERS),
        )
        if self.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response
