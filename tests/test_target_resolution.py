import unittest


class TargetResolutionTests(unittest.TestCase):
    def service(self, runtime=None, remotes=None):
        from sandbox.application.target_service import TargetService

        config = {"root": "/tmp/project", "runtime": runtime or {"default": "local"}}
        remotes = remotes or {}
        return TargetService(config_loader=lambda _root: config,
                             remote_lookup=lambda name: remotes.get(name))

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
