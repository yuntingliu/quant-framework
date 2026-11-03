import re
from typing import Any

SENSITIVE_KEYS = {"token", "access_token", "api_key", "password", "secret", "authorization", "auth", "credential", "credentials", "connection", "connection_file", "connectionfile"}


def redact_text(s: str) -> str:
    if not isinstance(s, str):
        return s
    # redact absolute paths (windows and unix)
    s = re.sub(r'[A-Za-z]:\\[^\s"\'<>]+', '<abs-path>', s)
    s = re.sub(r'(?<![:\w])/(?:[^\s"\'<>]+/)+[^\s"\'<>]+', '<abs-path>', s)
    # redact tokens/long hex
    s = re.sub(r'Bearer\s+[^\s]+', 'Bearer <redacted>', s, flags=re.IGNORECASE)
    s = re.sub(r'\b[a-fA-F0-9]{32,}\b', '<redacted>', s)
    s = re.sub(r'sk-[A-Za-z0-9]{20,}', '<redacted>', s)
    return s


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key).lower()
    if lowered in SENSITIVE_KEYS:
        return True
    parts = [part for part in re.split(r"[^a-z0-9]+", lowered) if part]
    return any(part in SENSITIVE_KEYS for part in parts)


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _is_sensitive_key(k):
                out[k] = '<redacted>'
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    return obj
