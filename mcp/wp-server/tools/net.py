from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import *  # noqa: F401,F403



@mcp.tool()
def http_fetch(url: str, method: str = "GET", follow_redirects: bool = True,
               headers: dict | None = None, body: str | None = None,
               max_body_bytes: int = 200_000, timeout: int = 15) -> dict:
    """Lightweight HTTP probe against the sandbox WP (or any URL).

    Use this for anonymous status/header/content-type checks where wp_rest
    would be wrong (no app-password auth wanted) and visit would be overkill
    (no need for a real browser, JS execution, or DOM querying). Common
    cases: verifying a feed URL returns the expected status + content-type,
    checking redirect chains, probing a rewrite-rule landing, smoke-testing
    a public endpoint.

    Returns {ok, status, final_url, headers, body_truncated, body, redirects}.
    `body` is trimmed to max_body_bytes; `body_truncated` is True when the
    response was longer. `redirects` lists each intermediate hop's
    (status, url) when follow_redirects is True.
    """
    try:
        with httpx.Client(follow_redirects=follow_redirects,
                          timeout=timeout) as client:
            req = client.build_request(method.upper(), url,
                                       headers=headers or {},
                                       content=body)
            resp = client.send(req)
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}

    raw = resp.content or b""
    truncated = len(raw) > max_body_bytes
    body_text = raw[:max_body_bytes].decode("utf-8", errors="replace")
    redirects = [(r.status_code, str(r.url)) for r in resp.history]
    return {
        "ok": 200 <= resp.status_code < 400,
        "status": resp.status_code,
        "final_url": str(resp.url),
        "headers": dict(resp.headers),
        "body": body_text,
        "body_truncated": truncated,
        "redirects": redirects,
    }

@mcp.tool()
def visit(url: str, login: bool = False, check_iframes: bool = False,
          screenshot: str | None = None, full_page: bool = False,
          timeout: int = 20, width: int = 1280, height: int = 800,
          wait_until: str = "domcontentloaded") -> dict:
    """Load `url` in headless Chromium and return a structured report
    (status, title, console errors, network failures, iframe inventory).

    Use this when the bug is browser-rendered — Gutenberg/Elementor editor
    state, JS execution, asset loading order, or any "X happens when the
    page loads in a real browser" symptom. For PHP, REST, SQL, or cron
    bugs, prefer `wp_cli` / `wp_rest` / `db_query` — those are faster and
    give cleaner evidence.

    Auto-login: if `url` contains `/wp-admin/` OR `login=True` is set,
    the runner submits wp-login.php with WP_ADMIN_USER / WP_ADMIN_PASSWORD
    (auto-injected by the sandbox setup) before navigation. The agent
    has full admin access against the sandbox WP — don't ask the user
    for credentials.
    """
    if not VISIT_SCRIPT.is_file():
        return {"ok": False, "error": f"missing {VISIT_SCRIPT}"}
    if not TOOLS_VENV_PY.exists():
        return {
            "ok": False,
            "error": "tools venv not built — run `./sb visit <url>` once "
                     "from the sandbox dir to provision Playwright + Chromium.",
        }
    cmd = [str(TOOLS_VENV_PY), str(VISIT_SCRIPT), url,
           "--timeout", str(timeout),
           "--width", str(width), "--height", str(height),
           "--wait-until", wait_until,
           "--auto-login"]
    if login:
        cmd.append("--login")
    if check_iframes:
        cmd.append("--check-iframes")
    if screenshot:
        cmd += ["--screenshot", screenshot]
        if full_page:
            cmd.append("--full-page")
    try:
        _u, _p = _admin_creds()
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=max(timeout + 30, 60),
                             cwd=str(SANDBOX_ROOT),
                             env={**os.environ, "WP_ADMIN_USER": _u,
                                  "WP_ADMIN_PASSWORD": _p})
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"visit subprocess timed out after {timeout + 30}s"}
    # visit.py emits JSON on stdout regardless of exit code; exit-nonzero
    # signals user-visible problems (we surface that as ok=False) but the
    # report itself is still useful, so include it either way.
    report = _safe_json(res.stdout) or {"raw_stdout": res.stdout}
    return {
        "ok": res.returncode == 0,
        "code": res.returncode,
        "report": report,
        "stderr": res.stderr,
    }
