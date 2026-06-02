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
COMPOSE_DIR = SANDBOX_ROOT / "runtime" / "compose"
DEFAULT_INSTANCE = "main"

# Per-instance helpers — mirror the CLI's resolution.

def _wp_root(instance: str) -> Path:
    return SANDBOX_ROOT / "runtime" / f"wp-{instance}"


def _log_path(instance: str) -> Path:
    return _wp_root(instance) / "wp-content" / "debug.log"


def _focus_file(instance: str) -> Path:
    return SANDBOX_ROOT / f".focus.{instance}"


def _active_file(instance: str) -> Path:
    return SANDBOX_ROOT / f".active-project.{instance}"


def _compose_file(instance: str) -> Path:
    return COMPOSE_DIR / f"{instance}.yml"


def _project_name(instance: str) -> str:
    return f"sandbox-{instance}"


# Cached config — invalidated when sandbox.yml mtime changes so we
# don't re-parse on every tool call but still pick up edits.
_cfg_cache: dict = {"mtime": 0.0, "data": None}


def _load_sandbox_yml() -> dict:
    """Read sandbox.yml (+ sandbox.local.yml override) for instance lookups.

    Cached on mtime so per-tool-call cost stays near-zero. Tools that
    need per-instance config (ports, admin) call _resolve_instance(name).
    """
    cfg_path = SANDBOX_ROOT / "sandbox.yml"
    local_path = SANDBOX_ROOT / "sandbox.local.yml"
    if not cfg_path.exists():
        return {}
    mtime = max(
        cfg_path.stat().st_mtime,
        local_path.stat().st_mtime if local_path.exists() else 0,
    )
    if _cfg_cache["data"] is not None and mtime == _cfg_cache["mtime"]:
        return _cfg_cache["data"]
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    if local_path.exists():
        local = yaml.safe_load(local_path.read_text()) or {}
        # Shallow merge — local overrides at the top level.
        for k, v in local.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    _cfg_cache["data"] = cfg
    _cfg_cache["mtime"] = mtime
    return cfg


def _resolve_instance(instance: str) -> dict:
    """Return per-instance ports/admin/app_password.

    Falls back to env vars (set by the CLI when registering the MCP
    server) for the default instance — keeps the pre-multi-instance
    `main`-only flow working even before sandbox.yml is read.
    """
    cfg = _load_sandbox_yml()
    runtime = cfg.get("runtime", {}) or {}
    inst = (cfg.get("instances") or {}).get(instance, {}) or {}

    rt_admin = runtime.get("admin") or {}
    inst_admin = inst.get("admin") or {}
    out = {
        "wordpress_port": inst.get("wordpress_port",
                                   runtime.get("wordpress_port", 8088)),
        "mailpit_port": inst.get("mailpit_port",
                                 runtime.get("mailpit_port", 8025)),
        "admin": {**rt_admin, **inst_admin},
    }

    # App password: per-instance under instances.<name>.app_password,
    # OR (for main only) the legacy mcp.wp.application_password key.
    if instance == DEFAULT_INSTANCE:
        app_pw = ((cfg.get("mcp") or {}).get("wp") or {}).get(
            "application_password", ""
        )
        # Env wins over file when both set — that's how the CLI primes the
        # server on startup before sandbox.local.yml is even written.
        out["app_password"] = os.environ.get("WP_APP_PASSWORD") or app_pw
        out["wordpress_port"] = int(
            os.environ.get("WP_URL", "").rsplit(":", 1)[-1].split("/")[0]
            or out["wordpress_port"]
        ) if os.environ.get("WP_URL") else out["wordpress_port"]
    else:
        out["app_password"] = inst.get("app_password", "")
    return out


# Default-instance values for tools that pre-date the multi-instance era
# (and for the docstring defaults). The CLI sets these env vars when it
# registers the MCP server; we honor them as the main instance.
WP_URL = os.environ.get("WP_URL", "http://localhost:8088")
WP_USER = os.environ.get("WP_ADMIN_USER", "admin")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://localhost:8025")

