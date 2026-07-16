from __future__ import annotations

import re
from pathlib import Path

from .base import RegistryCorruption

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def canonical_root(root: str | Path) -> str:
    if (not isinstance(root, (str, Path)) or not str(root) or
            any(ord(char) < 32 or ord(char) == 127 for char in str(root))):
        raise ValueError("registry root is invalid")
    resolved = Path(root).expanduser().resolve()
    if resolved == Path(resolved.anchor or "/"):
        raise ValueError("registry root is too broad")
    return str(resolved)


def validate_label(label: str) -> str:
    if not isinstance(label, str) or not _LABEL.fullmatch(label):
        raise ValueError("registry label is invalid")
    return label


def validate_record_identity(key: object, record: object) -> None:
    if not isinstance(key, str) or not isinstance(record, dict):
        raise RegistryCorruption("registry contains an invalid record")
    try:
        root = record["root"]
        label = validate_label(record["label"])
        canonical = canonical_root(root)
        if not Path(root).expanduser().is_absolute():
            raise ValueError("registry root must be absolute")
        expected_keys = {root, f"{root}::{label}", f"{canonical}::{label}"}
    except (KeyError, TypeError, ValueError) as exc:
        raise RegistryCorruption("registry contains an invalid record identity") from exc
    if key not in expected_keys or not isinstance(record.get("is_default"), bool):
        raise RegistryCorruption("registry contains an invalid record identity")


def backfill_record_identity(key: object, record: object) -> bool:
    """Fill identity fields omitted by older v2 writers; return whether changed."""
    if not isinstance(key, str) or not isinstance(record, dict):
        return False
    root = record.get("root")
    prefix = f"{root}::"
    changed = False
    if "label" not in record and isinstance(root, str):
        if key == root:
            record["label"] = "default"
            changed = True
        elif key.startswith(prefix):
            record["label"] = key[len(prefix):]
            changed = True
    if "is_default" not in record and "label" in record:
        record["is_default"] = record["label"] == "default"
        changed = True
    return changed
