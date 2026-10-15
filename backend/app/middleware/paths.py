from starlette.types import Scope


def route_path(scope: Scope) -> str:
    """The request path as routing sees it, i.e. without the ASGI ``root_path``.

    uvicorn's ``--root-path`` prefixes ``scope["path"]`` with the root path, so
    middleware that matches on paths must strip it the way Starlette's router does.
    """
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    if root_path and path.startswith(root_path) and (len(path) == len(root_path) or path[len(root_path)] == "/"):
        return path[len(root_path) :] or "/"
    return path
