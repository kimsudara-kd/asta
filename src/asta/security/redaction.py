"""Secret values that cannot leak by accident, and log/diagnostic scrubbing."""

from __future__ import annotations

import logging
import re
from typing import Any, NoReturn

MASK = "***REDACTED***"


class SecretValue:
    """Holds a secret string. str/repr/format/pickle/copy/json all refuse or mask it; the only way
    to read it is ``reveal()``, which the vault grants to an authorised connector lease alone."""

    __slots__ = ("_v",)

    def __init__(self, value: str) -> None:
        object.__setattr__(self, "_v", value)

    def __setattr__(self, name: str, value: Any) -> NoReturn:
        raise AttributeError("SecretValue is immutable")

    def reveal(self) -> str:
        return str(object.__getattribute__(self, "_v"))

    def __repr__(self) -> str:
        return f"SecretValue({MASK})"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return MASK

    def __reduce__(self) -> NoReturn:
        raise TypeError("SecretValue cannot be pickled")

    def __copy__(self) -> NoReturn:
        raise TypeError("SecretValue cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("SecretValue cannot be copied")

    def __eq__(self, other: object) -> bool:
        return False  # never comparable (prevents oracle-style probing through ==)

    def __hash__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return True


class SecretRegistry:
    """Known secret strings of the current process, so that ANY text (log line, exception message,
    traceback, diagnostic dump) can be scrubbed even if a secret was formatted into it."""

    def __init__(self) -> None:
        self._needles: set[str] = set()

    def register(self, secret: str) -> None:
        if len(secret) >= 6:
            self._needles.add(secret)

    def clear(self) -> None:
        self._needles.clear()

    def scrub(self, text: str) -> str:
        for n in sorted(self._needles, key=len, reverse=True):
            text = text.replace(n, MASK)
        return _GENERIC.sub(lambda m: m.group(1) + MASK, text)


# Generic shapes that must never appear in logs even when not registered.
_GENERIC = re.compile(
    r"((?:secret|token|password|authorization|api[-_ ]?key|signature)\s*[=:]\s*)[^\s,;&\"']{6,}",
    re.IGNORECASE,
)

REGISTRY = SecretRegistry()


class RedactingFilter(logging.Filter):
    """Scrubs the formatted message, args and exception text of every log record."""

    def __init__(self, registry: SecretRegistry = REGISTRY) -> None:
        super().__init__()
        self._r = registry

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._r.scrub(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            exc.args = tuple(self._r.scrub(str(a)) for a in exc.args)
        record.exc_text = None
        return True


def scrub_exception(exc: BaseException, registry: SecretRegistry = REGISTRY) -> str:
    """Safe text for crash reports / diagnostics."""
    import traceback

    return registry.scrub("".join(traceback.format_exception(exc)))
