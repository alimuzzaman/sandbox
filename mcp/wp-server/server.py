from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import mcp



import tools.instances  # noqa: F401
import tools.wp  # noqa: F401
import tools.net  # noqa: F401
import tools.data  # noqa: F401
import tools.fs  # noqa: F401
import tools.mail  # noqa: F401
import tools.context  # noqa: F401
import tools.cache  # noqa: F401
import tools.abilities  # noqa: F401
import tools.skills  # noqa: F401



if __name__ == "__main__":

    mcp.run()
