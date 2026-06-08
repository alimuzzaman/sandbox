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

Multi-instance: the CLI registers ONE server per sandbox instance, baking
SANDBOX_INSTANCE (+ that instance's WP_URL / WP_APP_PASSWORD / MAILPIT_URL)
into each registration's env. So every tool defaults to THIS server's
instance (SESSION_INSTANCE) instead of always `main`, and concurrent Claude
sessions on different instances never collide on focus/active-project state.
Pass `instance=` explicitly to override per call.
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

# Sandbox HTTPS proxy — mirrors the constants in `sb` so _site_url() can
# resolve the clean no-port browser URL (https://<inst>.sb) instead of a
# bare localhost:<port>. Keep in sync with sb's PROXY_* block.
PROXY_TLD = "sb"
PROXY_DIR = SANDBOX_ROOT / "runtime" / "proxy"
PROXY_CERTS_DIR = PROXY_DIR / "certs"
PROXY_CADDYFILE = PROXY_DIR / "Caddyfile"
PROXY_COMPOSE = PROXY_DIR / "proxy.yml"
PROXY_PROJECT = "sandbox-proxy"
DEFAULT_INSTANCE = "main"


# MCP-first model: ONE server. Every tool takes `project_dir` and resolves the
# target instance from the on-disk registry (sandbox_core). There is no
# SANDBOX_INSTANCE env binding and no per-instance server.

def _core():
    """Import the shared sandbox_core (config + registry). SANDBOX_ROOT is the
    sandbox install dir, so this resolves regardless of the server's cwd."""
    import sys
    if str(SANDBOX_ROOT) not in sys.path:
        sys.path.insert(0, str(SANDBOX_ROOT))
    import sandbox_core
    return sandbox_core


def _project_instance(project_dir: str):
    """Resolve a project dir to its instance NAME.

    Returns (name, None) when an instance exists, or (None, error_dict) when the
    path is invalid or no instance has been created yet. The error is returned
    (not raised) so tools surface it cleanly to the agent.
    """
    sc = _core()
    try:
        root = str(sc.find_project_root(project_dir))
    except Exception as e:  # ConfigError / bad path
        return None, {"ok": False, "error": f"invalid project_dir {project_dir!r}: {e}"}
    entry = sc.registry_get(root)
    if not entry or not entry.get("instance"):
        return None, {
            "ok": False,
            "error": f"no sandbox instance for project '{root}'. "
                     f"Call ensure_instance(project_dir={project_dir!r}) first.",
        }
    return entry["instance"], None

# Per-instance helpers — mirror the CLI's resolution.

def _wp_root(instance: str) -> Path:
    return SANDBOX_ROOT / "runtime" / f"wp-{instance}"


def _log_path(instance: str) -> Path:
    return _wp_root(instance) / "wp-content" / "debug.log"


def _focus_file(instance: str) -> Path:
    return SANDBOX_ROOT / f".focus.{instance}"


def _active_file(instance: str) -> Path:
    return SANDBOX_ROOT / f".active-project.{instance}"


def _find_focus_instances(slug: str) -> list[str]:
    """Every instance whose .focus.<inst> == slug. Lets focus_get redirect a
    caller that hit the wrong server namespace (e.g. asked main about a plugin
    that's actually focused on the embedpress instance) to the right one."""
    hits = []
    for fp in SANDBOX_ROOT.glob(".focus.*"):
        try:
            if fp.read_text().strip() == slug:
                hits.append(fp.name[len(".focus."):])
        except OSError:
            pass
    return sorted(hits)


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
    server) for the instance THIS server is bound to (SESSION_INSTANCE) —
    each per-instance registration bakes that instance's WP_URL /
    WP_APP_PASSWORD into env, so the env-priming path below applies to
    whichever instance owns this process, not just `main`.
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
        # Optional custom local domain (e.g. xx.sb) served by the sandbox
        # proxy. _site_url() turns this into the clean no-port browser URL.
        "domain": inst.get("domain"),
    }

    # App password file fallback: for `main` the legacy
    # mcp.wp.application_password key; for any other instance, that
    # instance's own instances.<name>.app_password.
    if instance == DEFAULT_INSTANCE:
        file_app_pw = ((cfg.get("mcp") or {}).get("wp") or {}).get(
            "application_password", ""
        )
    else:
        file_app_pw = inst.get("app_password", "")

    out["app_password"] = file_app_pw
    return out


def _proxy_container_running() -> bool:
    """True if the sandbox-proxy Caddy container is up. Mirrors sb."""
    try:
        res = subprocess.run(
            ["docker", "compose", "-p", PROXY_PROJECT, "-f", str(PROXY_COMPOSE),
             "--project-directory", str(SANDBOX_ROOT), "ps", "-q", "proxy"],
            capture_output=True, text=True, timeout=10)
        return res.returncode == 0 and bool((res.stdout or "").strip())
    except Exception:
        return False


