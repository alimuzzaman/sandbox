"""Private candidate inputs use exact local Git bytes and captured secret bytes."""

import base64
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.subprocess_support import run_test_process, synthetic_environment


class CandidateInputTests(unittest.TestCase):
    def test_full_lenzora_candidate_crosses_fixed_preparation_and_transport(self):
        self._full_lenzora_candidate("candidate-v1")

    def test_candidate_v2_crosses_private_preparation_hydration_and_transport(self):
        self._full_lenzora_candidate("candidate-v2")

    def _full_lenzora_candidate(self, input_contract):
        from sandbox.hosting.images.activation import private_inputs
        from sandbox.hosting.images.activation.v2_models import PrivateComposeInputSnapshotV2
        from sandbox.commands.hosting import _host_image_argv_runner
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport
        from tests.fixtures.hosting_image_activation import lenzora_compose_fixture
        from tests.test_hosting_image_activation_v2 import artifacts, TARGET

        plan, _proof, _snapshot = artifacts()
        fixture = lenzora_compose_fixture(plan)
        image_environment = {variable: next(image.image_ref for image in plan.receipt.images if image.name == name)
                             for name, variable in plan.policy.activation_environment_bindings}
        bindings = {row["service"]: row["image_ref"] for row in plan.as_mapping()["service_image_bindings"]
                    if row["kind"] == "persistent"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            docker = root / "docker"
            docker.write_text("\n".join(("#!/usr/bin/env python3", "import json,sys", "from pathlib import Path",
                "a=sys.argv[1:]",
                "if a and a[0]=='info': print('daemon-a'); sys.exit(0)",
                "if '--hash' in a: print(a[-1]+' '+'b'*64); sys.exit(0)",
                "if '--profiles' in a: print('job-orchestration'); sys.exit(0)",
                "if a and a[0]=='compose' and 'config' in a:",
                " p=a[a.index('--file')+1]",
                " print(Path(p).read_text()); sys.exit(0)", "sys.exit(99)")))
            docker.chmod(0o700)
            closed = {"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
            frame = {"schema_version": 2, "source_revision": plan.policy.source_revision,
                "runtime_directory": str(root), "candidate_id": "e" * 64,
                "compose_files": ["compose.yml"], "manifest": "sandbox.hosting.yml",
                "source_files": {"compose.yml": base64.b64encode(json.dumps(fixture["compose"]).encode()).decode(),
                    "sandbox.hosting.yml": base64.b64encode(b"version: 1\n").decode()},
                "environment": "WORKER_TOKEN=" + fixture["environment"]["WORKER_TOKEN"] + "\n",
                "compose_override": "{}",
                "render_contract": {"project_name": "fixture", "image_environment": image_environment,
                    "input_contract": input_contract,
                    "captured_environment": fixture["environment"],
                    "configuration_key": base64.b64encode(b"k" * 32).decode(),
                    "expected_services": sorted(fixture["compose"]["services"])}}
            with patch.object(private_inputs, "_ENV", closed):
                prepared = run_test_process(("python3", "-c", private_inputs.candidate_program()),
                    input=json.dumps(frame), text=True, capture_output=True)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            candidate = root / "activation-inputs" / ("e" * 64)
            effective = json.loads((candidate / "effective.json").read_text())
            self.assertEqual(len(effective["services"]), 20)
            self.assertEqual((candidate / "secret-0").read_text(), fixture["environment"]["WORKER_TOKEN"])
            results = [prepared]
            def ssh(_entry, command, **kwargs):
                result = run_test_process(shlex.split(command), env=synthetic_environment(closed),
                    input=kwargs.get("input_data"), text=True, capture_output=True)
                results.append(result)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result
            provider = {"snapshot_id": "compose-snapshot/full-candidate", "provider_revision": "fixture-v2",
                "input_contract": input_contract, "target": TARGET, "compose_files": (str(candidate / "effective.json"),),
                "project_name": "fixture", "project_directory": str(candidate), "environment_file": str(candidate / "environment.env")}
            with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh), \
                    patch("sandbox.transports.remote_hosting_activation.CLOSED_ENVIRONMENT", closed):
                transport = RegisteredRemoteActivationTransport(argv_runner=_host_image_argv_runner({}, compose_snapshot_provider=provider),
                    configuration_binding_key=b"k" * 32)
                arguments = {"compose_files": provider["compose_files"], "project_name": "fixture",
                    "selected_services": fixture["persistent_services"], "allowed_services": tuple(sorted(effective["services"])),
                    "service_image_bindings": bindings, "environment_bindings": image_environment}
                digest = transport.prepare_compose_snapshot_v2(**arguments, target=TARGET,
                    snapshot_id=provider["snapshot_id"], provider_revision=provider["provider_revision"], input_contract=input_contract)
                contract = transport.prepared_init_contract_v2(plan=plan, initializer_order=fixture["initializer_order"])
                self.assertEqual(contract.graph.prerequisite_groups, (("lenzora-job-queue",),))
                self.assertEqual(contract.graph.initializer_order, fixture["initializer_order"])
                self.assertEqual(len(contract.declarations), 3)
                snapshot = PrivateComposeInputSnapshotV2.create(snapshot_id=provider["snapshot_id"],
                    provider_revision=provider["provider_revision"], target=TARGET, plan_set_digest=plan.plan_set_digest,
                    selected_services=fixture["persistent_services"], configuration_digest=digest,
                    expires_at=4_000_000_000, input_contract=input_contract, init_contract=contract)
                provider.update(snapshot_digest=snapshot.snapshot_digest, render_digest=digest)
                rendered = transport.render_topology_v2(**arguments, topology_digest="sha256:" + "a" * 64,
                                                        private_compose_snapshot=snapshot.as_mapping())
            self.assertEqual(set(rendered["services"]), set(fixture["persistent_services"]))
            self.assertNotIn(fixture["environment"]["WORKER_TOKEN"], "".join(row.stdout + row.stderr for row in results))

    def test_retained_secret_material_refuses_foreign_paths_and_unsafe_files(self):
        from sandbox.hosting.images.activation.private_inputs import retained_secret_material
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            secret = root / "secret-0"
            secret.write_bytes(b"synthetic-one"); secret.chmod(0o600)
            compose = {"services": {"web": {"secrets": [{"source": "token", "target": "token"}]}},
                       "secrets": {"token": {"file": str(secret)}}}
            first = retained_secret_material(compose, str(root))
            self.assertEqual(first, {"token": base64.b64encode(b"synthetic-one").decode()})
            secret.write_bytes(b"synthetic-two")
            self.assertNotEqual(first, retained_secret_material(compose, str(root)))
            secret.chmod(0o644)
            with self.assertRaises(ValueError):
                retained_secret_material(compose, str(root))
            secret.chmod(0o600)
            compose["secrets"]["token"]["file"] = str(root.parent / "secret-0")
            with self.assertRaises(ValueError):
                retained_secret_material(compose, str(root))

    def test_effective_candidate_renders_exact_membership_and_captures_secret_files(self):
        from sandbox.hosting.images.activation import private_inputs
        compose = {"services": {"web": {"image": "ghcr.io/acme/web@sha256:" + "a" * 64,
            "secrets": [{"source": "token", "target": "token"}],
            "environment": {"TOKEN_FILE": "/run/secrets/token"}}},
            "secrets": {"token": {"environment": "TOKEN"}}}
        calls = []
        def execute(argv, **kwargs):
            from types import SimpleNamespace
            calls.append((argv, kwargs))
            self.assertNotIn("synthetic-token", str(argv))
            self.assertNotIn("TOKEN", kwargs["env"])
            return SimpleNamespace(returncode=0, stdout=json.dumps(compose).encode(), stderr=b"")
        with patch.object(private_inputs.subprocess, "run", side_effect=execute):
            content = private_inputs.render_candidate(
                {"compose-0.yml": b"services: {}\n", "environment.env": b"TOKEN=synthetic-token\n",
                 "compose.override.yml": b"services: {}\n"},
                project_name="fixture", image_environment={"WEB_IMAGE": compose["services"]["web"]["image"]},
                captured_environment={"TOKEN": "synthetic-token"}, key=b"k" * 32,
                candidate="/private/candidate", expected_services=("web",))
        self.assertEqual(content["secret-0"], b"synthetic-token")
        self.assertEqual(json.loads(content["effective.json"])["secrets"]["token"],
                         {"file": "/private/candidate/secret-0"})
        self.assertEqual(len(calls), 1)
        self.assertNotIn("up", calls[0][0])

    def test_fixed_remote_program_uses_captured_bytes_without_a_remote_checkout(self):
        from sandbox.hosting.images.activation import private_inputs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            image = "ghcr.io/acme/web@sha256:" + "a" * 64
            compose = {"services": {"web": {"image": image, "secrets": [{"source": "token", "target": "token"}]}},
                       "secrets": {"token": {"environment": "TOKEN"}}}
            docker = root / "docker"
            docker.write_text("#!/usr/bin/env python3\nprint(" + repr(json.dumps(compose)) + ")\n")
            docker.chmod(0o700)
            frame = {"schema_version": 2, "source_revision": "a" * 40,
                "runtime_directory": str(root), "candidate_id": "d" * 64,
                "compose_files": ["compose.yml"], "manifest": "sandbox.hosting.yml",
                "source_files": {"compose.yml": base64.b64encode(b"services: {}\n").decode(),
                    "sandbox.hosting.yml": base64.b64encode(b"version: 1\n").decode()},
                "environment": "TOKEN=synthetic-private-canary\n", "compose_override": "services: {}\n",
                "render_contract": {"project_name": "fixture", "image_environment": {"WEB_IMAGE": image},
                    "captured_environment": {"TOKEN": "synthetic-private-canary"},
                    "configuration_key": base64.b64encode(b"k" * 32).decode(), "expected_services": ["web"]}}
            with patch.object(private_inputs, "_ENV", {"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}):
                program = private_inputs.candidate_program()
            result = run_test_process(("python3", "-c", program), input=json.dumps(frame),
                                      text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"ok": True, "result": "prepared"})
            candidate = root / "activation-inputs" / ("d" * 64)
            self.assertEqual((candidate / "compose-0.yml").read_bytes(), b"services: {}\n")
            self.assertEqual((candidate / "manifest.yml").read_bytes(), b"version: 1\n")
            self.assertEqual((candidate / "secret-0").read_bytes(), b"synthetic-private-canary")
            self.assertNotIn("synthetic-private-canary", result.stdout + result.stderr + program)

    def test_concurrent_publishers_and_lost_publication_ack_preserve_candidate(self):
        from concurrent.futures import ThreadPoolExecutor
        from sandbox.hosting.images.activation import private_inputs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            content = {"secret-0": b"synthetic-canary"}
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: private_inputs.publish_candidate(root, "b" * 64, content), range(2)))
            self.assertEqual(sorted(results), ["prepared", "replayed"])
            original = private_inputs._publish_at
            def lose_ack(*args):
                original(*args)
                raise OSError("synthetic lost acknowledgement")
            with patch.object(private_inputs, "_publish_at", side_effect=lose_ack):
                with self.assertRaises(ValueError):
                    private_inputs.publish_candidate(root, "c" * 64, content)
            candidate = root / "activation-inputs" / ("c" * 64)
            self.assertEqual((candidate / "secret-0").read_bytes(), content["secret-0"])
            self.assertEqual(private_inputs.publish_candidate(root, "c" * 64, content), "replayed")

    def test_publication_replays_exact_bytes_and_preserves_conflicts(self):
        from sandbox.hosting.images.activation.private_inputs import publish_candidate
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate_id = "a" * 64
            content = {"compose-0.yml": b"services: {}\n", "secret-0": b"synthetic-private-value"}
            self.assertEqual(publish_candidate(root, candidate_id, content), "prepared")
            self.assertEqual(publish_candidate(root, candidate_id, content), "replayed")
            candidate = root / "activation-inputs" / candidate_id
            self.assertEqual(candidate.stat().st_mode & 0o777, 0o700)
            self.assertEqual((candidate / "secret-0").stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(ValueError, "candidate_refused"):
                publish_candidate(root, candidate_id, {**content, "secret-0": b"changed"})
            self.assertEqual((candidate / "secret-0").read_bytes(), content["secret-0"])
            (candidate / "secret-0").chmod(0o644)
            with self.assertRaisesRegex(ValueError, "candidate_refused"):
                publish_candidate(root, candidate_id, content)

    def test_capture_binds_clean_tracked_local_source_and_rejects_symlinks(self):
        from sandbox.hosting.images.activation.private_inputs import capture_source

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "compose.yml").write_text("services: {}\n")
            (root / "sandbox.hosting.yml").write_text("version: 1\n")
            for args in (("init", "-q"), ("add", "."),
                         ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                          "commit", "-qm", "fixture")):
                run_test_process(("git", "-C", str(root), *args), check=True, capture_output=True)
            revision = run_test_process(("git", "-C", str(root), "rev-parse", "HEAD"),
                check=True, capture_output=True, text=True).stdout.strip()
            captured = capture_source(root, revision, ("compose.yml",), "sandbox.hosting.yml")
            self.assertEqual(base64.b64decode(captured["compose.yml"]), b"services: {}\n")
            self.assertEqual(set(captured), {"compose.yml", "sandbox.hosting.yml"})
            with self.assertRaisesRegex(ValueError, "source_refused"):
                capture_source(root, "f" * 40, ("compose.yml",), "sandbox.hosting.yml")
            (root / "sandbox.hosting.yml").write_text("version: 2\n")
            with self.assertRaisesRegex(ValueError, "source_refused"):
                capture_source(root, revision, ("compose.yml",), "sandbox.hosting.yml")
            (root / "sandbox.hosting.yml").write_text("version: 1\n")
            (root / "foreign.yml").write_text("services: {}\n")
            (root / "compose.yml").unlink()
            (root / "compose.yml").symlink_to(root / "foreign.yml")
            with self.assertRaisesRegex(ValueError, "source_refused"):
                capture_source(root, revision, ("compose.yml",), "sandbox.hosting.yml")

    def test_materialization_keeps_file_semantics_and_binds_exact_bytes(self):
        from sandbox.hosting.images.activation.private_inputs import materialize_secrets

        canary = "synthetic-token-with-$-and-newline\n"
        compose = {"services": {"web": {"image": "fixture",
            "environment": {"TOKEN_FILE": "/run/secrets/token"},
            "secrets": [{"source": "token", "target": "token"}]}},
            "secrets": {"token": {"environment": "WORKER_TOKEN"}}}
        rendered, files, binding = materialize_secrets(compose,
            {"WORKER_TOKEN": canary}, "/private/candidate", b"k" * 32)
        self.assertEqual(files, {"secret-0": canary.encode()})
        self.assertEqual(rendered["secrets"], {"token": {"file": "/private/candidate/secret-0"}})
        self.assertEqual(rendered["services"], compose["services"])
        self.assertEqual(compose["secrets"], {"token": {"environment": "WORKER_TOKEN"}})
        changed = materialize_secrets(compose, {"WORKER_TOKEN": canary + "x"},
                                     "/private/candidate", b"k" * 32)
        self.assertNotEqual(binding, changed[2])
        self.assertNotIn(canary, binding)
        named = {**compose, "secrets": {"token": {"environment": "WORKER_TOKEN", "name": "fixture_token"}}}
        named_render, named_files, _ = materialize_secrets(named, {"WORKER_TOKEN": canary},
                                                         "/private/candidate", b"k" * 32)
        self.assertEqual(named_files, files)
        self.assertEqual(named_render["secrets"]["token"], {"name": "fixture_token", "file": "/private/candidate/secret-0"})
        absolute = {**compose, "services": {"web": {
            **compose["services"]["web"],
            "secrets": [{"source": "token", "target": "/run/secrets/token"}],
        }}}
        absolute_render, absolute_files, _ = materialize_secrets(
            absolute, {"WORKER_TOKEN": canary}, "/private/candidate", b"k" * 32)
        self.assertEqual(absolute_files, files)
        self.assertEqual(absolute_render["services"]["web"]["secrets"],
                         [{"source": "token", "target": "/run/secrets/token"}])
        for mounts in (
            [{"source": "token", "target": "token"},
             {"source": "token", "target": "/run/secrets/token"}],
            [{"source": "token", "target": "/run/secrets/../token"}],
            [{"source": "token", "target": "/tmp/token"}],
            [{"source": "token", "target": "token/child"}],
        ):
            with self.subTest(mounts=mounts), self.assertRaisesRegex(
                    ValueError, "secret_source_refused"):
                invalid = {**compose, "services": {"web": {
                    **compose["services"]["web"], "secrets": mounts,
                }}}
                materialize_secrets(
                    invalid, {"WORKER_TOKEN": canary}, "/private/candidate", b"k" * 32,
                )
        for secrets in ({}, {"WORKER_TOKEN": None}):
            with self.assertRaisesRegex(ValueError, "secret_source_refused"):
                materialize_secrets(compose, secrets, "/private/candidate", b"k" * 32)
        for source in ({"file": "/foreign"}, {"external": True},
                       {"environment": "WORKER_TOKEN", "file": "/foreign"}):
            with self.assertRaisesRegex(ValueError, "secret_source_refused"):
                materialize_secrets({**compose, "secrets": {"token": source}},
                    {"WORKER_TOKEN": canary}, "/private/candidate", b"k" * 32)

    def test_private_render_refuses_unowned_inputs_and_missing_secret_mounts(self):
        from sandbox.hosting.images.activation.private_inputs import materialize_secrets

        for compose in (
            {"services": {"web": {"volumes": [{"type": "bind", "source": "/source", "target": "/app"}]}}},
            {"services": {"web": {}}, "configs": {"config": {"file": "/foreign"}}},
            {"services": {"web": {}}, "networks": {"net": {"external": True}}},
            {"services": {"web": {"secrets": ["missing"]}}},
            {"services": {"web": {}}, "secrets": {"unused": {"environment": "TOKEN"}}},
        ):
            with self.assertRaises(ValueError):
                materialize_secrets(compose, {"TOKEN": "synthetic"}, "/private/candidate", b"k" * 32)
