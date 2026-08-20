"""MCP server smoke test (spec 001 Stage D — server.py split into app + tools/).

Runs in the MCP server's own venv (FastMCP lives there, not in .cli-venv), so
it's a subprocess test that skips cleanly if the venv isn't built. Verifies that
importing the thin server.py registers every grouped tool/prompt on the shared
`mcp` — the exact failure mode (decorators dropped during the split) that the
code review caught once already.
"""
import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = ROOT / "mcp" / "wp-server"
VENV_PY = MCP_DIR / ".venv" / "bin" / "python"

_PROBE = """
import os, asyncio, json, subprocess, tempfile
os.environ.setdefault("SANDBOX_ROOT", os.getcwd() + "/../..")
_probe_project_owner = tempfile.TemporaryDirectory(
    prefix=".sandbox-mcp-probe-", dir=os.environ["SANDBOX_ROOT"],
)
_probe_project = _probe_project_owner.name
import server                      # thin entry: imports app + all tools.* groups
from app import SANDBOX_INSTRUCTIONS, mcp
import inspect
from tools.context import focus_get
from tools.fs import fs_read, tail_log
async def go():
    tools = await mcp.list_tools()
    return len(tools), len(await mcp.list_prompts()), {tool.name for tool in tools}
t, p, names = asyncio.run(go())
tools = asyncio.run(mcp.list_tools())
print("SCHEMA", json.dumps([
    [tool.name, sorted(tool.inputSchema.get("required", [])), tool.outputSchema]
    for tool in tools
], sort_keys=True))
print("TOOLS", t)
print("PROMPTS", p)
print("INSTRUCTIONS", len(SANDBOX_INSTRUCTIONS))
print("FOCUS_DEFAULT_INCLUDE", inspect.signature(focus_get).parameters["include_claude_md"].default)
print("OUTPUT_DEFAULTS", inspect.signature(tail_log).parameters["lines"].default,
      inspect.signature(fs_read).parameters["max_bytes"].default)
print("HERMES", int({
    'hermes_status', 'hermes_run', 'hermes_job_status', 'hermes_job_kill',
    'hermes_cron_list', 'hermes_cron_validate', 'hermes_cron_create',
    'hermes_cron_route', 'hermes_cron_run', 'hermes_cron_output', 'hermes_health',
    'hermes_worktree_list', 'hermes_worktree_inspect', 'hermes_worktree_preserve',
    'hermes_gateway_converge',
    'hermes_cron_catalog', 'hermes_cron_reconcile', 'hermes_cron_verify',
    'hermes_repo_sync', 'hermes_authorization_sync', 'hermes_authorization_list', 'hermes_authorization_show',
    'hermes_authorization_request', 'hermes_authorization_approve',
} <= names))
import tools.hermes as hermes
calls = []
original_run_sb = hermes._run_sb
hermes._run_sb = lambda args, timeout: calls.append([args, timeout]) or {"ok": True}
hermes.hermes_status("remote")
hermes.hermes_run("remote", "repo", "non-secret", worktree=False, async_=True, timeout=100)
hermes.hermes_run("remote", "repo", "non-secret", worktree=True, async_=False, timeout=100)
hermes.hermes_job_status("remote", "0123456789abcdef", offset=7)
hermes.hermes_job_kill("remote", "0123456789abcdef")
hermes.hermes_cron_list("remote")
hermes.hermes_cron_validate("remote")
hermes.hermes_cron_create("remote", "every 4h", "bounded work", profile="terra", confirm=True)
hermes.hermes_cron_route("remote", "3359664aaf91", profile="terra", confirm=True)
hermes.hermes_cron_run("remote", "3359664aaf91", confirm=True)
hermes.hermes_cron_output("remote", "3359664aaf91", lines=50)
hermes.hermes_authorization_sync("remote")
hermes.hermes_authorization_list("remote")
hermes.hermes_authorization_show("remote", "0123456789abcdef")
hermes.hermes_authorization_request("remote", "lenzora-todo-task", "preview-overlay", "https://replay.example.test", "bounded review")
hermes.hermes_authorization_approve("remote", "0123456789abcdef", confirm=True)
hermes.hermes_health("remote")
hermes.hermes_worktree_list("remote")
hermes.hermes_repo_sync("remote", "sandbox", confirm=True)
hermes.hermes_gateway_converge("remote", confirm=True)
hermes.hermes_cron_catalog("remote")
hermes.hermes_cron_reconcile("remote", confirm=True, force_replace=True)
hermes.hermes_cron_verify("remote", "3359664aaf91", timeout=60, confirm=True)
hermes.hermes_worktree_inspect("remote", "sandbox-approved-spec-task")
hermes.hermes_worktree_preserve("remote", "sandbox-approved-spec-task", confirm=True)
print("HERMES_CALLS", json.dumps(calls))
hermes._run_sb = original_run_sb
def timed_out(*_args, **_kwargs):
    raise subprocess.TimeoutExpired("sb", 30)
server.subprocess.run = timed_out
hermes.HERMES_SERVICE = server._HermesCommandAdapter()
print("HERMES_TIMEOUT", hermes._run_sb(["hermes", "status"], 30)["error"])
import tools.instances as instances
instance_calls = []
class InstanceResult:
    returncode = 0
    stdout = '{"instance":"demo","root":"/tmp/hermes-worktree","status":"ready"}\\n'
    stderr = ''
instances.subprocess.run = lambda cmd, **kwargs: instance_calls.append([cmd, kwargs]) or InstanceResult()
instance = instances.ensure_instance("/tmp/hermes-worktree")
print("HERMES_INSTANCE", json.dumps([instance, instance_calls]))
import tools.wp as wp_tools
import tools.data as data_tools
import app as mcp_app
wp_tools.SANDBOX_ROOT = mcp_app.SANDBOX_ROOT
import sys as _sys
_mcp_root = str(mcp_app.SANDBOX_ROOT)
_sys.path = [entry for entry in _sys.path if entry != _mcp_root]
capability_import = mcp_app._require_project_capability(_probe_project, None, "wordpress.cli")
print("CAPABILITY_IMPORT", json.dumps(capability_import))
class _WrapperResult:
    returncode = 0
    stdout = "diagnostic\\n{\\"ok\\":true}\\n"
    stderr = ""
_original_subprocess_run = mcp_app.subprocess.run
mcp_app.subprocess.run = lambda *_args, **_kwargs: _WrapperResult()
print("WRAPPER_RESULT", json.dumps(mcp_app._run_sandbox_json(["sb"], 3)))
mcp_app.subprocess.run = lambda *_args, **_kwargs: type("ParseFailure", (), {
    "returncode": 2, "stdout": "diagnostic only\\n", "stderr": "bad invocation\\n",
})()
print("WRAPPER_PARSE_FAILURE", json.dumps(mcp_app._run_sandbox_json(["sb"], 3)))
mcp_app.subprocess.run = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    subprocess.TimeoutExpired(cmd="sb", timeout=3)
)
print("WRAPPER_TIMEOUT", json.dumps(mcp_app._run_sandbox_json(["sb"], 3)))
mcp_app.subprocess.run = _original_subprocess_run
invalid_mode = wp_tools.run_tests(_probe_project, mode="not-a-mode")
print("TEST_MODE_INVALID", json.dumps(invalid_mode))
wp_tools._require_project_capability = lambda *_args: None
wp_tools._project_instance = lambda *_args: ("fixture", None)
wp_tools._managed_execution_unavailable = lambda *_args, **_kwargs: None
class TestRunResult:
    returncode = 0
    stdout = "  mode:       unit\\nOK (1 test, 1 assertion)\\n"
    stderr = ""
test_calls = []
wp_tools.subprocess.run = lambda cmd, **kwargs: test_calls.append([cmd, kwargs]) or TestRunResult()
print("TEST_MODE_FORWARD", json.dumps(wp_tools.run_tests(
    _probe_project, mode="unit", phpunit_args="--filter Example", local=True)))
print("TEST_MODE_CALL", json.dumps(test_calls))
rejection = {"ok": False, "error": "blocked before side effects"}
wp_side_effects = []
wp_tools._require_project_capability = lambda *_args: rejection
wp_tools._wpcli = lambda *_args, **_kwargs: wp_side_effects.append("wpcli")
data_tools._require_project_capability = lambda *_args: rejection
data_tools._compose = lambda *_args, **_kwargs: wp_side_effects.append("compose")
print("CAPABILITY_REJECTION", json.dumps([
    wp_tools.wp_cli("core version", project_dir=_probe_project),
    data_tools.db_query("SELECT 1", project_dir=_probe_project),
    wp_side_effects,
]))
data_tools._require_project_capability = lambda *_args: None
data_tools._project_instance = lambda *_args: ("fixture", None)
snapshot_calls = []
data_tools._run_sandbox_json = lambda cmd, timeout: snapshot_calls.append([cmd, timeout]) or {
    "timed_out": False, "returncode": 0, "stdout": "saved\\n", "stderr": "", "payload": None,
}
print("MCP_SNAPSHOT", json.dumps([
    data_tools.snapshot("before", db_only=True, force=True, project_dir=_probe_project),
    snapshot_calls,
]))
data_tools._require_project_capability = lambda *_args: rejection
print("MCP_SNAPSHOT_CAPABILITY_REJECTION", json.dumps(
    data_tools.snapshot("before", db_only=True, force=True, project_dir=_probe_project)
))
"""


