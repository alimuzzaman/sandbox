from __future__ import annotations

import re


class RecoveryError(RuntimeError):
    def __init__(self, message: str, code: str = "recovery_error") -> None:
        super().__init__(message)
        self.code = code


_SECRET = re.compile(r"(?i)(token|password|passphrase|authorization|cookie|credential|secret)\s*[=:]\s*\S+")
_SECRET_KEY = re.compile(r"(?i)(token|password|passphrase|authorization|cookie|credential|secret)")
_URL_USERINFO = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@")
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")


def redact(value):
    if isinstance(value, str):
        value = _URL_USERINFO.sub(r"\g<scheme>", value)
        value = _BEARER.sub("Bearer [redacted]", value)
        return _SECRET.sub(lambda match: match.group(1) + "=[redacted]", value)
    if isinstance(value, dict):
        return {key: "[redacted]" if _SECRET_KEY.search(str(key)) else redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item) for item in value)
    return value


def result(ok: bool, action: str, *, remote: str | None = None,
           status: str | None = None, data: dict | None = None,
           error: RecoveryError | None = None) -> dict:
    return {
        "ok": ok, "action": action, "remote": redact(remote), "status": status,
        "data": redact(data or {}),
        "error": None if error is None else {
            "code": error.code, "message": redact(str(error)), "retryable": False,
        },
    }
