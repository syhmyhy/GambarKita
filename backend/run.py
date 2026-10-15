"""Start the API with hardened uvicorn settings: ``python run.py``.

Configuration comes from environment variables / ``backend/.env`` (see .env.example).
Run a single worker: live updates and rate-limit counters are held in process memory.
"""

import socket

import uvicorn

from app.core.config import Settings, get_settings

_ALL_INTERFACES = {"0.0.0.0", "::", ""}


def lan_address() -> str | None:
    """This machine's IPv4 address on the local network (best effort).

    Connecting a UDP socket sends nothing; it only makes the OS choose the
    outgoing interface, whose address is what other devices on the Wi-Fi use.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.253.155.219", 58162))  # any non-local address works; nothing is sent
            address = probe.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith(("127.", "0.")) else address


def startup_banner(settings: Settings) -> str:
    """Clickable URLs for the console (uvicorn only prints the bind address, e.g. 0.0.0.0)."""
    port = settings.port
    if settings.host in _ALL_INTERFACES:
        local = f"http://127.0.0.1:{port}"
        lines = [f"Local:    {local}"]
        network = lan_address()
        if network:
            lines.append(f"Network:  http://{network}:{port}")
    else:
        host = f"[{settings.host}]" if ":" in settings.host else settings.host
        local = f"http://{host}:{port}"
        lines = [f"Address:  {local}"]
    if settings.docs_enabled:
        lines.append(f"API docs: {local}/api/docs")
    lines.append(f"Website:  {settings.public_app_url}")
    return "\n  GambarKita API\n" + "".join(f"  * {line}\n" for line in lines)


def main() -> None:
    settings = get_settings()
    print(startup_banner(settings), flush=True)
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        workers=1,
        log_level=settings.log_level.lower(),
        # Honour X-Forwarded-For only from these proxy IPs (uvicorn default: 127.0.0.1).
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        server_header=False,
        # Clients only ever send tiny heartbeat frames.
        ws_max_size=64 * 1024,
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
        timeout_keep_alive=5,
        # Stalled clients (e.g. a download that is never read) must not block restarts forever.
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()