# Server-level instructions — Claude Code (and other MCP clients) surface
# this in the model's system context automatically on connect. Capped at
# 2KB by Claude Code; keep it punchy. Full operating prompt lives in
# CLAUDE.md and is loadable on demand via load_context().
SANDBOX_INSTRUCTIONS = """You're connected to the WPDeveloper Sandbox — a live WordPress dev stack with MCP tools (wp_cli, wp_rest, db_query, tail_log, visit, fs_read, ...) wired to WP_URL.

ACTIVATION: user says `focus <plugin>` / `work on <plugin>` / names a WPDeveloper plugin in a working context → handshake `focus_set` → `load_context` → `focus_get`. Don't re-run. Also engage on WP errors, stack traces, wp-admin debugging, or "work with sandbox." Stay quiet on non-WP work.

ADMIN ACCESS: sandbox WP is yours — full admin via wp_cli (in-container), wp_rest (app pw), visit (auto-login on wp-admin). Creds pre-wired; never ask.

REFLEXES when engaged:
- Bug / error / stack trace / "doesn't work" → first tool call REPRODUCES on the live stack. Not Read/Grep/find. Pick the lightest tool: PHP/REST/SQL/cron → wp_cli/wp_rest/db_query/tail_log. Browser-rendered → visit. Can't reproduce → BLOCKED.
- "Add" / "build" / "implement" / "create new" X → load_workflow('build-feature'); emit each phase as prose with bold headers (NOT fenced code blocks). Gates scale by Size: S = no gates after Phase 1 (auto-proceed), M = 1 gate after Phase 1, L = 2 gates (after Phase 1 + Phase 2).
- Any WP action → MCP tool, never raw bash / docker / curl / mysql.
- About to mutate DB / migrate / touch licensing → snapshot first.
- Editor authoring (Gutenberg stateful save, Elementor) → load_skill('wp-pilot'), drive real wp-admin.
- About to commit / push / tag / open PR → STOP, wait for user.

DEEPER CONTEXT: load_context (full guide), load_skill(name) for fix/bug-repro/snapshot/wp-debug/wp-pilot/fluentboards, load_workflow('build-feature') for new features.

ANTI-PATTERNS — catch yourself:
- FIXED from code reading. Only live MCP calls count as evidence.
- Bug-fix slicing (edit, test, edit, test) — use load_skill('fix'): read all, batch edits, verify once.
- Bash where an MCP tool exists.
- 3 clarifying questions — pick likeliest interpretation, work, flag the assumption.

Output: terse, evidence-first, no "I'll now do X" preamble, code refs as markdown links.
"""

mcp = FastMCP("sandbox", instructions=SANDBOX_INSTRUCTIONS)


# ----------------------------- helpers -------------------------------

def _compose(*args: str, instance: str = DEFAULT_INSTANCE,
             capture: bool = True, timeout: int = 60) -> dict:
    cf = _compose_file(instance)
    if not cf.exists():
        return {
            "ok": False,
            "error": f"no compose file for instance '{instance}' "
                     f"(expected {cf}). Run `./sb apply` from the sandbox "
                     f"dir to regenerate.",
        }
    cmd = ["docker", "compose",
           "-p", _project_name(instance),
           "-f", str(cf), *args]
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


def _wpcli(args: list[str], instance: str = DEFAULT_INSTANCE,
           timeout: int = 60) -> dict:
    return _compose("run", "--rm", "wpcli", *args,
                    instance=instance, timeout=timeout)