def _sandbox_proxy_active(domain: str) -> bool:
    """True when the proxy is running AND has a route for this domain. Mirrors
    sb._sandbox_proxy_active so the URL reported matches what `./sb instances`
    prints."""
    if not domain or not PROXY_CADDYFILE.exists():
        return False
    try:
        txt = PROXY_CADDYFILE.read_text()
    except OSError:
        return False
    if f"http://{domain} {{" not in txt and f"\n{domain} {{" not in txt:
        return False
    return _proxy_container_running()


def _valet_proxy_active(domain: str) -> bool:
    """True when legacy Valet serves a proxy for this domain. Mirrors sb."""
    if not domain:
        return False
    return (Path.home() / ".config" / "valet" / "Nginx" / domain).exists()


def _site_url(inst_cfg: dict) -> str:
    """The ACTUAL browser URL for an instance — the clean no-port proxy domain
    when one is serving, else localhost:<port>. Mirrors sb.site_url() precedence
    so MCP-reported URLs match `./sb instances`.

      • https://<domain>        — proxy serves it AND it's secured (cert)
      • http://<domain>         — proxy serves this .sb domain (clean, no port)
      • http://<domain>         — legacy Valet proxy (no port)
      • http://localhost:<port> — domain set but proxy NOT serving it, or no domain

    A .sb domain only resolves while the proxy + its *.sb DNS are up. When a
    domain is set but the proxy isn't serving it (down / DNS missing / lo0 alias
    dropped after reboot), fall back to localhost:<port> — NOT <domain>:<port>,
    which never resolves on a clean box and hangs the browser ("loading
    forever"). Mirrors sb.site_url().
    """
    port = inst_cfg["wordpress_port"]
    dom = inst_cfg.get("domain")
    if dom and dom.endswith(f".{PROXY_TLD}") and _sandbox_proxy_active(dom):
        cert = PROXY_CERTS_DIR / f"{dom}.pem"
        return f"https://{dom}" if cert.exists() else f"http://{dom}"
    if dom and _valet_proxy_active(dom):
        return f"http://{dom}"
    return f"http://localhost:{port}"


# Uniform admin creds (same across instances) for visit() auto-login —
# resolved from sandbox.yml runtime.admin, no per-instance env binding.

def _admin_creds() -> tuple[str, str]:
    rt = (_load_sandbox_yml().get("runtime") or {}).get("admin") or {}
    return rt.get("user", "admin"), rt.get("password", "admin")

# Server-level instructions — Claude Code (and other MCP clients) surface
# this in the model's system context automatically on connect. Capped at
# 2KB by Claude Code; keep it punchy. Full operating prompt lives in
# CLAUDE.md and is loadable on demand via load_context().
SANDBOX_INSTRUCTIONS = """You're connected to the WPDeveloper Sandbox — a per-project WordPress dev/test stack driven by MCP tools (ensure_instance, wp_cli, wp_rest, db_query, tail_log, run_tests, visit, fs_read, ...).

PROJECT HANDSHAKE — this is mandatory:
- Every tool takes `project_dir`. ALWAYS pass your current working directory (or the plugin's project root if you can determine it — the dir holding sandbox.config.* / .wp-env.json / .git). The server is a separate process; it cannot see your cd, so it relies on this.
- Before using any stack tool, call `ensure_instance(project_dir=...)`. It returns the instance + URL, booting one on demand if needed (may take ~1 min the first time). Other tools error with "call ensure_instance first" until then.
- One project directory ↔ one instance (per worktree). focus_get(project_dir) returns the project's plugin + its CLAUDE.md.

ACTIVATION: engage when the user wants to run/test a plugin, names a WPDeveloper plugin, or hits a WP error / stack trace / wp-admin issue. Stay quiet on non-WP work.

ADMIN ACCESS: the sandbox WP is yours — full admin via wp_cli (in-container), wp_rest (app pw), visit (auto-login on wp-admin). Creds pre-wired; never ask.

REFLEXES when engaged:
- Bug / error / stack trace / "doesn't work" → first call ensure_instance, then REPRODUCE on the live stack. Lightest tool: PHP/REST/SQL/cron → wp_cli/wp_rest/db_query/tail_log; browser-rendered → visit; tests → run_tests. Can't reproduce → BLOCKED.
- Any WP action → MCP tool, never raw bash / docker / curl / mysql.
- About to mutate DB / migrate / touch licensing → snapshot first.
- About to commit / push / tag / open PR → STOP, wait for user.

DEEPER CONTEXT: load_context (full guide); load_skill(name) for fix/bug-repro/snapshot/wp-debug/wp-pilot; load_workflow('build-feature') for new features.

ANTI-PATTERNS — catch yourself:
- "FIXED" from code reading. Only live MCP calls count as evidence.
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
    # --project-directory pins relative volume paths (./runtime/wp-<inst>) to the
    # sandbox root; without it docker resolves them against the compose file's
    # own dir (runtime/compose/) and the WP bind-mount silently misses.
    cmd = ["docker", "compose",
           "-p", _project_name(instance),
           "-f", str(cf),
           "--project-directory", str(SANDBOX_ROOT), *args]
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
def ensure_instance(project_dir: str) -> dict:
    """Ensure a sandbox WordPress instance exists for `project_dir`, creating it
    on demand, and return {ok, instance, url, ports, status, root, source}.

    project_dir: the plugin's project root (or your cwd). The server reads its
    sandbox.config.* / .wp-env.json, boots an instance keyed by that directory
    (one per worktree), installs WordPress, wires the plugin, and records it.
    **Call this FIRST** — other tools error until an instance exists. Idempotent:
    a ready project returns instantly; a cold boot pulls images + installs WP and
    can take ~1 minute.
    """
    sb = SANDBOX_ROOT / "sb"
    try:
        res = subprocess.run(
            [str(sb), "ensure", "--project-dir", project_dir, "--json"],
            capture_output=True, text=True, timeout=600, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ensure_instance timed out after 600s"}
    lines = (res.stdout or "").strip().splitlines()
    entry = _safe_json(lines[-1]) if lines else None
    if isinstance(entry, dict) and "instance" in entry:
        entry.setdefault("ok", True)
        return entry
    return {"ok": False, "code": res.returncode,
            "error": (res.stderr or res.stdout or "ensure failed").strip()[:1000]}


@mcp.tool()
def wp_cli(command: str, timeout: int = 60, *, project_dir: str) -> dict:
    """Run any wp-cli command. Pass the args after `wp` (e.g. 'plugin list').

    Note: this runs `wp <command>` directly. If you need shell features like
    `$(cat ...)`, pipes, or redirects, use wp_exec instead.

    project_dir: the plugin project to target (its instance must already exist —
    call ensure_instance first).
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    return _wpcli(shlex.split(command), instance=inst, timeout=timeout)