@unittest.skipUnless(VENV_PY.exists(), "MCP venv not built (run: ./sb mcp-install)")
class TestMcpServerSplit(unittest.TestCase):
    def test_project_instance_uses_persisted_selector_after_env_removed(self):
        """A separately launched MCP process sees the same project registry."""
        with tempfile.TemporaryDirectory(prefix="sb-mcp-home-") as td:
            base = Path(td)
            home = base / "home"
            selected = base / "selected"
            project = home / "project"
            (project / ".git").mkdir(parents=True)
            (project / "sandbox.config.json").write_text("{}\n")
            hint = home / ".config" / "sandbox" / "home"
            hint.parent.mkdir(parents=True)
            hint.write_text(str(selected) + "\n")

            env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(ROOT)}
            env.pop("SANDBOX_HOME", None)
            env.pop("SANDBOX_RUNTIME", None)
            register = (
                "import sandbox_core; sandbox_core.registry_put(%r, instance=%r)"
                % (str(project), "mcp-instance")
            )
            created = subprocess.run(
                [sys.executable, "-c", register], cwd=str(ROOT), env=env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            probe = (
                "import app; print(app._project_instance(%r)[0])"
                % str(project)
            )
            resolved = subprocess.run(
                [str(VENV_PY), "-c", probe], cwd=str(MCP_DIR),
                capture_output=True, text=True, timeout=90,
                env={**env, "SANDBOX_ROOT": str(ROOT)},
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            self.assertEqual(resolved.stdout.strip(), "mcp-instance")

    def test_mcp_relative_selector_falls_back_to_cwd_independent_default(self):
        """MCP never interprets a relative bootstrap hint against its CWD."""
        with tempfile.TemporaryDirectory(prefix="sb-mcp-relative-") as td:
            base = Path(td)
            home = base / "home"
            cwd_a = base / "cwd-a"
            cwd_b = base / "cwd-b"
            cwd_a.mkdir(parents=True)
            cwd_b.mkdir(parents=True)
            hint = home / ".config" / "sandbox" / "home"
            hint.parent.mkdir(parents=True)
            hint.write_text("relative-state\n")
            env = {
                **os.environ,
                "HOME": str(home),
                "PYTHONPATH": str(MCP_DIR) + os.pathsep + str(ROOT),
                "SANDBOX_ROOT": str(ROOT),
            }
            env.pop("SANDBOX_HOME", None)
            env.pop("SANDBOX_RUNTIME", None)
            probe = "import app; print(app._sandbox_base())"
            for cwd in (cwd_a, cwd_b):
                with self.subTest(cwd=cwd.name):
                    resolved = subprocess.run(
                        [str(VENV_PY), "-c", probe], cwd=str(cwd),
                        capture_output=True, text=True, timeout=90, env=env,
                    )
                    self.assertEqual(resolved.returncode, 0, resolved.stderr)
                    self.assertEqual(resolved.stdout.strip(), str((home / "sandbox").resolve()))

    def test_wordpress_remote_tests_bind_the_staged_cli_path(self):
        probe = """
from sandbox.core import _remote
from tools import wp
print(wp._remote_job_transport().remote_sb_path is _remote.remote_sb_path)
"""
        result = subprocess.run(
            [str(VENV_PY), "-c", probe], cwd=str(MCP_DIR),
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "SANDBOX_ROOT": str(ROOT), "SANDBOX_MCP_GROUPS": "all",
                 "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "True")

    def test_public_tool_schema_snapshot(self):
        r = subprocess.run(
            [str(VENV_PY), "-c", _PROBE], cwd=str(MCP_DIR),
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "SANDBOX_ROOT": str(ROOT), "SANDBOX_MCP_GROUPS": "all"})
        self.assertEqual(r.returncode, 0, f"server import failed:\n{r.stderr}")
        schema_line = next(line for line in r.stdout.splitlines() if line.startswith("SCHEMA "))
        actual = __import__("json").loads(schema_line.removeprefix("SCHEMA "))
        expected = (
            ("ensure_instance", "project_dir"), ("destroy_instance", "project_dir"),
            ("recreate_instance", "project_dir"), ("setup_domains", ""),
            ("secure_instance", "project_dir"), ("apply_config", "project_dir"),
            ("domain_status", "project_dir"), ("domain_plan", "project_dir"),
            ("domain_apply", "project_dir"), ("domain_cleanup", "project_dir"),
            ("domain_support", ""), ("ingress_status", ""),
            ("ingress_support", ""),
            ("ingress_plan", ""), ("ingress_cleanup", "project_dir"),
            ("ingress_reconcile", "project_dir"),
            ("ingress_reconsider", "consent_identity"), ("ingress_apply", ""),
            ("instance_status", "project_dir"), ("instance_logs", "project_dir"),
            ("instance_exec", "command,project_dir"),
            ("native_support", ""), ("native_preflight", ""),
            ("native_install_plan", ""),
            ("job_start", "command,project_dir"), ("job_matrix", "command,project_dir,workspaces"), ("job_status", "job_id"),
            ("job_list", ""), ("job_output", "job_id"),
            ("job_follow", "job_id"), ("job_metrics", "job_id"), ("job_reconcile", ""), ("job_retention", ""), ("job_cancel", "job_id"),
            ("job_artifacts", "job_id"), ("job_artifact_get", "artifact_id,job_id"),
            ("job_retry", "job_id"), ("job_cleanup", "job_id"),
            ("workspace_create", ""), ("workspace_list", ""),
            ("workspace_status", ""), ("workspace_reset", ""),
            ("workspace_destroy", ""), ("workspace_migration_plan", ""),
            ("workspace_migration_apply", "plan_id"),
            ("wp_cli", "command,project_dir"), ("wp_exec", "command,project_dir"),
            ("wp_rest", "method,path,project_dir"), ("run_tests", "project_dir"),
            ("wp_cli_async", "command,project_dir"), ("wp_cli_job", "job_id,project_dir"),
            ("wp_cli_job_kill", "job_id,project_dir"), ("http_fetch", "url"),
            ("pixelmatch_diff", "build,reference"), ("visit", "url"),
            ("db_query", "project_dir,sql"), ("import_content", "project_dir,seed_file"),
            ("snapshot", "name,project_dir"),
            ("wp_reset", "project_dir"), ("tail_log", "project_dir"),
            ("fs_read", "path,project_dir"), ("fs_write", "content,path,project_dir"),
            ("fs_list", "project_dir"), ("mail_list", "project_dir"),
            ("mail_get", "message_id,project_dir"), ("focus_get", "project_dir"),
            ("activate_plugin", "project_dir,slug"), ("deactivate_plugin", "project_dir,slug"),
            ("load_context", ""), ("load_workflow", "name"), ("load_skill", "name"),
            ("cache_info", ""), ("cache_clear", ""),
            ("resource_status", ""), ("resource_cleanup_plan", "scope"),
            ("resource_cleanup_apply", "plan_id"),
            ("feedback_submit", "summary"), ("feedback_list", ""),
            ("wp_eval_live", "code,project_dir"), ("list_skills", "project_dir"),
            ("skill_write", "description,project_dir,title"), ("skill_edit", "project_dir,slug"),
            ("skill_delete", "project_dir,slug"), ("qm_capture", "project_dir"),
            ("xdebug", "project_dir"), ("run_e2e", "project_dir"),
            ("ci_plan", "workflow"), ("ci_run", "project_dir,workflow"),
            ("async_job_status", "job_id"), ("async_job_kill", "job_id"),
            ("secret_source_info", "project_dir,source"),
            ("secret_inspect", "project_dir,source"),
            ("secret_validate", "key,profile,project_dir,source"),
            ("secret_use_profile", "profile,project_dir"),
            ("run_plugin_check", "project_dir"), ("remote_deploy", "project_dir,remote"),
            ("hermes_status", "remote"), ("hermes_run", "prompt,remote,repo"),
            ("hermes_job_status", "job_id,remote"), ("hermes_job_kill", "job_id,remote"),
            ("hermes_cron_list", "remote"), ("hermes_cron_validate", "remote"),
            ("hermes_cron_create", "prompt,remote,schedule"), ("hermes_cron_route", "job_id,remote"),
            ("hermes_cron_run", "job_id,remote"), ("hermes_cron_output", "job_id,remote"),
            ("hermes_authorization_sync", "remote"), ("hermes_authorization_list", "remote"),
            ("hermes_authorization_show", "remote,request_id"),
            ("hermes_authorization_request", "job,reason,remote,replay_origin,scope"),
            ("hermes_authorization_approve", "remote,request_id"),
            ("hermes_health", "remote"), ("hermes_worktree_list", "remote"),
            ("hermes_worktree_inspect", "name,remote"), ("hermes_worktree_preserve", "name,remote"),
            ("hermes_repo_sync", "remote,repo"), ("hermes_gateway_converge", "remote"),
            ("hermes_cron_catalog", "remote"), ("hermes_cron_reconcile", "remote"),
            ("hermes_cron_verify", "job_id,remote"), ("recovery_profiles", ""),
            ("recovery_plan", ""), ("recovery_list", ""), ("recovery_verify", "backup_id"),
            ("recovery_create", ""), ("recovery_restore_plan", "backup_id"),
            ("recovery_restore_apply", "backup_id"), ("recovery_schedule_plan", ""),
            ("recovery_retention_plan", ""),
        )
        self.assertEqual(len(actual), 129)
        self.assertEqual([(name, ",".join(required)) for name, required, _response in actual], list(expected))
        self.assertTrue(all(response is None for _name, _required, response in actual), actual)

    def test_tools_and_prompts_register(self):
        r = subprocess.run(
            [str(VENV_PY), "-c", _PROBE], cwd=str(MCP_DIR),
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "SANDBOX_ROOT": str(ROOT), "SANDBOX_MCP_GROUPS": "all"})
        self.assertEqual(r.returncode, 0, f"server import failed:\n{r.stderr}")
        out = dict(
            line.split() for line in r.stdout.split("\n")
            if line.startswith(("TOOLS ", "PROMPTS ", "HERMES ")))
        # The pre-split server had 26 @mcp.tool + 8 @mcp.prompt; require parity.
        self.assertGreaterEqual(int(out["TOOLS"]), 26, r.stdout)
        self.assertGreaterEqual(int(out["PROMPTS"]), 8, r.stdout)
        self.assertEqual(out.get("HERMES"), "1", r.stdout)

        instructions = next(line for line in r.stdout.splitlines()
                            if line.startswith("INSTRUCTIONS "))
        self.assertLessEqual(int(instructions.split()[1]), 7000, r.stdout)
        focus_default = next(line for line in r.stdout.splitlines()
                             if line.startswith("FOCUS_DEFAULT_INCLUDE "))
        self.assertEqual(focus_default, "FOCUS_DEFAULT_INCLUDE False")
        output_defaults = next(line for line in r.stdout.splitlines()
                              if line.startswith("OUTPUT_DEFAULTS "))
        self.assertEqual(output_defaults, "OUTPUT_DEFAULTS 50 100000")

        invalid = next(line for line in r.stdout.splitlines()
                       if line.startswith("TEST_MODE_INVALID "))
        self.assertEqual(__import__("json").loads(invalid.removeprefix("TEST_MODE_INVALID "))["error"],
                         "test mode must be auto, unit, or integration")
        forwarded = next(line for line in r.stdout.splitlines()
                         if line.startswith("TEST_MODE_FORWARD "))
        self.assertEqual(__import__("json").loads(forwarded.removeprefix("TEST_MODE_FORWARD "))["mode"], "unit")
        call = next(line for line in r.stdout.splitlines()
                    if line.startswith("TEST_MODE_CALL "))
        self.assertIn("unit", __import__("json").loads(call.removeprefix("TEST_MODE_CALL "))[0][0])

        wrapper_result = next(line for line in r.stdout.splitlines()
                              if line.startswith("WRAPPER_RESULT "))
        self.assertEqual(__import__("json").loads(wrapper_result.removeprefix("WRAPPER_RESULT "))["payload"], {"ok": True})
        parse_failure = next(line for line in r.stdout.splitlines()
                             if line.startswith("WRAPPER_PARSE_FAILURE "))
        parse = __import__("json").loads(parse_failure.removeprefix("WRAPPER_PARSE_FAILURE "))
        self.assertFalse(parse["timed_out"])
        self.assertEqual(parse["returncode"], 2)
        self.assertIsNone(parse["payload"])
        self.assertEqual(parse["stderr"], "bad invocation\n")
        wrapper_timeout = next(line for line in r.stdout.splitlines()
                               if line.startswith("WRAPPER_TIMEOUT "))
        timeout = __import__("json").loads(wrapper_timeout.removeprefix("WRAPPER_TIMEOUT "))
        self.assertTrue(timeout["timed_out"])
        self.assertIsNone(timeout["returncode"])

        calls_line = next(line for line in r.stdout.splitlines() if line.startswith("HERMES_CALLS "))
        calls = __import__("json").loads(calls_line.removeprefix("HERMES_CALLS "))
        self.assertEqual(calls[0], [["hermes", "status", "--remote", "remote"], 30])
        self.assertIn("--no-worktree", calls[1][0])
        self.assertIn("--async", calls[1][0])
        self.assertNotIn("--async", calls[2][0])
        self.assertEqual(calls[2][1], 130)
        self.assertEqual(calls[3][0][-2:], ["--offset", "7"])
        self.assertEqual(calls[4][0][1:3], ["job", "kill"])
        self.assertEqual(calls[5][0][1:3], ["cron", "list"])
        self.assertEqual(calls[6][0][1:3], ["cron", "validate"])
        self.assertIn("--profile", calls[7][0])
        self.assertIn("--confirm", calls[7][0])
        self.assertEqual(calls[8][0][1:3], ["cron", "route"])
        self.assertEqual(calls[9][0][1:3], ["cron", "run"])
        self.assertEqual(calls[10][0][1:3], ["cron", "output"])
        self.assertEqual(calls[10][0][-2:], ["--lines", "50"])
        self.assertEqual(calls[11][0][1:3], ["authorization", "sync"])
        self.assertEqual(calls[12][0][1:3], ["authorization", "list"])
        self.assertEqual(calls[13][0][1:3], ["authorization", "show"])
        self.assertEqual(calls[14][0][1:3], ["authorization", "request"])
        self.assertIn("--job", calls[14][0])
        self.assertIn("--replay-origin", calls[14][0])
        self.assertEqual(calls[15][0][1:3], ["authorization", "approve"])
        self.assertIn("--confirm", calls[15][0])
        self.assertEqual(calls[16][0][1], "health")
        self.assertEqual(calls[17][0][1:3], ["worktree", "list"])
        self.assertEqual(calls[18][0][1:3], ["repo", "sync"])
        self.assertIn("--confirm", calls[18][0])
        self.assertIn("--confirm", calls[19][0])
        self.assertEqual(calls[20][0][1:3], ["cron", "catalog"])
        self.assertIn("--force-replace", calls[21][0])
        self.assertEqual(calls[22][0][1:3], ["cron", "verify"])
        self.assertEqual(calls[23][0][1:3], ["worktree", "inspect"])
        self.assertEqual(calls[24][0][1:3], ["worktree", "preserve"])
        self.assertIn("--confirm", calls[24][0])
        timeout_line = next(line for line in r.stdout.splitlines() if line.startswith("HERMES_TIMEOUT "))
        self.assertIn("timed out", timeout_line.lower())
        instance_line = next(line for line in r.stdout.splitlines() if line.startswith("HERMES_INSTANCE "))
        instance, instance_calls = __import__("json").loads(instance_line.removeprefix("HERMES_INSTANCE "))
        self.assertTrue(instance["ok"])
        self.assertEqual(instance_calls[0][0][-3:], ["--project-dir", "/tmp/hermes-worktree", "--json"])
        rejection_line = next(line for line in r.stdout.splitlines()
                              if line.startswith("CAPABILITY_REJECTION "))
        wp_result, db_result, side_effects = __import__("json").loads(
            rejection_line.removeprefix("CAPABILITY_REJECTION ")
        )
        self.assertFalse(wp_result["ok"])
        self.assertFalse(db_result["ok"])
        self.assertEqual(side_effects, [])
        snapshot_line = next(line for line in r.stdout.splitlines()
                             if line.startswith("MCP_SNAPSHOT "))
        snapshot, calls = __import__("json").loads(
            snapshot_line.removeprefix("MCP_SNAPSHOT ")
        )
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["instance"], "fixture")
        self.assertEqual(snapshot["snapshot"], "before")
        self.assertEqual(snapshot["mode"], "db-only")
        self.assertTrue(snapshot["forced"])
        self.assertNotIn("saved", snapshot)
        self.assertEqual(calls[0], [
            [str(ROOT / "sb"), "--instance", "fixture", "snapshot", "before", "--db-only", "--force"], 300,
        ])
        capability_line = next(line for line in r.stdout.splitlines()
                               if line.startswith("MCP_SNAPSHOT_CAPABILITY_REJECTION "))
        capability_result = __import__("json").loads(
            capability_line.removeprefix("MCP_SNAPSHOT_CAPABILITY_REJECTION ")
        )
        self.assertEqual(capability_result, {"ok": False, "error": "blocked before side effects"})
        capability_import_line = next(line for line in r.stdout.splitlines()
                                      if line.startswith("CAPABILITY_IMPORT "))
        capability_import = __import__("json").loads(
            capability_import_line.removeprefix("CAPABILITY_IMPORT ")
        )
        if capability_import:
            self.assertNotIn("No module named 'sandbox'", capability_import.get("error", ""))
        wrapper_line = next(line for line in r.stdout.splitlines()
                            if line.startswith("WRAPPER_RESULT "))
        wrapper = __import__("json").loads(wrapper_line.removeprefix("WRAPPER_RESULT "))
        self.assertEqual(wrapper["payload"], {"ok": True})
        self.assertFalse(wrapper["timed_out"])
        timeout_line = next(line for line in r.stdout.splitlines()
                            if line.startswith("WRAPPER_TIMEOUT "))
        self.assertTrue(__import__("json").loads(
            timeout_line.removeprefix("WRAPPER_TIMEOUT "))["timed_out"])


