import unittest

import sandbox.core._hermes as legacy
import sandbox.hermes.facade as facade
import sandbox.hermes.service as service


class TestHermesFacade(unittest.TestCase):
    def test_every_legacy_public_callable_remains_reachable_without_wrapping(self):
        public_callables = {
            name for name, value in vars(legacy).items()
            if not name.startswith("_") and callable(value)
        }
        self.assertTrue(public_callables)
        for name in public_callables:
            with self.subTest(name=name):
                self.assertIs(getattr(facade, name), getattr(legacy, name))

    def test_public_facade_preserves_legacy_function_identity(self):
        for name in ("status", "run", "job_status", "job_kill", "gateway", "backup_list"):
            with self.subTest(name=name):
                self.assertIs(getattr(facade, name), getattr(legacy, name))

    def test_composition_service_has_an_explicit_factory(self):
        factory = getattr(service, "compose_hermes_service", None)
        self.assertTrue(callable(factory), "service must expose compose_hermes_service(dependencies)")
        composed = factory({}) if callable(factory) else None
        self.assertIsNotNone(composed.state)
        self.assertIsNotNone(composed.routing)
        self.assertIsNotNone(composed.jobs)
        self.assertIsNotNone(composed.gateway)
        self.assertIsNotNone(composed.backup)

    def test_migrated_public_functions_are_explicit_facade_exports(self):
        for name in ("status", "run", "job_status", "job_kill", "gateway", "backup_list"):
            with self.subTest(name=name):
                self.assertIn(name, facade.__dict__)
                self.assertIs(facade.__dict__[name], getattr(legacy, name))

    def test_command_service_preserves_argument_order_and_timeout(self):
        calls = []
        command_service = service.HermesCommandService(
            lambda args, timeout: calls.append((args, timeout)) or {"ok": True}
        )

        result = command_service.run(["hermes", "status", "--remote", "fixture"], 30)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls, [(["hermes", "status", "--remote", "fixture"], 30)])

    def test_final_facade_regression_preserves_legacy_error_envelope(self):
        expected = {
            "ok": False,
            "data": None,
            "error": {"code": "unknown_remote", "message": "remote is not configured"},
        }
        command_service = service.HermesCommandService(
            lambda _args, _timeout: expected
        )

        actual = command_service.run(["hermes", "status", "--remote", "missing"], 30)

        self.assertEqual(actual, expected)
        self.assertIsNot(actual, expected)


if __name__ == "__main__": unittest.main()