def _wpcli_shell(shell_cmd: str, instance: str = DEFAULT_INSTANCE,
                 timeout: int = 60) -> dict:
    """Run a wp-cli command through sh -c so $(...) / pipes work."""
    return _compose(
        "run", "--rm", "--entrypoint", "sh", "wpcli", "-c", shell_cmd,
        instance=instance, timeout=timeout,
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
def wp_cli(command: str, timeout: int = 60,
           instance: str = DEFAULT_INSTANCE) -> dict:
    """Run any wp-cli command. Pass the args after `wp` (e.g. 'plugin list').

    Note: this runs `wp <command>` directly. If you need shell features like
    `$(cat ...)`, pipes, or redirects, use wp_exec instead.

    instance: which sandbox instance to target (default: main).
    """
    return _wpcli(shlex.split(command), instance=instance, timeout=timeout)


@mcp.tool()
def wp_exec(command: str, container: str = "wp", workdir: str | None = None,
            timeout: int = 120, instance: str = DEFAULT_INSTANCE) -> dict:
    """Run an arbitrary shell command inside a container (default `wp`).

    Use for composer, npm, node, php scripts, file ops, etc. Runs as the
    container's default user. Supports pipes, $(...) and redirects since
    it goes through `sh -c`.

    container: 'wp' (default), 'db', 'wpcli', or 'mailpit'.
    instance: which sandbox instance to target (default: main).
    """
    args = ["exec"]
    if workdir:
        args += ["-w", workdir]
    args += ["-T", container, "sh", "-c", command]
    return _compose(*args, instance=instance, timeout=timeout)


@mcp.tool()
def wp_rest(method: str, path: str, body: dict | None = None,
            query: dict | None = None,
            instance: str = DEFAULT_INSTANCE) -> dict:
    """Call the WordPress REST API.

    path: e.g. '/wp/v2/posts' (leading slash optional)
    Auth via Application Password — auto-provisioned by `./sb install`.
    instance: which sandbox instance to hit (default: main).
    """
    inst_cfg = _resolve_instance(instance)
    app_pw = inst_cfg["app_password"]
    if not app_pw:
        return {
            "ok": False,
            "error": f"no application_password for instance '{instance}'. "
                     f"Run `./sb install --instance {instance}` "
                     f"(auto-provisions one).",
        }
    port = inst_cfg["wordpress_port"]
    user = inst_cfg["admin"].get("user", "admin")
    base = f"http://localhost:{port}"
    url = f"{base}/wp-json{'/' if not path.startswith('/') else ''}{path}"
    try:
        with httpx.Client(auth=(user, app_pw), timeout=30.0) as c:
            r = c.request(method.upper(), url, params=query, json=body)
        return {"ok": r.is_success, "status": r.status_code,
                "body": _safe_json(r.text)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}


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
def db_query(sql: str, mutate: bool = False,
             instance: str = DEFAULT_INSTANCE) -> dict:
    """Run a SQL query against the WP database.

    Reads (SELECT/SHOW/DESCRIBE/EXPLAIN) run freely.
    Writes (INSERT/UPDATE/DELETE/ALTER/CREATE/DROP/TRUNCATE/REPLACE) require
    mutate=true — an explicit acknowledgement that this changes data.

    instance: which sandbox instance to target (default: main).
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
    return _wpcli(["db", "query", sql], instance=instance)


@mcp.tool()
def tail_log(lines: int = 100, instance: str = DEFAULT_INSTANCE) -> dict:
    """Tail wp-content/debug.log for one instance (default: main)."""
    log_path = _log_path(instance)
    if not log_path.exists():
        return {"ok": True, "lines": [], "note": "debug.log not yet created",
                "path": str(log_path)}
    try:
        data = log_path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        return {"ok": True, "lines": text.splitlines()[-lines:],
                "path": str(log_path)}
    except OSError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def fs_read(path: str, max_bytes: int = 200_000,
            instance: str = DEFAULT_INSTANCE) -> dict:
    """Read a file under runtime/wp-<instance>/ (the WordPress install).

    path is relative to runtime/wp-<instance>/ — e.g. 'wp-content/themes/my-theme/style.css'.
    Refuses paths that escape the WP root.
    instance: which sandbox instance (default: main).
    """
    wp_root = _wp_root(instance)
    target = _safe_resolve(path, wp_root)
    if target is None:
        return {"ok": False, "error": f"path escapes WP root: {path!r}"}
    if not target.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if not target.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    data = target.read_bytes()[:max_bytes]
    try:
        return {"ok": True, "path": str(target.relative_to(wp_root)),
                "size": target.stat().st_size,
                "truncated": target.stat().st_size > max_bytes,
                "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"ok": True, "path": str(target.relative_to(wp_root)),
                "size": target.stat().st_size, "binary": True,
                "note": "binary file; use wp_exec to inspect"}


@mcp.tool()
def fs_write(path: str, content: str, create_dirs: bool = True,
             instance: str = DEFAULT_INSTANCE) -> dict:
    """Write a file under runtime/wp-<instance>/. Creates parent dirs by default.

    Refuses paths that escape WP root. Returns bytes written.
    instance: which sandbox instance (default: main).
    """
    wp_root = _wp_root(instance)
    target = _safe_resolve(path, wp_root)
    if target is None:
        return {"ok": False, "error": f"path escapes WP root: {path!r}"}
    if create_dirs:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"ok": True, "path": str(target.relative_to(wp_root)),
            "bytes": len(content.encode("utf-8"))}


@mcp.tool()
def fs_list(path: str = "", depth: int = 1,
            instance: str = DEFAULT_INSTANCE) -> dict:
    """List files under runtime/wp-<instance>/<path>. depth=1 is shallow."""
    wp_root = _wp_root(instance)
    target = _safe_resolve(path or ".", wp_root)
    if target is None or not target.exists():
        return {"ok": False, "error": f"not found or escapes root: {path!r}"}
    out = []
    base_depth = len(target.parts)
    for p in target.rglob("*"):
        if len(p.parts) - base_depth > depth:
            continue
        out.append({
            "path": str(p.relative_to(wp_root)),
            "type": "dir" if p.is_dir() else "file",
            "size": p.stat().st_size if p.is_file() else None,
        })
        if len(out) >= 500:
            out.append({"note": "truncated at 500 entries"})
            break
    return {"ok": True, "root": str(target.relative_to(wp_root)), "entries": out}


# ------------------ Mailpit (test SMTP inbox) ------------------------

def _mailpit_url(instance: str) -> str:
    if instance == DEFAULT_INSTANCE and os.environ.get("MAILPIT_URL"):
        return os.environ["MAILPIT_URL"]
    port = _resolve_instance(instance)["mailpit_port"]
    return f"http://localhost:{port}"


@mcp.tool()
def mail_list(limit: int = 20, instance: str = DEFAULT_INSTANCE) -> dict:
    """List the most recent messages caught by Mailpit (test SMTP)."""
    base = _mailpit_url(instance).rstrip("/")
    try:
        r = httpx.get(f"{base}/api/v1/messages",
                      params={"limit": limit}, timeout=10.0)
        return {"ok": r.is_success, "status": r.status_code,
                "body": _safe_json(r.text)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def mail_get(message_id: str, instance: str = DEFAULT_INSTANCE) -> dict:
    """Get a single message from Mailpit (headers, text, html)."""
    base = _mailpit_url(instance).rstrip("/")
    try:
        r = httpx.get(f"{base}/api/v1/message/{message_id}", timeout=10.0)
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
              max_bytes: int = 16_000,
              instance: str = DEFAULT_INSTANCE) -> dict:
    """Return the currently-focused plugin's slug, source path, CLAUDE.md
    content, and any skill packs it ships (so Claude can read them on demand).

    Works for ANY plugin — looks for `CLAUDE.md` and `.claude/skills/*/SKILL.md`
    inside the focused plugin's source repo. No plugin name is hardcoded.

    Devs set focus with `./sb focus <slug>`. Claude should default
    file edits, debugging, and questions to that plugin's repo, and should
    read any `available_skills[*]` SKILL.md that's relevant to the task.

    instance: which sandbox instance (default: main). Focus + active project
    are per-instance.
    """
    ff = _focus_file(instance)
    af = _active_file(instance)
    focus = ff.read_text().strip() if ff.exists() else None
    active = af.read_text().strip() if af.exists() else None
    out = {"ok": True, "instance": instance, "focus": focus,
           "active_project": active,
           "source_path": None, "claude_md": None, "available_skills": []}
    if not focus:
        return out

    # Defensive check: warn when focus and active_project disagree (focused
    # on a plugin that isn't in the active project's plugin list). Common
    # cause: user ran `./sb use <project-A>` then `./sb focus <plugin-not-in-A>`
    # before the auto-link landed. Tells the agent the state is drifty.
    if active:
        try:
            import yaml as _yaml
            cfg_path = SANDBOX_ROOT / "sandbox.yml"
            local_path = SANDBOX_ROOT / "sandbox.local.yml"
            cfg_data = {}
            for p in (cfg_path, local_path):
                if p.exists():
                    loaded = _yaml.safe_load(p.read_text()) or {}
                    # Shallow merge — local overrides
                    projs = (loaded.get("projects") or {})
                    cfg_data.setdefault("projects", {}).update(projs)
            proj = (cfg_data.get("projects") or {}).get(active) or {}
            active_slugs = [(pl or {}).get("slug")
                            for pl in (proj.get("plugins") or [])]
            if active_slugs and focus not in active_slugs:
                out["mismatch_warning"] = (
                    f"focus '{focus}' isn't in active_project '{active}'"
                    f" (which contains {active_slugs}). Run `./sb focus"
                    f" {focus}` to auto-switch the active project."
                )
        except Exception:
            # Don't break focus_get over a config-read error.
            pass

    # Resolve focused plugin's source repo. Symlinks now live at depth 1
    # inside wp-content/plugins/ (was runtime/plugins/ — depth 2 — pre-fix).
    candidates = [
        _wp_root(instance) / "wp-content" / "plugins" / focus,
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
def focus_set(plugin_slug: str,
              instance: str = DEFAULT_INSTANCE) -> dict:
    """Set the focused plugin slug. Pass empty string to clear.

    When setting a focus, this shells out to `./sb focus <slug>` so the
    CLI's auto-link logic runs: if the focused plugin isn't already in
    the active project's plugin list, the active project is switched
    to one that contains it. Keeps focus + active_project consistent.

    instance: which sandbox instance's focus to set (default: main).
    """
    ff = _focus_file(instance)
    af = _active_file(instance)
    if not plugin_slug:
        if ff.exists():
            ff.unlink()
        return {"ok": True, "instance": instance, "focus": None}

    slug = plugin_slug.strip()
    sb = SANDBOX_ROOT / "sb"
    if not sb.exists():
        # Fallback: just write the file without the auto-link niceties.
        ff.write_text(slug)
        return {"ok": True, "instance": instance, "focus": slug,
                "warning": "./sb not found — focus set without auto-link"}

    res = subprocess.run(
        [str(sb), "--instance", instance, "focus", slug],
        capture_output=True, text=True, cwd=str(SANDBOX_ROOT), timeout=120,
    )
    out = {
        "ok": res.returncode == 0,
        "instance": instance,
        "focus": ff.read_text().strip() if ff.exists() else None,
        "active_project": af.read_text().strip() if af.exists() else None,
        "stdout": res.stdout,
    }
    if res.stderr.strip():
        out["stderr"] = res.stderr
    return out


# ------------------ legacy convenience -------------------------------

@mcp.tool()
def activate_plugin(slug: str, instance: str = DEFAULT_INSTANCE) -> dict:
    """wp plugin activate <slug>. Slug must match the plugin folder name."""
    return _wpcli(["plugin", "activate", slug], instance=instance)


@mcp.tool()
def deactivate_plugin(slug: str, instance: str = DEFAULT_INSTANCE) -> dict:
    """wp plugin deactivate <slug>."""
    return _wpcli(["plugin", "deactivate", slug], instance=instance)


@mcp.tool()
def import_content(seed_file: str, authors: str = "create",
                   instance: str = DEFAULT_INSTANCE) -> dict:
    """Import a WXR XML from runtime/seeds/. Pass just the filename."""
    return _wpcli(["import", f"/seeds/{seed_file}",
                   f"--authors={authors}"], instance=instance, timeout=180)


# ----------------------------- headless browser ------------------------

VISIT_SCRIPT = SANDBOX_ROOT / "tools" / "visit" / "visit.py"
TOOLS_VENV_PY = SANDBOX_ROOT / "runtime" / ".venv-tools" / "bin" / "python"


@mcp.tool()
def visit(url: str, login: bool = False, check_iframes: bool = False,
          screenshot: str | None = None, full_page: bool = False,
          timeout: int = 20, width: int = 1280, height: int = 800,
          wait_until: str = "domcontentloaded",
          instance: str = DEFAULT_INSTANCE) -> dict:
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
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=max(timeout + 30, 60),
                             cwd=str(SANDBOX_ROOT))
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


# ----------------------------- sandbox context loaders -----------------

SANDBOX_CLAUDE_MD = SANDBOX_ROOT / "CLAUDE.md"
SANDBOX_SKILLS_DIR = SANDBOX_ROOT / "skills"
SANDBOX_WORKFLOWS_DIR = SANDBOX_ROOT / "workflows"


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


def _list_sandbox_workflows() -> list[dict]:
    out = []
    if not SANDBOX_WORKFLOWS_DIR.is_dir():
        return out
    for entry in sorted(SANDBOX_WORKFLOWS_DIR.iterdir()):
        wf_md = entry / "WORKFLOW.md"
        if entry.is_dir() and wf_md.is_file():
            meta = _parse_skill_metadata(wf_md)
            out.append({
                "name": meta["name"] or entry.name,
                "description": meta["description"],
                "path": str(wf_md.relative_to(SANDBOX_ROOT)),
            })
    return out


@mcp.tool()
def load_workflow(name: str) -> dict:
    """Return the full text of a top-level sandbox workflow (WORKFLOW.md).

    Workflows are multi-phase playbooks (vs. skills, which are reflexes).
    Use this when the situation calls for a deliberate multi-stage process
    with user gates between phases — e.g. `load_workflow('build-feature')`
    before starting a net-new feature, so you run the
    establish → plan → build loop with explicit confirmation at each gate.

    Workflow names match the directories under sandbox/workflows/.
    """
    wf_md = SANDBOX_WORKFLOWS_DIR / name / "WORKFLOW.md"
    if not wf_md.is_file():
        return {
            "ok": False,
            "error": f"no workflow '{name}' (looked at {wf_md})",
            "available_workflows": [w["name"] for w in _list_sandbox_workflows()],
        }
    return {
        "ok": True,
        "name": name,
        "path": str(wf_md.relative_to(SANDBOX_ROOT)),
        "content": wf_md.read_text(errors="replace"),
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
def build_feature(task: str = "") -> str:
    """Engage the three-phase feature-building workflow (workflows/build-feature/WORKFLOW.md).

    Generic across plugins. Phase 1 (ESTABLISH) captures spec + impact +
    edge cases; Phase 2 (PLAN) audits reuse + slices for de-risk; Phase 3
    (BUILD) executes slice-by-slice with live verification. User gates
    between each phase.
    """
    wf_md = SANDBOX_WORKFLOWS_DIR / "build-feature" / "WORKFLOW.md"
    if not wf_md.is_file():
        return f"Workflow 'build-feature' not found at {wf_md}."
    body = wf_md.read_text(errors="replace")
    header = (
        "The user has invoked the `build-feature` workflow. Follow its "
        "three-phase contract for the rest of this conversation. Do NOT "
        "skip ahead to Phase 3 — Phase 1 and Phase 2 each end with a "
        "structured block that you wait on user sign-off for.\n\n"
    )
    if task:
        header += f"FEATURE REQUEST FROM USER:\n{task}\n\n"
    return header + "--- WORKFLOW CONTRACT ---\n\n" + body


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