@unittest.skipUnless(VENV_PY.exists(), "MCP venv not built (run: ./sb mcp-install)")
class TestMcpDataBoundaries(unittest.TestCase):
    """Keep reset responses bounded at the MCP tool boundary.

    The child process imports the real MCP tool module, while its CLI call is
    replaced with a local fake. This exercises the public function without
    contacting a runtime or exposing command/path diagnostics.
    """

    def _run_probe(self, stdout: str, stderr: str, returncode: int) -> dict:
        probe = f"""
import json, os
os.environ.setdefault("SANDBOX_ROOT", os.getcwd() + "/../..")
from tools import data as data_tools
data_tools._require_project_capability = lambda *_args: None
data_tools._project_instance = lambda *_args: ("fixture", None)
class Result:
    returncode = {returncode!r}
    stdout = {stdout!r}
    stderr = {stderr!r}
calls = []
def fake_run(command, **kwargs):
    calls.append([command, kwargs])
    return Result()
data_tools.subprocess.run = fake_run
result = data_tools.wp_reset(confirm=True, project_dir="/tmp/project")
print("WP_RESET_RESULT", json.dumps({{"result": result, "calls": calls}}, default=str))
"""
        completed = subprocess.run(
            [str(VENV_PY), "-c", probe], cwd=str(MCP_DIR),
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "SANDBOX_ROOT": str(ROOT)},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        line = next(line for line in completed.stdout.splitlines()
                     if line.startswith("WP_RESET_RESULT "))
        return json.loads(line.removeprefix("WP_RESET_RESULT "))

    def test_confirmed_reset_returns_bounded_metadata(self):
        payload = self._run_probe(
            "success diagnostics: /private/fixture/wp db reset --yes",
            "",
            0,
        )
        self.assertEqual(payload["result"], {
            "ok": True,
            "instance": "fixture",
            "operation": "reset",
            "rebaseline": False,
            "confirmed": True,
        })
        self.assertNotIn("output", payload["result"])
        self.assertEqual(payload["calls"][0][0], [
            str(ROOT / "sb"), "--instance", "fixture", "reset", "--yes",
        ])

    def test_reset_failure_maps_diagnostics_to_bounded_error(self):
        sentinel_path = "/private/fixture/wp-db-reset-command"
        payload = self._run_probe(
            f"failed command: {sentinel_path} wp db reset --yes",
            f"traceback includes {sentinel_path}",
            1,
        )
        self.assertEqual(payload["result"], {"ok": False, "error": "reset_failed"})
        self.assertNotIn("output", payload["result"])
        self.assertNotIn(sentinel_path, json.dumps(payload["result"]))
        self.assertNotIn("wp db reset", json.dumps(payload["result"]))