@mcp.tool()
def wp_exec(command: str, container: str = "wp", workdir: str | None = None,
            timeout: int = 120, *, project_dir: str) -> dict:
    """Run an arbitrary shell command inside a container (default `wp`).

    Use for composer, npm, node, php scripts, file ops, etc. Runs as the
    container's default user. Supports pipes, $(...) and redirects since
    it goes through `sh -c`.

    container: 'wp' (default), 'db', 'wpcli', or 'mailpit'.
    project_dir: the plugin project to target (call ensure_instance first).
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    args = ["exec"]
    if workdir:
        args += ["-w", workdir]
    args += ["-T", container, "sh", "-c", command]
    return _compose(*args, instance=inst, timeout=timeout)


@mcp.tool()
def wp_rest(method: str, path: str, body: dict | None = None,
            query: dict | None = None, *, project_dir: str) -> dict:
    """Call the WordPress REST API.

    path: e.g. '/wp/v2/posts' (leading slash optional)
    Auth via Application Password — auto-provisioned when the instance installs.
    project_dir: the plugin project to target (call ensure_instance first).
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    inst_cfg = _resolve_instance(inst)
    app_pw = inst_cfg["app_password"]
    if not app_pw:
        return {
            "ok": False,
            "error": f"no application_password for instance '{inst}'. "
                     f"Re-run ensure_instance(project_dir={project_dir!r}).",
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
def db_query(sql: str, mutate: bool = False, *, project_dir: str) -> dict:
    """Run a SQL query against the WP database.

    Reads (SELECT/SHOW/DESCRIBE/EXPLAIN) run freely.
    Writes (INSERT/UPDATE/DELETE/ALTER/CREATE/DROP/TRUNCATE/REPLACE) require
    mutate=true — an explicit acknowledgement that this changes data.

    project_dir: the plugin project to target (call ensure_instance first).
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
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
    return _wpcli(["db", "query", sql], instance=inst)


@mcp.tool()
def tail_log(lines: int = 100, *, project_dir: str) -> dict:
    """Tail wp-content/debug.log for the project's instance."""
    inst, err = _project_instance(project_dir)
    if err:
        return err
    log_path = _log_path(inst)
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
def fs_read(path: str, max_bytes: int = 200_000, *, project_dir: str) -> dict:
    """Read a file under the project instance's WordPress install.

    path is relative to the WP root — e.g. 'wp-content/themes/my-theme/style.css'.
    Refuses paths that escape the WP root.
    project_dir: the plugin project to target (call ensure_instance first).
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    wp_root = _wp_root(inst)
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
             *, project_dir: str) -> dict:
    """Write a file under the project instance's WordPress install. Creates
    parent dirs by default. Refuses paths that escape WP root. Returns bytes.

    project_dir: the plugin project to target (call ensure_instance first).
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    wp_root = _wp_root(inst)
    target = _safe_resolve(path, wp_root)
    if target is None:
        return {"ok": False, "error": f"path escapes WP root: {path!r}"}
    if create_dirs:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"ok": True, "path": str(target.relative_to(wp_root)),
            "bytes": len(content.encode("utf-8"))}


