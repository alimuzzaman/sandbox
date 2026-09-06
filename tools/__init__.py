"""Shared repository tooling package.

The repository also ships the MCP tool groups under ``mcp/wp-server/tools``.
Both directories historically used the top-level name ``tools``.  Test and
inspection processes can import this package before the MCP server adds its
directory to ``sys.path``; extend this package's search path up front so a
cached repository package still resolves the MCP modules deterministically.
"""

from __future__ import annotations

from pathlib import Path
import sys


_MCP_TOOLS = Path(__file__).resolve().parent.parent / "mcp" / "wp-server" / "tools"
if _MCP_TOOLS.is_dir():
    _MCP_ROOT = _MCP_TOOLS.parent
    if str(_MCP_ROOT) not in sys.path:
        sys.path.append(str(_MCP_ROOT))
    __path__.append(str(_MCP_TOOLS))
