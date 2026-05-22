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

# Server-level instructions — Claude Code (and other MCP clients) surface
# this in the model's system context automatically on connect. Capped at
# 2KB by Claude Code; keep it punchy. Full operating prompt lives in
# CLAUDE.md and is loadable on demand via load_context().
SANDBOX_INSTRUCTIONS = """You're connected to the WPDeveloper Sandbox — a live WordPress dev stack with MCP tools (wp_cli, wp_rest, db_query, tail_log, visit, fs_read, ...) wired to WP_URL.

ACTIVATION: when the user says `focus <plugin>`, `work on <plugin>`, or names a WPDeveloper plugin in a working/debugging context, run this handshake in order — `focus_set(<plugin>)` → `load_context()` → `focus_get()`. That puts you in sandbox mode (full operating prompt + plugin conventions + skill list). Don't re-run on subsequent turns. Also engage on WP errors, stack traces, debugging wp-admin, or "work with sandbox." Stay quiet on non-WP work.

REFLEXES when engaged:
- Bug / error / stack trace / "doesn't work" → your literal FIRST tool call REPRODUCES it via wp_cli, wp_rest, visit, or tail_log. Not Read. Not Grep. Not find. Can't reproduce → return BLOCKED. Never guess a fix from code reading.
- Any WP action → MCP tool, never raw bash / docker / curl / mysql.
- About to mutate DB / migrate / touch licensing → snapshot first.
- Editor-authored content (Gutenberg stateful save(), Elementor) → load_skill('wp-pilot'), drive real wp-admin, not hand PHP.
- About to commit / push / tag / open PR → STOP, wait for user.

DEEPER CONTEXT (call as needed): load_context (full sandbox guide), load_skill(name) for fix / bug-repro / snapshot / wp-debug / wp-pilot / fluentboards.

ANTI-PATTERNS — catch yourself:
- Declaring FIXED from code reading. Only a live MCP call is evidence.
- Slicing (edit, test, edit, test) — use load_skill('fix'): read all call sites once, batch edits, verify once.
- Bash where an MCP tool exists.
- 3 clarifying questions before starting — pick the most probable interpretation, work, flag assumption.

Output: terse, evidence-first, no "I'll now do X" preamble, code refs as markdown links.
"""

mcp = FastMCP("sandbox", instructions=SANDBOX_INSTRUCTIONS)


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

def _parse_skill_metadata(skill_md: Path) -> dict:
    """Pull `name:` and `description:` out of a SKILL.md's YAML frontmatter.

    Tolerant of missing/malformed frontmatter — returns empty strings then.
    """
    name, description = "", ""
    try:
        text = skill_md.read_text(errors="replace")
    except OSError:
        return {"name": name, "description": description}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
    return {"name": name, "description": description}


@mcp.tool()
def focus_get(include_claude_md: bool = True,
              max_bytes: int = 16_000) -> dict:
    """Return the currently-focused plugin's slug, source path, CLAUDE.md
    content, and any skill packs it ships (so Claude can read them on demand).

    Works for ANY plugin — looks for `CLAUDE.md` and `.claude/skills/*/SKILL.md`
    inside the focused plugin's source repo. No plugin name is hardcoded.

    Devs set focus with `./sb focus <slug>`. Claude should default
    file edits, debugging, and questions to that plugin's repo, and should
    read any `available_skills[*]` SKILL.md that's relevant to the task.
    """
    focus = FOCUS_FILE.read_text().strip() if FOCUS_FILE.exists() else None
    active = ACTIVE_FILE.read_text().strip() if ACTIVE_FILE.exists() else None
    out = {"ok": True, "focus": focus, "active_project": active,
           "source_path": None, "claude_md": None, "available_skills": []}
    if not focus:
        return out

    # Resolve focused plugin's source repo. Symlinks now live at depth 1
    # inside wp-content/plugins/ (was runtime/plugins/ — depth 2 — pre-fix).
    candidates = [
        SANDBOX_ROOT / "runtime" / "wp" / "wp-content" / "plugins" / focus,
        SANDBOX_ROOT / "runtime" / "plugins" / focus,           # legacy
        SANDBOX_ROOT / "plugins" / focus,                       # default plugins_home
    ]
    src = None
    for link in candidates:
        if link.is_symlink() or link.is_dir():
            src = link.resolve()
            break
    if not src or not src.exists():
        out["error"] = (f"focused plugin '{focus}' not found in any of: "
                        f"{', '.join(str(c) for c in candidates)}")
        return out
    out["source_path"] = str(src)

    # 1. Plugin's own CLAUDE.md (auto-injected).
    if include_claude_md:
        for candidate in ("CLAUDE.md", ".claude/CLAUDE.md"):
            cmd = src / candidate
            if cmd.exists() and cmd.is_file():
                data = cmd.read_bytes()[:max_bytes]
                out["claude_md"] = data.decode("utf-8", errors="replace")
                out["claude_md_path"] = str(cmd)
                out["claude_md_truncated"] = cmd.stat().st_size > max_bytes
                break

    # 2. Plugin's skill packs at .claude/skills/<name>/SKILL.md — enumerate
    #    them but don't inline content (Claude reads on demand via fs_read).
    skills_dir = src / ".claude" / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            skill_md = entry / "SKILL.md"
            if entry.is_dir() and skill_md.is_file():
                meta = _parse_skill_metadata(skill_md)
                out["available_skills"].append({
                    "name": meta["name"] or entry.name,
                    "description": meta["description"],
                    "path": str(skill_md),
                })

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


# ----------------------------- sandbox context loaders -----------------

SANDBOX_CLAUDE_MD = SANDBOX_ROOT / "CLAUDE.md"
SANDBOX_SKILLS_DIR = SANDBOX_ROOT / "skills"


