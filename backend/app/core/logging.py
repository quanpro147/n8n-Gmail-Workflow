import json
import logging
import sys
from datetime import UTC, datetime

# Only these `extra=` keys are emitted. Anything else (e.g. an email body passed by
# mistake) is dropped, so sensitive data cannot leak into logs through `extra`.
ALLOWED_EXTRA_KEYS = frozenset(
    {
        "message_id",
        "status",
        "action",
        "category",
        "priority",
        "error_code",
        "attempt",
        "tool_calls",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "outcome",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(
            {key: value for key, value in record.__dict__.items() if key in ALLOWED_EXTRA_KEYS}
        )
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            # Frames don't include locals; keep exception messages free of email content.
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Third-party HTTP clients can log request URLs/headers at DEBUG; keep them quiet.
    for noisy in ("httpx", "httpcore", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
