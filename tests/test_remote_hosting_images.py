import unittest

from tests.hosting_image_fixtures import local_observation, stage_request, staging_policy


class TestRemoteHostingImages(unittest.TestCase):
    def test_result_parser_is_closed_and_bounded(self):
        from sandbox.transports.remote_hosting_images import RemoteImageStageError, parse_stage_response
        value = {"schema_version": 1, "ok": True, "code": "staged", "payload": {}}
        self.assertTrue(parse_stage_response(value).ok)
        with self.assertRaises(RemoteImageStageError):
            parse_stage_response({**value, "helper_output": "unsafe"})

    def test_fixed_transport_has_no_remote_job_or_caller_command_surface(self):
        from pathlib import Path
        source = (Path(__file__).parent.parent / "sandbox/transports/remote_hosting_images.py").read_text()
        self.assertNotIn("RemoteJobTransport", source)
        self.assertIn("systemd-run", source); self.assertIn("KillMode=control-group", source)
        self.assertIn("Delegate=no", source); self.assertIn("ProtectControlGroups=yes", source)

    def test_helper_pull_is_exact_and_has_no_tag_build_or_compose_path(self):
        from pathlib import Path
        source = (Path(__file__).parent.parent / "sandbox/hosting/images/staging_helper.py").read_text()
        self.assertIn('(\"docker\", \"pull\", plan[\"repository_qualified_digest\"])', source)
        self.assertIn("org.sandbox.application-topology.v1", source)
        self.assertIn("org.sandbox.application-topology.v1", source)
        for forbidden in ('\"latest\"', '\"docker\", \"build\"', "compose", "prune"):
            self.assertNotIn(forbidden, source)

    def test_observation_and_proof_drift_matrix_is_fail_closed(self):
        from sandbox.hosting.images.staging_models import (
            LocalImageObservation, StagedImageProof, StagingContractError, staging_digest,
        )
        policy = staging_policy(); request = stage_request(policy=policy)
        baseline = local_observation(policy)
        def rebuilt(**changes):
            body = baseline.body_mapping(); body.update(changes)
            if "observed_topology" in changes:
                body["topology_digest"] = staging_digest(
                    "sandbox.hosting.images.topology.v1", body["observed_topology"])
            if "config_digest" in changes and "local_image_id" not in changes:
                body["local_image_id"] = body["config_digest"]
            constructor = dict(body); constructor["target"] = baseline.target
            identity = dict(body)
            return LocalImageObservation(observation_id=staging_digest(
                "sandbox.hosting.images.local-observation.v1", identity), **constructor)
        for changes in (
            {"target_epoch_end": "other-machine"}, {"daemon_epoch_end": "other-daemon"},
            {"local_image_id": "sha256:" + "4" * 64},
        ):
            with self.subTest(changes=changes), self.assertRaises(StagingContractError):
                rebuilt(**changes)
        for changes in (
            {"repository": "other/repository"},
            {"repo_digest": "ghcr.io/other@sha256:" + "1" * 64},
            {"config_digest": "sha256:" + "3" * 64},
            {"platform": {"os": "linux", "architecture": "arm64"}},
            {"observed_topology": {"persistent_services": ["web"], "one_shot_services": []}},
        ):
            with self.subTest(changes=changes), self.assertRaises(StagingContractError):
                StagedImageProof.create(request, policy, rebuilt(**changes), 1)

    def test_helper_and_transport_measure_exact_installed_artifact_before_broker_ready(self):
        from pathlib import Path
        root = Path(__file__).parent.parent
        transport = (root / "sandbox/transports/remote_hosting_images.py").read_text()
        installer = (root / "scripts/install-remote.sh").read_text()
        self.assertIn("sha256sum -- ", transport)
        self.assertIn("manifest.json", transport)
        self.assertIn("/proc/self/fd/", transport)
        self.assertIn("READY_TIMEOUT_SECONDS", transport)
        self.assertNotIn('"--collect"', transport)
        self.assertIn("installed image staging helper digest mismatch", installer)
        self.assertIn("installed staging helper manifest mismatch", installer)


if __name__ == "__main__": unittest.main()
