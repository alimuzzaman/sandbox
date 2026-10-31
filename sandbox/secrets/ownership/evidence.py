"""Admit controller-local, operator-attested storage catalog evidence.

This module never connects to a database or provider. The queries and their
output grammar are pinned by contracts/storage-evidence.md in spec 069.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime
from pathlib import Path

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.provider_identity import canonical_https_origin

from .models import Destination, StorageAdmission


INDEX_STATE_QUERY = (
    "SELECT c.relname, t.relname, i.indisunique, i.indisvalid, i.indisready, "
    "i.indislive, pg_get_indexdef(i.indexrelid) FROM pg_catalog.pg_index i "
    "JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid "
    "JOIN pg_catalog.pg_class t ON t.oid=i.indrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
    "WHERE n.nspname='public' AND t.relnamespace=c.relnamespace "
    "AND c.relname='uq_secrets_v2_key_folder_id_shared';"
)
# Infisical v0.166.5: secret-v2-bridge-fns.ts:55; project-bot-fns.ts:27-37;
# secret-service.ts:1639 calls getBotKey(projectId) without the bot-key override.
# https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/services/secret-v2-bridge/secret-v2-bridge-fns.ts#L55
# https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/services/project-bot/project-bot-fns.ts#L27-L37
# https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/services/secret/secret-service.ts#L1639
V2_SELECTION_QUERY = (
    "SELECT p.id::text AS project_id, p.version AS version, "
    "(p.version = 3) AS should_use_secret_v2_bridge FROM public.projects AS p "
    "WHERE p.id = $1;"
)
STORAGE_CONTRACT_CONFIRMED = bool(INDEX_STATE_QUERY and V2_SELECTION_QUERY)
# No exact indexdef is claimed until the disposable-instance proof is supplied.
EXPECTED_INDEXDEF: str | None = None

_MAX_EVIDENCE_BYTES = 262_144
_ROOT_KEYS = frozenset((
    "kind", "version", "destination", "capturedAt", "expiresAt", "producer", "queries",
))
_INDEX_FIELDS = ["uq_secrets_v2_key_folder_id_shared", "secrets_v2", "t", "t", "t", "t"]
_INDEX_PREFIX = re.compile(
    r'CREATE UNIQUE INDEX (?:uq_secrets_v2_key_folder_id_shared|"uq_secrets_v2_key_folder_id_shared") '
    r'ON (?:(?:public|"public")\.)?(?:secrets_v2|"secrets_v2") USING btree '
    r'\((?:key|"key"), "folderId"\) WHERE (.+)'
)
_PREDICATE_TOKEN = re.compile(r"\s*(::|[()=]|'shared'|\"type\"|type\b|text\b|character varying\b)")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


def _refuse(code: str = "evidence_inadmissible") -> SecretBrokerError:
    messages = {
        "evidence_inadmissible": "storage evidence does not satisfy the operator attestation contract",
        "evidence_expired": "storage evidence is outside its admission window",
        "provider_contract_unconfirmed": "storage selection contract is not confirmed",
    }
    return SecretBrokerError(code, messages[code])


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _refuse()
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _refuse()


def _private_bytes(path: Path) -> bytes:
    """Read one private regular file from one fd, refusing replacement or drift."""
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                or before.st_size > _MAX_EVIDENCE_BYTES):
            raise _refuse()
        chunks = bytearray()
        while True:
            chunk = os.read(fd, min(65_536, _MAX_EVIDENCE_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > _MAX_EVIDENCE_BYTES:
                raise _refuse()
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
                  "st_mode", "st_uid", "st_nlink")
        if (any(getattr(before, field) != getattr(after, field) for field in fields)
                or len(chunks) != after.st_size):
            raise _refuse()
        return bytes(chunks)
    except (OSError, ValueError, TypeError):
        raise _refuse() from None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                raise _refuse() from None


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise _refuse()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        raise _refuse() from None


def _one_row(output: object) -> str:
    if not isinstance(output, str) or not output:
        raise _refuse()
    row = output.removesuffix("\n").removesuffix("\r")
    if not row or "\n" in row or "\r" in row:
        raise _refuse()
    return row


def _shared_predicate(predicate: str) -> bool:
    """Parse only type = 'shared', allowing balanced parentheses and text casts."""
    tokens = []
    offset = 0
    while offset < len(predicate):
        match = _PREDICATE_TOKEN.match(predicate, offset)
        if match is None or len(tokens) >= 64:
            return False
        tokens.append(match[1])
        offset = match.end()
    position = 0

    def term():
        nonlocal position
        if position >= len(tokens):
            raise ValueError()
        token = tokens[position]
        position += 1
        if token == "(":
            node = expression()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError()
            position += 1
        elif token in ("type", '"type"'):
            node = ("column", "type")
        elif token == "'shared'":
            node = ("literal", "shared")
        else:
            raise ValueError()
        while position < len(tokens) and tokens[position] == "::":
            position += 1
            if (position >= len(tokens) or tokens[position] not in ("text", "character varying")
                    or node[0] not in ("column", "literal")):
                raise ValueError()
            position += 1
        return node

    def expression():
        nonlocal position
        node = term()
        if position < len(tokens) and tokens[position] == "=":
            position += 1
            node = ("equal", node, term())
        return node

    try:
        node = expression()
        return position == len(tokens) and node == ("equal", ("column", "type"), ("literal", "shared"))
    except (ValueError, RecursionError):
        return False


def _index_output(output: object) -> None:
    fields = _one_row(output).split("|")
    if len(fields) != 7 or fields[:6] != _INDEX_FIELDS:
        raise _refuse()
    definition = " ".join(fields[6].split())
    if EXPECTED_INDEXDEF is not None:
        if definition != EXPECTED_INDEXDEF:
            raise _refuse()
        return
    match = _INDEX_PREFIX.fullmatch(definition)
    if match is None or not _shared_predicate(match[1]):
        raise _refuse()


def admit_evidence(path: Path, destination: Destination, *, now: float) -> StorageAdmission:
    """Return an operator_attested admission or a fixed safe refusal."""
    if not STORAGE_CONTRACT_CONFIRMED:
        raise _refuse("provider_contract_unconfirmed")
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise _refuse()
    raw = _private_bytes(path)
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise _refuse() from None
    if (type(data) is not dict or set(data) != _ROOT_KEYS
            or data["kind"] != "sandbox-ownership-storage-evidence"
            or type(data["version"]) is not int or data["version"] != 1
            or data["producer"] != "operator_db_catalog_query"):
        raise _refuse()
    try:
        origin = canonical_https_origin(destination.endpoint)
    except (ValueError, TypeError, AttributeError):
        raise _refuse() from None
    if (type(data["destination"]) is not dict
            or data["destination"] != {"endpoint": origin, "projectId": destination.project_id}):
        raise _refuse()
    captured = _timestamp(data["capturedAt"])
    expires = _timestamp(data["expiresAt"])
    if not 0 < expires - captured <= 3600:
        raise _refuse()
    if captured > now or expires <= now or now - captured > 3600:
        raise _refuse("evidence_expired")
    queries = data["queries"]
    if type(queries) is not list or len(queries) != 2:
        raise _refuse()
    by_id = {}
    for query in queries:
        if (type(query) is not dict or not isinstance(query.get("id"), str)
                or query["id"] in by_id):
            raise _refuse()
        by_id[query["id"]] = query
    if set(by_id) != {"index_state", "v2_selection"}:
        raise _refuse()
    index, selection = by_id["index_state"], by_id["v2_selection"]
    if (set(index) != {"id", "sql", "output"} or index["sql"] != INDEX_STATE_QUERY
            or set(selection) != {"id", "sql", "params", "output"}
            or selection["sql"] != V2_SELECTION_QUERY
            or selection["params"] != [destination.project_id]):
        raise _refuse()
    _index_output(index["output"])
    # Version 3 only; p.id is bound as supplied, never forced to UUID.
    if _one_row(selection["output"]).split("|") != [destination.project_id, "3", "t"]:
        raise _refuse()
    return StorageAdmission(
        evidence_sha256=hashlib.sha256(raw).hexdigest(), destination_origin=origin,
        project_id=destination.project_id, captured_at=captured, expires_at=expires,
        attestation="operator_attested",
    )


__all__ = [
    "EXPECTED_INDEXDEF", "INDEX_STATE_QUERY", "STORAGE_CONTRACT_CONFIRMED",
    "V2_SELECTION_QUERY", "admit_evidence",
]
