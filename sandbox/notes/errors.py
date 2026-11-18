"""One error type for every ``sb notes`` / ``sb mockups`` refusal."""

from __future__ import annotations


class NotesError(Exception):
    def __init__(self, code: str, message: str, **detail) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_payload(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message, **self.detail}}
