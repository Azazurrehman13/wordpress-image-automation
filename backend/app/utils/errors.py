from __future__ import annotations

from typing import Any


class AppError(Exception):
    """A user-facing, expected failure. `code` is stable and shown in the UI."""

    def __init__(self, code: str, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details
