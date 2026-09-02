import unittest

from tests.fixtures.hosting_image_activation import (
    DIGEST_B, FakeRuntime, activation_policy, activation_request,
)


class ActivationRuntimeTests(unittest.TestCase):
    def test_local_proof_rejects_different_registered_target_identity(self):
        from sandbox.hosting.images.activation.runtime_observer import RuntimeObserver
        runtime = FakeRuntime(); request = activation_request()
        original = runtime.observe_local_image
        def changed(**kwargs):
            value = original(**kwargs)
            value["target_identity_start"] = value["target_identity_end"] = "different-target"
            return value
        runtime.observe_local_image = changed
        with self.assertRaisesRegex(ValueError, "local_image_mismatch"):
            RuntimeObserver(runtime).prove_local(
                target=request.proof.target.as_mapping(), proof=request.proof)

    def test_rendered_topology_rejects_tag_build_pull_platform_and_orphans(self):
        from sandbox.hosting.images.activation.runtime_observer import validate_rendered_topology
        request = activation_request(); policy = activation_policy(); runtime = FakeRuntime()
        image = request.plan.image.repository_qualified_digest
        base = runtime.render_topology(selected_services=policy.selected_services,
                                       image_overrides={name: image for name in policy.selected_services})
        self.assertEqual(
            base["services"],
            {item["service"]: {key: value for key, value in item.items()
                               if key != "service"}
             for item in policy.compose_projection})
        validate_rendered_topology(base, selected_services=policy.selected_services,
                                   exact_image=image, exact_platform=request.plan.image.platform.as_mapping(),
                                   exact_topology_digest=request.proof.observed_identity["topology_digest"],
                                   exact_service_projection=policy.compose_projection,
                                   exact_runtime_epoch=request.proof.target.daemon_identity,
                                   exact_configuration_digest=base["configuration_digest"])
        mutations = (
            lambda raw: raw["services"]["web"].update(image="ghcr.io/acme/widget:latest"),
            lambda raw: raw["services"]["web"].update(build={"context": "."}),
            lambda raw: raw["services"]["web"].update(pull_policy="always"),
            lambda raw: raw.update(orphans=["old"]),
            lambda raw: raw.update(configuration_digest="sha256:" + "e" * 64),
        )
        import copy
        for mutate in mutations:
            candidate = copy.deepcopy(base); mutate(candidate)
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    validate_rendered_topology(candidate, selected_services=policy.selected_services,
                                               exact_image=image,
                                               exact_platform=request.plan.image.platform.as_mapping(),
                                               exact_topology_digest=request.proof.observed_identity["topology_digest"],
                                               exact_service_projection=policy.compose_projection,
                                               exact_runtime_epoch=request.proof.target.daemon_identity,
                                               exact_configuration_digest=base["configuration_digest"])
        with self.assertRaises(ValueError):
            validate_rendered_topology(
                base, selected_services=policy.selected_services,
                exact_image=image, exact_platform=request.plan.image.platform.as_mapping(),
                exact_topology_digest=request.proof.observed_identity["topology_digest"],
                exact_service_projection=policy.compose_projection[:-1],
                exact_runtime_epoch=request.proof.target.daemon_identity,
                exact_configuration_digest=base["configuration_digest"])

    def test_running_observation_requires_one_coherent_epoch_and_exact_health(self):
        from sandbox.hosting.images.activation.runtime_observer import RuntimeObserver
        runtime = FakeRuntime(); request = activation_request(); policy = activation_policy()
        observation = RuntimeObserver(runtime).observe(
            target=request.proof.target.as_mapping(), selected_services=policy.selected_services,
            exact_image=request.plan.image.repository_qualified_digest,
            local_image_id=request.plan.image.config_digest,
            config_digest=request.plan.image.config_digest,
            platform=request.plan.image.platform.as_mapping(),
            topology_digest=request.proof.observed_identity["topology_digest"], edge_identity="edge-a",
            compose_project="widget", compose_config_hashes={name: DIGEST_B
                                                               for name in policy.selected_services})
        self.assertTrue(observation.health_complete)

    def test_running_observation_rejects_independently_observed_target_replacement(self):
        from sandbox.hosting.images.activation.runtime_observer import RuntimeObserver
        runtime = FakeRuntime(); request = activation_request(); policy = activation_policy()
        original = runtime.observe_running
        def replaced(**kwargs):
            value = original(**kwargs); value["target_epoch_end"] = "machine-replaced"; return value
        runtime.observe_running = replaced
        with self.assertRaises(ValueError):
            RuntimeObserver(runtime).observe(
                target=request.proof.target.as_mapping(), selected_services=policy.selected_services,
                exact_image=request.plan.image.repository_qualified_digest,
                local_image_id=request.plan.image.config_digest,
                config_digest=request.plan.image.config_digest,
                platform=request.plan.image.platform.as_mapping(),
                topology_digest=request.proof.observed_identity["topology_digest"],
                edge_identity="edge-a", compose_project="widget",
                compose_config_hashes={name: DIGEST_B for name in policy.selected_services})

    def test_running_observation_rejects_stable_compose_config_drift(self):
        from sandbox.hosting.images.activation.runtime_observer import RuntimeObserver
        runtime = FakeRuntime(); request = activation_request(); policy = activation_policy()
        original = runtime.observe_running
        def drifted(**kwargs):
            value = original(**kwargs)
            value["services"][0]["compose_config_hash"] = "sha256:" + "c" * 64
            return value
        runtime.observe_running = drifted
        with self.assertRaises(ValueError):
            RuntimeObserver(runtime).observe(
                target=request.proof.target.as_mapping(),
                selected_services=policy.selected_services,
                exact_image=request.plan.image.repository_qualified_digest,
                local_image_id=request.plan.image.config_digest,
                config_digest=request.plan.image.config_digest,
                platform=request.plan.image.platform.as_mapping(),
                topology_digest=request.proof.observed_identity["topology_digest"],
                edge_identity="edge-a", compose_project="widget",
                compose_config_hashes={name: DIGEST_B for name in policy.selected_services})

    def test_transport_running_identity_and_platform_come_from_inspect_without_defaults(self):
        import json
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport
        image_id = "sha256:" + "d" * 64
        def runner(*, argv, environment, private_environment, private_environment_source,
                   redact_environment_keys, timeout_seconds, max_output_bytes):
            if argv[:2] == ("docker", "info"):
                stdout = "daemon-a\n"
            else:
                self.assertEqual(argv, ("sandbox-activation-observe-running", "widget", "web"))
                stdout = json.dumps([{"service": "web", "runtime_identity": "container-runtime-a",
                    "compose_project": "widget", "declared_image": "ghcr.io/acme/widget@" + DIGEST_B,
                    "repository_digest": "ghcr.io/acme/widget@" + DIGEST_B,
                    "local_image_id": image_id, "config_digest": image_id,
                    "platform": {"os": "linux", "architecture": "arm64", "variant": "v8"},
                    "topology_identity": "topology-a", "compose_config_hash": DIGEST_B,
                    "healthy": True}])
            return {"returncode": 0, "stdout": stdout, "stderr": "", "terminated": True}
        transport = RegisteredRemoteActivationTransport(argv_runner=runner,
            target_identity_observer=lambda: {"machine_identity": "machine-a",
                                               "target_identity": "target-a"},
            configuration_binding_key=b"k" * 32)
        observed = transport.observe_running(target={}, services=("web",),
                                             compose_project="widget")
        self.assertEqual(observed["services"][0]["platform"],
                         {"os": "linux", "architecture": "arm64", "variant": "v8"})
        self.assertEqual(observed["services"][0]["runtime_identity"], "container-runtime-a")

    def test_render_refuses_unsnapshotted_compose_resources_before_effect(self):
        import json
        from sandbox.transports.remote_hosting_activation import (
            RegisteredRemoteActivationTransport, RemoteActivationError,
        )
        image = "ghcr.io/acme/widget@sha256:" + "a" * 64
        base = {"services": {"web": {"image": image, "build": None,
            "pull_policy": "never", "platform": "linux/amd64", "depends_on": {},
            "labels": {"org.sandbox.application-topology.v1": "topology-a"}}},
            "x-sandbox-configuration-digest": "sha256:" + "b" * 64,
            "x-sandbox-compose-config-hashes": {"web": DIGEST_B},
            "x-sandbox-has-configs": False, "x-sandbox-has-secrets": False,
            "x-sandbox-has-external-networks": False}
        cases = (
            "x-sandbox-has-configs", "x-sandbox-has-secrets",
            "x-sandbox-has-external-networks",
        )
        for marker in cases:
            rendered = {**base, marker: True}
            def runner(**_kwargs):
                return {"returncode": 0, "stdout": json.dumps(rendered), "stderr": "",
                        "terminated": True}
            transport = RegisteredRemoteActivationTransport(argv_runner=runner,
                target_identity_observer=lambda: {"machine_identity": "machine-a",
                                                   "target_identity": "target-a"},
                configuration_binding_key=b"k" * 32)
            with self.subTest(marker=marker), \
                    self.assertRaisesRegex(RemoteActivationError, "topology_mismatch"):
                transport.render_topology(
                    compose_files=("compose.yml",), project_name="widget",
                    selected_services=("web",), image_overrides={"web": image})

    def test_transport_local_observation_preserves_arm64_variant(self):
        import json
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport
        manifest = "sha256:" + "a" * 64
        config = "sha256:" + "d" * 64
        image = "ghcr.io/acme/widget@" + manifest
        def runner(*, argv, environment, private_environment, private_environment_source,
                   redact_environment_keys, timeout_seconds, max_output_bytes):
            stdout = "daemon-a\n" if argv[:2] == ("docker", "info") else json.dumps([{
                "Id": config, "Os": "linux", "Architecture": "arm64", "Variant": "v8",
                "RepoDigests": [image]}])
            return {"returncode": 0, "stdout": stdout, "stderr": "", "terminated": True}
        transport = RegisteredRemoteActivationTransport(argv_runner=runner,
            target_identity_observer=lambda: {"machine_identity": "machine-a",
                                               "target_identity": "target-a"})
        observed = transport.observe_local_image(target={}, repository_digest=image)
        self.assertEqual(observed["platform"],
                         {"os": "linux", "architecture": "arm64", "variant": "v8"})

    def test_transport_running_ignores_same_service_from_foreign_compose_project(self):
        import json
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport
        calls = []
        def runner(*, argv, **_kwargs):
            calls.append(argv)
            if argv[:2] == ("docker", "info"):
                stdout = "daemon-a\n"
            elif argv[:1] == ("sandbox-activation-observe-running",):
                stdout = "[]"
            else:
                raise AssertionError("foreign container must not be inspected")
            return {"returncode": 0, "stdout": stdout, "stderr": "", "terminated": True}
        transport = RegisteredRemoteActivationTransport(argv_runner=runner,
            target_identity_observer=lambda: {"machine_identity": "machine-a",
                                               "target_identity": "target-a"},
            configuration_binding_key=b"k" * 32)
        observed = transport.observe_running(target={}, services=("web",),
                                             compose_project="widget")
        self.assertEqual(observed["services"], [])
        self.assertEqual(calls[1], ("sandbox-activation-observe-running", "widget", "web"))

    def test_replace_services_uses_closed_runner_and_validated_render_source(self):
        import json
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport
        image = "ghcr.io/acme/widget@sha256:" + "a" * 64
        rendered = {"services": {"web": {"image": image, "build": None,
            "pull_policy": "never", "platform": "linux/amd64", "depends_on": {},
            "labels": {"org.sandbox.application-topology.v1": "topology-a"}}},
            "x-sandbox-configuration-digest": "sha256:" + "b" * 64,
            "x-sandbox-compose-config-hashes": {"web": DIGEST_B},
            "x-sandbox-has-configs": False, "x-sandbox-has-secrets": False,
            "x-sandbox-has-external-networks": False}
        calls = []
        def runner(**kwargs):
            calls.append(kwargs)
            argv = kwargs["argv"]
            if argv[:2] == ("docker", "info"):
                stdout = "daemon-a\n"
            elif "config" in argv:
                stdout = json.dumps(rendered)
            else:
                self.assertEqual(set(kwargs["private_environment"]), {
                    "SANDBOX_ACTIVATION_CONFIGURATION_HMAC_KEY"})
                self.assertIsNone(kwargs["redact_environment_keys"])
                self.assertEqual(kwargs["private_environment_source"]["kind"],
                                 "compose_replace_v1")
                self.assertEqual(argv[2:4], ("--file", "-"))
                stdout = ""
            return {"returncode": 0, "stdout": stdout, "stderr": "", "terminated": True}
        transport = RegisteredRemoteActivationTransport(argv_runner=runner,
            target_identity_observer=lambda: {"machine_identity": "machine-a",
                                               "target_identity": "target-a"},
            configuration_binding_key=b"k" * 32)
        transport.render_topology(compose_files=("compose.yml",), project_name="widget",
            selected_services=("web",), image_overrides={"web": image})
        transport.replace_services(compose_files=("compose.yml",), project_name="widget",
            services=("web",), exact_image=image,
            environment_overrides={"SANDBOX_ACTIVATION_IMAGE_WEB": image},
            timeout_seconds=30)
        self.assertEqual(sum(1 for item in calls
                             if item["private_environment_source"].get("kind") ==
                             "compose_replace_v1"), 1)


if __name__ == "__main__": unittest.main()
