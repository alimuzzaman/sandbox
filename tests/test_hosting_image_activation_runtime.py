import unittest

from tests.fixtures.hosting_image_activation import FakeRuntime, activation_policy, activation_request


class ActivationRuntimeTests(unittest.TestCase):
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
                                   exact_runtime_epoch=request.proof.target.daemon_identity)
        mutations = (
            lambda raw: raw["services"]["web"].update(image="ghcr.io/acme/widget:latest"),
            lambda raw: raw["services"]["web"].update(build={"context": "."}),
            lambda raw: raw["services"]["web"].update(pull_policy="always"),
            lambda raw: raw.update(orphans=["old"]),
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
                                               exact_runtime_epoch=request.proof.target.daemon_identity)
        with self.assertRaises(ValueError):
            validate_rendered_topology(
                base, selected_services=policy.selected_services,
                exact_image=image, exact_platform=request.plan.image.platform.as_mapping(),
                exact_topology_digest=request.proof.observed_identity["topology_digest"],
                exact_service_projection=policy.compose_projection[:-1],
                exact_runtime_epoch=request.proof.target.daemon_identity)

    def test_running_observation_requires_one_coherent_epoch_and_exact_health(self):
        from sandbox.hosting.images.activation.runtime_observer import RuntimeObserver
        runtime = FakeRuntime(); request = activation_request(); policy = activation_policy()
        observation = RuntimeObserver(runtime).observe(
            target=request.proof.target.as_mapping(), selected_services=policy.selected_services,
            exact_image=request.plan.image.repository_qualified_digest,
            local_image_id=request.plan.image.config_digest,
            config_digest=request.plan.image.config_digest,
            platform=request.plan.image.platform.as_mapping(),
            topology_digest=request.proof.observed_identity["topology_digest"], edge_identity="edge-a")
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
                edge_identity="edge-a")

    def test_transport_running_identity_and_platform_come_from_inspect_without_defaults(self):
        import json
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport
        image_id = "sha256:" + "d" * 64
        def runner(*, argv, environment, private_environment, private_environment_source,
                   redact_environment_keys, timeout_seconds, max_output_bytes):
            if argv[:2] == ("docker", "info"):
                stdout = "daemon-a\n"
            elif argv[:2] == ("docker", "ps"):
                stdout = json.dumps({"ID": "container-a",
                    "Labels": "com.docker.compose.service=web"}) + "\n"
            elif argv[:3] == ("docker", "image", "inspect"):
                stdout = json.dumps([{"Id": image_id, "Os": "linux",
                                      "Architecture": "arm64", "Variant": "v8"}])
            else:
                stdout = json.dumps([{"Id": "container-runtime-a", "Image": image_id,
                    "Config": {"Image": "ghcr.io/acme/widget@" + "sha256:" + "a" * 64,
                               "Labels": {"org.sandbox.application-topology.v1": "topology-a"}},
                    "State": {"Health": {"Status": "healthy"}}}])
            return {"returncode": 0, "stdout": stdout, "stderr": "", "terminated": True}
        transport = RegisteredRemoteActivationTransport(argv_runner=runner,
            target_identity_observer=lambda: {"machine_identity": "machine-a",
                                               "target_identity": "target-a"})
        observed = transport.observe_running(target={}, services=("web",))
        self.assertEqual(observed["services"][0]["platform"],
                         {"os": "linux", "architecture": "arm64", "variant": "v8"})
        self.assertEqual(observed["services"][0]["runtime_identity"], "container-runtime-a")

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


if __name__ == "__main__": unittest.main()
