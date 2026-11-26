"""Regression tests for storage attribution, cleanup routine healing, and panel cleanup API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sandbox.core._remote as remote_core
from sandbox.commands import ui_dash
from sandbox.hosting import destroy
from sandbox.remote_runtime import host_inventory, releases
from sandbox.resources import reclaim
from sandbox.resources.cleanup_routine import units as routine_units
from sandbox.resources.remote import LocalProbeAdapter, _program
from sandbox.workspaces.repository import WorkspaceRepository
from tests.subprocess_support import run_test_process


class OwnershipProjectionIndexingTests(unittest.TestCase):
    def test_projection_reads_job_index_once_and_counts_active_jobs_and_leases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = WorkspaceRepository(
                root / "runtime" / "workspaces" / "index.sqlite3",
                root / "runtime" / "jobs" / "workspaces",
            )
            ws1 = repo.register("project:alpha", "ws-one")
            ws2 = repo.register("project:beta", "ws-two")
            raw_index = {
                "jobs": [
                    {
                        "job_id": "j1",
                        "workspace_id": ws1.workspace_id,
                        "lifecycle": "running",
                        "workspace_lease_active": True,
                    },
                    {
                        "job_id": "j2",
                        "project_identity": "project:beta",
                        "workspace_label": "ws-two",
                        "lifecycle": "accepted",
                        "workspace_lease_active": False,
                    },
                    {
                        "job_id": "j3",
                        "workspace_id": ws1.workspace_id,
                        "lifecycle": "completed",
                        "workspace_lease_active": False,
                    },
                ],
                "active_workspaces": [],
            }
            with mock.patch.object(
                repo, "_read_raw_job_index", return_value=raw_index,
            ) as reader:
                projection = repo.ownership_projection()
            self.assertEqual(reader.call_count, 1)
            by_id = {row["workspace_id"]: row for row in projection["records"]}
            self.assertEqual(by_id[ws1.workspace_id]["active_references"]["jobs"], 1)
            self.assertEqual(by_id[ws1.workspace_id]["active_references"]["leases"], 1)
            self.assertEqual(by_id[ws2.workspace_id]["active_references"]["jobs"], 1)
            self.assertEqual(by_id[ws2.workspace_id]["active_references"]["leases"], 0)


class ProbeResilienceFixesTests(unittest.TestCase):
    def test_local_probe_timeout_decodes_bytes_stdout_envelope(self):
        envelope = '{"ok": true, "capacity": {"used_bytes": 42}}\n'
        timeout_exc = subprocess.TimeoutExpired(
            cmd=["python3"],
            timeout=1,
            output=envelope.encode("utf-8"),
            stderr=b"slow scan",
        )
        with mock.patch("sandbox.resources.remote.subprocess.run", side_effect=timeout_exc):
            result = LocalProbeAdapter()._run({"action": "observe", "budget_seconds": 1}, 1)
        self.assertEqual(result.returncode, 124)
        self.assertIn('"used_bytes": 42', result.stdout)
        self.assertIn("slow scan", result.stderr)

    def test_walk_rows_caps_managed_keep_depth_to_two_levels(self):
        source = _program({
            "action": "observe",
            "thorough": False,
            "budget_seconds": 5,
            "managed_host": True,
            "remote_name": "local",
        })
        namespace = {"__name__": "__probe_test__"}
        exec(compile(source, "<probe-test>", "exec"), namespace)
        namespace["DIRECTORY_MIN_BYTES"] = 1024 * 1024
        sample = (
            "1\t/home/u/sandbox\n"
            "1\t/home/u/sandbox/deploy-src\n"
            "1\t/home/u/sandbox/deploy-src/ws-a\n"
            "1\t/home/u/sandbox/deploy-src/ws-a/node_modules\n"
            "1\t/home/u/sandbox/deploy-src/ws-a/node_modules/pkg\n"
            "2048\t/home/u/sandbox/deploy-src/ws-a/big-dir\n"
        )
        rows, complete = namespace["walk_rows"](
            ["printf", sample], 5, 1024, ("/home/u/sandbox",),
        )
        paths = {path for _size, path in rows}
        self.assertTrue(complete)
        self.assertIn("/home/u/sandbox", paths)
        self.assertIn("/home/u/sandbox/deploy-src", paths)
        self.assertIn("/home/u/sandbox/deploy-src/ws-a", paths)
        self.assertIn("/home/u/sandbox/deploy-src/ws-a/big-dir", paths)
        self.assertNotIn("/home/u/sandbox/deploy-src/ws-a/node_modules", paths)
        self.assertNotIn("/home/u/sandbox/deploy-src/ws-a/node_modules/pkg", paths)

    def test_materialize_lock_matches_scratch_pattern(self):
        self.assertTrue(
            any(
                pattern.match(".sandbox-materialize-0123456789abcdef.lock")
                for pattern in reclaim.SCRATCH_PATTERNS
            )
        )


class RoutineUnitStabilityAndHealingTests(unittest.TestCase):
    def test_stable_sb_path_prefers_sb_src_symlink_over_release_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "sandbox"
            rel_sb = home / "releases" / "abcdef123456" / "sb"
            rel_sb.parent.mkdir(parents=True)
            rel_sb.write_text("#!/bin/sh\n")
            rel_sb.chmod(0o755)
            stable = home / "sb-src"
            stable.symlink_to(rel_sb.parent)
            resolved = routine_units.stable_sb_path(str(rel_sb), str(home))
            self.assertEqual(resolved, str(stable / "sb"))

    def test_service_health_reports_unhealthy_when_exec_binary_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o700)
            unit_dir = Path(tmp) / ".config" / "systemd" / "user"
            unit_dir.mkdir(parents=True)
            (unit_dir / routine_units.SERVICE).write_text(
                "[Service]\nExecStart=/nonexistent/releases/dead/sb resources routine --routine-run --json\n"
            )
            with mock.patch.dict(os.environ, {"HOME": tmp}), \
                    mock.patch.object(routine_units.shutil, "which", return_value="/usr/bin/systemctl"), \
                    mock.patch.object(routine_units, "_run", return_value=(0, "ActiveState=failed\nResult=exit-code\n")):
                health = routine_units.service_health()
            self.assertFalse(health["healthy"])
            self.assertFalse(health["exec_exists"])
            self.assertEqual(health["active_state"], "failed")
            self.assertEqual(health["result"], "exit-code")

    def test_release_activate_heals_pinned_routine_service_execstart(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "sandbox"
            rev = "a" * 24
            rel_dir = home / "releases" / rev
            rel_dir.mkdir(parents=True)
            os.chmod(home / "releases", 0o700)
            os.chmod(rel_dir, 0o700)
            (rel_dir / "sb").write_text("#!/bin/sh\n")
            (rel_dir / "sb").chmod(0o755)
            marker = rel_dir / ".release.json"
            marker.write_text(json.dumps({
                "schema_version": 1, "name": rev, "state": "complete",
                "runtime_revision": rev, "built_at": 1,
            }))
            os.chmod(marker, 0o600)

            unit_dir = tmp_path / ".config" / "systemd" / "user"
            unit_dir.mkdir(parents=True)
            service_path = unit_dir / "sandbox-cleanup-routine.service"
            old_exec = f"{home}/releases/67af48fa6e5ba1a5742d6e35/sb"
            service_path.write_text(
                f"[Service]\nType=oneshot\nExecStart={old_exec} resources routine --routine-run --json\n"
            )

            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            log_file = tmp_path / "systemctl.log"
            systemctl = fake_bin / "systemctl"
            systemctl.write_text(f"#!/bin/sh\necho \"$@\" >> {log_file}\nexit 0\n")
            systemctl.chmod(0o755)

            env = {
                **os.environ,
                "HOME": tmp,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            }
            proc = subprocess.run(
                [sys.executable, "-c", releases.PROGRAM, str(home), "activate", rev,
                 str(unit_dir / "sandbox-mcp-remote.service"),
                 str(home / "runtime" / "owner-origin.json")],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            updated = service_path.read_text()
            self.assertIn(f"ExecStart={home}/sb-src/sb resources routine --routine-run --json", updated)
            self.assertNotIn("67af48fa6e5ba1a5742d6e35", updated)
            self.assertIn("daemon-reload", log_file.read_text())


class WebCleanupEndpointAndInventoryTests(unittest.TestCase):
    def test_cleanup_request_allowed_requires_panel_cleanup_header_and_same_origin(self):
        base_headers = {
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
            "Content-Type": "application/json",
            "X-Sandbox-Panel": "cleanup",
            "Sec-Fetch-Site": "same-origin",
            "Content-Length": "42",
        }
        self.assertTrue(ui_dash._cleanup_request_allowed("127.0.0.1", "127.0.0.1", base_headers))
        self.assertFalse(
            ui_dash._cleanup_request_allowed(
                "127.0.0.1", "127.0.0.1", {**base_headers, "X-Sandbox-Panel": "share"},
            )
        )
        self.assertFalse(
            ui_dash._cleanup_request_allowed(
                "127.0.0.1", "127.0.0.1", {**base_headers, "Origin": "https://evil.example"},
            )
        )

    def test_web_remote_cleanup_plan_and_confirm_gated_apply(self):
        plan_id = "a" * 32
        fake_svc = mock.Mock()
        fake_svc.plan.return_value = {
            "ok": True,
            "data": {
                "plan_id": plan_id,
                "tier": "safe",
                "expires_at": "2026-11-26T20:00:00Z",
                "estimated_reclaimable_bytes": 1024 * 1024 * 500,
                "candidates": [
                    {
                        "display_name": "demo-workspace-1",
                        "kind": "worktree",
                        "class": "WORKTREE_IDLE",
                        "tier": "safe",
                        "reason": "finished_job_idle",
                        "bytes": 1024 * 1024 * 500,
                    },
                ],
                "skipped": [{"reason": "active_job"}],
                "inventory_status": "complete",
                "deployment_inventory_complete": True,
                "engine_complete": True,
                "unmeasured_count": 0,
                "truncated": False,
            },
        }
        fake_svc.cleanup.return_value = {
            "ok": True,
            "status": "completed",
            "data": {
                "run_id": "b" * 32,
                "plan_id": plan_id,
                "tier": "safe",
                "observed_reclaimed_bytes": 1024 * 1024 * 500,
                "planned_bytes": 1024 * 1024 * 500,
                "outcomes": [{"status": "removed"}],
                "budget_exhausted": False,
            },
        }

        with mock.patch.object(ui_dash, "_hosts_control_service", return_value=True), \
                mock.patch.object(remote_core, "get_remote", return_value=None), \
                mock.patch("sandbox.resources.context.reclaim_service", return_value=fake_svc) as factory, \
                mock.patch.object(host_inventory, "invalidate_storage_cache") as invalidate:
            code, plan_body = ui_dash._web_remote_cleanup(
                {"action": "plan", "remote": "this-server", "tier": "safe"}
            )
            self.assertEqual(code, 200)
            self.assertTrue(plan_body["ok"])
            self.assertEqual(plan_body["plan_id"], plan_id)
            self.assertEqual(plan_body["candidate_count"], 1)
            self.assertEqual(plan_body["skipped_reasons"], {"active_job": 1})
            factory.assert_called_with(None)

            refused_code, refused = ui_dash._web_remote_cleanup({
                "action": "apply", "remote": "this-server", "tier": "safe",
                "plan_id": plan_id, "confirm": False,
            })
            self.assertEqual(refused_code, 400)
            self.assertEqual(refused["code"], "confirmation_required")
            fake_svc.cleanup.assert_not_called()

            apply_code, apply_body = ui_dash._web_remote_cleanup({
                "action": "apply", "remote": "this-server", "tier": "safe",
                "plan_id": plan_id, "confirm": True,
            })
            self.assertEqual(apply_code, 200)
            self.assertTrue(apply_body["ok"])
            self.assertEqual(apply_body["removed_count"], 1)
            self.assertEqual(apply_body["observed_reclaimed_bytes"], 1024 * 1024 * 500)
            fake_svc.cleanup.assert_called_once_with(
                plan_id=plan_id, tier="safe", confirm=True, trigger="web_panel", budget_seconds=300,
            )
            invalidate.assert_called_once()

    def test_invalidate_storage_cache_clears_cached_probe_slot(self):
        host_inventory._record_slow_probe(LocalProbeAdapter, {"ok": True})
        self.assertIsNotNone(host_inventory._slow_probes[LocalProbeAdapter]["value"])
        host_inventory.invalidate_storage_cache()
        self.assertIsNone(host_inventory._slow_probes[LocalProbeAdapter]["value"])


class HostingDestroySourceCleanupTests(unittest.TestCase):
    def _run_teardown(self, *, remove_source: bool, sibling_env: bool = False,
                      working_dir_label: str = ""):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "bin" / "docker"
            fake.parent.mkdir()
            label_line = working_dir_label.replace("'", "'\"'\"'")
            fake.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  ps)\n"
                "    case \"$*\" in\n"
                f"      *working_dir*) printf '%s\\n' '{label_line}' ;;\n"
                "    esac\n"
                "    ;;\n"
                "esac\n"
                "exit 0\n"
            )
            fake.chmod(0o755)
            paths = destroy._paths(tmp, "site", "sandbox")
            Path(paths["runtime"]).mkdir(parents=True)
            if sibling_env:
                (Path(paths["project_runtime"]) / "production").mkdir(parents=True)
            Path(paths["source"]).mkdir(parents=True)
            (Path(paths["source"]) / "index.php").write_text("<?php\n")

            cmd = destroy._remote_teardown_command(
                "sandbox-host-site-sandbox", paths, remove_source=remove_source,
            )
            result = run_test_process(
                ["bash", "-c", cmd],
                env={"PATH": f"{fake.parent}:/usr/bin:/bin"},
                capture_output=True, text=True,
            )
            return (
                result,
                Path(paths["runtime"]).exists(),
                Path(paths["source"]).exists(),
                paths["source"],
            )

    def test_removes_deploy_src_when_last_environment_and_no_container_uses_it(self):
        result, runtime_exists, source_exists, _ = self._run_teardown(remove_source=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed", result.stdout)
        self.assertFalse(runtime_exists)
        self.assertFalse(source_exists)

    def test_preserves_deploy_src_when_sibling_runtime_environment_exists_on_host(self):
        result, runtime_exists, source_exists, _ = self._run_teardown(
            remove_source=True, sibling_env=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(runtime_exists)
        self.assertTrue(source_exists)

    def test_preserves_deploy_src_when_another_container_uses_its_working_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = destroy._paths(tmp, "site", "sandbox")
            fake = root / "bin" / "docker"
            fake.parent.mkdir()
            fake.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  ps)\n"
                "    case \"$*\" in\n"
                f"      *working_dir*) printf '%s/sub\\n' '{paths['source']}' ;;\n"
                "    esac\n"
                "    ;;\n"
                "esac\n"
                "exit 0\n"
            )
            fake.chmod(0o755)
            Path(paths["runtime"]).mkdir(parents=True)
            Path(paths["source"]).mkdir(parents=True)
            cmd = destroy._remote_teardown_command(
                "sandbox-host-site-sandbox", paths, remove_source=True,
            )
            result = run_test_process(
                ["bash", "-c", cmd],
                env={"PATH": f"{fake.parent}:/usr/bin:/bin"},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(Path(paths["runtime"]).exists())
            self.assertTrue(Path(paths["source"]).exists())

    def test_preserves_deploy_src_when_remove_source_is_false(self):
        result, runtime_exists, source_exists, _ = self._run_teardown(remove_source=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(runtime_exists)
        self.assertTrue(source_exists)


if __name__ == "__main__":
    unittest.main()
