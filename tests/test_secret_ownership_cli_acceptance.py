"""Spec069 acceptance after supported CLI acceptance, synthetic data only."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

from sandbox.config.secrets import normalize_secret_config
from sandbox.secrets.audit import OWNERSHIP_OPERATIONS, SecretAudit
from sandbox.secrets.authority import bind_provider_authorities, validate_ownership_grant
from sandbox.secrets.models import MAX_OUTPUT_BYTES, SecretBrokerError
from sandbox.secrets.ownership import migrate, scoped_run, source_view
from sandbox.secrets.ownership.approval import ApprovalStore
from sandbox.secrets.ownership.broker import TRIM_STRING_CODE_POINTS
from sandbox.secrets.ownership.cleanup import run_cleanup
from sandbox.secrets.ownership.journal import destination_sha256, journal_dir, read_private_file
from sandbox.secrets.ownership.models import NameState, ScopedRunRequest
from tests.secret_ownership_support import (
    Clock, FakeAPI, FakeOwnershipAdapter, SyntheticOwnership, command_in_process,
    fake_grant, journal_states, private_write, require,
)


def expect_error(code, function):
    seen = None
    try:
        function()
    except SecretBrokerError as error:
        seen = error.code
    require(seen == code, "expected fixed broker refusal was not observed")


def execute(case, adapter):
    ctx, _clock, approval = case
    return migrate.run_migration(ctx, "personal", approval.approval_id, confirm=True,
                                 timeout_seconds=2, adapter=adapter)


def refusal_request(synthetic, variant):
    if variant == "cross-domain":
        return ("ALPHA", "GAMMA"), (sys.executable, "-I", str(synthetic.probe)), "other_domain"
    if variant == "direct":
        argv = ("/nonexistent-synthetic/sudo", "true")
    elif variant == "env-wrapper":
        argv = ("/usr/bin/env", "sudo", "true")
    else:
        target = synthetic.root / "sudo"
        private_write(target, b"#!/bin/sh\nexit 97\n")
        link = synthetic.root / "benign-command"
        link.symlink_to(target)
        argv = (str(link),)
    return ("ALPHA",), argv, "escalation_unsupported"


class SecretOwnershipCLIAcceptanceTests(unittest.TestCase):
    """Acceptance test suite for secret ownership under standard-library unittest."""

    def __init__(self, methodName="runTest", **params):
        super().__init__(methodName)
        self.params = params

    def __str__(self):
        if self.params:
            if "codepoint" in self.params and "edge" in self.params:
                cp = self.params["codepoint"]
                edge = self.params["edge"]
                param_desc = f"boundary-{cp:04x}, edge={edge}"
            else:
                param_desc = ", ".join(f"{k}={v}" for k, v in self.params.items())
            return f"{self._testMethodName} [{param_desc}] ({self.__class__.__module__}.{self.__class__.__qualname__})"
        return super().__str__()

    def id(self):
        base = super().id()
        if self.params:
            if "codepoint" in self.params and "edge" in self.params:
                cp = self.params["codepoint"]
                edge = self.params["edge"]
                param_desc = f"boundary_{cp:04x}_edge_{edge}"
            else:
                param_desc = "_".join(f"{k}_{v}" for k, v in self.params.items())
            return f"{base}__{param_desc}"
        return base

    def setUp(self):
        self.exit_stack = ExitStack()
        self.addCleanup(self.exit_stack.close)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("network or native credential backend reached")

        import sandbox.secrets.credentials.manifest as credentials
        self.exit_stack.enter_context(patch.object(credentials, "create_credential_store", forbidden))
        self.exit_stack.enter_context(patch.object(socket, "create_connection", forbidden))

        previous_umask = os.umask(0o077)
        self.addCleanup(lambda: os.umask(previous_umask))

        temp_root = Path("/tmp").resolve(strict=True)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="s069-acceptance-", dir=temp_root)
        self.addCleanup(self.temp_dir.cleanup)

        root = Path(self.temp_dir.name).resolve()
        require(root.parent == temp_root, "temporary root must be canonical")
        self.synthetic = SyntheticOwnership(root)

    def tearDown(self):
        if hasattr(self, "synthetic"):
            ctx = self.synthetic.context()
            if ctx.runtime_root.exists():
                self.synthetic.records_are_public(ctx)

    def migration_case(self):
        ctx = self.synthetic.context()
        clock = Clock()
        approval = self.synthetic.synthetic_approval(ctx, self.exit_stack, clock)
        return ctx, clock, approval

    def test_cli_clean_classification_is_names_only_and_source_unchanged(self):
        before = self.synthetic.source.stat()
        code, body = self.synthetic.cli_ownership("classify")
        after = self.synthetic.source.stat()
        require(code == 0 and body["code"] == "classified", "clean classification failed")
        require(body["names"] == {"unchecked": ["ALPHA", "BETA"]}, "unexpected classification names")
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
        require(all(getattr(before, field) == getattr(after, field) for field in fields),
                "classification changed source metadata")
        ctx = self.synthetic.context()
        continuity, _ = source_view.read_names(ctx.registry, "synthetic-file")
        values = source_view.read_values(ctx.registry, "synthetic-file", tuple(self.synthetic.values), continuity)
        try:
            require(all(values[key] == self.synthetic.values[key].encode("ascii") for key in values),
                    "classification changed synthetic source bytes")
        finally:
            migrate.wipe(values)

    def test_cli_blocking_classification_preserves_undecided_and_multiple_assignments(self):
        self.synthetic.block["entries"][-1]["profile"] = "undecided"
        self.synthetic.block["entries"].append(self.synthetic.entry("BETA", "personal", name="BETA_COPY"))
        self.synthetic.write_config()
        code, body = self.synthetic.cli_ownership("classify")
        require(code == 2 and body["code"] == "classification_blocked", "blocking classification failed")
        require(body["names"].get("undecided") == ["GAMMA"], "undecided key missing")
        require(body["names"].get("multiply_assigned") == ["BETA"], "multiple assignment missing")
        require(body["names"].get("unchecked") == ["ALPHA"], "classification consulted destination")

    def test_cli_unconfirmed_migration_is_a_credential_free_plan(self):
        code, body = self.synthetic.cli_ownership("migrate")
        require(code == 0 and body["code"] == "dry_run", "migration dry run failed")
        require(body["names"] == {"unchecked": ["ALPHA", "BETA"]}, "dry run checked provider")
        require(body["storage_attestation"] == "operator_attested", "attestation label changed")
        root = self.synthetic.context().runtime_root
        require(not (root / "journals").exists() and not (root / "approvals").exists(),
                "dry run wrote migration authority")

    def test_cli_confirm_without_approval_refuses(self):
        code, body = self.synthetic.cli_ownership("migrate", "--confirm")
        require(code == 3 and body["code"] == "approval_required", "missing approval accepted")
        require(not (self.synthetic.context().runtime_root / "journals").exists(), "unapproved journal created")

    def test_cli_approve_non_tty_refuses_without_approval_artifacts(self):
        code, body = self.synthetic.cli_ownership("approve", "--operation", "migrate")
        require(code == 3 and body["code"] == "approval_human_only", "non-TTY approval accepted")
        require(not (self.synthetic.sandbox_home / "runtime" / "secret-ownership" / "approvals").exists(),
                "non-TTY call created approval artifacts")

    def test_cli_profile_requires_explicit_keys(self):
        code, body = self.synthetic.cli(*self.synthetic.scoped_args())
        require(code == 2 and body["code"] == "ownership_keys_required", "implicit selection accepted")

    def test_cli_scoped_run_injects_only_selected_consumer_presence(self):
        code, body = self.synthetic.cli(*self.synthetic.scoped_args("ALPHA"))
        require(code == 0 and body["code"] == "ran", "scoped run failed")
        require(body["names"] == {"selected": ["ALPHA"]}, "selected names changed")
        require(json.loads(body["result"]["output"]) == ["APP_ALPHA"], "unselected binding reached child")

    def test_cli_selection_and_escalation_refusals(self, variant=None):
        if variant is None:
            variant = self.params.get("variant", "cross-domain")
        keys, argv, expected = refusal_request(self.synthetic, variant)
        code, body = self.synthetic.cli(*self.synthetic.scoped_args(*keys, argv=argv))
        require(code == 2 and body["code"] == expected, "unsafe CLI request was not refused")

    def test_scoped_refusals_precede_all_source_reads(self, variant=None):
        if variant is None:
            variant = self.params.get("variant", "cross-domain")
        ctx = self.synthetic.context()
        keys, argv, expected = refusal_request(self.synthetic, variant)
        reads = []

        def forbidden_read(*_args, **_kwargs):
            reads.append(True)
            raise SecretBrokerError("source_changed", "test-only read sentinel")

        self.exit_stack.enter_context(patch.object(scoped_run, "read_names", forbidden_read))
        self.exit_stack.enter_context(patch.object(scoped_run, "read_values", forbidden_read))
        request = ScopedRunRequest("personal", keys, argv, 2, MAX_OUTPUT_BYTES)
        expect_error(expected, lambda: scoped_run.resolve_scoped_bindings(ctx, request))
        require(not reads, "refusal occurred after a source read")

    def test_source_view_detects_value_only_edit_with_restored_mtime(self):
        ctx = self.synthetic.context()
        approved, names = source_view.read_names(ctx.registry, "synthetic-file")
        before = self.synthetic.source.stat()
        replacement = secrets.token_hex(24)
        self.synthetic.canaries.append(replacement)
        self.synthetic.values["ALPHA"] = replacement
        self.synthetic.write_source()
        os.utime(self.synthetic.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        fresh, fresh_names = source_view.read_names(ctx.registry, "synthetic-file")
        require(approved.size == fresh.size and approved.mtime_ns == fresh.mtime_ns and names == fresh_names,
                "continuity fixture changed more than the value and ctime")
        require(approved.ctime_ns != fresh.ctime_ns, "filesystem did not advance ctime")
        expect_error("source_changed", lambda: source_view.read_values(
            ctx.registry, "synthetic-file", ("ALPHA",), approved))

    def test_no_ownership_preserves_normalized_shape_and_legacy_authority_digest(self):
        descriptor = {"type": "infisical", "endpoint": "https://manager.example.invalid",
                      "projectId": "legacy-project", "environment": "dev", "path": "/",
                      "key": "ALPHA", "clientId": "legacy-reader", "mcpModes": ["keys"],
                      "timeoutSeconds": 2}
        normalized = normalize_secret_config({"root": str(self.synthetic.project),
                                              "secrets": {"sources": {"legacy": descriptor}}})
        require(set(normalized) == {"sources", "useProfiles"}, "legacy config gained ownership fields")
        sources, profiles = normalized["sources"], normalized["useProfiles"]
        bound = bind_provider_authorities(sources, profiles, project_root=self.synthetic.project)
        explicit_none = bind_provider_authorities(sources, profiles, project_root=self.synthetic.project, ownership=None)
        # This hashes canonical public authority metadata only, never a secret value.
        metadata = {"version": 1, "source": "legacy", "project_root": str(self.synthetic.project),
                    "descriptor": {key: sources["legacy"].get(key) for key in (
                        "type", "endpoint", "projectId", "environment", "path", "key", "clientId",
                        "mcpModes", "timeoutSeconds")}, "profiles": []}
        baseline = "a1-" + hashlib.sha256(json.dumps(
            metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
        require(bound == explicit_none and bound["legacy"]["authorityDigest"] == baseline,
                "legacy authority digest changed")

    def test_cli_without_ownership_preserves_inspect_keys_shape(self):
        self.synthetic.write_config(ownership=False)
        code, body = self.synthetic.cli("inspect", "--source", "synthetic-file", "--json",
                                        "--project-dir", str(self.synthetic.project), ownership=False)
        require(code == 0, "legacy key inspection failed")
        require(set(body) == {"ok", "operation", "source", "keys", "count", "correlation_id"},
                "legacy keys result shape changed")
        require(body["operation"] == "keys" and body["keys"] == ["ALPHA", "BETA", "GAMMA"] and body["count"] == 3,
                "legacy key listing changed")

    def test_evidence_and_contract_gates_precede_provider_work(self, gate=None):
        if gate is None:
            gate = self.params.get("gate", "missing-evidence")
        import sandbox.secrets.ownership.manifest as manifest

        ctx, clock, approval = self.migration_case()
        api = FakeAPI(clock=clock, canaries=self.synthetic.canaries)
        adapter = FakeOwnershipAdapter(api)
        if gate == "missing-evidence":
            def refuse_evidence(*_args):
                raise SecretBrokerError("evidence_required", "test-only missing evidence")

            self.exit_stack.enter_context(patch.object(migrate, "load_admission", refuse_evidence))
            expected = "evidence_required"
        else:
            self.exit_stack.enter_context(patch.object(manifest, "OWNERSHIP_MANIFEST", {"infisical-v4": {
                "capabilities": ("read", "create", "revoke"), "contract_confirmed": False,
            }}))
            adapter, expected = None, "provider_contract_unconfirmed"
        expect_error(expected, lambda: migrate.run_migration(
            ctx, "personal", approval.approval_id, confirm=True, timeout_seconds=2, adapter=adapter))
        require(not api.requests and not api.grants and not api.posts, "refused gate contacted provider")
        require(not (ctx.runtime_root / "journals").exists(), "refused gate wrote migration journal")

    def test_fake_api_clean_migration_verifies_all_and_journals_before_post(self):
        case = self.migration_case()
        ctx, clock, _approval = case
        api = FakeAPI(clock=clock, canaries=self.synthetic.canaries)

        before_sends = []
        adapter = FakeOwnershipAdapter(api)
        original_open_session = adapter.open_session

        def tracking_open_session(grant, *, deadline):
            session = original_open_session(grant, deadline=deadline)
            original_create = session.create

            def tracking_create(name, value, *, comment, before_send):
                def wrapped_before_send():
                    before_sends.append(name)
                    before_send()

                return original_create(name, value, comment=comment, before_send=wrapped_before_send)

            session.create = tracking_create
            return session

        adapter.open_session = tracking_open_session

        def parse_unique_pairs(pairs):
            res = {}
            for k, v in pairs:
                if k in res:
                    raise ValueError("duplicate field")
                res[k] = v
            return res

        def before_post(name):
            require(name not in api.posts, "POST preceded durable attempted record")
            require(name in before_sends, "POST preceded durable attempted record")
            digest = destination_sha256(ctx.config.profiles["personal"].destination)
            journal_path = journal_dir(ctx.runtime_root, "personal", digest) / "journal.jsonl"
            raw = read_private_file(journal_path)
            end = raw.rfind(b"\n") + 1
            lines = raw[:end].split(b"\n")[:-1] if end else []
            records = [json.loads(line.decode("ascii"), object_pairs_hook=parse_unique_pairs)
                       for line in lines[1:]]
            name_records = [r for r in records if r.get("name") == name]
            require(bool(name_records) and name_records[-1].get("state") == "attempted",
                    "POST preceded durable attempted record")

        api.on_post = before_post
        result = execute(case, adapter)
        require(result["_exit"] == 0 and result["names"] == {"verified": ["ALPHA", "BETA"]},
                "clean fake-provider migration failed")
        require(api.posts == ["ALPHA", "BETA"] and adapter.validated_batches == [("ALPHA", "BETA")],
                "batch validation or create count changed")
        require(before_sends == ["ALPHA", "BETA"], "batch validation or create count changed")
        require(all(item.capability == "create" for item in api.grants), "clean migration grant changed")
        self.synthetic.no_disclosure(result)

    def test_initial_existing_blocks_entire_batch_without_value_reads(self):
        case = self.migration_case()
        _ctx, clock, _approval = case
        api = FakeAPI(clock=clock, canaries=self.synthetic.canaries)
        api.items["BETA"] = self.synthetic.fresh_bytes()
        reads = []

        def forbidden(*_args):
            reads.append(True)
            raise SecretBrokerError("source_changed", "test-only read sentinel")

        self.exit_stack.enter_context(patch.object(migrate, "read_entry_values", forbidden))
        result = execute(case, FakeOwnershipAdapter(api))
        require(result["code"] == "pre_existing" and result["_exit"] == 4, "existing item did not block batch")
        require(not api.posts and not reads, "pre-existing batch read values or sent POST")
        require(not any(view for _method, _name, view in api.requests), "pre-existing preflight read a provider value")

    def test_concurrent_conflict_stops_and_keeps_prior_verified_receipt(self):
        case = self.migration_case()
        ctx, clock, _approval = case
        api = FakeAPI(script={"BETA": "conflict"}, clock=clock, canaries=self.synthetic.canaries)
        result = execute(case, FakeOwnershipAdapter(api))
        require(result["_exit"] == 5 and result["names"] == {"conflict": ["BETA"], "verified": ["ALPHA"]},
                "conflict response did not record verified ALPHA and conflict BETA")
        require(journal_states(ctx)["ALPHA"] is NameState.VERIFIED, "prior verified journal receipt lost")
        require(api.posts == ["ALPHA", "BETA"], "conflict item stopped batch without executing create")
        self.synthetic.no_disclosure(result)

    def test_unknown_create_resumes_with_read_then_create_and_exactly_one_item(self):
        case = self.migration_case()
        ctx, clock, _approval = case
        api = FakeAPI(script={"ALPHA": "lost"}, clock=clock, canaries=self.synthetic.canaries)
        adapter = FakeOwnershipAdapter(api)
        first = execute(case, adapter)
        require(first["_exit"] == 5 and first["names"] == {"acceptance_unknown": ["ALPHA"], "pending": ["BETA"]},
                "lost post did not report acceptance_unknown and pending")
        require(api.posts == ["ALPHA"] and journal_states(ctx) == {"ALPHA": NameState.ACCEPTANCE_UNKNOWN, "BETA": NameState.PENDING},
                "interrupted item did not mark unknown and preserve pending")
        self.synthetic.no_disclosure(first)
        # Resuming with the created item retained by FakeAPI reconciles ALPHA under READ
        # and proceeds to BETA with exactly one POST.
        second = execute(case, adapter)
        require(second["_exit"] == 0 and second["names"] == {"verified": ["BETA"], "reconciled": ["ALPHA"]},
                "resume after acceptance_unknown did not reconcile ALPHA and verify BETA")
        require(api.posts == ["ALPHA", "BETA"], "resumed migration re-posted reconciled item")
        require(adapter.validated_batches == [("ALPHA", "BETA"), ("BETA",)],
                "batch was not validated on both runs")
        require(
            [(g.capability, g.names) for g in api.grants] == [
                ("create", ("ALPHA", "BETA")),
                ("read", ("ALPHA",)),
                ("create", ("ALPHA", "BETA")),
            ],
            "grant sequence did not execute first create, exact read resume, and create for pending",
        )
        self.synthetic.no_disclosure(second)

    def test_expired_token_stops_then_expired_approval_blocks_resume(self):
        case = self.migration_case()
        ctx, clock, approval = case
        api = FakeAPI(clock=clock, expires=True, canaries=self.synthetic.canaries)
        adapter = FakeOwnershipAdapter(api)
        first = execute(case, adapter)
        require(first["_exit"] == 5 and api.posts == ["ALPHA"], "token expiry did not stop batch")
        require(journal_states(ctx) == {"ALPHA": NameState.VERIFIED, "BETA": NameState.PENDING},
                "token expiry lost settled or pending states")
        clock.now = approval.expires_at + 1
        expect_error("approval_expired", lambda: execute(case, adapter))
        require(api.posts == ["ALPHA"] and len(api.grants) == 1, "expired approval performed provider work")

    def test_widened_grant_refused_before_session(self, field=None):
        if field is None:
            field = self.params.get("field", "names")
        ctx, _clock, _approval = self.migration_case()
        grant = fake_grant(ctx, capability="read", names=("ALPHA",))
        if field == "names":
            changed = replace(grant, names=("ALPHA", "GAMMA"))
        elif field == "endpoint":
            changed = replace(grant, destination=replace(grant.destination, endpoint="https://elsewhere.example.invalid"))
        elif field == "client":
            changed = replace(grant, client_id="wider-client")
        else:
            changed = replace(grant, capability="create")
        expect_error("authority_mismatch", lambda: validate_ownership_grant(changed))

    def test_unconfirmed_responses_never_enable_source_cleanup(self, behavior=None, state=None):
        if behavior is None:
            behavior = self.params.get("behavior", "approval")
        if state is None:
            state = self.params.get("state", "unresolved")
        case = self.migration_case()
        ctx, clock, _approval = case
        api = FakeAPI(script={"ALPHA": behavior}, clock=clock, canaries=self.synthetic.canaries)
        adapter = FakeOwnershipAdapter(api)
        result = execute(case, adapter)
        require(result["_exit"] == 5 and result["names"].get(state) == ["ALPHA"],
                "unconfirmed response received a verified receipt")
        require(not result["names"].get("verified") and not result["names"].get("created"),
                "unconfirmed response counted as created or verified")
        cleanup = run_cleanup(ctx, "personal", None, confirm=False, adapter=adapter)
        require(cleanup["code"] == "cleanup_ineligible" and cleanup["_exit"] == 3,
                "unconfirmed response enabled cleanup")
        require(api.posts == ["ALPHA"], "batch continued past unresolved response")
        self.synthetic.no_disclosure(result, cleanup)

    def test_cli_adapter_injection_reports_revocation_unknown_exit_six(self):
        case = self.migration_case()
        ctx, clock, approval = case
        api = FakeAPI(clock=clock, revoke_unknown=True, canaries=self.synthetic.canaries)
        code, body = command_in_process(
            ctx, self.exit_stack, FakeOwnershipAdapter(api), "ownership", "migrate", "--profile", "personal",
            "--approval", approval.approval_id, "--confirm", "--timeout-seconds", "2",
        )
        require(code == 6 and body["code"] == "cleanup_unknown", "revocation uncertainty exit changed")
        require(body["names"] == {"verified": ["ALPHA", "BETA"]}, "revocation lost verification receipts")
        require("operator-managed" in body["next_action"] and "revocation not confirmed" in body["next_action"],
                "revocation uncertainty guidance missing")
        self.synthetic.no_disclosure(body)

    def test_last_batch_trimstring_input_rejected_before_any_post(self, codepoint=None, edge=None):
        if codepoint is None:
            codepoint = self.params.get("codepoint", sorted(TRIM_STRING_CODE_POINTS)[0])
        if edge is None:
            edge = self.params.get("edge", "leading")
        value = self.synthetic.values["BETA"]
        decorated = chr(codepoint) + value if edge == "leading" else value + chr(codepoint)
        self.synthetic.canaries.append(decorated)
        parser_inexpressible = unicodedata.category(chr(codepoint)) in {"Cc", "Cf"}
        if not parser_inexpressible:
            self.synthetic.values["BETA"] = decorated
            self.synthetic.write_source()
        ctx, clock = self.synthetic.context(), Clock()
        approval = self.synthetic.synthetic_approval(ctx, self.exit_stack, clock)
        if parser_inexpressible:
            # The dotenv grammar intentionally rejects controls. Exercise the
            # provider batch seam with RAM-only bytes rather than an invalid fixture.
            original_read = migrate.read_entry_values

            def synthetic_batch(*args):
                values = original_read(*args)
                values["BETA"][:] = b"\0" * len(values["BETA"])
                values["BETA"] = bytearray(decorated.encode("utf-8"))
                return values

            self.exit_stack.enter_context(patch.object(migrate, "read_entry_values", synthetic_batch))
        api = FakeAPI(clock=clock, canaries=self.synthetic.canaries)
        adapter = FakeOwnershipAdapter(api)
        result = execute((ctx, clock, approval), adapter)
        require(result["code"] == "value_unsupported" and result["_exit"] == 4,
                "last batch TrimString boundary was not rejected losslessly")
        require(not api.posts and not api.items, "batch validation happened after a POST")
        require(adapter.validated_batches == [("ALPHA", "BETA")], "entire batch not validated")

    def test_foreign_controller_marker_stops_and_preserves_owner_guidance(self):
        case = self.migration_case()
        _ctx, clock, _approval = case
        api = FakeAPI(clock=clock, canaries=self.synthetic.canaries)
        foreign = secrets.token_hex(16)
        api.items["ALPHA"] = self.synthetic.fresh_bytes()
        api.comments["ALPHA"] = "sandbox-ownership controller=" + foreign + " run=run_" + foreign + "_" + secrets.token_hex(8)
        result = execute(case, FakeOwnershipAdapter(api))
        require(result["code"] == "journal_missing_owner_elsewhere" and result["_exit"] == 3,
                "foreign controller destination was not blocked")
        require(foreign in result["next_action"] and "owning controller" in result["next_action"],
                "foreign controller guidance missing")
        require(not api.posts and not any(view for _method, _name, view in api.requests),
                "foreign marker caused create or value read")

    def test_ownership_audit_omits_revision_and_input_channel_keys(self):
        audit_path = self.synthetic.root / "ownership_audit.jsonl"
        audit = SecretAudit(audit_path, actor="acceptance-actor")
        for op in sorted(OWNERSHIP_OPERATIONS):
            cid = audit.intent(op, "synthetic-source", ["ALPHA", "BETA"], surface="cli")
            audit.outcome(cid, op, "synthetic-source", ["ALPHA", "BETA"], surface="cli",
                          decision="succeeded", count=2)
        raw = read_private_file(audit_path)
        lines = [line for line in raw.split(b"\n") if line]
        require(len(lines) == len(OWNERSHIP_OPERATIONS) * 2, "audit record count mismatch")
        for line in lines:
            event = json.loads(line.decode("utf-8"))
            require("revision" not in event, "ownership audit leaked revision key")
            require("input_channel" not in event, "ownership audit leaked input_channel key")
            require(event["operation"] in OWNERSHIP_OPERATIONS, "unexpected operation in audit")
            require(event["keys"] == ["ALPHA", "BETA"], "unexpected keys in audit")

    def test_ownership_audit_refuses_non_null_revision_and_input_channel(self):
        audit_path = self.synthetic.root / "ownership_audit_refusal.jsonl"
        audit = SecretAudit(audit_path, actor="acceptance-actor")
        cid = audit.intent("ownership_classify", "synthetic-source", ["ALPHA"], surface="cli")
        expect_error("audit_invalid", lambda: audit.outcome(
            cid, "ownership_classify", "synthetic-source", ["ALPHA"], surface="cli",
            decision="succeeded", revision="non-null-rev",
        ))
        expect_error("audit_invalid", lambda: audit.intent(
            "ownership_classify", "synthetic-source", ["ALPHA"], surface="cli",
            input_channel="stdin",
        ))
        expect_error("audit_invalid", lambda: audit.outcome(
            cid, "ownership_classify", "synthetic-source", ["ALPHA"], surface="cli",
            decision="succeeded", input_channel="stdin",
        ))

    def test_legacy_audit_revision_handling_unchanged(self):
        audit_path = self.synthetic.root / "legacy_audit.jsonl"
        audit = SecretAudit(audit_path, actor="acceptance-actor")
        cid1 = audit.intent("metadata", "legacy-source", ["KEY1"], surface="cli", input_channel="stdin")
        audit.outcome(cid1, "metadata", "legacy-source", ["KEY1"], surface="cli",
                      decision="succeeded", revision="legacy-rev-42", input_channel="stdin")
        cid2 = audit.intent("metadata", "legacy-source", ["KEY2"], surface="cli")
        audit.outcome(cid2, "metadata", "legacy-source", ["KEY2"], surface="cli",
                      decision="succeeded", revision=None)
        raw = read_private_file(audit_path)
        lines = [line for line in raw.split(b"\n") if line]
        require(len(lines) == 4, "legacy audit record count mismatch")
        ev_intent1, ev_outcome1, ev_intent2, ev_outcome2 = [
            json.loads(line.decode("utf-8")) for line in lines
        ]
        require(ev_intent1.get("input_channel") == "stdin", "legacy intent lost input_channel")
        require("revision" in ev_outcome1 and ev_outcome1["revision"] == "legacy-rev-42",
                "legacy outcome lost non-null revision")
        require(ev_outcome1.get("input_channel") == "stdin", "legacy outcome lost input_channel")
        require("revision" in ev_outcome2 and ev_outcome2["revision"] is None,
                "legacy outcome lost null revision field")
        require("input_channel" in ev_outcome2 and ev_outcome2["input_channel"] is None,
                "legacy outcome lost null input_channel field")


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    test_class = SecretOwnershipCLIAcceptanceTests

    # 1. test_cli_clean_classification_is_names_only_and_source_unchanged
    suite.addTest(test_class("test_cli_clean_classification_is_names_only_and_source_unchanged"))

    # 2. test_cli_blocking_classification_preserves_undecided_and_multiple_assignments
    suite.addTest(test_class("test_cli_blocking_classification_preserves_undecided_and_multiple_assignments"))

    # 3. test_cli_unconfirmed_migration_is_a_credential_free_plan
    suite.addTest(test_class("test_cli_unconfirmed_migration_is_a_credential_free_plan"))

    # 4. test_cli_confirm_without_approval_refuses
    suite.addTest(test_class("test_cli_confirm_without_approval_refuses"))

    # 5. test_cli_approve_non_tty_refuses_without_approval_artifacts
    suite.addTest(test_class("test_cli_approve_non_tty_refuses_without_approval_artifacts"))

    # 6. test_cli_profile_requires_explicit_keys
    suite.addTest(test_class("test_cli_profile_requires_explicit_keys"))

    # 7. test_cli_scoped_run_injects_only_selected_consumer_presence
    suite.addTest(test_class("test_cli_scoped_run_injects_only_selected_consumer_presence"))

    # 8. test_cli_selection_and_escalation_refusals (4 variants)
    for variant in ("cross-domain", "direct", "env-wrapper", "benign-symlink"):
        suite.addTest(test_class("test_cli_selection_and_escalation_refusals", variant=variant))

    # 9. test_scoped_refusals_precede_all_source_reads (4 variants)
    for variant in ("cross-domain", "direct", "env-wrapper", "benign-symlink"):
        suite.addTest(test_class("test_scoped_refusals_precede_all_source_reads", variant=variant))

    # 10. test_source_view_detects_value_only_edit_with_restored_mtime
    suite.addTest(test_class("test_source_view_detects_value_only_edit_with_restored_mtime"))

    # 11. test_no_ownership_preserves_normalized_shape_and_legacy_authority_digest
    suite.addTest(test_class("test_no_ownership_preserves_normalized_shape_and_legacy_authority_digest"))

    # 12. test_cli_without_ownership_preserves_inspect_keys_shape
    suite.addTest(test_class("test_cli_without_ownership_preserves_inspect_keys_shape"))

    # 13. test_evidence_and_contract_gates_precede_provider_work (2 variants)
    for gate in ("missing-evidence", "unconfirmed-contract"):
        suite.addTest(test_class("test_evidence_and_contract_gates_precede_provider_work", gate=gate))

    # 14. test_fake_api_clean_migration_verifies_all_and_journals_before_post
    suite.addTest(test_class("test_fake_api_clean_migration_verifies_all_and_journals_before_post"))

    # 15. test_initial_existing_blocks_entire_batch_without_value_reads
    suite.addTest(test_class("test_initial_existing_blocks_entire_batch_without_value_reads"))

    # 16. test_concurrent_conflict_stops_and_keeps_prior_verified_receipt
    suite.addTest(test_class("test_concurrent_conflict_stops_and_keeps_prior_verified_receipt"))

    # 17. test_unknown_create_resumes_with_read_then_create_and_exactly_one_item
    suite.addTest(test_class("test_unknown_create_resumes_with_read_then_create_and_exactly_one_item"))

    # 18. test_expired_token_stops_then_expired_approval_blocks_resume
    suite.addTest(test_class("test_expired_token_stops_then_expired_approval_blocks_resume"))

    # 19. test_widened_grant_refused_before_session (4 variants)
    for field in ("names", "endpoint", "client", "capability"):
        suite.addTest(test_class("test_widened_grant_refused_before_session", field=field))

    # 20. test_unconfirmed_responses_never_enable_source_cleanup (3 variants)
    for behavior, state in (
        ("approval", "unresolved"),
        ("bad_readback", "unresolved"),
        ("malformed", "acceptance_unknown"),
    ):
        suite.addTest(test_class("test_unconfirmed_responses_never_enable_source_cleanup", behavior=behavior, state=state))

    # 21. test_cli_adapter_injection_reports_revocation_unknown_exit_six
    suite.addTest(test_class("test_cli_adapter_injection_reports_revocation_unknown_exit_six"))

    # 22. test_last_batch_trimstring_input_rejected_before_any_post (50 variants)
    for codepoint in sorted(TRIM_STRING_CODE_POINTS):
        for edge in ("leading", "trailing"):
            suite.addTest(test_class("test_last_batch_trimstring_input_rejected_before_any_post", codepoint=codepoint, edge=edge))

    # 23. test_foreign_controller_marker_stops_and_preserves_owner_guidance
    suite.addTest(test_class("test_foreign_controller_marker_stops_and_preserves_owner_guidance"))

    # 24. test_ownership_audit_omits_revision_and_input_channel_keys
    suite.addTest(test_class("test_ownership_audit_omits_revision_and_input_channel_keys"))

    # 25. test_ownership_audit_refuses_non_null_revision_and_input_channel
    suite.addTest(test_class("test_ownership_audit_refuses_non_null_revision_and_input_channel"))

    # 26. test_legacy_audit_revision_handling_unchanged
    suite.addTest(test_class("test_legacy_audit_revision_handling_unchanged"))

    return suite


if __name__ == "__main__":
    unittest.main()