def _list_sandbox_skills() -> list[dict]:
    out = []
    if not SANDBOX_SKILLS_DIR.is_dir():
        return out
    for entry in sorted(SANDBOX_SKILLS_DIR.iterdir()):
        skill_md = entry / "SKILL.md"
        if entry.is_dir() and skill_md.is_file():
            meta = _parse_skill_metadata(skill_md)
            out.append({
                "name": meta["name"] or entry.name,
                "description": meta["description"],
                "path": str(skill_md.relative_to(SANDBOX_ROOT)),
            })
    return out


@mcp.tool()
def load_context() -> dict:
    """Return the full Sandbox CLAUDE.md (operating guide).

    Call this when the user wants to "work with sandbox" or when you've
    decided you're engaged in WP work and need the full operating prompt
    beyond the 2KB instructions baseline. Also returns the list of
    available top-level sandbox skills so you know which load_skill()
    calls are available.
    """
    if not SANDBOX_CLAUDE_MD.exists():
        return {"ok": False, "error": f"missing {SANDBOX_CLAUDE_MD}"}
    return {
        "ok": True,
        "claude_md": SANDBOX_CLAUDE_MD.read_text(errors="replace"),
        "claude_md_path": str(SANDBOX_CLAUDE_MD),
        "available_skills": _list_sandbox_skills(),
    }


@mcp.tool()
def load_skill(name: str) -> dict:
    """Return the full text of a top-level sandbox skill (SKILL.md).

    Use this when a reflex tells you to engage a specific skill — e.g.
    `load_skill('fix')` before starting a bug-fix loop, `load_skill('wp-pilot')`
    before editor-driven authoring. Skill names match the directories
    under sandbox/skills/ (fix, bug-repro, snapshot, wp-debug, wp-pilot,
    fluentboards).
    """
    skill_md = SANDBOX_SKILLS_DIR / name / "SKILL.md"
    if not skill_md.is_file():
        return {
            "ok": False,
            "error": f"no skill '{name}' (looked at {skill_md})",
            "available_skills": [s["name"] for s in _list_sandbox_skills()],
        }
    return {
        "ok": True,
        "name": name,
        "path": str(skill_md.relative_to(SANDBOX_ROOT)),
        "content": skill_md.read_text(errors="replace"),
    }


# ----------------------------- user-invoked prompts --------------------
# Surface as /mcp__sandbox__<name> slash commands in Claude Code.
# These are USER-invoked (the model cannot trigger them programmatically —
# for that we expose load_skill / load_context as tools above).

def _skill_prompt_body(name: str, task: str = "") -> str:
    skill_md = SANDBOX_SKILLS_DIR / name / "SKILL.md"
    if not skill_md.is_file():
        return f"Skill '{name}' not found at {skill_md}."
    body = skill_md.read_text(errors="replace")
    header = (
        f"The user has invoked the `{name}` sandbox skill. Follow its "
        f"contract for the rest of this conversation.\n\n"
    )
    if task:
        header += f"TASK FROM USER:\n{task}\n\n"
    return header + "--- SKILL CONTRACT ---\n\n" + body


@mcp.prompt()
def activate(task: str = "") -> str:
    """Load the full sandbox operating guide (CLAUDE.md) into this session."""
    cm = SANDBOX_CLAUDE_MD.read_text(errors="replace") if SANDBOX_CLAUDE_MD.exists() else "(missing CLAUDE.md)"
    header = "The user has activated sandbox mode. Follow this operating guide for the rest of the conversation.\n\n"
    if task:
        header += f"TASK FROM USER:\n{task}\n\n"
    return header + "--- SANDBOX CLAUDE.md ---\n\n" + cm


@mcp.prompt()
def focus(plugin: str) -> str:
    """Activate sandbox mode focused on a specific plugin.

    Runs the activation handshake: sets the focused plugin, loads the
    full sandbox operating guide, and instructs you to call focus_get
    next for the plugin's own conventions + skills.
    """
    return (
        f"The user is focusing on plugin '{plugin}' and entering sandbox mode. "
        f"Run this handshake now:\n\n"
        f"1. Call focus_set(plugin_slug='{plugin}') to persist the choice.\n"
        f"2. Call load_context() to pull the full sandbox CLAUDE.md.\n"
        f"3. Call focus_get() to fetch the plugin's own CLAUDE.md + available skills.\n\n"
        f"Then acknowledge with one line (which plugin is loaded, which skills are available) "
        f"and await the user's task. Follow the sandbox operating prompt for the rest of the conversation."
    )


@mcp.prompt()
def fix(task: str = "") -> str:
    """Engage the one-pass bug-fix loop (skills/fix/SKILL.md)."""
    return _skill_prompt_body("fix", task)


@mcp.prompt()
def bug_repro(task: str = "") -> str:
    """Reproduce a bug live on the running stack (skills/bug-repro/SKILL.md)."""
    return _skill_prompt_body("bug-repro", task)


@mcp.prompt()
def snapshot(task: str = "") -> str:
    """Snapshot / restore guidance (skills/snapshot/SKILL.md)."""
    return _skill_prompt_body("snapshot", task)


@mcp.prompt()
def wp_debug(task: str = "") -> str:
    """Debugging the WP stack (skills/wp-debug/SKILL.md)."""
    return _skill_prompt_body("wp-debug", task)


@mcp.prompt()
def wp_pilot(task: str = "") -> str:
    """Headless wp-admin authoring (skills/wp-pilot/SKILL.md)."""
    return _skill_prompt_body("wp-pilot", task)


if __name__ == "__main__":
    mcp.run()