@mcp.tool()
def fs_list(path: str = "", depth: int = 1, *, project_dir: str) -> dict:
    """List files under the project instance's WP install. depth=1 is shallow."""
    inst, err = _project_instance(project_dir)
    if err:
        return err
    wp_root = _wp_root(inst)
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
    port = _resolve_instance(instance)["mailpit_port"]
    return f"http://localhost:{port}"


@mcp.tool()
def mail_list(limit: int = 20, *, project_dir: str) -> dict:
    """List the most recent messages caught by Mailpit (test SMTP)."""
    inst, err = _project_instance(project_dir)
    if err:
        return err
    base = _mailpit_url(inst).rstrip("/")
    try:
        r = httpx.get(f"{base}/api/v1/messages",
                      params={"limit": limit}, timeout=10.0)
        return {"ok": r.is_success, "status": r.status_code,
                "body": _safe_json(r.text)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def mail_get(message_id: str, *, project_dir: str) -> dict:
    """Get a single message from Mailpit (headers, text, html)."""
    inst, err = _project_instance(project_dir)
    if err:
        return err
    base = _mailpit_url(inst).rstrip("/")
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
def focus_get(project_dir: str, include_claude_md: bool = True,
              max_bytes: int = 16_000) -> dict:
    """Return the project's instance + focused plugin, its CLAUDE.md, and any
    skill packs it ships (so Claude can read them on demand).

    In the per-project model the project root IS the plugin's source repo, so
    this reads CLAUDE.md / .claude/skills/*/SKILL.md from `project_dir` directly.
    Requires the instance to exist — call ensure_instance first.
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    sc = _core()
    root = Path(sc.find_project_root(project_dir))
    inst_cfg = _resolve_instance(inst)
    wp_url = _site_url(inst_cfg)
    ff = _focus_file(inst)
    focus = ff.read_text().strip() if ff.exists() else root.name

    out = {
        "ok": True, "instance": inst, "project_root": str(root),
        "focus": focus,
        "wordpress_url": wp_url,
        "admin_url": f"{wp_url}/wp-admin",
        "mailpit_url": f"http://localhost:{inst_cfg['mailpit_port']}",
        "source_path": str(root), "claude_md": None, "available_skills": [],
    }

    if include_claude_md:
        for candidate in ("CLAUDE.md", ".claude/CLAUDE.md"):
            cmd = root / candidate
            if cmd.is_file():
                out["claude_md"] = cmd.read_bytes()[:max_bytes].decode(
                    "utf-8", errors="replace")
                out["claude_md_path"] = str(cmd)
                out["claude_md_truncated"] = cmd.stat().st_size > max_bytes
                break

    skills_dir = root / ".claude" / "skills"
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


# Note: the old cross-instance focus_set / focus_resolve handshake is removed —
# in the per-project model each project dir has exactly one instance, and
# ensure_instance presets focus to the project's plugin. focus_get(project_dir)
# is the only focus tool needed.


# ------------------ legacy convenience -------------------------------

@mcp.tool()
def activate_plugin(slug: str, *, project_dir: str) -> dict:
    """wp plugin activate <slug>. Slug must match the plugin folder name."""
    inst, err = _project_instance(project_dir)
    if err:
        return err
    return _wpcli(["plugin", "activate", slug], instance=inst)


@mcp.tool()
def deactivate_plugin(slug: str, *, project_dir: str) -> dict:
    """wp plugin deactivate <slug>."""
    inst, err = _project_instance(project_dir)
    if err:
        return err
    return _wpcli(["plugin", "deactivate", slug], instance=inst)


@mcp.tool()
def import_content(seed_file: str, authors: str = "create",
                   *, project_dir: str) -> dict:
    """Import a WXR XML from runtime/seeds/. Pass just the filename."""
    inst, err = _project_instance(project_dir)
    if err:
        return err
    return _wpcli(["import", f"/seeds/{seed_file}",
                   f"--authors={authors}"], instance=inst, timeout=180)


# ----------------------------- headless browser ------------------------

VISIT_SCRIPT = SANDBOX_ROOT / "tools" / "visit" / "visit.py"
TOOLS_VENV_PY = SANDBOX_ROOT / "runtime" / ".venv-tools" / "bin" / "python"


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
