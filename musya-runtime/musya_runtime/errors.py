"""Error types shared by every stage of the MUSYA toolchain."""

from __future__ import annotations


class MusyaError(Exception):
    """Base class for all MUSYA errors."""

    kind = "Error"

    def __init__(self, message: str, line: int | None = None, column: int | None = None,
                 source_name: str | None = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column
        self.source_name = source_name

    def location(self) -> str:
        parts = []
        if self.source_name:
            parts.append(self.source_name)
        if self.line is not None:
            parts.append(str(self.line))
            if self.column is not None:
                parts.append(str(self.column))
        return ":".join(parts)

    def __str__(self) -> str:  # pragma: no cover - formatting only
        loc = self.location()
        return f"{self.kind} at {loc}: {self.message}" if loc else f"{self.kind}: {self.message}"

    def to_dict(self) -> dict:
        return {
            "type": self.kind,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "source": self.source_name,
        }


class MusyaSyntaxError(MusyaError):
    kind = "SyntaxError"


class MusyaRuntimeError(MusyaError):
    """A runtime error. ``kind`` can be overridden per instance (TypeError, NameError, ...)."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None,
                 source_name: str | None = None, kind: str = "RuntimeError"):
        super().__init__(message, line, column, source_name)
        self.kind = kind


class MusyaPermissionError(MusyaRuntimeError):
    def __init__(self, message: str, line: int | None = None, column: int | None = None,
                 source_name: str | None = None):
        super().__init__(message, line, column, source_name, kind="PermissionError")


class MusyaLimitError(MusyaRuntimeError):
    """Raised when a sandbox limit (steps, depth, size, time) is exceeded. Not catchable by scripts."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None,
                 source_name: str | None = None):
        super().__init__(message, line, column, source_name, kind="LimitError")


class MusyaThrow(MusyaRuntimeError):
    """A value thrown with ``throw`` that was not caught."""

    def __init__(self, value, line: int | None = None, column: int | None = None,
                 source_name: str | None = None):
        msg = value.get("message") if isinstance(value, dict) and "message" in value else value
        super().__init__(str(msg), line, column, source_name, kind="Thrown")
        self.value = value


class MusyaAssertionError(MusyaRuntimeError):
    def __init__(self, message: str, line: int | None = None, column: int | None = None,
                 source_name: str | None = None):
        super().__init__(message, line, column, source_name, kind="AssertionError")
