from types import SimpleNamespace
import unittest


class TestManagedWordPress(unittest.TestCase):
    def test_bootstrap_uses_only_fixed_digest_bound_helper_verb(self):
        from sandbox.runtimes.managed.wordpress import ManagedWordPressBootstrap
        calls = []
        process = SimpleNamespace(run=lambda argv, timeout: calls.append((argv, timeout)) or
                                  SimpleNamespace(returncode=0))
        component = ManagedWordPressBootstrap(process=process, helper="/fixed/helper")
        plan = {"machine_id": "sb-0123456789ab", "policy_digest": "a" * 64}
        self.assertTrue(component.initialize(plan)["ok"])
        self.assertEqual(calls, [(('sudo', '-n', '/fixed/helper', 'wordpress-bootstrap',
                                   'sb-0123456789ab', 'a' * 64), 300)])

    def test_invalid_identity_fails_before_privileged_process(self):
        from sandbox.runtimes.managed.wordpress import ManagedWordPressBootstrap
        process = SimpleNamespace(run=lambda *_args, **_kwargs:
                                  self.fail("privileged process must not run"))
        component = ManagedWordPressBootstrap(process=process, helper="/fixed/helper")
        with self.assertRaises(ValueError):
            component.initialize({"machine_id": "foreign", "policy_digest": "a" * 64})


if __name__ == "__main__": unittest.main()
