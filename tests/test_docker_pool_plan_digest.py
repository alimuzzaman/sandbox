"""Spec 063 US4: the Docker-pool plan is bound to a digest of what restarts."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.core import _remote
from tests.subprocess_support import synthetic_environment


_DOCKER = '''#!/usr/bin/env python3
import json, sys
import os
args = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
if args[:2] == ["ps", "-q"]:
    calls = os.path.join(here, "ps-calls")
    count = int(open(calls).read()) + 1 if os.path.exists(calls) else 1
    open(calls, "w").write(str(count))
    extra = "\\nc-new" if count > 1 and os.path.exists(os.path.join(here, "grow")) else ""
    print("c-hosted\\nc-other\\nc-plain" + extra)
elif args[:2] == ["network", "ls"]:
    print("network-one")
elif args[:2] == ["network", "inspect"]:
    print(json.dumps([{"Id": "a" * 64, "Options": {}}]))
elif args and args[0] == "inspect":
    rows = {"c-hosted": "always\\tsandbox-host-shop-production",
            "c-other": "no\\tsomething-else", "c-plain": "no", "c-new": "no\\tlate"}
    for container in args[3:]:
        print(rows[container])
raise SystemExit(0)
'''


def _write(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o755)


class ProgramDigestTests(unittest.TestCase):
    HOSTED = [["shop/production", "sandbox-host-shop-production"]]

    def _run(self, root: Path, *, confirm: bool, plan_digest: str | None,
             pools_current: bool = False, grow: bool = False, hosted=None):
        binary = root / "bin"
        binary.mkdir()
        if grow:
            (binary / "grow").write_text("")
        _write(binary / "docker", _DOCKER)
        _write(binary / "ip", "#!/usr/bin/env python3\nprint('[]')\n")
        _write(binary / "dockerd", "#!/usr/bin/env python3\nraise SystemExit(0)\n")
        restarts = root / "restarts"
        _write(binary / "systemctl",
               "#!/usr/bin/env python3\nimport sys\nopen(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
               % str(restarts))
        config = root / "daemon.json"
        original = {"log-driver": "json-file"}
        if pools_current:
            original["default-address-pools"] = list(_remote.REMOTE_DOCKER_ADDRESS_POOLS)
        config.write_text(json.dumps(original))
        source = _remote._remote_docker_pool_program(
            confirm=confirm, hosted=self.HOSTED if hosted is None else hosted,
            plan_digest=plan_digest)
        source = source.replace(
            'pathlib.Path("/etc/docker/daemon.json")', f"pathlib.Path({str(config)!r})"
        ).replace(
            'pathlib.Path("/run/lock/sandbox-docker-pool.lock")',
            f"pathlib.Path({str(root / 'pool.lock')!r})")
        environment = synthetic_environment()
        environment["PATH"] = str(binary) + os.pathsep + environment.get("PATH", "")
        result = subprocess.run([sys.executable, "-c", source], capture_output=True,
                                text=True, env=environment, check=False, timeout=15)
        return result, config, original, restarts

    def _assert_untouched(self, config, original, restarts):
        self.assertFalse(restarts.exists())
        self.assertEqual(json.loads(config.read_text()), original)
        self.assertEqual(list(config.parent.glob("daemon.json.bak-*")), [])

    def test_plan_counts_other_running_containers_and_digests_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, config, original, restarts = self._run(
                Path(temporary), confirm=False, plan_digest=None)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(payload["other_running_containers"], 2)
            self.assertEqual(payload["plan_digest"],
                             _remote.docker_pool_plan_digest(["shop/production"], 2))
            self.assertRegex(payload["plan_digest"], r"^[0-9a-f]{16}$")
            self._assert_untouched(config, original, restarts)

    def test_container_started_after_first_check_refuses_before_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            digest = _remote.docker_pool_plan_digest(["shop/production"], 2)
            result, config, original, restarts = self._run(
                Path(temporary), confirm=True, plan_digest=digest, grow=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["code"], "docker_pool_plan_changed")
            self.assertEqual(payload["other_running_containers"], 3)
            self.assertEqual(payload["plan_digest"],
                             _remote.docker_pool_plan_digest(["shop/production"], 3))
            self._assert_untouched(config, original, restarts)

    def test_changed_hosted_targets_with_same_count_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            planned = _remote.docker_pool_plan_digest(["blog/staging"], 2)
            result, config, original, restarts = self._run(
                Path(temporary), confirm=True, plan_digest=planned)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)["code"], "docker_pool_plan_changed")
            self._assert_untouched(config, original, restarts)

    def test_matching_digest_with_current_pools_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            digest = _remote.docker_pool_plan_digest(["shop/production"], 2)
            result, _, _, restarts = self._run(
                Path(temporary), confirm=True, plan_digest=digest, pools_current=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "unchanged")
            self.assertFalse(restarts.exists())

    def test_hosted_strings_round_trip_without_program_injection(self):
        hostile = 'x\'"); raise SystemExit(9) #/{env}\n'
        with tempfile.TemporaryDirectory() as temporary:
            result, _, _, _ = self._run(
                Path(temporary), confirm=False, plan_digest=None,
                hosted=[[hostile, "sandbox-host-shop-production"]])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["plan_digest"],
                             _remote.docker_pool_plan_digest([hostile], 2))

    def test_stale_digest_refuses_with_zero_restarts_and_untouched_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            stale = _remote.docker_pool_plan_digest(["shop/production"], 1)
            result, config, original, restarts = self._run(
                Path(temporary), confirm=True, plan_digest=stale)
            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["code"], "docker_pool_plan_changed")
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["plan_digest"],
                             _remote.docker_pool_plan_digest(["shop/production"], 2))
            self._assert_untouched(config, original, restarts)

    def test_matching_digest_applies(self):
        with tempfile.TemporaryDirectory() as temporary:
            digest = _remote.docker_pool_plan_digest(["shop/production"], 2)
            result, config, _, restarts = self._run(
                Path(temporary), confirm=True, plan_digest=digest)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "complete")
            self.assertIn("restart docker", restarts.read_text())

    def test_stale_digest_refuses_even_when_pools_are_already_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, _, _, restarts = self._run(
                Path(temporary), confirm=True, plan_digest="0" * 16, pools_current=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)["code"], "docker_pool_plan_changed")
            self.assertFalse(restarts.exists())


def _completed(returncode=0, stdout=""):
    return subprocess.CompletedProcess(["ssh"], returncode, stdout=stdout, stderr="")


class WrapperDigestTests(unittest.TestCase):
    def test_digest_is_sixteen_hex_and_order_independent(self):
        first = _remote.docker_pool_plan_digest(["b/x", "a/y"], 3)
        self.assertEqual(first, _remote.docker_pool_plan_digest(["a/y", "b/x"], 3))
        self.assertNotEqual(first, _remote.docker_pool_plan_digest(["a/y", "b/x"], 4))
        self.assertNotEqual(first, _remote.docker_pool_plan_digest(["a/y"], 3))
        self.assertRegex(first, r"^[0-9a-f]{16}$")

    @patch("sandbox.core._remote.ssh_run")
    def test_confirm_without_digest_never_reaches_the_remote(self, ssh_run):
        for digest in (None, "", "XYZ", "a" * 15, "A" * 16):
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(ValueError, "docker_pool_plan_digest_required"):
                    _remote.remote_docker_pool({"ssh": "t"}, confirm=True, plan_digest=digest)
        ssh_run.assert_not_called()

    @patch("sandbox.core._remote.ssh_run")
    def test_recover_interrupted_stays_digest_free(self, ssh_run):
        ssh_run.return_value = _completed(stdout=json.dumps({
            "ok": True, "status": "recovery_planned", "requires_confirm": True,
            "recovery_candidate_count": 0, "recovery_window_seconds": 180,
            "recovery_expected_count": 2, "recovery_evidence_count": 2,
            "recovery_removed_count": 0,
        }))
        _remote.remote_docker_pool({"ssh": "t"}, confirm=True, recover_interrupted=True,
                                   expected_running=2)
        ssh_run.assert_called_once()

    @patch("sandbox.core._remote.ssh_run")
    def test_plan_changed_receipt_is_accepted_with_new_digest(self, ssh_run):
        ssh_run.return_value = _completed(returncode=2, stdout=json.dumps({
            "ok": False, "status": "failed", "code": "docker_pool_plan_changed",
            "message": "remote text", "plan_digest": "b" * 16,
            "other_running_containers": 5,
        }))
        result = _remote.remote_docker_pool({"ssh": "t"}, confirm=True, plan_digest="a" * 16,
                                            hosted=[["shop/production", "sandbox-host-shop-production"]])
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "docker_pool_plan_changed")
        self.assertEqual(result["plan_digest"], "b" * 16)
        self.assertEqual(result["hosted_targets"], ["shop/production"])

    @patch("sandbox.core._remote.ssh_run")
    def test_plan_reports_hosted_targets_and_no_restart_alternative(self, ssh_run):
        ssh_run.return_value = _completed(stdout=json.dumps({
            "ok": True, "status": "planned", "requires_confirm": True,
            "network_count": 1, "running_container_count": 3,
            "restart_policy_none_count": 2, "current_pools_configured": False,
            "current_pool_count": 0, "current_pools_digest": "sha256:" + "a" * 64,
            "desired_pools": list(_remote.REMOTE_DOCKER_ADDRESS_POOLS),
            "subnet_capacity": 1, "subnet_capacity_total": 1,
            "subnet_capacity_allocated": 0, "subnet_capacity_status": "complete",
            "restart_required": True, "route_overlap_count": 0, "apply_safe": True,
            "other_running_containers": 2, "plan_digest": "c" * 16,
        }))
        result = _remote.remote_docker_pool(
            {"ssh": "t"}, hosted=[["b/x", "sandbox-host-b-x"], ["a/y", "sandbox-host-a-y"]],
            remote_name="vps")
        self.assertEqual(result["hosted_targets"], ["a/y", "b/x"])
        self.assertEqual(result["plan_digest"], "c" * 16)
        self.assertEqual(result["other_running_containers"], 2)
        self.assertIn("./sb remote network-range propose vps", result["no_restart_alternative"])

    _PLANNED = {
        "ok": True, "status": "planned", "requires_confirm": True,
        "network_count": 1, "running_container_count": 3,
        "restart_policy_none_count": 2, "current_pools_configured": False,
        "current_pool_count": 0, "current_pools_digest": "sha256:" + "a" * 64,
        "desired_pools": list(_remote.REMOTE_DOCKER_ADDRESS_POOLS),
        "subnet_capacity": 1, "subnet_capacity_total": 1,
        "subnet_capacity_allocated": 0, "subnet_capacity_status": "complete",
        "restart_required": True, "route_overlap_count": 0, "apply_safe": True,
        "other_running_containers": 2, "plan_digest": "c" * 16,
    }

    @patch("sandbox.core._remote.ssh_run")
    def test_required_plan_evidence_rejects_null_and_invalid_values(self, ssh_run):
        for field, value in (("plan_digest", None), ("plan_digest", "C" * 16),
                             ("plan_digest", 7), ("other_running_containers", None),
                             ("other_running_containers", -1),
                             ("other_running_containers", True),
                             ("other_running_containers", "2")):
            with self.subTest(field=field, value=value):
                ssh_run.return_value = _completed(stdout=json.dumps(
                    {**self._PLANNED, field: value}))
                with self.assertRaises(RuntimeError):
                    _remote.remote_docker_pool({"ssh": "t"})

    @patch("sandbox.core._remote.ssh_run")
    def test_plan_changed_receipt_needs_evidence_and_exit_two(self, ssh_run):
        receipt = {"ok": False, "status": "failed", "code": "docker_pool_plan_changed",
                   "message": "m", "plan_digest": "b" * 16, "other_running_containers": 1}
        cases = [(2, {**receipt, "plan_digest": None}),
                 (2, {key: value for key, value in receipt.items()
                      if key != "other_running_containers"}),
                 (3, receipt)]
        for returncode, payload in cases:
            with self.subTest(returncode=returncode, payload=payload):
                ssh_run.return_value = _completed(returncode=returncode,
                                                  stdout=json.dumps(payload))
                with self.assertRaises(RuntimeError):
                    _remote.remote_docker_pool({"ssh": "t"}, confirm=True,
                                               plan_digest="a" * 16)

    @patch("sandbox.core._remote.ssh_run")
    def test_plan_without_digest_evidence_is_incomplete(self, ssh_run):
        ssh_run.return_value = _completed(stdout=json.dumps({
            "ok": True, "status": "planned", "requires_confirm": True,
            "network_count": 1, "running_container_count": 3,
            "restart_policy_none_count": 2, "current_pools_configured": False,
            "current_pool_count": 0, "current_pools_digest": "sha256:" + "a" * 64,
            "desired_pools": list(_remote.REMOTE_DOCKER_ADDRESS_POOLS),
            "subnet_capacity": 1, "subnet_capacity_total": 1,
            "subnet_capacity_allocated": 0, "subnet_capacity_status": "complete",
            "restart_required": True, "route_overlap_count": 0, "apply_safe": True,
        }))
        with self.assertRaisesRegex(RuntimeError, "incomplete evidence"):
            _remote.remote_docker_pool({"ssh": "t"})


class HandlerDigestTests(unittest.TestCase):
    def _call(self, inventory=None, **flags):
        from sandbox.commands import remote as command
        args = SimpleNamespace(name="vps", confirm=False, recover_interrupted=False,
                               expected_running=None, expected_removed=0,
                               recovery_since=None, plan_digest=None)
        for key, value in flags.items():
            setattr(args, key, value)
        out = io.StringIO()
        with patch.object(command.sr, "get_remote", return_value={"ssh": "ops@registered-target"}), \
                patch.object(command, "hosting_state_keys", **(inventory or {"return_value": [
                    "vps/shop/production", "vps/blog/staging", "other/shop/production"]})), \
                patch.object(command.sr, "remote_docker_pool",
                             return_value={"ok": True, "status": "planned"}) as pool, \
                patch("sys.stdout", out):
            try:
                command._cmd_docker_pool(args, True)
                code = 0
            except SystemExit as exc:
                code = exc.code
        return json.loads(out.getvalue()), pool, code

    def test_confirm_without_digest_refuses_before_any_remote_call(self):
        payload, pool, code = self._call(confirm=True)
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "docker_pool_plan_digest_required")
        self.assertIn("--plan-digest", payload["error"]["message"])
        pool.assert_not_called()

    def test_handler_passes_only_this_remotes_hosted_targets(self):
        payload, pool, code = self._call(confirm=True, plan_digest="d" * 16)
        self.assertEqual(code, 0)
        kwargs = pool.call_args.kwargs
        self.assertEqual(kwargs["plan_digest"], "d" * 16)
        self.assertEqual(kwargs["remote_name"], "vps")
        self.assertEqual(kwargs["hosted"], [
            ["blog/staging", "sandbox-host-blog-staging"],
            ["shop/production", "sandbox-host-shop-production"]])

    def test_unreadable_inventory_refuses_plan_and_apply(self):
        from sandbox.core._hosting import HostingError
        broken = {"side_effect": HostingError("invalid managed-host state format")}
        for flags in ({}, {"confirm": True, "plan_digest": "d" * 16}):
            with self.subTest(flags=flags):
                payload, pool, code = self._call(inventory=broken, **flags)
                self.assertEqual(code, 1)
                self.assertEqual(payload["error"]["code"], "docker_pool_unavailable")
                self.assertIn("hosting inventory", payload["error"]["message"])
                pool.assert_not_called()

    def test_recovery_bypasses_unreadable_inventory(self):
        from sandbox.core._hosting import HostingError
        payload, pool, code = self._call(
            inventory={"side_effect": HostingError("bad")}, confirm=True,
            recover_interrupted=True, expected_running=2)
        self.assertEqual(code, 0)
        self.assertEqual(pool.call_args.kwargs["hosted"], [])

    def test_recover_interrupted_confirm_needs_no_digest(self):
        payload, pool, code = self._call(confirm=True, recover_interrupted=True,
                                         expected_running=2)
        self.assertEqual(code, 0)
        pool.assert_called_once()

    def test_cli_refuses_confirm_without_digest_before_remote_lookup(self):
        from tests.test_cli import run_sb
        missing = "missing-remote-for-pool-digest-test"
        refused = run_sb("remote", "docker-pool", missing, "--confirm", "--json")
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(json.loads(refused.stdout)["error"]["code"],
                         "docker_pool_plan_digest_required")
        parsed = run_sb("remote", "docker-pool", missing, "--confirm",
                        "--plan-digest", "e" * 16, "--json")
        self.assertNotIn("unrecognized arguments", parsed.stderr)
        self.assertIn("no remote named", parsed.stderr + parsed.stdout)


if __name__ == "__main__":
    unittest.main()
