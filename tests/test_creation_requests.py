"""Creation ownership regressions from isolated owner acceptance runs."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.application.runtime_service import RuntimeService
from sandbox.config.facade import project_identity, resolve_project_config
from sandbox.core import _instances, _paths
from sandbox.project_registry.json import JsonRegistryRepository
from sandbox.runtimes.base import AdapterRegistry, OperationRequest, RuntimeDependencies
from sandbox.runtimes.compose import ComposeAdapter
from sandbox.server_config import creation_requests as creation
from sandbox.server_config.models import (
    creation_digest, creation_intent_fields, validate_creation_context,
    validate_creation_receipt,
)
from sandbox.services.process import ProcessResult
from tests.subprocess_support import run_test_process

ROOT = Path(__file__).resolve().parents[1]


class _InterruptedCommit(BaseException):
    pass


class _RegistryPort:
    """Use actual registry commits and locks; inject only the filesystem cut point."""

    def __init__(self, root):
        self.root = root
        self.replace_count = 0
        self.fail_at = None
        self.failure = OSError
        self.repository = JsonRegistryRepository(root / "registry.json", replace=self.replace)
        self.descriptors = {}

    def replace(self, source, destination):
        self.replace_count += 1
        if self.replace_count == self.fail_at:
            raise self.failure("synthetic registry commit interruption")
        os.replace(source, destination)

    def project_lock(self, root):
        key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()
        return JsonRegistryRepository(self.root / "locks" / key / "unused.json").transaction()

    def load_project_config(self, root, label="default", **_kwargs):
        selected = self.descriptors.get((str(root), label))
        if selected is not None:
            return copy.deepcopy(selected)
        return resolve_project_config(root, label=label, legacy_loader=self._unexpected_loader)

    @staticmethod
    def _unexpected_loader(*_args, **_kwargs):
        raise AssertionError("unexpected WordPress descriptor discovery")

    def registry_get(self, root, label="default"):
        return self.repository.get(str(root), label=label)

    def registry_put(self, root, label="default", **fields):
        return self.repository.put(str(root), label=label, **fields)

    def registry_all(self):
        return self.repository.all()

    def registry_remove(self, root, label="default"):
        return self.repository.remove(str(root), label=label)

    def registry_find_instance(self, name):
        return next((row for row in self.repository.all().values()
                     if row.get("instance") == name), None)


class _Process:
    def __init__(self, registry):
        self.registry = registry
        self.calls = []
        self.pending_at_effect = []
        self.fail_start = False

    def run(self, argv, *, cwd=None, env=None, timeout=None):
        self.calls.append(tuple(argv))
        self.pending_at_effect.extend(
            receipt["completion"]
            for row in self.registry.registry_all().values() if row["root"] == str(cwd)
            for receipt in row.get("creation_receipts", [])
            if receipt["completion"] == "pending"
        )
        if "config" in argv:
            return ProcessResult(tuple(argv), 0, "web\n", "")
        if self.fail_start and "up" in argv:
            return ProcessResult(tuple(argv), 1, "", "synthetic start failure")
        return ProcessResult(tuple(argv), 0, "started\n", "")


class _Http:
    def __init__(self):
        self.calls = []

    def probe(self, url, *, timeout=5):
        self.calls.append(url)
        return True


class _Ports:
    def __init__(self):
        self.calls = 0

    def allocate(self, preferred=None):
        self.calls += 1
        return preferred or 49100 + self.calls


class CreationRequestOwnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="creation-owner-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(_paths, "RUNTIME_DIR", self.home / "runtime"))
        self.registry = _RegistryPort(self.home / "owners")
        self.process = _Process(self.registry)
        self.http = _Http()
        self.ports = _Ports()
        self.adapter = ComposeAdapter(RuntimeDependencies(
            process=self.process, http=self.http, ports=self.ports,
            paths=object(), proxy=object(), registry=self.registry,
        ), self.registry, timeout=120)

        def artifact_dir(runtime_id):
            path = self.home / "artifacts" / runtime_id
            path.mkdir(parents=True, exist_ok=True)
            return path

        self.stack.enter_context(patch.object(self.adapter, "_artifact_dir", side_effect=artifact_dir))
        adapters = AdapterRegistry()
        adapters.register("compose", self.adapter, kinds=("compose",), owner="tests.creation")
        self.service = RuntimeService(
            resolve_descriptor=self.registry.load_project_config, adapters=adapters,
        )
        self.wp_calls = []
        self.wp_values = {}

    def project(self, name):
        root = self.home / name
        root.mkdir()
        (root / "compose.yaml").write_text(
            "services: {web: {image: synthetic.invalid/web:fixture}}\n")
        (root / "sandbox.config.json").write_text(json.dumps({
            "kind": "compose", "compose": {
                "file": "compose.yaml", "service": "web", "internal_port": 80,
                "health_path": "/healthz",
            },
        }))
        return root

    def context(self, root, request_id, *, label="default", descriptor=None):
        descriptor = descriptor or self.registry.load_project_config(root, label=label)
        fields = creation_intent_fields(
            descriptor, project_identity=project_identity(descriptor, label=label)["identity"],
            target_scope_digest=creation_digest({"target": str(root), "label": label}),
            delivery_intent_digest=creation_digest({"intent": request_id}),
            label=label, create_allowed=True,
        )
        return validate_creation_context({
            "schema_version": 1,
            "operation_id": "op-" + hashlib.sha256(request_id.encode()).hexdigest()[:24],
            "request_id": request_id, "job_id": None,
            "intent_digest": creation_digest(fields), "intent_fields": fields,
            **{key: fields[key] for key in ("project_identity", "project_root_digest", "label")},
        })

    def invoke(self, root, context, operation="ensure", expected=None):
        arguments = {"create": True, "creation_context": context}
        if expected is not None:
            arguments["expected_incarnation"] = expected
        return self.service.invoke(OperationRequest(
            str(root), operation, label=context["label"], arguments=arguments))

    def proof(self, root, context):
        return creation.lookup_creation_receipt(
            self.registry.registry_get(root, label=context["label"]), context)

    def counts(self):
        return len(self.process.calls), len(self.http.calls), self.ports.calls

    def seed_receipt(self, root, context, completion, *, kind="compose"):
        creation.reserve_creation_request(
            creation.scope_key(context), creation.request_key(context),
            context["operation_id"], context["intent_digest"],
        )
        instance = "instance-" + context["label"]
        incarnation = "inc_" + hashlib.sha256(context["label"].encode()).hexdigest()[:32]
        receipts = creation.pending_receipts(None, context, instance, incarnation, "created")
        record = self.registry.registry_put(root, label=context["label"],
            instance=instance, kind=kind, instance_incarnation_id=incarnation,
            creation_receipts=receipts)
        if completion in {"succeeded", "failed"}:
            self.registry.registry_put(root, label=context["label"],
                **creation.finish_creation_fields(record, context, succeeded=completion == "succeeded"))
        elif completion == "unknown":
            self.registry.registry_put(root, label=context["label"], creation_receipts=[
                validate_creation_receipt(dict(receipts[0], completion="unknown"))])
        return incarnation, instance

    def seed_wordpress(self, root, label, completion="succeeded"):
        descriptor = {"root": str(root), "kind": "wordpress", "server": "nginx",
                      "slug": "synthetic", "label": label}
        self.registry.descriptors[(str(root), label)] = descriptor
        context = self.context(root, "url/" + label, label=label, descriptor=descriptor)
        incarnation, instance = self.seed_receipt(root, context, completion, kind="wordpress")
        return context, incarnation, instance

    def wpcli(self, argv, *, instance, **_kwargs):
        self.wp_calls.append((instance, argv[1], argv[2]))
        if argv[1] == "update":
            self.wp_values[(instance, argv[2])] = argv[3]
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=0, stdout=self.wp_values.get((instance, argv[2]), ""))

    def mutate_url(self, root, owner, url):
        context, incarnation, instance = owner
        return _instances.mutate_creation_url(root, label=context["label"],
            creation_context=context, expected_incarnation=incarnation,
            instance_id=instance, url=url)

    def test_cli_query_preserves_original_request_namespace_without_state_writes(self):
        root = self.project("query")
        query_home = self.home / "query-home"
        query_home.mkdir()
        environment = {"HOME": str(query_home), "SANDBOX_HOME": str(query_home),
            "SANDBOX_PROJECT_ROOTS": str(self.home), "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"}

        def snapshot(path):
            return {str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
                    for item in path.rglob("*") if item.is_file()}

        source_before, owner_before = snapshot(root), snapshot(query_home)
        base = [sys.executable, str(ROOT / "sb"), "ensure", "--local",
                "--project-dir", str(root), "--label", "default", "--json"]
        prepared = run_test_process([*base, "--creation-prepare-json", json.dumps({
            "delivery_intent_digest": creation_digest("cli"),
            "target_scope_digest": creation_digest("target"), "create_allowed": True,
        })], env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        payload = json.loads(prepared.stdout)
        self.assertTrue(payload["ok"])
        for request_id in ("creation/cli-original", "r/" + "x" * 254):
            with self.subTest(request_bytes=len(request_id)):
                fields = payload["intent_fields"]
                context = {"schema_version": 1, "operation_id": "cli-operation",
                    "request_id": request_id, "job_id": None,
                    "intent_fields": fields, "intent_digest": payload["intent_digest"],
                    **{key: fields[key] for key in ("project_identity", "project_root_digest", "label")}}
                result = run_test_process([*base, "--creation-receipt", "--creation-context-json",
                    json.dumps(context)], env=environment, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], "creation_request_unknown")
        self.assertEqual(snapshot(root), source_before)
        self.assertEqual(snapshot(query_home), owner_before)

    def test_created_reused_and_successful_original_replay_keep_exact_ownership(self):
        root = self.project("created")
        context = self.context(root, "creation/original")
        self.assertTrue(self.invoke(root, context).ok)
        receipt = self.proof(root, context)["creation_receipt"]
        self.assertEqual((receipt["relation"], receipt["completion"]), ("created", "succeeded"))
        self.assertTrue(self.process.pending_at_effect)
        before = self.counts()
        replay = self.invoke(root, context)
        self.assertTrue(replay.ok)
        self.assertEqual(replay.data["creation_receipt"], receipt)
        self.assertEqual(self.counts(), before)
        reused = self.context(root, "creation/reused")
        self.assertTrue(self.invoke(root, reused).ok)
        next_receipt = self.proof(root, reused)["creation_receipt"]
        self.assertEqual(next_receipt["relation"], "reused")
        self.assertEqual(next_receipt["instance_incarnation_id"], receipt["instance_incarnation_id"])

    def test_failed_start_replay_preserves_failure_and_never_repeats_effects(self):
        root = self.project("failed")
        context = self.context(root, "creation/failed")
        self.process.fail_start = True
        with self.assertRaisesRegex(RuntimeError, "synthetic start failure"):
            self.invoke(root, context)
        self.process.fail_start = False
        receipt = self.proof(root, context)["creation_receipt"]
        self.assertEqual(receipt["completion"], "failed")
        self.assertTrue(self.process.pending_at_effect)
        before = self.counts()
        replay = self.invoke(root, context)
        self.assertFalse(replay.ok)
        self.assertEqual(replay.data["error"]["code"], "instance_ensure_failed")
        self.assertEqual(replay.data["creation_receipt"], receipt)
        self.assertEqual(self.counts(), before)

    def test_pending_and_unknown_compose_replays_are_not_success(self):
        root = self.project("unfinished")
        for completion in ("pending", "unknown"):
            with self.subTest(completion=completion):
                context = self.context(root, "creation/" + completion, label=completion)
                incarnation, _instance = self.seed_receipt(root, context, completion)
                receipt = self.proof(root, context)["creation_receipt"]
                before = self.counts()
                replay = self.invoke(root, context, expected=incarnation)
                self.assertFalse(replay.ok)
                self.assertEqual(replay.data["error"]["code"], "creation_request_unknown")
                self.assertEqual(replay.data["creation_receipt"], receipt)
                self.assertEqual(self.counts(), before)
                self.assertTrue(self.proof(root, context)["ok"])

    def test_wordpress_retained_failed_pending_unknown_never_enter_runtime(self):
        root = self.home / "wordpress"
        root.mkdir()
        with ExitStack() as stack:
            stack.enter_context(patch.object(_instances, "_core", return_value=self.registry))
            tripwires = [stack.enter_context(patch.object(_instances, name,
                side_effect=AssertionError("replay entered external runtime")))
                for name in ("docker_daemon_preflight", "compose", "wpcli")]
            tripwires.extend(stack.enter_context(patch("sandbox.commands.lifecycle." + name,
                side_effect=AssertionError("replay entered lifecycle effects")))
                for name in ("cmd_up", "cmd_install"))
            for completion in ("failed", "pending", "unknown"):
                with self.subTest(completion=completion):
                    context, incarnation, _instance = self.seed_wordpress(root, completion, completion)
                    receipt = self.proof(root, context)["creation_receipt"]
                    before = self.registry.registry_get(root, label=completion)
                    result = _instances.ensure_instance({}, str(root), label=completion,
                        create=True, creation_context=context, expected_incarnation=incarnation)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"]["code"], "instance_ensure_failed"
                        if completion == "failed" else "creation_request_unknown")
                    self.assertEqual(result["creation_receipt"], receipt)
                    self.assertEqual(self.registry.registry_get(root, label=completion), before)
                    self.assertTrue(self.proof(root, context)["ok"])
            for tripwire in tripwires:
                tripwire.assert_not_called()

    def test_descriptor_drift_and_changed_intent_refuse_before_effects(self):
        root = self.project("drift")
        context = self.context(root, "creation/original")
        self.assertTrue(self.invoke(root, context).ok)
        before = self.counts()
        descriptor = self.registry.load_project_config(root)
        descriptor["internal_port"] = 81
        self.registry.descriptors[(str(root), "default")] = descriptor
        with self.assertRaisesRegex(ValueError, "creation_request_conflict"):
            self.invoke(root, context)
        del self.registry.descriptors[(str(root), "default")]
        changed = copy.deepcopy(context)
        changed["intent_fields"]["delivery_intent_digest"] = creation_digest("changed")
        changed["intent_digest"] = creation_digest(changed["intent_fields"])
        with self.assertRaisesRegex(ValueError, "creation_request_conflict"):
            self.invoke(root, changed)
        self.assertEqual(self.counts(), before)

    def test_guard_before_registry_interruption_never_reenters_creation(self):
        root = self.project("interrupted")
        context = self.context(root, "creation/interrupted")
        self.registry.fail_at = self.registry.replace_count + 1
        self.registry.failure = _InterruptedCommit
        with self.assertRaises(_InterruptedCommit):
            self.invoke(root, context)
        self.registry.fail_at = None
        self.registry.failure = OSError
        self.assertIsNone(self.registry.registry_get(root))
        self.assertIsNotNone(creation.read_creation_request(
            creation.scope_key(context), creation.request_key(context)))
        # Port selection belongs to the interrupted initial attempt, not replay.
        self.assertEqual(self.counts(), (0, 0, 1))
        before = self.counts()
        result = self.invoke(root, context)
        self.assertFalse(result.ok)
        self.assertEqual(result.data["error"]["code"], "creation_request_unknown")
        self.assertEqual(self.counts(), before)

    def test_covered_reconcile_has_only_one_effectful_continuation(self):
        root = self.project("reconcile")
        context = self.context(root, "creation/reconcile")
        self.assertTrue(self.invoke(root, context).ok)
        incarnation = self.proof(root, context)["creation_receipt"]["instance_incarnation_id"]
        before = self.counts()
        self.assertTrue(self.invoke(root, context, "apply", incarnation).ok)
        self.assertEqual(self.counts(), (before[0] + 1, before[1], before[2]))
        before = self.counts()
        with self.assertRaisesRegex(ValueError, "creation_request_unknown"):
            self.invoke(root, context, "apply", incarnation)
        self.assertEqual(self.counts(), before)

    def test_evicted_receipt_and_removed_instance_cannot_bypass_original_guards(self):
        root = self.project("eviction")
        contexts = [self.context(root, "creation/eviction-" + str(index)) for index in range(34)]
        for context in contexts:
            self.assertTrue(self.invoke(root, context).ok)
        before = self.counts()
        expired = self.invoke(root, contexts[0])
        self.assertFalse(expired.ok)
        self.assertEqual(expired.data["error"]["code"], "creation_request_expired")
        self.registry.registry_remove(root)
        absent = self.invoke(root, contexts[-1])
        self.assertFalse(absent.ok)
        self.assertEqual(absent.data["error"]["code"], "creation_request_unknown")
        self.assertEqual(self.counts(), before)
        for context in (contexts[0], contexts[-1]):
            self.assertIsNotNone(creation.read_creation_request(
                creation.scope_key(context), creation.request_key(context)))

    def test_capacity_refuses_new_request_but_preserves_existing_lookup(self):
        scope = {"capacity": "one"}
        for index in range(4096):
            self.assertEqual(creation.reserve_creation_request(scope, {"request": index},
                "capacity-" + str(index), creation_digest(index))["state"], "new")
        self.assertEqual(creation.reserve_creation_request(scope, {"request": 0},
            "capacity-0", creation_digest(0))["state"], "existing")
        with self.assertRaisesRegex(ValueError, "creation_request_capacity"):
            creation.reserve_creation_request(scope, {"request": 4096},
                "capacity-4096", creation_digest(4096))
        self.assertIsNone(creation.read_creation_request(scope, {"request": 4096}))

    def test_url_mutation_preserves_other_label_and_refuses_changed_binding(self):
        root = self.home / "wordpress"
        root.mkdir()
        alpha = self.seed_wordpress(root, "alpha")
        beta = self.seed_wordpress(root, "beta")
        before_beta = self.registry.registry_get(root, label="beta")
        with patch.object(_instances, "_core", return_value=self.registry), \
                patch.object(_instances, "wpcli", side_effect=self.wpcli):
            result = self.mutate_url(root, alpha, "https://alpha.example.test")
            self.assertEqual(result["result_code"], "remote_instance_url_verified")
            self.assertEqual(self.registry.registry_get(root, label="beta"), before_beta)
            self.assertEqual(len(self.wp_calls), 4)
            self.assertTrue(all(call[0] == alpha[2] for call in self.wp_calls))
            self.assertEqual(self.mutate_url(root, alpha, "https://alpha.example.test"), result)
            with self.assertRaisesRegex(ValueError, "creation_request_conflict"):
                self.mutate_url(root, alpha, "https://other.example.test")
            with self.assertRaisesRegex(ValueError, "instance_incarnation_changed"):
                self.mutate_url(root, (alpha[0], beta[1], beta[2]), "https://alpha.example.test")
            self.assertEqual(len(self.wp_calls), 4)

    def test_url_result_commit_loss_preserves_unknown_without_rewriting(self):
        root = self.home / "wordpress"
        root.mkdir()
        alpha = self.seed_wordpress(root, "alpha")
        beta = self.seed_wordpress(root, "beta")
        with patch.object(_instances, "_core", return_value=self.registry), \
                patch.object(_instances, "wpcli", side_effect=self.wpcli):
            successful = self.mutate_url(root, alpha, "https://alpha.example.test")
            before_alpha = self.registry.registry_get(root, label="alpha")
            # Reservation is committed first; only the final observation write fails.
            self.registry.fail_at = self.registry.replace_count + 2
            with self.assertRaisesRegex(OSError, "synthetic registry commit interruption"):
                self.mutate_url(root, beta, "https://beta.example.test")
            self.registry.fail_at = None
            self.assertEqual(len(self.wp_calls), 8)
            replay = self.mutate_url(root, beta, "https://beta.example.test")
            self.assertEqual(replay["result_code"], "remote_instance_url_incomplete")
            self.assertEqual(replay["writes"], {"home": "unknown", "siteurl": "unknown"})
            self.assertEqual(len(self.wp_calls), 8)
            self.assertEqual(self.registry.registry_get(root, label="alpha"), before_alpha)
            self.assertEqual(self.mutate_url(root, alpha, "https://alpha.example.test"), successful)
            self.assertEqual(len(self.wp_calls), 8)


if __name__ == "__main__":
    unittest.main()
