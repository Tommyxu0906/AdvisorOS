"""Central secret redaction.

Everything that could carry a user's API key passes through here before it reaches a log,
an exception report, an analytics payload, or a stored artifact.

The rule is deliberately blunt: redact by field name *and* by value shape. Field-name matching
catches structured data; the value regex catches keys pasted into free text, which is how they
usually leak.
"""

from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[REDACTED]"

#: Field names whose values are always replaced, matched case-insensitively as substrings.
SENSITIVE_FIELD_MARKERS: tuple[str, ...] = (
    "anthropic_api_key",
    "api_key",
    "apikey",
    "x-api-key",
    "authorization",
    "auth_token",
    "access_token",
    "refresh_token",
    "bearer",
    "credential",
    "secret",
    "password",
    "passwd",
    "private_key",
)

#: Anthropic keys look like `sk-ant-api03-...`. Match the family broadly, plus generic bearer
#: tokens and any long `sk-` prefixed string.
_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
)

_MAX_DEPTH = 12


def is_sensitive_key(name: str) -> bool:
    lowered = str(name).lower()
    return any(marker in lowered for marker in SENSITIVE_FIELD_MARKERS)


def redact_text(text: str) -> str:
    """Replace anything that looks like a credential inside free text."""
    out = text
    for pattern in _KEY_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively redact secrets from arbitrary data.

    Handles dicts, lists, tuples, sets, strings, exceptions, and Pydantic models. Unknown objects
    are rendered via `repr()` and text-redacted, so a stray object holding a key still cannot leak.
    """
    if _depth > _MAX_DEPTH:
        return "[TRUNCATED]"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return redact_text(value)

    if isinstance(value, dict):
        return {
            k: (REDACTED if is_sensitive_key(k) else redact(v, _depth + 1))
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        rendered = [redact(v, _depth + 1) for v in value]
        return type(value)(rendered) if isinstance(value, (list, tuple)) else rendered

    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {redact_text(str(value))}"

    # Pydantic models and other objects exposing a dict-ish view.
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return redact(dump(), _depth + 1)
        except Exception:  # noqa: BLE001 - redaction must never raise
            pass

    return redact_text(repr(value))


class RedactingFilter(logging.Filter):
    """Logging filter that redacts the message, its args, and any attached exception."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            else:
                record.msg = redact(record.msg)

            if record.args:
                if isinstance(record.args, dict):
                    record.args = redact(record.args)
                else:
                    record.args = tuple(redact(a) for a in record.args)

            if record.exc_info and record.exc_info[1] is not None:
                exc = record.exc_info[1]
                cleaned = redact_text(str(exc))
                if cleaned != str(exc):
                    # Replace the exception with a scrubbed equivalent so the traceback
                    # formatter cannot re-emit the original text.
                    record.exc_info = (
                        record.exc_info[0],
                        type(exc)(cleaned),
                        record.exc_info[2],
                    )
        except Exception:  # noqa: BLE001 - a failing filter must not drop the log line
            record.msg = "[REDACTION FAILED — message suppressed]"
            record.args = ()
            record.exc_info = None
        return True


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Attach the redacting filter to a logger and to every existing root handler."""
    target = logger or logging.getLogger()
    filt = RedactingFilter()
    if not any(isinstance(f, RedactingFilter) for f in target.filters):
        target.addFilter(filt)
    for handler in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
