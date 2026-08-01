import io
import unittest
from unittest import mock


class Args:
    action = "install"
    web_server = "nginx"
    json = True
    project_dir = "."
    label = "default"


class Plan:
    simulation_digest = "a" * 64


class Planner:
    def plan(self, **kwargs): return Plan()


class Service:
    def __init__(self): self.interactive = None
    def apply(self, plan, *, interactive):
        self.interactive = interactive
        return {"ok": False, "state": "pending_confirmation", "mutated": False,
                "reason": {"code": "pending_install_confirmation"}}


class TestNativeInstallCommand(unittest.TestCase):
    def test_non_tty_install_stays_pending_and_delegates_no_mutation(self):
        from sandbox.commands.native import cmd_native
        service = Service(); output = io.StringIO()
        with mock.patch("sandbox.application.context.managed_package_planner",
                        return_value=Planner()), \
                mock.patch("sandbox.application.context.managed_package_service",
                           return_value=service), mock.patch("sys.stdout", output):
            cmd_native({}, Args())
        self.assertFalse(service.interactive)
        self.assertIn('"state": "pending_confirmation"', output.getvalue())


if __name__ == "__main__": unittest.main()
