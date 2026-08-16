import unittest


class TargetResolutionTests(unittest.TestCase):
    def service(self, runtime=None, remotes=None):
        from sandbox.application.target_service import TargetService

        config = {"root": "/tmp/project", "runtime": runtime or {"default": "local"}}
        remotes = remotes or {}
        return TargetService(config_loader=lambda _root: config,
                             remote_lookup=lambda name: remotes.get(name),
                             remote_list=lambda: remotes)

    def test_explicit_local_beats_configured_remote(self):
        from sandbox.jobs.models import TargetRequest

        service = self.service(
            {"default": "remote", "remote": "vps", "workspace": "default"},
            {"vps": {"provisioned": True}},
        )
        target = service.resolve(TargetRequest("/tmp/project", local=True))
        self.assertEqual((target.kind, target.remote_name, target.sources["target"]),
                         ("local", None, "explicit"))

    def test_explicit_named_remote_beats_config_and_validates_registration(self):
        from sandbox.jobs.models import TargetRequest

        service = self.service(
            {"default": "remote", "remote": "old"},
            {"new": {"provisioned": True, "capabilities": ["job.exec"]}},
        )
        target = service.resolve(TargetRequest("/tmp/project", remote="new",
                                               required_capability="job.exec"))
        self.assertEqual(target.remote_name, "new")
        self.assertTrue(target.namespace.startswith("remote:new:"))

    def test_configured_remote_and_workspace_are_selected(self):
        from sandbox.jobs.models import TargetRequest

        target = self.service(
            {"default": "remote", "remote": "vps", "workspace": "node-unit"},
            {"vps": {"provisioned": True}},
        ).resolve(TargetRequest("/tmp/project"))
        self.assertEqual((target.kind, target.workspace_label), ("remote", "node-unit"))

    def test_no_remote_configuration_defaults_local(self):
        from sandbox.jobs.models import TargetRequest

        target = self.service().resolve(TargetRequest("/tmp/project"))
        self.assertEqual(target.kind, "local")
        self.assertTrue(target.namespace.startswith("local:"))

    def test_one_configured_remote_is_inferred_through_the_catalog_callback(self):
        from sandbox.jobs.models import TargetRequest

        target = self.service(remotes={"vps": {"provisioned": True}}).resolve(
            TargetRequest("/tmp/project"))
        self.assertEqual((target.kind, target.remote_name), ("remote", "vps"))
        self.assertEqual(target.sources["remote_selection"], "single-configured")

    def test_inference_opt_out_keeps_a_configured_remote_local(self):
        """Instance lifecycle must not follow the one registered remote.

        `sb ensure` with no selector booted a local instance for years; once a
        single remote was registered, inference silently deployed every project
        to the VPS instead.
        """
        from sandbox.jobs.models import TargetRequest

        target = self.service(remotes={"vps": {"provisioned": True}}).resolve(
            TargetRequest("/tmp/project", allow_inferred_remote=False))
        self.assertEqual((target.kind, target.remote_name), ("local", None))
        self.assertEqual(target.sources["remote_selection"], "local")

    def test_inference_opt_out_still_honours_a_project_remote_target(self):
        from sandbox.jobs.models import TargetRequest

        target = self.service(
            {"default": "remote", "remote": "vps"},
            {"vps": {"provisioned": True}},
        ).resolve(TargetRequest("/tmp/project", allow_inferred_remote=False))
        self.assertEqual((target.kind, target.remote_name), ("remote", "vps"))

    def test_ambiguous_configured_remotes_fail_closed(self):
        from sandbox.application.target_service import TargetResolutionError
        from sandbox.jobs.models import TargetRequest

        service = self.service(remotes={
            "alpha": {"provisioned": True}, "beta": {"provisioned": True},
        })
        with self.assertRaisesRegex(TargetResolutionError, "multiple configured remotes"):
            service.resolve(TargetRequest("/tmp/project"))

    def test_invalid_combinations_unknown_unprovisioned_and_capability_fail(self):
        from sandbox.application.target_service import TargetResolutionError
        from sandbox.jobs.models import TargetRequest

        service = self.service(remotes={
            "cold": {"provisioned": False},
            "limited": {"provisioned": True, "capabilities": ["status"]},
        })
        cases = (
            TargetRequest("/tmp/project", local=True, remote="cold"),
            TargetRequest("/tmp/project", remote="missing"),
            TargetRequest("/tmp/project", remote="cold"),
            TargetRequest("/tmp/project", remote="limited", required_capability="job.exec"),
        )
        for request in cases:
            with self.subTest(request=request), self.assertRaises(TargetResolutionError):
                service.resolve(request)

    def test_local_and_remote_workspace_namespaces_do_not_collide(self):
        from sandbox.jobs.models import TargetRequest

        service = self.service(remotes={"vps": {"provisioned": True}})
        local = service.resolve(TargetRequest("/tmp/project", local=True, workspace="same"))
        remote = service.resolve(TargetRequest("/tmp/project", remote="vps", workspace="same"))
        self.assertNotEqual(local.namespace, remote.namespace)


if __name__ == "__main__":
    unittest.main()
