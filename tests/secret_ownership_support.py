"""Synthetic-only ownership acceptance fixtures and an in-memory provider API.

The API replaces transport only. Production worker parsing, framing decisions,
batch validation, grants, approvals checks and journals remain in the test path.
No approval or bootstrap credential is persisted by these helpers.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import sys
import time
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from sandbox.commands.secrets import cmd_secrets, configure_parser
from sandbox.config.secrets import normalize_secret_config
from sandbox.secrets.authority import validate_ownership_grant
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership import migrate
from sandbox.secrets.ownership.approval import ApprovalStore
from sandbox.secrets.ownership.broker import validate_create_inputs
from sandbox.secrets.ownership.journal import (
    destination_sha256, load_controller_id, new_qualified_id, read_journal_states,
)
from sandbox.secrets.ownership.models import (
    Approval, CreateOutcome, Existence, NameState, OwnershipConfig,
    OwnershipContext, RevocationOutcome, StorageAdmission,
)
from sandbox.secrets.ownership.source_view import read_names
from sandbox.secrets.ownership.worker import (
    REVOKE_ACKNOWLEDGMENT, REVOKE_PATH, SECRET_PREFIX, _Uncertain, _Worker,
)
from sandbox.secrets.sources import SourceRegistry
from tests.subprocess_support import run_test_process, synthetic_environment


REPO = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    """Fixed failures avoid value-bearing assertion introspection."""
    if not condition:
        raise AssertionError(message)


def private_write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
    finally:
        os.close(descriptor)


def decode_public(raw: str) -> dict:
    try:
        result = json.loads(raw)
    except (ValueError, TypeError):
        raise AssertionError("expected one public JSON result")
    require(isinstance(result, dict), "public result must be an object")
    return result


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def time(self) -> float:
        return self.now


class SyntheticOwnership:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.sandbox_home = self.root / "sandbox-home"
        self.tmp = self.root / "temp"
        for directory in (self.project, self.home, self.sandbox_home, self.tmp):
            directory.mkdir(mode=0o700)
        self.source = self.project / ".env.synthetic"
        self.values = {name: secrets.token_hex(24) for name in ("ALPHA", "BETA", "GAMMA")}
        self.canaries = list(self.values.values())
        self.write_source()
        self.probe = self.root / "presence.py"
        private_write(self.probe, (
            'import json, os\n'
            'print(json.dumps([n for n in ("APP_ALPHA", "APP_BETA", "GAMMA") '
            'if n in os.environ]))\n'
        ).encode("ascii"))
        self.block = {
            "profiles": {
                domain: {"domain": domain, "destination": {
                    "endpoint": "https://manager.example.invalid", "projectId": domain + "-project",
                    "environment": "dev", "path": "/", "readClientId": domain + "-reader",
                    "createClientId": domain + "-creator", "timeoutSeconds": 2, "gateway": "none",
                }} for domain in ("personal", "office")
            },
            "entries": [self.entry("ALPHA", "personal", consumer="APP_ALPHA"),
                        self.entry("BETA", "personal", consumer="APP_BETA"),
                        self.entry("GAMMA", "office")],
        }
        self.sources = {"synthetic-file": {
            "type": "file", "path": self.source.name, "format": "dotenv", "mcpModes": ["keys"],
        }}
        self.write_config()
        self.env = synthetic_environment({
            "HOME": str(self.home), "SANDBOX_HOME": str(self.sandbox_home),
            "SANDBOX_PROJECT_ROOTS": str(self.root), "TMPDIR": str(self.tmp),
            "TMP": str(self.tmp), "TEMP": str(self.tmp), "PYTHONDONTWRITEBYTECODE": "1",
        })

    def __repr__(self) -> str:
        return "<synthetic ownership fixture>"

    @staticmethod
    def entry(key, profile, *, name=None, consumer=None, origin="source") -> dict:
        return {"source": "synthetic-file", "key": key, "profile": profile,
                "name": name or key, "consumer": consumer or key, "origin": origin}

    def write_source(self) -> None:
        # Only generated synthetic bytes are written; no raw source is opened for reading.
        text = "".join(name + "='" + value + "'\n" for name, value in self.values.items())
        private_write(self.source, text.encode("utf-8"))

    def fresh_bytes(self) -> bytearray:
        value = secrets.token_hex(24)
        self.canaries.append(value)
        return bytearray(value.encode("ascii"))

    def write_config(self, *, ownership=True) -> None:
        block = {"sources": self.sources}
        if ownership:
            block["ownership"] = self.block
        private_write(self.project / "sandbox.config.json", json.dumps({
            "slug": "synthetic-ownership", "secrets": block,
        }).encode("ascii"))

    def context(self) -> OwnershipContext:
        normalized = normalize_secret_config({
            "root": str(self.project), "secrets": {"sources": self.sources, "ownership": self.block},
        })
        runtime = self.sandbox_home / "runtime" / "secret-ownership"
        registry = SourceRegistry(self.project, normalized["sources"],
                                  personal_path=self.home / ".env.unused")
        return OwnershipContext(
            OwnershipConfig.from_normalized(normalized["ownership"]), registry,
            self.project, runtime, load_controller_id(runtime), None, None,
        )

    def no_disclosure(self, *outputs, ownership=True) -> None:
        for output in outputs:
            text = output if isinstance(output, str) else json.dumps(output, sort_keys=True)
            require(all(value not in text for value in self.canaries), "synthetic value disclosure")
            require("r1_" not in text, "revision disclosure")
            if ownership:
                self._metadata_only(output)

    def _metadata_only(self, node) -> None:
        if isinstance(node, dict):
            forbidden = {"value", "secretValue", "length", "exact_length", "length_bucket",
                         "prefix", "suffix", "revision", "value_sha256"}
            require(not forbidden.intersection(node), "value-derived public field")
            for value in node.values():
                self._metadata_only(value)
        elif isinstance(node, (tuple, list)):
            for value in node:
                self._metadata_only(value)

    def records_are_public(self, ctx: OwnershipContext) -> None:
        # Only ownership-generated metadata artifacts, never the dotenv source.
        for path in ctx.runtime_root.rglob("*"):
            if path.is_file() and path.suffix in (".json", ".jsonl"):
                raw = path.read_text("ascii")
                self.no_disclosure(raw)
                for line in raw.splitlines():
                    self._metadata_only(decode_public(line))
                require(path.stat().st_mode & 0o777 == 0o600, "state file permissions")
            elif path.is_dir():
                require(path.stat().st_mode & 0o777 == 0o700, "state directory permissions")

    def cli(self, *args: str, ownership=True) -> tuple[int, dict]:
        try:
            result = run_test_process(
                [str(REPO / "sb"), "secrets", *args], env=self.env, cwd=REPO,
                timeout=4, capture_output=True, text=True, input="",
            )
        except subprocess.TimeoutExpired:
            raise AssertionError("CLI exceeded the finite acceptance deadline")
        self.no_disclosure(result.stdout, result.stderr, ownership=ownership)
        body = decode_public(result.stdout)
        self.no_disclosure(body, ownership=ownership)
        return result.returncode, body

    def cli_ownership(self, action: str, *args: str) -> tuple[int, dict]:
        return self.cli("ownership", action, "--profile", "personal", "--json",
                        "--project-dir", str(self.project), *args)

    def scoped_args(self, *keys: str, argv=None) -> list[str]:
        args = ["run", "--ownership-profile", "personal", "--project-dir", str(self.project), "--json"]
        for key in keys:
            args.extend(("--ownership-key", key))
        return args + ["--", *(argv or (sys.executable, "-I", str(self.probe)))]

    def synthetic_approval(self, ctx, patcher, clock: Clock) -> Approval:
        """In-memory load/evidence seams only; real require_current stays active."""
        continuity, _ = read_names(ctx.registry, "synthetic-file")
        destination = ctx.config.profiles["personal"].destination
        approval = Approval(
            new_qualified_id("ap", ctx.controller_id), "migrate", os.geteuid(),
            pwd.getpwuid(os.geteuid()).pw_name, ctx.controller_id, "personal", (continuity,),
            ctx.config.map_sha256, destination_sha256(destination),
            migrate.authority_for(ctx, "personal", "create"), "0" * 64,
            clock.now + 120, clock.now, clock.now + 120, "cli_tty",
        )
        patcher.enter_context(patch.object(ApprovalStore, "load", lambda _store, _identifier: approval))
        patcher.enter_context(patch.object(migrate, "time", clock))
        patcher.enter_context(patch.object(migrate, "load_admission", lambda *_args: StorageAdmission(
            "0" * 64, destination.endpoint, destination.project_id,
            clock.now, clock.now + 120, "operator_attested",
        )))
        return approval


def command_in_process(ctx, patcher, adapter, *argv) -> tuple[int, dict]:
    from contextlib import redirect_stderr, redirect_stdout
    import sandbox.secrets.context as composition

    parser = argparse.ArgumentParser()
    configure_parser(parser)
    args = parser.parse_args(list(argv) + ["--project-dir", str(ctx.project_root), "--json"])
    patcher.enter_context(patch.object(composition, "build_ownership_context", lambda _root: ctx))
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = cmd_secrets({}, args, ownership_adapter=adapter)
    require(not stderr.getvalue(), "unexpected CLI stderr")
    return result, decode_public(stdout.getvalue())


def fake_grant(ctx, *, capability="create", names=("ALPHA", "BETA")):
    return migrate.build_grant(ctx, "personal", capability, tuple(names))


class FakeAPI:
    """Exact-name create-only Infisical responses, held entirely in memory."""
    def __init__(self, *, script=None, clock=None, expires=False, revoke_unknown=False,
                 canaries=None) -> None:
        self.script = dict(script or {})
        self.clock = clock or Clock()
        self.expires = expires
        self.revoke_unknown = revoke_unknown
        self.canaries = canaries if canaries is not None else []
        self.items = {}
        self.comments = {}
        self.requests = []  # method/name/view only; never bodies or values
        self.grants = []
        self.posts = []
        self.on_post = None
        self.bad_readback = set()

    def __repr__(self) -> str:
        return "<in-memory synthetic API>"

    @staticmethod
    def response(status, data):
        return status, bytearray(json.dumps(data).encode("utf-8"))

    def request(self, grant, method, target, body=None, **_kwargs):
        url = urlsplit(target)
        if target == REVOKE_PATH:
            self.requests.append((method, "revoke", False))
            return self.response(503 if self.revoke_unknown else 200,
                                 {} if self.revoke_unknown else {"message": REVOKE_ACKNOWLEDGMENT})
        require(url.path.startswith(SECRET_PREFIX), "unexpected provider route")
        name = unquote(url.path.removeprefix(SECRET_PREFIX))
        require(name in grant.names, "fake API rejected wider name authority")
        query = parse_qs(url.query)
        view = query.get("viewSecretValue") == ["true"]
        self.requests.append((method, name, view))
        if method == "POST":
            require(grant.capability == "create", "POST requires a create grant")
            if self.on_post is not None:
                self.on_post(name)
            self.posts.append(name)
            data = json.loads(body)
            destination = grant.destination
            require(set(data) == {"projectId", "environment", "secretPath", "secretValue",
                                  "secretComment", "type"}, "create wire shape changed")
            require((data["projectId"], data["environment"], data["secretPath"], data["type"])
                    == (destination.project_id, destination.environment, destination.path, "shared"),
                    "create destination changed")
            behavior = self.script.get(name)
            if behavior == "conflict":
                foreign_value = secrets.token_hex(24)
                self.canaries.append(foreign_value)
                self.items[name] = bytearray(foreign_value.encode("ascii"))
            if name in self.items:
                return self.response(400, {"message": "Secret '" + name + "' already exists"})
            if behavior == "approval":
                return self.response(200, {"approval": {"id": "synthetic-request"}})
            if behavior == "malformed":
                return 200, bytearray(b"{invalid-json")
            self.items[name] = bytearray(data["secretValue"].encode("utf-8"))
            self.comments[name] = data["secretComment"]
            if behavior == "bad_readback":
                self.bad_readback.add(name)
            if self.expires:
                self.clock.now += 2
            if behavior == "lost":
                raise _Uncertain("operation_timeout")
            # The create reply intentionally omits the value, as the frozen contract allows.
            # Live v0.166.5 create replies also omit secretPath (observed 2026-10-27).
            record = self.record(grant, name, hidden=False, include_value=False)
            del record["secretPath"]
            return self.response(200, {"secret": record})
        require(method == "GET", "unexpected provider method")
        require(query == {"projectId": [grant.destination.project_id],
                          "environment": [grant.destination.environment],
                          "secretPath": [grant.destination.path], "type": ["shared"],
                          "viewSecretValue": ["true" if view else "false"],
                          "expandSecretReferences": ["false"], "includeImports": ["false"]},
                "exact-name GET wire shape changed")
        if name not in self.items:
            return self.response(404, {"message": "not found"})
        if view and name in self.bad_readback:
            return self.response(200, {"secret": {"secretKey": name}})
        return self.response(200, {"secret": self.record(grant, name, hidden=not view)})

    def record(self, grant, name, *, hidden, include_value=True):
        record = {"secretKey": name, "workspace": grant.destination.project_id,
                  "environment": grant.destination.environment, "secretPath": grant.destination.path,
                  "type": "shared", "secretComment": self.comments.get(name, "")}
        if hidden:
            # Live v0.166.5 returns its fixed mask, not null (observed 2026-10-27).
            record.update(secretValueHidden=True, secretValue="<hidden-by-infisical>")
        elif include_value:
            record.update(secretValueHidden=False, secretValue=self.items[name].decode("utf-8"))
        return record


class _MemoryChannel:
    def __init__(self) -> None:
        self.messages = []
        self.value = None
        self.before_send = None
        self.name = None

    def send_json(self, message):
        self.messages.append(message)

    def recv_value(self):
        return bytearray(self.value)

    def recv_json(self):
        self.before_send()
        return {"op": "go", "name": self.name}


class FakeOwnershipSession:
    def __init__(self, api, grant, deadline) -> None:
        self.api, self.grant = api, grant
        self.token_expires_at = api.clock.now + (1 if api.expires else 90)
        self.owners = {}
        self.channel = _MemoryChannel()
        # Deliberately skip production initialization, login and native credential stores.
        self.worker = object.__new__(_Worker)
        self.worker.channel = self.channel
        self.worker.grant = grant
        self.worker.deadline = deadline
        self.worker._work_end = time.monotonic() + 2
        self.worker.token = secrets.token_hex(24)
        api.canaries.append(self.worker.token)
        self.worker.token_expires_at = self.token_expires_at
        self.worker.revocation = None
        self.worker._revoking = False
        self.worker.started, self.worker.attempted = set(), set()
        self.worker._request = lambda *args, **kwargs: api.request(grant, *args, **kwargs)
        self.status = None

    def __enter__(self):
        validate_ownership_grant(self.grant)
        return self

    def __exit__(self, *_exc):
        self.status = self.worker.revoke()

    def policy_requires_approval(self):
        return None

    def exists(self, names):
        self.worker._exists(self.worker._names(list(names)))
        response = self.channel.messages[-1]
        self.owners = response["owners"]
        return {name: Existence(state) for name, state in response["result"].items()}

    def create(self, name, value, *, comment, before_send):
        self.channel.value, self.channel.name = value, name
        self.channel.before_send = before_send
        try:
            self.worker._create(self.worker._name(name), comment)
            response = self.channel.messages[-1]
            require(response["op"] == "created", "worker create handshake incomplete")
            return CreateOutcome(name, NameState(response["state"]), response["reason"])
        finally:
            self.channel.value = self.channel.before_send = None

    def readback_equals(self, name, value):
        self.channel.value = value
        try:
            self.worker._readback(self.worker._name(name))
            return self.channel.messages[-1]["equal"]
        finally:
            self.channel.value = None

    def read(self, names):
        require(self.grant.capability == "read", "read requires a read grant")
        result = {}
        for name in self.worker._names(list(names)):
            value = self.worker._fetch_value(name)
            require(isinstance(value, bytearray), "fake exact read failed")
            result[name] = value
        return result

    def revocation(self):
        require(self.status is not None, "session revocation requested before close")
        return RevocationOutcome(self.status, self.token_expires_at, "operator-managed cleanup")


class FakeOwnershipAdapter:
    def __init__(self, api=None) -> None:
        self.api = api or FakeAPI()
        self.validated_batches = []

    def validate_create_inputs(self, grant, values):
        self.validated_batches.append(tuple(sorted(values)))
        validate_create_inputs(grant, values)

    def open_session(self, grant, *, deadline):
        validate_ownership_grant(grant)
        self.api.grants.append(grant)
        return FakeOwnershipSession(self.api, grant, deadline)


def journal_states(ctx):
    return read_journal_states(ctx.runtime_root, "personal",
                               destination_sha256(ctx.config.profiles["personal"].destination)) or {}
