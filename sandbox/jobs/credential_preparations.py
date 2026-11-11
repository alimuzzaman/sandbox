"""Retained nonsecret target preparation, never host/lifetime/resource authority.

Lives in the existing jobs database/transaction owner. Admission's server-only
preparer supplies parsed intent with freshly allocated copy/incarnation values.
The original scope freezes one preparation; no read creates or refreshes it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time

from sandbox.hosting.shared_credentials.models import CredentialIntent, RequestScope

_KEY = "credential_target_preparations_schema_version"


@dataclass(frozen=True, slots=True)
class TargetPreparationEvidence:
    scope: RequestScope
    project_identity: str
    project: str
    environment: str
    name: str
    copy_id: str
    incarnation: str
    source_commit: str
    intent_digest: str
    operation_id: str | None
    canonical_intent: str = field(repr=False)
    created_at: float

    def operation_request(self) -> dict:
        """Nonsecret proposed request data, explicitly not an authority proof."""
        data = json.loads(self.canonical_intent)
        data.pop("principal_id")
        if data.get("retry_of") is None:
            data.pop("retry_of", None)
        return data


def _error(code="credential_target_preparation_invalid"):
    from .credential_operations import LedgerError
    raise LedgerError(code)


def initialize_schema(connection):
    version = connection.execute("SELECT value FROM schema_meta WHERE key=?", (_KEY,)).fetchone()
    if version is not None and version[0] != "1":
        _error("credential_schema_unsupported")
    connection.execute('''CREATE TABLE IF NOT EXISTS credential_target_preparations (
        installation_id TEXT NOT NULL, incarnation TEXT NOT NULL,
        principal_id TEXT NOT NULL, project_ref TEXT NOT NULL, request_id TEXT NOT NULL,
        project_identity TEXT NOT NULL, target_project TEXT NOT NULL,
        basis_json TEXT NOT NULL, intent_json TEXT NOT NULL, intent_digest TEXT NOT NULL,
        target_name TEXT NOT NULL, copy_id TEXT NOT NULL, target_incarnation TEXT NOT NULL,
        source_commit TEXT NOT NULL, environment TEXT NOT NULL, created_at REAL NOT NULL,
        operation_id TEXT UNIQUE REFERENCES credential_operations(operation_id),
        PRIMARY KEY(installation_id,incarnation,principal_id,project_ref,request_id)
    )''')
    connection.execute("INSERT INTO schema_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (_KEY, "1"))


def _scope(scope):
    if type(scope) is not RequestScope:
        _error()
    return (scope.installation_id, scope.incarnation, scope.principal_id, scope.project_ref, scope.request_id)


def _find(connection, scope):
    return connection.execute('''SELECT * FROM credential_target_preparations WHERE
        installation_id=? AND incarnation=? AND principal_id=? AND project_ref=? AND request_id=?''',
        _scope(scope)).fetchone()


def _basis(intent, project):
    if (type(intent) is not CredentialIntent or intent.action != "stack_up"
            or not isinstance(project, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", project) is None
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", intent.target_ref.target_id) is None
            or re.fullmatch(r"[0-9a-f]{8}", intent.target_ref.copy_id) is None
            or re.fullmatch(r"i-[0-9a-f]{32}", intent.target_ref.incarnation) is None):
        _error()
    data = intent.as_dict()
    # Copy/incarnation come from the original server preparation, not a second
    # plan invocation. Everything else, including deadline and retry, is frozen.
    data["target_ref"] = {"target_id": intent.target_ref.target_id}
    return json.dumps({"project": project, "intent": data}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _evidence(row):
    try:
        canonical = row["intent_json"]
        data = json.loads(canonical)
        scope = RequestScope(row["installation_id"], row["incarnation"], row["principal_id"],
                             row["project_ref"], row["request_id"])
        if (not isinstance(canonical, str) or len(canonical.encode()) > 65536
                or hashlib.sha256(canonical.encode()).hexdigest() != row["intent_digest"]
                or data["action"] != "stack_up" or data["request_id"] != scope.request_id
                or data["principal_id"] != scope.principal_id or data["project_ref"] != scope.project_ref
                or data["target_ref"] != {"target_id": row["target_name"],
                    "copy_id": row["copy_id"], "incarnation": row["target_incarnation"]}
                or data["source_commit"] != row["source_commit"] or data["environment"] != row["environment"]
                or data["expected_installation"]["installation_id"] != scope.installation_id
                or data["expected_installation"]["incarnation"] != scope.incarnation
                or not math.isfinite(row["created_at"])):
            _error()
        return TargetPreparationEvidence(scope, row["project_identity"], row["target_project"],
            row["environment"], row["target_name"], row["copy_id"], row["target_incarnation"],
            row["source_commit"], row["intent_digest"], row["operation_id"], canonical, row["created_at"])
    except (TypeError, ValueError, KeyError, OverflowError):
        _error()


def lookup(ledger, scope):
    with ledger._transaction() as connection:
        row = _find(connection, scope)
        return _evidence(row) if row is not None else None


def prepare(ledger, intent, *, project_identity, project):
    from .credential_operations import _private_identity
    basis = _basis(intent, project)
    if not _private_identity(project_identity):
        _error()
    scope = intent.request_scope()
    with ledger._transaction(write=True) as connection:
        row = _find(connection, scope)
        if row is not None:
            if row["basis_json"] != basis or row["project_identity"] != project_identity:
                _error("credential_intent_conflict")
            return _evidence(row)
        if ledger._find(connection, scope) is not None:
            # Never create preparation retroactively for an existing request.
            _error("credential_target_preparation_conflict")
        if intent.requested_deadline <= time.time():
            _error("credential_deadline_expired")
        connection.execute('''INSERT INTO credential_target_preparations
            (installation_id,incarnation,principal_id,project_ref,request_id,project_identity,
             target_project,basis_json,intent_json,intent_digest,target_name,copy_id,target_incarnation,
             source_commit,environment,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (*_scope(scope), project_identity, project, basis, intent.canonical_json(), intent.intent_digest(),
             intent.target_ref.target_id, intent.target_ref.copy_id, intent.target_ref.incarnation,
             intent.source_commit, intent.environment, time.time()))
        return _evidence(_find(connection, scope))


def require_for_reservation(connection, intent, *, project_identity):
    if intent.action != "stack_up":
        return
    row = _find(connection, intent.request_scope())
    if (row is None or row["project_identity"] != project_identity
            or row["intent_json"] != intent.canonical_json() or row["intent_digest"] != intent.intent_digest()):
        _error("credential_target_preparation_missing")
    _evidence(row)


def bind_operation(connection, intent, operation_id):
    if intent.action != "stack_up":
        return
    row = _find(connection, intent.request_scope())
    if row is None or row["operation_id"] not in (None, operation_id):
        _error("credential_target_preparation_conflict")
    connection.execute('''UPDATE credential_target_preparations SET operation_id=? WHERE
        installation_id=? AND incarnation=? AND principal_id=? AND project_ref=? AND request_id=?''',
        (operation_id, *_scope(intent.request_scope())))


__all__ = ["TargetPreparationEvidence"]
