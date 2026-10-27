"""Durable per-name cleanup progress sidecar and crash recovery (R3)."""

from __future__ import annotations

import hmac
import json
import math
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from sandbox.secrets.models import SecretBrokerError
from .journal import (
    ensure_private_dir, is_qualified_id, journal_dir, qualified_controller,
    read_private_file, write_private_file, _unique_pairs, _reject_constant,
    _CONTROLLER_ID, _PROFILE, _SHA256,
)
from .models import MapEntry, OwnershipConfig, SourceContinuity, canonical_json
from .source_view import read_names

_ITEM_KEYS = frozenset((
    "name", "key", "source_alias", "state", "approval_id", "dev", "ino",
    "names_sha256", "updated_at",
))
_HEADER_KEYS = frozenset((
    "cleanup", "controller_id", "profile", "destination_sha256", "map_sha256",
    "authority_digest", "items",
))
_PROVIDER_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_AUTHORITY_DIGEST = re.compile(r"^o1-[0-9a-f]{64}$")


def cleanup_progress_path(runtime_root: Path, profile: str, destination_sha256: str) -> Path:
    return journal_dir(runtime_root, profile, destination_sha256) / "cleanup.json"


class CleanupProgress:
    """Manages durable cleanup progress sidecar with crash-safe recovery."""

    def __init__(
        self,
        path: Path,
        *,
        controller_id: str,
        profile: str,
        destination_sha256: str,
        map_sha256: str,
        authority_digest: str,
        items: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.controller_id = controller_id
        self.profile = profile
        self.destination_sha256 = destination_sha256
        self.map_sha256 = map_sha256
        self.authority_digest = authority_digest
        self.items: dict[str, dict[str, Any]] = dict(items) if items is not None else {}
        self._changed: bool = False
        self.reintroduced_names: set[str] = set()

    @property
    def names(self) -> dict[str, dict[str, Any]]:
        return self.items

    @classmethod
    def load_or_recover(
        cls,
        root: Path,
        profile: str,
        destination_sha256: str,
        *args: Any,
        controller_id: str | None = None,
        authority_digest: str | None = None,
        map_sha256: str | None = None,
        registry: Any = None,
        current_continuities: Mapping[str, SourceContinuity] | None = None,
        config: OwnershipConfig | None = None,
        entries: Sequence[MapEntry] | None = None,
        persist: bool = True,
        **kwargs: Any,
    ) -> "CleanupProgress":
        # Support positional and keyword argument patterns flexibly
        cid = controller_id or (args[0] if len(args) > 0 else kwargs.get("controller_id"))
        msha = map_sha256 or (args[1] if len(args) > 1 else kwargs.get("map_sha256"))
        adig = authority_digest or (args[2] if len(args) > 2 else kwargs.get("authority_digest"))
        reg = registry or (args[3] if len(args) > 3 else kwargs.get("registry"))

        if not cid or not msha or not adig or reg is None:
            raise SecretBrokerError("authority_mismatch", "cleanup progress arguments missing")

        path = cleanup_progress_path(root, profile, destination_sha256)
        try:
            raw = read_private_file(path, limit=256 * 1024)
        except FileNotFoundError:
            # Only actual FileNotFoundError means no sidecar exists.
            return cls(
                path,
                controller_id=cid,
                profile=profile,
                destination_sha256=destination_sha256,
                map_sha256=msha,
                authority_digest=adig,
                items={},
            )
        except Exception as exc:
            raise SecretBrokerError("authority_mismatch", "cleanup progress sidecar could not be read safely") from exc

        try:
            data = json.loads(
                raw.decode("ascii"),
                object_pairs_hook=_unique_pairs,
                parse_constant=_reject_constant,
            )
        except Exception:
            raise SecretBrokerError("authority_mismatch", "cleanup progress sidecar is corrupt") from None

        if not isinstance(data, dict) or set(data) != _HEADER_KEYS:
            raise SecretBrokerError("authority_mismatch", "cleanup progress sidecar header invalid")

        if type(data["cleanup"]) is not int or isinstance(data["cleanup"], bool) or data["cleanup"] != 1:
            raise SecretBrokerError("authority_mismatch", "cleanup progress version invalid")

        if (not isinstance(data["controller_id"], str)
                or not _CONTROLLER_ID.fullmatch(data["controller_id"])
                or data["controller_id"] != cid):
            raise SecretBrokerError("authority_mismatch", "cleanup progress controller mismatch")

        if (not isinstance(data["profile"], str)
                or not _PROFILE.fullmatch(data["profile"])
                or data["profile"] != profile):
            raise SecretBrokerError("authority_mismatch", "cleanup progress profile mismatch")

        if (not isinstance(data["destination_sha256"], str)
                or not _SHA256.fullmatch(data["destination_sha256"])
                or not hmac.compare_digest(data["destination_sha256"], destination_sha256)):
            raise SecretBrokerError("authority_mismatch", "cleanup progress destination mismatch")

        if (not isinstance(data["map_sha256"], str)
                or not _SHA256.fullmatch(data["map_sha256"])
                or not hmac.compare_digest(data["map_sha256"], msha)):
            raise SecretBrokerError("authority_mismatch", "cleanup progress map mismatch")

        if (not isinstance(data["authority_digest"], str)
                or not _AUTHORITY_DIGEST.fullmatch(data["authority_digest"])
                or not hmac.compare_digest(data["authority_digest"], adig)):
            raise SecretBrokerError("authority_mismatch", "cleanup progress authority mismatch")

        items_raw = data["items"]
        if not isinstance(items_raw, dict):
            raise SecretBrokerError("authority_mismatch", "cleanup progress items invalid")

        valid_map: dict[str, tuple[str, str]] | None = None
        if config is not None:
            valid_map = {e.name: (e.source, e.key) for e in config.entries if e.profile == profile}
        elif entries is not None:
            valid_map = {e.name: (e.source, e.key) for e in entries if getattr(e, "profile", profile) == profile}

        parsed_items: dict[str, dict[str, Any]] = {}
        for name, item in items_raw.items():
            if not isinstance(name, str) or not _PROVIDER_NAME.fullmatch(name):
                raise SecretBrokerError("authority_mismatch", "cleanup progress item name invalid")
            if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} keys invalid")
            if item["name"] != name:
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} name mismatch")
            if not isinstance(item["key"], str) or not item["key"]:
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} key invalid")
            if not isinstance(item["source_alias"], str) or not item["source_alias"]:
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} source alias invalid")
            if item["state"] not in ("prepared", "removed"):
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} state invalid")
            if (not isinstance(item["approval_id"], str)
                    or not is_qualified_id(item["approval_id"], "ap")
                    or qualified_controller(item["approval_id"]) != cid):
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} approval invalid")
            if type(item["dev"]) is not int or isinstance(item["dev"], bool) or item["dev"] < 0:
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} dev invalid")
            if type(item["ino"]) is not int or isinstance(item["ino"], bool) or item["ino"] < 0:
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} ino invalid")
            if not isinstance(item["names_sha256"], str) or not _SHA256.fullmatch(item["names_sha256"]):
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} names hash invalid")
            if (type(item["updated_at"]) not in (int, float)
                    or isinstance(item["updated_at"], bool)
                    or not math.isfinite(item["updated_at"])
                    or item["updated_at"] < 0):
                raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} updated_at invalid")

            if valid_map is not None:
                if name not in valid_map:
                    raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} not in current map")
                expected_source, expected_key = valid_map[name]
                if item["source_alias"] != expected_source or item["key"] != expected_key:
                    raise SecretBrokerError("authority_mismatch", f"cleanup progress item {name} source/key binding mismatch")

            parsed_items[name] = dict(item)

        instance = cls(
            path,
            controller_id=cid,
            profile=profile,
            destination_sha256=destination_sha256,
            map_sha256=msha,
            authority_digest=adig,
            items=parsed_items,
        )
        instance._recover(reg, current_continuities=current_continuities)
        if persist and instance._changed:
            instance.save()
        return instance

    def _recover(
        self,
        registry: Any,
        *,
        current_continuities: Mapping[str, SourceContinuity] | None = None,
    ) -> None:
        """Crash recovery: observe registered source via read_names same-FD metadata."""
        if not self.items:
            return

        observed: dict[str, tuple[SourceContinuity, tuple[str, ...]]] = {}

        def get_source(alias: str) -> tuple[SourceContinuity, tuple[str, ...]]:
            if alias not in observed:
                observed[alias] = read_names(registry, alias)
            return observed[alias]

        for name, item in sorted(self.items.items()):
            source_alias = item["source_alias"]
            continuity, current_names = get_source(source_alias)
            if current_continuities and source_alias in current_continuities:
                expected_cont = current_continuities[source_alias]
                if not continuity.same_as(expected_cont):
                    raise SecretBrokerError("source_changed", f"source {source_alias} changed during cleanup recovery")

            if item["state"] == "prepared":
                # Check staged dev/inode
                if continuity.dev == item["dev"] and continuity.ino == item["ino"]:
                    # Inode matches staged file: verify names hash and key absence
                    if (hmac.compare_digest(continuity.names_sha256, item["names_sha256"])
                            and item["key"] not in current_names):
                        # Atomic replace completed before checkpoint write/crash!
                        item["state"] = "removed"
                        item["updated_at"] = time.time()
                        self._changed = True
                    else:
                        raise SecretBrokerError(
                            "authority_mismatch",
                            f"cleanup recovery staged file metadata mismatch for {name}",
                        )
                else:
                    # Inode does not match staged file: replacement did NOT occur
                    if item["key"] in current_names:
                        # Key is present: leave pending INTENT for safe retry under fresh human approval
                        pass
                    else:
                        # Key is missing with unexpected inode/names mismatch: fail closed!
                        raise SecretBrokerError(
                            "cleanup_ineligible",
                            f"cleanup source key {item['key']} is missing with unexpected inode/names mismatch",
                        )

            elif item["state"] == "removed":
                # Historical removed entry in chain.
                # Validate absent names + bind intent.
                if item["key"] in current_names:
                    # Key was reintroduced! Cannot treat as proven removed.
                    self.reintroduced_names.add(name)

    def persist_recovery(self) -> None:
        if self._changed:
            self.save()
            self._changed = False

    def proven_removed_names(self) -> frozenset[str]:
        return frozenset(
            name for name, item in self.items.items()
            if item.get("state") == "removed" and name not in self.reintroduced_names
        )

    def proven_removed(self) -> set[str]:
        return set(self.proven_removed_names())

    def prepare(
        self,
        name: str,
        key: str,
        source_alias: str,
        staged: SourceContinuity,
        approval_id: str,
    ) -> None:
        self.items[name] = {
            "name": name,
            "key": key,
            "source_alias": source_alias,
            "state": "prepared",
            "approval_id": approval_id,
            "dev": staged.dev,
            "ino": staged.ino,
            "names_sha256": staged.names_sha256,
            "updated_at": time.time(),
        }
        self.save()

    def checkpoint_removed(
        self,
        name: str,
        key: str,
        source_alias: str,
        dev: int,
        ino: int,
        approval_id: str,
        *,
        names_sha256: str | None = None,
    ) -> None:
        existing = self.items.get(name, {})
        sha = names_sha256 or existing.get("names_sha256", "")
        self.items[name] = {
            "name": name,
            "key": key,
            "source_alias": source_alias,
            "state": "removed",
            "approval_id": approval_id,
            "dev": dev,
            "ino": ino,
            "names_sha256": sha,
            "updated_at": time.time(),
        }
        self.save()

    def save(self) -> None:
        ensure_private_dir(self.path.parent)
        payload = {
            "cleanup": 1,
            "controller_id": self.controller_id,
            "profile": self.profile,
            "destination_sha256": self.destination_sha256,
            "map_sha256": self.map_sha256,
            "authority_digest": self.authority_digest,
            "items": self.items,
        }
        write_private_file(self.path, canonical_json(payload), exclusive=False)


__all__ = ["CleanupProgress", "cleanup_progress_path"]