@unittest.skipUnless(VENV_PY.exists(), "MCP venv not built (run: ./sb mcp-install)")
class TestMcpPhpExtensionBoundaries(unittest.TestCase):
    """Exercise the runtime MCP projection in an isolated interpreter.

    Loading the runtime group imports the shared command modules, whose global
    command registry is intentionally single-writer.  Keep these status probes
    in a child process so they cannot collide with the remote-first jobs tests,
    regardless of unittest discovery order.
    """

    def _run_status_probe(self) -> dict:
        probe = r'''
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

root = Path(os.environ["SANDBOX_ROOT"])
dependencies = types.ModuleType("dependencies")
dependencies.ToolDependencies = object
spec = importlib.util.spec_from_file_location(
    "sandbox_test_mcp_runtime", root / "mcp" / "wp-server" / "tools" / "runtime.py")
module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"dependencies": dependencies}):
    spec.loader.exec_module(module)

module._project_instance = lambda _project_dir, _label: ("fixture", None)
module._core = lambda: SimpleNamespace(
    registry_find_instance=lambda _instance: {
        "instance": "fixture", "root": "/project", "kind": "wordpress",
        "label": "default",
    })
# Exercise the MCP transport with the adapter-supplied status payload.  The
# shared projector must close the report before returning it to the caller;
# this is intentionally not a direct adapter invocation.
module._runtime_service = lambda: SimpleNamespace(invoke=lambda _request: SimpleNamespace(
    ok=report["ok"], operation="status", project_kind="wordpress",
    data={"state": "ready" if report["ok"] else "blocked",
          "php_extensions": report}))

digest = "sha256:" + "a" * 64
states = {}
for state in ("ready", "missing", "stale", "discarded"):
    report = {
        "ok": state == "ready",
        "exit_code": 0 if state == "ready" else 1,
        "desired": {
            "profile": "wordpress@1",
            "catalog": {"revision": 1, "digest": digest,
                         "private_path": "/private/catalog"},
            "requirements": [
                {"name": "gd", "state": "enabled", "version": None,
                 "receipt": "private Dockerfile"},
                {"name": "tokenizer", "state": "enabled", "version": None},
            ],
            "resolution_digest": digest,
            "build_digest": digest,
            "context_path": "/private/context",
        },
        "provenance": {
            "state": state,
            "recipe_catalog_digest": digest,
            "parent_digests": {"web": digest, "wpcli": digest,
                                "private": "/private/parent"},
            "recipe_ids": ["php-gd"],
            "context_path": "/private/receipt",
            "receipt_content": "private Dockerfile contents",
            "password": "fixture-password",
        },
        "observed": {
            plane: {
                "state": "ready",
                "php_version": "8.3.0",
                "sapi": plane,
                "extensions": {
                    "gd": {"enabled": True, "version": "2.3.0",
                            "private_path": "/private/ext"},
                    "tokenizer": {"enabled": True, "version": None},
                },
                "issues": [],
                "receipt": "private probe output",
            }
            for plane in ("web", "cli", "exec", "phpunit")
        },
        "readiness": {"state": "ready", "private": "/private/readiness"},
        "staleness": {"state": "fresh", "reason": "all_four_planes_observed",
                       "private": "receipt body"},
        "drift": {"state": "ready", "private": "credentials"},
        "issues": [],
        "receipt": {"path": "/private/raw-receipt", "content": "secret"},
        "private_top_level": "fixture-password",
    }
    with patch("sandbox.core.load_config", return_value={}), \
            patch("sandbox.core.resolve_instances", return_value={"fixture": {
                "php_extensions": {"extensions": {"gd": True}},
            }}), \
            patch("sandbox.core.php_extension_status", return_value=report):
        states[state] = module.instance_status("/project")
print(json.dumps(states, sort_keys=True))
'''
        completed = subprocess.run(
            [str(VENV_PY), "-c", probe], cwd=str(MCP_DIR),
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "SANDBOX_ROOT": str(ROOT),
                 "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip())

    def test_status_states_are_public_bounded_and_fail_closed(self):
        states = self._run_status_probe()
        allowed_extension_top_level = {
            "ok", "exit_code", "desired", "provenance", "observed", "readiness",
            "staleness", "drift", "issues",
        }
        for state, result in states.items():
            with self.subTest(state=state):
                self.assertEqual(set(result), {
                    "ok", "operation", "state", "php_extensions", "exit_code", "mutated",
                })
                self.assertFalse(result["mutated"])
                extension = result["php_extensions"]
                self.assertEqual(set(extension), allowed_extension_top_level)
                self.assertEqual(extension["provenance"]["state"], state)
                self.assertEqual(extension["ok"], state == "ready")
                self.assertEqual(extension["exit_code"], 0 if state == "ready" else 1)
                self.assertEqual(set(extension["provenance"]), {
                    "state", "recipe_catalog_digest", "parent_digests", "recipe_ids",
                })
                self.assertEqual(set(extension["provenance"]["parent_digests"]), {"web", "wpcli"})
                for plane in extension["observed"].values():
                    self.assertEqual(set(plane), {"state", "php_version", "sapi", "extensions", "issues"})
                    for extension_name in ("gd", "tokenizer"):
                        self.assertEqual(set(plane["extensions"][extension_name]), {
                            "enabled", "version",
                        })
                serialized = json.dumps(result, sort_keys=True)
                for forbidden in (
                    "/private/", "private Dockerfile", "private probe output",
                    "fixture-password", "credentials", "secret",
                ):
                    self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
