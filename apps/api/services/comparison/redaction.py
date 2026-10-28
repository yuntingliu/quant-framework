import re
from typing import Any, Dict


SENSITIVE_KEYS = {"token", "access_token", "api_key", "password", "secret", "authorization", "auth", "credential", "credentials", "connection", "connection_file", "connectionfile"}


def redact_text(s: str) -> str:
    if not isinstance(s, str):
        return s
    # redact absolute paths (windows and unix)
    s = re.sub(r'[A-Za-z]:\\\\[^\s"\'<>]+', '<abs-path>', s)
    s = re.sub(r'(?<![:\w])/(?:[^\s"\'<>]+/)+[^\s"\'<>]+', '<abs-path>', s)
    # redact tokens/long hex
    s = re.sub(r'Bearer\s+[^\s]+', 'Bearer <redacted>', s, flags=re.IGNORECASE)
    s = re.sub(r'\b[a-fA-F0-9]{32,}\b', '<redacted>', s)
    s = re.sub(r'sk-[A-Za-z0-9]{20,}', '<redacted>', s)
    return s


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(sk in kl for sk in SENSITIVE_KEYS):
                out[k] = '<redacted>'
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    return obj