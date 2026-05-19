"""wp-mcp: expose the Sandbox WordPress runtime to any MCP-speaking LLM.

Tools:
  - wp_cli            run any wp-cli command inside the sandbox
  - wp_exec           run any shell command inside the wp container (composer, npm, php, …)
  - wp_rest           call the WP REST API (uses an Application Password)
  - db_query          run any SQL (writes require mutate=true)
  - tail_log          tail wp-content/debug.log
  - fs_read / fs_write read/write files under runtime/wp/ (scoped)
  - mail_list / mail_get   browse Mailpit
  - focus_get / focus_set  which plugin Claude should default to working on
  - activate_plugin / deactivate_plugin / import_content (legacy helpers)

Designed to be launched over stdio by an LLM client. See README.md.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

SANDBOX_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = SANDBOX_ROOT / "docker-compose.yml"
WP_ROOT = SANDBOX_ROOT / "runtime" / "wp"
LOG_PATH = WP_ROOT / "wp-content" / "debug.log"
FOCUS_FILE = SANDBOX_ROOT / ".focus"
ACTIVE_FILE = SANDBOX_ROOT / ".active-project"

WP_URL = os.environ.get("WP_URL", "http://localhost:8088")
WP_USER = os.environ.get("WP_ADMIN_USER", "admin")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://localhost:8025")

mcp = FastMCP("wp-mcp")


# ----------------------------- helpers -------------------------------

def _compose(*args: str, capture: bool = True, timeout: int = 60) -> dict:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    try:
        res = subprocess.run(
            cmd, capture_output=capture, text=True,
            timeout=timeout, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s", "cmd": cmd}
    return {
        "ok": res.returncode == 0,
        "code": res.returncode,
        "stdout": res.stdout,
        "stderr": res.stderr,
    }


def _wpcli(args: list[str], timeout: int = 60) -> dict:
    return _compose("run", "--rm", "wpcli", *args, timeout=timeout)


def _wpcli_shell(shell_cmd: str, timeout: int = 60) -> dict:
    """Run a wp-cli command through sh -c so $(...) / pipes work."""
    return _compose(
        "run", "--rm", "--entrypoint", "sh", "wpcli", "-c", shell_cmd,
        timeout=timeout,
    )


def _safe_resolve(rel_path: str, root: Path) -> Path | None:
    """Resolve rel_path against root and refuse anything that escapes root."""
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _safe_json(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


# ----------------------------- tools ---------------------------------

@mcp.tool()
def wp_cli(command: str, timeout: int = 60) -> dict:
    """Run any wp-cli command. Pass the args after `wp` (e.g. 'plugin list').

    Note: this runs `wp <command>` directly. If you need shell features like
    `$(cat ...)`, pipes, or redirects, use wp_exec instead.
    """
    return _wpcli(shlex.split(command), timeout=timeout)


@mcp.tool()
def wp_exec(command: str, container: str = "wp", workdir: str | None = None,
            timeout: int = 120) -> dict:
    """Run an arbitrary shell command inside a container (default `wp`).

    Use for composer, npm, node, php scripts, file ops, etc. Runs as the
    container's default user. Supports pipes, $(...) and redirects since
    it goes through `sh -c`.

    container: 'wp' (default), 'db', 'wpcli', or 'mailpit'.
    """
    args = ["exec"]
    if workdir:
        args += ["-w", workdir]
    args += ["-T", container, "sh", "-c", command]
    return _compose(*args, timeout=timeout)


@mcp.tool()
def wp_rest(method: str, path: str, body: dict | None = None,
            query: dict | None = None) -> dict:
    """Call the WordPress REST API.

    path: e.g. '/wp/v2/posts' (leading slash optional)
    Auth via Application Password — set WP_APP_PASSWORD env var.
    """
    if not WP_APP_PASSWORD:
        return {
            "ok": False,
            "error": "WP_APP_PASSWORD not set. Run `./sandbox install` "
                     "(auto-provisions one) or generate at "
                     f"{WP_URL}/wp-admin/profile.php and export it.",
        }
    url = f"{WP_URL.rstrip('/')}/wp-json{'/' if not path.startswith('/') else ''}{path}"
    try:
        with httpx.Client(auth=(WP_USER, WP_APP_PASSWORD), timeout=30.0) as c:
            r = c.request(method.upper(), url, params=query, json=body)
        return {"ok": r.is_success, "status": r.status_code,
                "body": _safe_json(r.text)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def db_query(sql: str, mutate: bool = False) -> dict:
    """Run a SQL query against the WP database.

    Reads (SELECT/SHOW/DESCRIBE/EXPLAIN) run freely.
    Writes (INSERT/UPDATE/DELETE/ALTER/CREATE/DROP/TRUNCATE/REPLACE) require
    mutate=true — an explicit acknowledgement that this changes data.
    """
    head = sql.strip().split(None, 1)[0].upper() if sql.strip() else ""
    reads = {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH"}
    writes = {"INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER",
              "CREATE", "DROP", "TRUNCATE", "GRANT", "REVOKE", "SET"}
    if head in writes and not mutate:
        return {
            "ok": False,
            "error": f"refused: {head} requires mutate=true (writes the DB)",
        }
    if head not in reads and head not in writes:
        return {"ok": False, "error": f"unrecognized statement type: {head!r}"}
    return _wpcli(["db", "query", sql])


@mcp.tool()
def tail_log(lines: int = 100) -> dict:
    """Tail wp-content/debug.log."""
    if not LOG_PATH.exists():
        return {"ok": True, "lines": [], "note": "debug.log not yet created"}
    try:
        data = LOG_PATH.read_bytes()
        text = data.decode("utf-8", errors="replace")
        return {"ok": True, "lines": text.splitlines()[-lines:],
                "path": str(LOG_PATH)}
    except OSError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def fs_read(path: str, max_bytes: int = 200_000) -> dict:
    """Read a file under runtime/wp/ (the WordPress install).

    path is relative to runtime/wp/ — e.g. 'wp-content/themes/my-theme/style.css'.
    Refuses paths that escape the WP root.
    """
    target = _safe_resolve(path, WP_ROOT)
    if target is None:
        return {"ok": False, "error": f"path escapes WP root: {path!r}"}
    if not target.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if not target.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    data = target.read_bytes()[:max_bytes]
    try:
        return {"ok": True, "path": str(target.relative_to(WP_ROOT)),
                "size": target.stat().st_size,
                "truncated": target.stat().st_size > max_bytes,
                "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"ok": True, "path": str(target.relative_to(WP_ROOT)),
                "size": target.stat().st_size, "binary": True,
                "note": "binary file; use wp_exec to inspect"}


@mcp.tool()
def fs_write(path: str, content: str, create_dirs: bool = True) -> dict:
    """Write a file under runtime/wp/. Creates parent dirs by default.

    Refuses paths that escape WP root. Returns bytes written.
    """
    target = _safe_resolve(path, WP_ROOT)
    if target is None:
        return {"ok": False, "error": f"path escapes WP root: {path!r}"}
    if create_dirs:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"ok": True, "path": str(target.relative_to(WP_ROOT)),
            "bytes": len(content.encode("utf-8"))}


@mcp.tool()
def fs_list(path: str = "", depth: int = 1) -> dict:
    """List files under runtime/wp/<path>. depth=1 is shallow."""
    target = _safe_resolve(path or ".", WP_ROOT)
    if target is None or not target.exists():
        return {"ok": False, "error": f"not found or escapes root: {path!r}"}
    out = []
    base_depth = len(target.parts)
    for p in target.rglob("*"):
        if len(p.parts) - base_depth > depth:
            continue
        out.append({
            "path": str(p.relative_to(WP_ROOT)),
            "type": "dir" if p.is_dir() else "file",
            "size": p.stat().st_size if p.is_file() else None,
        })
        if len(out) >= 500:
            out.append({"note": "truncated at 500 entries"})
            break
    return {"ok": True, "root": str(target.relative_to(WP_ROOT)), "entries": out}


# ------------------ Mailpit (test SMTP inbox) ------------------------

@mcp.tool()
def mail_list(limit: int = 20) -> dict:
    """List the most recent messages caught by Mailpit (test SMTP)."""
    try:
        r = httpx.get(f"{MAILPIT_URL.rstrip('/')}/api/v1/messages",
                      params={"limit": limit}, timeout=10.0)
        return {"ok": r.is_success, "status": r.status_code,
                "body": _safe_json(r.text)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def mail_get(message_id: str) -> dict:
    """Get a single message from Mailpit (headers, text, html)."""
    try:
        r = httpx.get(f"{MAILPIT_URL.rstrip('/')}/api/v1/message/{message_id}",
                      timeout=10.0)
        return {"ok": r.is_success, "status": r.status_code,
                "body": _safe_json(r.text)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}


# ------------------ focus (which plugin am I working on) -------------

@mcp.tool()
def focus_get(include_claude_md: bool = True,
              max_bytes: int = 16_000) -> dict:
    """Return the currently-focused plugin slug, active project, and the
    plugin's CLAUDE.md content (auto-injected so Claude follows its conventions).

    Devs set focus with `./sandbox focus <slug>`. Claude should default
    file edits, debugging, and questions to that plugin's repo.
    """
    focus = FOCUS_FILE.read_text().strip() if FOCUS_FILE.exists() else None
    active = ACTIVE_FILE.read_text().strip() if ACTIVE_FILE.exists() else None
    out = {"ok": True, "focus": focus, "active_project": active,
           "source_path": None, "claude_md": None}
    if not focus:
        return out

    # Resolve focused plugin's source repo from the runtime symlink.
    link = SANDBOX_ROOT / "runtime" / "plugins" / focus
    if link.is_symlink():
        src = link.resolve()
        out["source_path"] = str(src)
        if include_claude_md:
            for candidate in ("CLAUDE.md", ".claude/CLAUDE.md"):
                cmd = src / candidate
                if cmd.exists() and cmd.is_file():
                    data = cmd.read_bytes()[:max_bytes]
                    out["claude_md"] = data.decode("utf-8", errors="replace")
                    out["claude_md_path"] = str(cmd)
                    out["claude_md_truncated"] = cmd.stat().st_size > max_bytes
                    break
    return out


@mcp.tool()
def focus_set(plugin_slug: str) -> dict:
    """Set the focused plugin slug. Pass empty string to clear."""
    if plugin_slug:
        FOCUS_FILE.write_text(plugin_slug.strip())
        return {"ok": True, "focus": plugin_slug.strip()}
    if FOCUS_FILE.exists():
        FOCUS_FILE.unlink()
    return {"ok": True, "focus": None}


# ------------------ legacy convenience -------------------------------

@mcp.tool()
def activate_plugin(slug: str) -> dict:
    """wp plugin activate <slug>. Slug must match the plugin folder name."""
    return _wpcli(["plugin", "activate", slug])


@mcp.tool()
def deactivate_plugin(slug: str) -> dict:
    """wp plugin deactivate <slug>."""
    return _wpcli(["plugin", "deactivate", slug])


@mcp.tool()
def import_content(seed_file: str, authors: str = "create") -> dict:
    """Import a WXR XML from runtime/seeds/. Pass just the filename."""
    return _wpcli(["import", f"/seeds/{seed_file}",
                   f"--authors={authors}"], timeout=180)


if __name__ == "__main__":
    mcp.run()
