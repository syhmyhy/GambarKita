"""Logging setup with a per-request correlation id."""

import logging
import re
from contextvars import ContextVar

LOGGER_NAME = "gambarkita"

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


_HOST_TOKEN_RE = re.compile(r"(gambarkita\.host\.)[A-Za-z0-9_-]+")


class RedactHostTokenFilter(logging.Filter):
    """Scrub host tokens from log records.

    Hosts authenticate WebSockets with the subprotocol ``gambarkita.host.<token>``.
    At DEBUG level the websockets library (through uvicorn) logs every handshake
    header, which would write the token to the logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            if _MARKER in message:
                record.msg = _redact(message)
                record.args = None
            # Handshake errors can quote the header inside the exception text.
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text and _MARKER in record.exc_text:
                record.exc_text = _redact(record.exc_text)
            if record.stack_info and _MARKER in record.stack_info:
                record.stack_info = _redact(record.stack_info)
        except Exception:  # noqa: BLE001 - never break logging
            pass
        return True


_MARKER = "gambarkita.host."


def _redact(text: str) -> str:
    return _HOST_TOKEN_RE.sub(r"\1<redacted>", text)


# uvicorn.asgi logs whole ASGI scopes (including subprotocols) at trace level.
_REDACTED_LOGGERS = ("uvicorn.error", "uvicorn.access", "uvicorn.asgi", "websockets", LOGGER_NAME)


def install_log_redaction() -> None:
    """Attach the token scrubber to every logger that can see handshake headers (idempotent)."""
    for name in _REDACTED_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(existing, RedactHostTokenFilter) for existing in logger.filters):
            logger.addFilter(RedactHostTokenFilter())


def configure_logging(level: str) -> None:
    """Attach a single stream handler to the application logger (idempotent)."""
    install_log_redaction()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    if not any(getattr(handler, "_gambarkita", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler._gambarkita = True  # type: ignore[attr-defined]
        handler.addFilter(_RequestIdFilter())
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s")
        )
        logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
