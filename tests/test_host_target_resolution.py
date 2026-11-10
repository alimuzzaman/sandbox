"""Spec 072: --target / --name resolve recorded stacks and plain targets."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands import hosting as hosting_cmd
from sandbox.hosting import target_resolution
from sandbox.hosting.target_resolution import TargetRefusal

PLAIN = """version: 1
project: example-site
environments:
  production:
    compose:
      files: [compose.yml]
      service: web
      container_port: 8080
    healthcheck:
      path: /
      statuses: [200, 399]
    deploy:
      allowed_branches: [main]
      require_clean: true
    host:
      primary: site.example.test
    cloudflare:
      proxied: true
      tls: origin-ca
      ssl_mode: strict
"""

TEMPLATE = PLAIN.replace("project: example-site", "project: hub-{slug}").replace(
    "version: 1\n", "version: 1\nstack:\n  slug: '[a-z0-9-]+'\n")


class _Fixture:
    def __init__(self, test: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        patcher = patch("sandbox.core._paths.RUNTIME_DIR", self.runtime)
        patcher.start()
        test.addCleanup(patcher.stop)

    def checkout(self, text: str, name: str = "checkout") -> str:
        root = self.base / name
        root.mkdir()
        (root / "sandbox.hosting.yml").write_text(text)
        (root / "compose.yml").write_text("services: {}\n")
        return str(root)

    def stack(self, slug: str = "demo", project: str = "hub-demo",
              environment: str = "sandbox", job_id: str | None = None) -> dict:
        directory = self.runtime / "stacks" / slug
        directory.mkdir(parents=True)
        (directory / "sandbox.hosting.yml").write_text("rendered\n")
        record = {"remote": "myvps", "slug": slug, "project": project,
                  "environment": environment,
                  "manifest": str(directory / "sandbox.hosting.yml"),
                  "last_apply": {"job_id": job_id, "status": "submitted"}}
        return {"version": 1, "stacks": {f"myvps/{slug}": record}}


def _state(*keys: str) -> dict:
    return {"hosts": {key: {"commit": "a" * 40} for key in keys}}


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.fx = _Fixture(self)

    def test_stack_target_uses_rendered_manifest_not_cwd_template(self):
        registry = self.fx.stack(job_id="job-1")
        template_dir = self.fx.checkout(TEMPLATE)
        resolved = target_resolution.resolve(
            "myvps", target="hub-demo/sandbox", project_dir=template_dir,
            state=_state("myvps/hub-demo/sandbox"), registry=registry)
        self.assertEqual(resolved.kind, "stack")
        self.assertEqual(resolved.slug, "demo")
        self.assertEqual(Path(resolved.project_dir), self.fx.runtime / "stacks" / "demo")
        self.assertEqual(resolved.last_apply_job_id, "job-1")
        self.assertEqual(resolved.state_key, "myvps/hub-demo/sandbox")

    def test_stack_name_resolves_same_target(self):
        registry = self.fx.stack()
        resolved = target_resolution.resolve(
            "myvps", name="demo", state=_state("myvps/hub-demo/sandbox"), registry=registry)
        self.assertEqual((resolved.project, resolved.environment), ("hub-demo", "sandbox"))

    def test_plain_target_resolves_through_project_dir(self):
        directory = self.fx.checkout(PLAIN)
        resolved = target_resolution.resolve(
            "myvps", target="example-site/production", project_dir=directory,
            state=_state("myvps/example-site/production"), registry={"version": 1, "stacks": {}})
        self.assertEqual(resolved.kind, "plain")
        self.assertEqual(resolved.project_dir, str(Path(directory).resolve()))

    def test_unrecorded_target_is_refused_before_reading_any_manifest(self):
        with patch.object(target_resolution.hosting, "validate_manifest") as validate:
            with self.assertRaises(TargetRefusal) as caught:
                target_resolution.resolve(
                    "myvps", target="example-site/production", project_dir="/nonexistent",
                    state=_state(), registry={"version": 1, "stacks": {}})
        self.assertEqual(caught.exception.code, "host_target_unknown")
        validate.assert_not_called()

    def test_stack_without_successful_apply_is_unknown_with_hint(self):
        registry = self.fx.stack()
        with self.assertRaises(TargetRefusal) as caught:
            target_resolution.resolve("myvps", name="demo", state=_state(), registry=registry)
        self.assertEqual(caught.exception.code, "host_target_unknown")
        self.assertIn("stack demo has no successful apply", str(caught.exception))

    def test_template_without_matching_stack_is_unknown_target(self):
        directory = self.fx.checkout(TEMPLATE)
        with self.assertRaises(TargetRefusal) as caught:
            target_resolution.resolve(
                "myvps", target="hub-other/production", project_dir=directory,
                state=_state("myvps/hub-other/production"), registry={"version": 1, "stacks": {}})
        self.assertEqual(caught.exception.code, "host_target_unknown")
        self.assertIn("stack template", str(caught.exception))

    def test_manifest_for_another_project_is_refused(self):
        directory = self.fx.checkout(PLAIN)
        with self.assertRaises(TargetRefusal) as caught:
            target_resolution.resolve(
                "myvps", target="other-site/production", project_dir=directory,
                state=_state("myvps/other-site/production"), registry={"version": 1, "stacks": {}})
        self.assertEqual(caught.exception.code, "host_target_unknown")

    def test_environment_mismatch_and_bad_selectors_are_invalid(self):
        registry = self.fx.stack()
        state = _state("myvps/hub-demo/sandbox")
        cases = [
            dict(target="hub-demo/sandbox", environment="production"),
            dict(target="hub-demo"),
            dict(target="Hub/../x"),
            dict(target="hub-demo/sandbox", name="demo"),
            dict(),
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(TargetRefusal) as caught:
                target_resolution.resolve("myvps", state=state, registry=registry, **kwargs)
            self.assertEqual(caught.exception.code, "host_target_invalid")
        with self.assertRaises(TargetRefusal):
            target_resolution.resolve(None, target="hub-demo/sandbox", state=state, registry=registry)

    def test_stack_record_under_a_foreign_key_or_remote_is_not_trusted(self):
        registry = self.fx.stack()
        record = registry["stacks"].pop("myvps/demo")
        registry["stacks"]["myvps/other"] = record
        state = _state("myvps/hub-demo/sandbox")
        with self.assertRaises(TargetRefusal) as caught:
            target_resolution.resolve("myvps", name="demo", state=state, registry=registry)
        self.assertEqual(caught.exception.code, "host_target_invalid")
        registry = self.fx_other_remote()
        with self.assertRaises(TargetRefusal) as caught:
            target_resolution.resolve("myvps", name="demo", state=state, registry=registry)
        self.assertEqual(caught.exception.code, "host_target_unknown")

    def fx_other_remote(self) -> dict:
        record = {"remote": "elsewhere", "slug": "demo", "project": "hub-demo",
                  "environment": "sandbox", "manifest": "/x/sandbox.hosting.yml"}
        return {"version": 1, "stacks": {"elsewhere/demo": record}}

    def test_stack_manifest_outside_its_runtime_dir_is_refused(self):
        registry = self.fx.stack()
        record = registry["stacks"]["myvps/demo"]
        record["manifest"] = self.fx.checkout(PLAIN) + "/sandbox.hosting.yml"
        with self.assertRaises(TargetRefusal) as caught:
            target_resolution.resolve("myvps", name="demo", state=_state("myvps/hub-demo/sandbox"),
                                      registry=registry)
        self.assertEqual(caught.exception.code, "host_target_invalid")


def _args(action: str, **overrides) -> SimpleNamespace:
    values = dict(action=action, remote="myvps", lines=20, apply_log=False, json=True,
                  project_dir=None, environment=None, target=None, name=None,
                  image_action=None, initializer=None, local_images=None, timeout=None,
                  all=False, confirm=False)
    values.update(overrides)
    return SimpleNamespace(**values)


class ReadOnlyCommandTests(unittest.TestCase):
    """Feedback da5d522e: logs/status/diagnose take --target on a stack."""

    def setUp(self):
        self.fx = _Fixture(self)
        registry = self.fx.stack()
        self.template_dir = self.fx.checkout(TEMPLATE)
        for target, value in (
                ("sandbox.hosting.stack_registry.read", registry),
                ("sandbox.commands.hosting.hosting.load_host_state",
                 _state("myvps/hub-demo/sandbox")),
                ("sandbox.commands.hosting.remote.get_remote", {"provisioned": True})):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.validated = {"project": "hub-demo", "environment": "sandbox"}

    def _run(self, args) -> tuple[dict, unittest.mock.MagicMock]:
        buffer = io.StringIO()
        with patch("sandbox.commands.hosting.hosting.validate_manifest",
                   return_value=self.validated) as validate, redirect_stdout(buffer):
            try:
                hosting_cmd.cmd_host({}, args)
            except SystemExit as exc:
                self.fail(f"exited {exc.code}: {buffer.getvalue()}")
        return json.loads(buffer.getvalue()), validate

    def test_logs_with_target_reads_stack_manifest(self):
        with patch.object(hosting_cmd, "_read_host_logs", return_value="hub | ready\n"):
            payload, validate = self._run(_args("logs", target="hub-demo/sandbox",
                                                project_dir=self.template_dir))
        self.assertEqual(payload["output"], "hub | ready\n")
        directory, environment = validate.call_args.args
        self.assertEqual(Path(directory), self.fx.runtime / "stacks" / "demo")
        self.assertEqual(environment, "sandbox")

    def test_logs_with_name_reads_stack_manifest(self):
        with patch.object(hosting_cmd, "_read_host_logs", return_value="ok\n"):
            payload, validate = self._run(_args("logs", name="demo"))
        self.assertTrue(payload["ok"])
        self.assertEqual(Path(validate.call_args.args[0]), self.fx.runtime / "stacks" / "demo")

    def test_status_and_diagnose_with_target_read_stack_manifest(self):
        for action, helper in (("status", "_host_runtime_status"),
                               ("diagnose", "_host_runtime_diagnose")):
            with self.subTest(action=action), patch.object(
                    hosting_cmd, helper, return_value={"project": "hub-demo"}):
                payload, validate = self._run(_args(action, target="hub-demo/sandbox",
                                                    project_dir=self.template_dir))
                self.assertTrue(payload["ok"])
                self.assertEqual(Path(validate.call_args.args[0]),
                                 self.fx.runtime / "stacks" / "demo")

    def test_unknown_target_is_a_json_refusal(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer), self.assertRaises(SystemExit) as caught:
            hosting_cmd.cmd_host({}, _args("logs", target="nope/sandbox"))
        self.assertEqual(caught.exception.code, 1)
        payload = json.loads(buffer.getvalue())
        self.assertEqual((payload["ok"], payload["code"]), (False, "host_target_unknown"))


if __name__ == "__main__":
    unittest.main()
