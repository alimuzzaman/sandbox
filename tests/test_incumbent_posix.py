import unittest


class TestIncumbentPosix(unittest.TestCase):
    def request(self):
        from sandbox.runtimes.base import OperationRequest
        return OperationRequest("/project", "preflight")

    def test_user_authority_collision_and_lower_isolation_are_explicit(self):
        from sandbox.runtimes.incumbent.posix import PosixAdapter
        profile = {"authority": "user", "document_root": "/srv/site", "php": "/usr/bin/php",
                   "database": {"host": "localhost", "name": "demo", "user": "demo"}}
        result = PosixAdapter(profile=profile, platform="linux",
                              path_validator=lambda _profile: True).invoke(self.request())
        self.assertTrue(result.ok); self.assertEqual(result.data["runtime"]["isolation"], "trusted_shared_host")
        self.assertFalse(result.data["runtime"]["route_mutations"])
        for profile, collision, reason in (({**profile, "authority": "sandbox"}, False, "user_authority_required"),
                                           (profile, True, "profile_collision")):
            with self.subTest(reason=reason):
                blocked = PosixAdapter(profile=profile, platform="linux",
                                       collision_checker=lambda _p: collision,
                                       path_validator=lambda _profile: True).invoke(self.request())
                self.assertEqual(blocked.data["reason"]["code"], reason)

    def test_unowned_or_unavailable_declared_paths_fail_closed(self):
        from sandbox.runtimes.incumbent.posix import PosixAdapter
        profile = {"authority": "user", "document_root": "/srv/site",
                   "php": "/usr/bin/php"}
        blocked = PosixAdapter(profile=profile, platform="linux",
                               path_validator=lambda _profile: False).invoke(self.request())
        self.assertEqual(blocked.data["reason"]["code"], "profile_ownership_invalid")
