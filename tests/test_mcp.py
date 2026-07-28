"""MCP server smoke test (spec 001 Stage D — server.py split into app + tools/).

Runs in the MCP server's own venv (FastMCP lives there, not in .cli-venv), so
it's a subprocess test that skips cleanly if the venv isn't built. Verifies that
importing the thin server.py registers every grouped tool/prompt on the shared
`mcp` — the exact failure mode (decorators dropped during the split) that the
code review caught once already.
"""
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = ROOT / "mcp" / "wp-server"
VENV_PY = MCP_DIR / ".venv" / "bin" / "python"

_PROBE = """
import os, asyncio, json, subprocess
os.environ.setdefault("SANDBOX_ROOT", os.getcwd() + "/../..")
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
import sys as _sys
_mcp_root = str(mcp_app.SANDBOX_ROOT)
_sys.path = [entry for entry in _sys.path if entry != _mcp_root]
capability_import = mcp_app._require_project_capability("/tmp/project", None, "wordpress.cli")
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
invalid_mode = wp_tools.run_tests("/tmp/project", mode="not-a-mode")
print("TEST_MODE_INVALID", json.dumps(invalid_mode))
wp_tools._require_project_capability = lambda *_args: None
wp_tools._project_instance = lambda *_args: ("fixture", None)
class TestRunResult:
    returncode = 0
    stdout = "  mode:       unit\\nOK (1 test, 1 assertion)\\n"
    stderr = ""
test_calls = []
wp_tools.subprocess.run = lambda cmd, **kwargs: test_calls.append([cmd, kwargs]) or TestRunResult()
print("TEST_MODE_FORWARD", json.dumps(wp_tools.run_tests(
    "/tmp/project", mode="unit", phpunit_args="--filter Example")))
print("TEST_MODE_CALL", json.dumps(test_calls))
rejection = {"ok": False, "error": "blocked before side effects"}
wp_side_effects = []
wp_tools._require_project_capability = lambda *_args: rejection
wp_tools._wpcli = lambda *_args, **_kwargs: wp_side_effects.append("wpcli")
data_tools._require_project_capability = lambda *_args: rejection
data_tools._compose = lambda *_args, **_kwargs: wp_side_effects.append("compose")
print("CAPABILITY_REJECTION", json.dumps([
    wp_tools.wp_cli("core version", project_dir="/tmp/project"),
    data_tools.db_query("SELECT 1", project_dir="/tmp/project"),
    wp_side_effects,
]))
"""


@unittest.skipUnless(VENV_PY.exists(), "MCP venv not built (run: ./sb mcp-install)")
class TestMcpServerSplit(unittest.TestCase):
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
            ("instance_status", "project_dir"), ("instance_logs", "project_dir"),
            ("instance_exec", "command,project_dir"),
            ("job_start", "command,project_dir"), ("job_matrix", "command,project_dir,workspaces"), ("job_status", "job_id"),
            ("job_list", ""), ("job_output", "job_id"),
            ("job_follow", "job_id"), ("job_metrics", "job_id"), ("job_reconcile", ""), ("job_retention", ""), ("job_cancel", "job_id"),
            ("job_artifacts", "job_id"), ("job_artifact_get", "artifact_id,job_id"),
            ("job_retry", "job_id"), ("job_cleanup", "job_id"),
            ("workspace_create", "project_dir"), ("workspace_list", "project_dir"),
            ("workspace_status", "project_dir"), ("workspace_reset", "project_dir"),
            ("workspace_destroy", "project_dir"),
            ("wp_cli", "command,project_dir"), ("wp_exec", "command,project_dir"),
            ("wp_rest", "method,path,project_dir"), ("run_tests", "project_dir"),
            ("wp_cli_async", "command,project_dir"), ("wp_cli_job", "job_id,project_dir"),
            ("wp_cli_job_kill", "job_id,project_dir"), ("http_fetch", "url"),
            ("pixelmatch_diff", "build,reference"), ("visit", "url"),
            ("db_query", "project_dir,sql"), ("import_content", "project_dir,seed_file"),
            ("wp_reset", "project_dir"), ("tail_log", "project_dir"),
            ("fs_read", "path,project_dir"), ("fs_write", "content,path,project_dir"),
            ("fs_list", "project_dir"), ("mail_list", "project_dir"),
            ("mail_get", "message_id,project_dir"), ("focus_get", "project_dir"),
            ("activate_plugin", "project_dir,slug"), ("deactivate_plugin", "project_dir,slug"),
            ("load_context", ""), ("load_workflow", "name"), ("load_skill", "name"),
            ("cache_info", ""), ("cache_clear", ""),
            ("resource_status", ""), ("resource_cleanup_plan", "scope"),
            ("resource_cleanup_apply", "plan_id"),
            ("wp_eval_live", "code,project_dir"), ("list_skills", "project_dir"),
            ("skill_write", "description,project_dir,title"), ("skill_edit", "project_dir,slug"),
            ("skill_delete", "project_dir,slug"), ("qm_capture", "project_dir"),
            ("xdebug", "project_dir"), ("run_e2e", "project_dir"),
            ("ci_plan", "workflow"), ("ci_run", "project_dir,workflow"),
            ("async_job_status", "job_id"), ("async_job_kill", "job_id"),
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
        self.assertEqual(len(actual), 105)
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
        self.assertLessEqual(int(instructions.split()[1]), 1000, r.stdout)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
