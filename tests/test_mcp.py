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
from app import mcp
async def go():
    tools = await mcp.list_tools()
    return len(tools), len(await mcp.list_prompts()), {tool.name for tool in tools}
t, p, names = asyncio.run(go())
print("TOOLS", t)
print("PROMPTS", p)
print("HERMES", int({'hermes_status', 'hermes_run', 'hermes_job_status', 'hermes_job_kill'} <= names))
import tools.hermes as hermes
calls = []
original_run_sb = hermes._run_sb
hermes._run_sb = lambda args, timeout: calls.append([args, timeout]) or {"ok": True}
hermes.hermes_status("remote")
hermes.hermes_run("remote", "repo", "non-secret", worktree=False, async_=True, timeout=100)
hermes.hermes_run("remote", "repo", "non-secret", worktree=True, async_=False, timeout=100)
hermes.hermes_job_status("remote", "0123456789abcdef", offset=7)
hermes.hermes_job_kill("remote", "0123456789abcdef")
print("HERMES_CALLS", json.dumps(calls))
hermes._run_sb = original_run_sb
def timed_out(*_args, **_kwargs):
    raise subprocess.TimeoutExpired("sb", 30)
hermes.subprocess.run = timed_out
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
    def test_tools_and_prompts_register(self):
        r = subprocess.run(
            [str(VENV_PY), "-c", _PROBE], cwd=str(MCP_DIR),
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "SANDBOX_ROOT": str(ROOT)})
        self.assertEqual(r.returncode, 0, f"server import failed:\n{r.stderr}")
        out = dict(
            line.split() for line in r.stdout.split("\n")
            if line.startswith(("TOOLS ", "PROMPTS ", "HERMES ")))
        # The pre-split server had 26 @mcp.tool + 8 @mcp.prompt; require parity.
        self.assertGreaterEqual(int(out["TOOLS"]), 26, r.stdout)
        self.assertGreaterEqual(int(out["PROMPTS"]), 8, r.stdout)
        self.assertEqual(out.get("HERMES"), "1", r.stdout)

        calls_line = next(line for line in r.stdout.splitlines() if line.startswith("HERMES_CALLS "))
        calls = __import__("json").loads(calls_line.removeprefix("HERMES_CALLS "))
        self.assertEqual(calls[0], [["hermes", "status", "--remote", "remote"], 30])
        self.assertIn("--no-worktree", calls[1][0])
        self.assertIn("--async", calls[1][0])
        self.assertNotIn("--async", calls[2][0])
        self.assertEqual(calls[2][1], 130)
        self.assertEqual(calls[3][0][-2:], ["--offset", "7"])
        self.assertEqual(calls[4][0][1:3], ["job", "kill"])
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
