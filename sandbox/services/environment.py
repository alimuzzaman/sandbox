"""Small explicit child environments for test-shaped subprocesses."""

from __future__ import annotations

from collections.abc import Mapping
import os


# Read these compatibility values one at a time.  Never enumerate the parent
# environment: an inherited credential must not become child output merely
# because a test prints or serializes its environment.
_COMPATIBILITY_KEYS = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
)


class ExplicitEnvironment(Mapping[str, str]):
    """A private Popen mapping that never renders or JSON-serializes values."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"<explicit child environment: {len(self)} variables>"

    __str__ = __repr__


def compatible_subprocess_environment(
    overrides: Mapping[str, str] | None = None,
) -> ExplicitEnvironment:
    """Return a minimal child environment plus explicit caller overrides."""
    if overrides is os.environ:
        raise ValueError("overrides must not be the parent environment")
    if overrides is not None and (
        not isinstance(overrides, Mapping)
        or any(
            not isinstance(key, str) or not key or "\x00" in key
            or not isinstance(value, str) or "\x00" in value
            for key, value in overrides.items()
        )
    ):
        raise ValueError("overrides must contain NUL-free string keys and values")
    environment: dict[str, str] = {}
    for key in _COMPATIBILITY_KEYS:
        value = os.environ.get(key)
        if value is not None:
            environment[key] = value
    if overrides is not None:
        environment.update(overrides)
    return ExplicitEnvironment(environment)
