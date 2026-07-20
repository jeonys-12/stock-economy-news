from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
SENSITIVE_NAMES = {
    "crtfc_key",
    "auth_key",
    "authorization",
    "opendart_api_key",
    "krx_api_key",
    "openai_api_key",
}
PLAIN_ASSIGNMENT = re.compile(
    r"(?i)(crtfc_key|auth_key|authorization|opendart_api_key|krx_api_key|openai_api_key)"
    r"(\s*[=:]\s*)([^&\s,;}'\"]+)"
)
QUOTED_ASSIGNMENT = re.compile(
    r"(?i)(['\"]?(?:crtfc_key|auth_key|authorization|opendart_api_key|krx_api_key|openai_api_key)"
    r"['\"]?\s*:\s*['\"])([^'\"]+)(['\"])"
)


def _known_secret_values() -> list[str]:
    values = []
    for name in ("OPENDART_API_KEY", "KRX_API_KEY", "OPENAI_API_KEY"):
        value = os.getenv(name, "").strip()
        if len(value) >= 8:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if not parts.query:
            return value
        query = [
            (key, REDACTED if key.lower() in SENSITIVE_NAMES else item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return value


def redact_text(value: Any, limit: int | None = None) -> str:
    text = str(value)
    for secret in _known_secret_values():
        text = text.replace(secret, REDACTED)
    text = re.sub(r"https?://[^\s'\"<>]+", lambda match: redact_url(match.group(0)), text)
    text = QUOTED_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}", text)
    text = PLAIN_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return text[:limit] if limit else text


def redact_structure(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_NAMES:
                result[key] = REDACTED
            else:
                result[key] = redact_structure(item)
        return result
    if isinstance(value, list):
        return [redact_structure(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_structure(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
