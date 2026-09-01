import unittest

from tests.fixtures.hosting_image_activation import (
    FakeRuntime, init_declaration, staged_proof,
)
from tests.hosting_image_fixtures import CONFIG_DIGEST, MANIFEST_DIGEST


class ActivationInitTests(unittest.TestCase):
    def test_create_inspect_effect_start_wait_cleanup_order_and_receipt(self):
        from sandbox.hosting.images.activation.init_runner import InitRunner
        runtime = FakeRuntime(); durable = []
        runner = InitRunner(runtime, persist_effect_entered=lambda *identity: durable.append(identity))
        receipt = runner.run(
            init_declaration(), exact_image=f"ghcr.io/acme/widget@{MANIFEST_DIGEST}",
            local_image_id=CONFIG_DIGEST, platform={"os": "linux", "architecture": "amd64"},
            target=staged_proof().target.as_mapping(), runtime_epoch="daemon-a")
        self.assertEqual(runtime.calls, ["create", "start", "remove"])
        self.assertEqual(len(durable), 1)
        self.assertTrue(receipt.termination_complete and receipt.cleanup_complete)

    def test_every_possible_execution_crash_fences_and_never_restarts(self):
        from sandbox.hosting.images.activation.init_runner import InitExecutionUncertain, InitRunner
        for method in ("start_init", "wait_init", "remove_init"):
            with self.subTest(boundary=method):
                runtime = FakeRuntime(); original = getattr(runtime, method)
                calls = {"count": 0}
                def crash(*args, **kwargs):
                    calls["count"] += 1
                    if calls["count"] == 1: raise RuntimeError("crash")
                    return original(*args, **kwargs)
                setattr(runtime, method, crash)
                runner = InitRunner(runtime, persist_effect_entered=lambda *_: None)
                with self.assertRaises(InitExecutionUncertain):
                    runner.run(init_declaration(),
                        exact_image=f"ghcr.io/acme/widget@{MANIFEST_DIGEST}",
                        local_image_id=CONFIG_DIGEST,
                        platform={"os": "linux", "architecture": "amd64"},
                        target=staged_proof().target.as_mapping(), runtime_epoch="daemon-a")
                self.assertLessEqual(runtime.started, 1)

    def test_mismatch_removes_without_start_and_secret_value_never_enters_receipt(self):
        from sandbox.hosting.images.activation.init_runner import InitRunner
        runtime = FakeRuntime(); runtime.inspect_init = lambda _handle: {"wrong": True}
        with self.assertRaises(ValueError):
            InitRunner(runtime, persist_effect_entered=lambda *_: None).run(
                init_declaration(), exact_image=f"ghcr.io/acme/widget@{MANIFEST_DIGEST}",
                local_image_id=CONFIG_DIGEST,
                platform={"os": "linux", "architecture": "amd64"},
                target=staged_proof().target.as_mapping(), runtime_epoch="daemon-a")
        self.assertEqual(runtime.started, 0)

    def test_real_inspect_normalizes_declared_env_and_image_platform_independently(self):
        import json
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport
        image = "ghcr.io/acme/widget@" + MANIFEST_DIGEST
        invocations = []
        def runner(*, argv, environment, private_environment, private_environment_source,
                   redact_environment_keys, timeout_seconds, max_output_bytes):
            invocations.append((argv, dict(environment), dict(private_environment)))
            if argv[:2] == ("docker", "create"):
                stdout = "container-a\n"
            elif argv[:2] == ("docker", "info"):
                stdout = "daemon-a\n"
            elif argv[:3] == ("docker", "image", "inspect"):
                stdout = json.dumps([{"Id": CONFIG_DIGEST, "Os": "linux",
                                      "Architecture": "amd64", "RepoDigests": [image]}])
            else:
                stdout = json.dumps({"Config": {"Image": image,
                    "Cmd": ["migrate", "--once"],
                    "Labels": {"org.sandbox.activation.dependencies": "[]"}},
                    "DeclaredEnvironmentKeys": ["DATABASE_URL"],
                    "DeclaredEnvironmentMatch": True,
                    "State": {"Running": False}, "HostConfig": {"Privileged": False},
                    "NetworkSettings": {"Networks": {"default": {}}},
                    "Mounts": [{"Type": "volume", "Name": "data",
                                "Destination": "/data", "RW": True}],
                    "Image": CONFIG_DIGEST})
            return {"returncode": 0, "stdout": stdout, "stderr": "", "terminated": True}
        target = staged_proof().target.as_mapping()
        transport = RegisteredRemoteActivationTransport(
            argv_runner=runner, target_identity_observer=lambda: {
                "machine_identity": target["machine_identity"],
                "target_identity": target["target_identity"]},
            init_environment_provider=lambda service, keys: {
                "DATABASE_URL": "synthetic"})
        handle = transport.create_init(declaration=init_declaration(), image=image,
            platform={"os": "linux", "architecture": "amd64"}, target=target, start=False)
        observed = transport.inspect_init(handle)
        self.assertEqual(observed, handle.expected)
        self.assertEqual(observed["environment_keys"], ["DATABASE_URL"])
        self.assertEqual(observed["platform"], {"os": "linux", "architecture": "amd64"})
        self.assertNotIn("synthetic", repr(handle.expected))
        create_argv, create_environment, private_environment = next(item for item in invocations
                                                if item[0][:2] == ("docker", "create"))
        self.assertNotIn("synthetic", create_argv)
        self.assertNotIn("DATABASE_URL", create_environment)
        self.assertEqual(private_environment["DATABASE_URL"], "synthetic")

    def test_post_create_observation_failure_removes_container_and_private_cache(self):
        from sandbox.transports.remote_hosting_activation import (
            RegisteredRemoteActivationTransport, RemoteActivationError,
        )
        calls = []
        def runner(*, argv, environment, private_environment, private_environment_source,
                   redact_environment_keys, timeout_seconds, max_output_bytes):
            calls.append(argv)
            if argv[:2] == ("docker", "create"):
                return {"returncode": 0, "stdout": "container-fail\n", "stderr": "",
                        "terminated": True}
            if argv[:2] == ("docker", "rm"):
                return {"returncode": 0, "stdout": "", "stderr": "", "terminated": True}
            return {"returncode": 1, "stdout": "", "stderr": "unavailable",
                    "terminated": True}
        target = staged_proof().target.as_mapping()
        transport = RegisteredRemoteActivationTransport(argv_runner=runner,
            target_identity_observer=lambda: {"machine_identity": "machine-a",
                                               "target_identity": "target-a"},
            init_environment_provider=lambda service, keys: {"DATABASE_URL": "synthetic"})
        with self.assertRaises(RemoteActivationError):
            transport.create_init(declaration=init_declaration(),
                image="ghcr.io/acme/widget@" + MANIFEST_DIGEST,
                platform={"os": "linux", "architecture": "amd64"},
                target=target, start=False)
        self.assertTrue(any(argv[:2] == ("docker", "rm") for argv in calls))
        self.assertEqual(transport._init_environment_sources, {})

    def test_default_compose_source_is_bound_to_successful_render_and_rejects_drift(self):
        import hashlib
        import json
        from sandbox.transports.remote_hosting_activation import (
            RegisteredRemoteActivationTransport, RemoteActivationError,
        )
        image = "ghcr.io/acme/widget@" + MANIFEST_DIGEST
        rendered = {"services": {
            "web": {"image": image, "build": None, "pull_policy": "never",
                    "platform": "linux/amd64", "depends_on": {},
                    "labels": {"org.sandbox.application-topology.v1": "topology-a"}},
            "migrate": {"image": image, "platform": "linux/amd64"}}}
        expected_digest = "sha256:" + hashlib.sha256(json.dumps(
            rendered, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        seen_sources = []; fail_source = {"value": False}
        def runner(*, argv, environment, private_environment, private_environment_source,
                   redact_environment_keys, timeout_seconds, max_output_bytes):
            if private_environment_source:
                seen_sources.append(private_environment_source)
                if fail_source["value"]:
                    return {"returncode": 91, "stdout": "", "stderr": "compose_source_refused",
                            "terminated": True}
                if private_environment_source["render_digest"] != expected_digest:
                    return {"returncode": 91, "stdout": "", "stderr": "compose_source_mismatch",
                            "terminated": True}
                return {"returncode": 0, "stdout": "container-default\n", "stderr": "",
                        "terminated": True}
            if argv[:3] == ("docker", "compose", "--file"):
                return {"returncode": 0, "stdout": json.dumps(rendered), "stderr": "",
                        "terminated": True}
            if argv[:2] == ("docker", "info"):
                return {"returncode": 0, "stdout": "daemon-a\n", "stderr": "",
                        "terminated": True}
            return {"returncode": 0, "stdout": json.dumps([{"Id": CONFIG_DIGEST,
                    "Os": "linux", "Architecture": "amd64"}]), "stderr": "",
                    "terminated": True}
        target = staged_proof().target.as_mapping()
        transport = RegisteredRemoteActivationTransport(argv_runner=runner,
            target_identity_observer=lambda: {"machine_identity": target["machine_identity"],
                                               "target_identity": target["target_identity"]})
        transport.render_topology(compose_files=("compose.yml",), project_name="widget",
            selected_services=("web",), image_overrides={"web": image})
        handle = transport.create_init(declaration=init_declaration(), image=image,
            platform={"os": "linux", "architecture": "amd64"}, target=target, start=False)
        self.assertEqual(seen_sources[0]["environment"], {
            "SANDBOX_ACTIVATION_IMAGE_WEB": image})
        self.assertEqual(seen_sources[0]["render_digest"], expected_digest)
        self.assertNotIn("value", repr(handle.expected))
        fail_source["value"] = True
        with self.assertRaises(RemoteActivationError):
            transport.create_init(declaration=init_declaration(), image=image,
                platform={"os": "linux", "architecture": "amd64"}, target=target, start=False)
        fail_source["value"] = False
        transport._compose_selector["render_digest"] = "sha256:" + "f" * 64
        with self.assertRaises(RemoteActivationError):
            transport.create_init(declaration=init_declaration(), image=image,
                platform={"os": "linux", "architecture": "amd64"}, target=target, start=False)


if __name__ == "__main__": unittest.main()
