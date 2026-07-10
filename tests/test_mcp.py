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
import os, asyncio
os.environ.setdefault("SANDBOX_ROOT", os.getcwd() + "/../..")
import server                      # thin entry: imports app + all tools.* groups
from app import mcp
async def go():
    tools = await mcp.list_tools()
    return len(tools), len(await mcp.list_prompts()), {tool.name for tool in tools}
t, p, names = asyncio.run(go())
print("TOOLS", t)
print("PROMPTS", p)
print("HERMES", int('hermes_status' in names and 'hermes_run' in names))
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
            if line.startswith(("TOOLS", "PROMPTS", "HERMES")))
        # The pre-split server had 26 @mcp.tool + 8 @mcp.prompt; require parity.
        self.assertGreaterEqual(int(out["TOOLS"]), 26, r.stdout)
        self.assertGreaterEqual(int(out["PROMPTS"]), 8, r.stdout)
        self.assertEqual(out.get("HERMES"), "1", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
