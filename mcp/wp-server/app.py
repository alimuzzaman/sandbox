from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



__all__ = ['COMPOSE_DIR', 'PROXY_CADDYFILE', 'PROXY_CERTS_DIR', 'PROXY_COMPOSE', 'PROXY_DIR', 'PROXY_PROJECT', 'PROXY_TLD', 'SANDBOX_CLAUDE_MD', 'SANDBOX_INSTRUCTIONS', 'SANDBOX_ROOT', 'SANDBOX_SKILLS_DIR', 'SANDBOX_WORKFLOWS_DIR', 'TOOLS_VENV_PY', 'VISIT_SCRIPT', '_HERD_BIN_DIR', '_active_file', '_admin_creds', '_compose', '_compose_file', '_core', '_find_focus_instances', '_focus_file', '_herd_host_env', '_herd_php_bin', '_host_php_bin', '_host_run', '_host_wp_bin', '_instance_php_version', '_instance_server', '_is_herd', '_list_sandbox_skills', '_list_sandbox_workflows', '_load_sandbox_yml', '_log_path', '_mailpit_url', '_parse_skill_metadata', '_project_instance', '_project_name', '_proxy_container_running', '_require_project_capability', '_resolve_instance', '_run_sandbox_json', '_safe_json', '_safe_resolve', '_sandbox_proxy_active', '_site_url', '_skill_prompt_body', '_valet_proxy_active', '_wp_root', '_wpcli', '_wpcli_shell', 'mcp']



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

MCP-first model: the CLI registers ONE `sandbox` server (no per-instance env
binding). Every tool takes `project_dir` and resolves the target instance from
the on-disk registry (sandbox_core) per call, so a single registration serves
every project from any directory. Pass `instance=` to override the resolved
instance for a single call.
"""

SANDBOX_ROOT = Path(__file__).resolve().parents[2]


# Per-user base for ALL machine-state (spec 009). SANDBOX_ROOT stays = the repo
# checkout (for CODE assets: skills, workflows, CLAUDE.md, visit.py, sandbox_core
# import); STATE paths derive from SANDBOX_HOME instead. The CLI (_paths.py) and
# sandbox_core resolve this identically — same env, same default — so the two
# processes never disagree about where state lives.
def _sandbox_base() -> Path:
    return Path(os.environ.get("SANDBOX_HOME", "~/sandbox")).expanduser().resolve()


def _runtime_dir() -> Path:
    """$SANDBOX_HOME/runtime, with a backward-compat fallback to the pre-009
    in-repo runtime until `sb migrate` runs. SANDBOX_RUNTIME (tests) wins."""
    explicit = os.environ.get("SANDBOX_RUNTIME")
    if explicit:
        return Path(explicit)
    new = _sandbox_base() / "runtime"
    legacy = SANDBOX_ROOT / "runtime"
    if not (new / "registry.json").exists() and (legacy / "registry.json").exists():
        return legacy
    return new


RUNTIME_DIR = _runtime_dir()


def _local_yml_path() -> Path:
    """sandbox.local.yml under the base, falling back to the repo root (pre-009)."""
    new = _sandbox_base() / "sandbox.local.yml"
    return new if new.exists() else SANDBOX_ROOT / "sandbox.local.yml"


COMPOSE_DIR = RUNTIME_DIR / "compose"

PROXY_TLD = "tst"

PROXY_DIR = RUNTIME_DIR / "proxy"

PROXY_CERTS_DIR = PROXY_DIR / "certs"

PROXY_CADDYFILE = PROXY_DIR / "Caddyfile"

PROXY_COMPOSE = PROXY_DIR / "proxy.yml"

PROXY_PROJECT = "sandbox-proxy"

def _core():
    """Import the shared sandbox_core (config + registry). SANDBOX_ROOT is the
    sandbox install dir, so this resolves regardless of the server's cwd."""
    import sys
    if str(SANDBOX_ROOT) not in sys.path:
        sys.path.insert(0, str(SANDBOX_ROOT))
    import sandbox_core
    return sandbox_core

def _project_instance(project_dir: str, label: str | None = None):
    """Resolve a project dir (+ optional label) to its instance NAME.

    label=None resolves the sole/default instance for that root (identical to
    pre-multi-instance behavior when there's only one). When the root has
    several instances and none is default, or `label` names one that doesn't
    exist, returns a structured "which one?" error listing the real labels.

    Returns (name, None) when an instance exists, or (None, error_dict)
    otherwise. The error is returned (not raised) so tools surface it cleanly
    to the agent.
    """
    sc = _core()
    try:
        root = str(sc.find_project_root(project_dir))
    except Exception as e:  # ConfigError / bad path
        return None, {"ok": False, "error": f"invalid project_dir {project_dir!r}: {e}"}
    entries = sc.registry_list_for_root(root)
    if not entries:
        return None, {
            "ok": False,
            "error": f"no sandbox instance for project '{root}'. "
                     f"Call ensure_instance(project_dir={project_dir!r}) first.",
        }
    if label:
        entry = next((e for e in entries if e.get("label") == label), None)
        if not entry:
            return None, {
                "ok": False,
                "error": f"no instance labelled '{label}' for '{root}'. "
                         f"Labels: {[e['label'] for e in entries]}",
            }
    else:
        # Preserve the historical default-instance behavior while keeping
        # multi-instance roots deterministic: prefer the explicit default
        # label, otherwise resolve a sole entry, and refuse ambiguity.
        entry = next((e for e in entries if e.get("label") == "default"), None)
        if entry is None and len(entries) == 1:
            entry = entries[0]
        if entry is None:
            return None, {
                "ok": False,
                "error": f"project '{root}' has multiple instances; pass label",
                "labels": [e.get("label") for e in entries],
            }
    return entry["instance"], None


def _require_project_capability(project_dir: str, label: str | None, capability: str):
    """Return an MCP error before tool-specific helpers run, or None."""
    # The MCP server is normally launched from ``mcp/wp-server`` (or an
    # installed wrapper), so the repository root is not guaranteed to be on
    # Python's import path.  Capability-gated tools still use the shared
    # runtime package; make that import location explicit before importing it.
    import sys
    if str(SANDBOX_ROOT) not in sys.path:
        sys.path.insert(0, str(SANDBOX_ROOT))
    from sandbox.application.context import wordpress_runtime_service
    from sandbox.core._config import load_config

    try:
        error = wordpress_runtime_service(load_config()).check(
            project_dir, capability, label=label or "default"
        )
    except Exception as exc:
        return {"ok": False, "error": f"project capability resolution failed: {exc}"}
    if error is None:
        return None
    return {
        "ok": False,
        "error": error.message,
        "code": error.code,
        "project_kind": error.project_kind,
        "required_capability": capability,
        "available_capabilities": list(error.available_capabilities),
    }
    if len(entries) == 1:
        return entries[0]["instance"], None
    default = next((e for e in entries if e.get("is_default")), None)
    if default:
        return default["instance"], None
    return None, {
        "ok": False,
        "error": f"'{root}' has multiple instances and none is default; pass label=. "
                 f"Labels: {[e['label'] for e in entries]}",
    }

def _wp_root(instance: str) -> Path:
    return RUNTIME_DIR / f"wp-{instance}"

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

_cfg_cache: dict = {"mtime": 0.0, "data": None}

def _load_sandbox_yml() -> dict:
    """Read sandbox.yml (+ sandbox.local.yml override) for instance lookups.

    Cached on mtime so per-tool-call cost stays near-zero. Tools that
    need per-instance config (ports, admin) call _resolve_instance(name).
    """
    cfg_path = SANDBOX_ROOT / "sandbox.yml"
    local_path = _local_yml_path()
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
    """Return per-instance ports/admin/app_password, resolved from sandbox.yml
    (+ sandbox.local.yml). The app password is read from the per-instance
    instances.<name>.app_password key (per-project model — no global key).
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
        # Optional custom local domain (e.g. xx.tst) served by the sandbox
        # proxy. _site_url() turns this into the clean no-port browser URL.
        "domain": inst.get("domain"),
        # Local proxy TLD (sandbox.config.json `tld`, persisted into the block by
        # sb's _build_instance_block). Default tst. Used to detect proxy domains.
        "tld": inst.get("tld", PROXY_TLD),
    }

    # App password: the instance's own instances.<name>.app_password.
    out["app_password"] = inst.get("app_password", "")
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
      • http://<domain>         — proxy serves this .tst domain (clean, no port)
      • http://<domain>         — legacy Valet proxy (no port)
      • http://localhost:<port> — domain set but proxy NOT serving it, or no domain

    A .tst domain only resolves while the proxy + its *.tst DNS are up. When a
    domain is set but the proxy isn't serving it (down / DNS missing / lo0 alias
    dropped after reboot), fall back to localhost:<port> — NOT <domain>:<port>,
    which never resolves on a clean box and hangs the browser ("loading
    forever"). Mirrors sb.site_url().
    """
    port = inst_cfg["wordpress_port"]
    dom = inst_cfg.get("domain")
    tld = inst_cfg.get("tld") or PROXY_TLD
    if dom and dom.endswith(f".{tld}") and _sandbox_proxy_active(dom):
        cert = PROXY_CERTS_DIR / f"{dom}.pem"
        return f"https://{dom}" if cert.exists() else f"http://{dom}"
    if dom and _valet_proxy_active(dom):
        return f"http://{dom}"
    return f"http://localhost:{port}"

def _admin_creds() -> tuple[str, str]:
    rt = (_load_sandbox_yml().get("runtime") or {}).get("admin") or {}
    return rt.get("user", "admin"), rt.get("password", "admin")

SANDBOX_INSTRUCTIONS = """You're connected to the WPDeveloper Sandbox — a per-project runtime driven by MCP tools (ensure_instance, instance_status, instance_logs, instance_exec, wp_cli, wp_rest, db_query, tail_log, run_tests, visit, fs_read, ...).

PROJECT HANDSHAKE — this is mandatory:
- Every tool takes `project_dir`. ALWAYS pass your current working directory (or the plugin's project root if you can determine it — the dir holding sandbox.config.* / .wp-env.json / .git). The server is a separate process; it cannot see your cd, so it relies on this.
- Before using any stack tool, call `ensure_instance(project_dir=...)`. It returns the instance + URL, booting one on demand if needed (may take ~1 min the first time). Other tools error with "call ensure_instance first" until then.
- Generic PHP, JavaScript/Node, Docker-native, Laravel/Sail, Astro, and similar projects use the framework-neutral Compose adapter. Use `instance_status`, `instance_logs`, and `instance_exec` for them; WordPress-only tools fail closed before side effects.
- One project directory ↔ one-or-more instances (per worktree) — a root normally has exactly one (unchanged behavior); pass `label=` to target or mint an ADDITIONAL instance of the same root (e.g. 'qa', 'php81') for side-by-side testing. Omit `label` for the default/sole instance. focus_get(project_dir) returns the project's plugin + its CLAUDE.md.

ACTIVATION: engage when the user wants to run/test a plugin, names a WPDeveloper plugin, or hits a WP error / stack trace / wp-admin issue. Stay quiet on non-WP work.

ADMIN ACCESS: the sandbox WP is yours — full admin via wp_cli (in-container), wp_rest (app pw), visit (auto-login on wp-admin). Creds pre-wired; never ask.

REFLEXES when engaged:
- Bug / error / stack trace / "doesn't work" → first call ensure_instance, then REPRODUCE on the live stack. Lightest tool: PHP/REST/SQL/cron → wp_cli/wp_rest/db_query/tail_log; browser-rendered → visit; tests → run_tests. Can't reproduce → BLOCKED.
- Any WP action → MCP tool, never raw bash / docker / curl / mysql.
- About to mutate DB / migrate / touch licensing → snapshot first.
- About to commit / push / tag / open PR → STOP, wait for user.

DEEPER CONTEXT: load_context (full guide); load_skill(name) for fix/bug-repro/snapshot/wp-debug/wp-pilot; load_workflow('build-feature') for new features.

CONFIG KEYS (sandbox.config.json / sandbox.config.override.json):
- plugins: install + activate (slugs, zip URLs, or "." for project root)
- mappings: mount as symlink + activate (wp-path → host path; "." resolved relative to project root)
- mappings_inactive: mount as symlink but do NOT activate — use for pro plugins that FSI/imports should activate on demand
- user-global layer: $SANDBOX_HOME/config.json (default ~/sandbox/config.json) applies to EVERY project (under the project: project wins scalars, lists/dicts union). Declare a shared Pro plugin once as mappings_inactive there; absolute/~ host paths only.

MACHINE-STATE BASE (spec 009): all generated state + per-machine config/secrets live under one base $SANDBOX_HOME (default ~/sandbox), NOT in the repo. CLI + this server resolve the same base. `./sb migrate --apply` relocates a pre-009 in-repo setup; `./sb home <dir>` moves the base. Until migrated, a fallback reads the old in-repo locations so nothing breaks.

ANTI-PATTERNS — catch yourself:
- "FIXED" from code reading. Only live MCP calls count as evidence.
- Bug-fix slicing (edit, test, edit, test) — use load_skill('fix'): read all, batch edits, verify once.
- Bash where an MCP tool exists.
- 3 clarifying questions — pick likeliest interpretation, work, flag the assumption.

Output: terse, evidence-first, no "I'll now do X" preamble, code refs as markdown links.
"""

mcp = FastMCP("sandbox", instructions=SANDBOX_INSTRUCTIONS)

def _compose(*args: str, instance: str,
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

def _instance_server(instance: str) -> str:
    """The instance's web server (apache|nginx|litespeed|herd). Read from the
    registry (written by ensure_instance); falls back to the sandbox.local.yml
    instance block. 'herd' means HOST-served (Herd + host MySQL, no docker)."""
    entry = _core().registry_find_instance(instance)
    if entry:
        return entry.get("server") or "nginx"
    blk = (_load_sandbox_yml().get("instances", {}) or {}).get(instance, {}) or {}
    return blk.get("server", "nginx")

def _is_herd(instance: str) -> bool:
    return _instance_server(instance) == "herd"

def _host_run(cmd: list[str], timeout: int = 60, cwd: Path | None = None,
              env: dict | None = None) -> dict:
    """Run a host command (herd instances) with the same result shape as
    _compose, so every caller is runtime-agnostic."""
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd) if cwd else str(SANDBOX_ROOT),
            env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s", "cmd": cmd}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"host command not found: {e}"}
    return {"ok": res.returncode == 0, "code": res.returncode,
            "stdout": res.stdout, "stderr": res.stderr}

def _host_wp_bin() -> str:
    import shutil as _shutil
    return _shutil.which("wp") or "/usr/local/bin/wp"

_HERD_BIN_DIR = Path(os.environ.get(
    "SANDBOX_HERD_BIN_DIR",
    str(Path.home() / "Library" / "Application Support" / "Herd" / "bin")))

def _host_php_bin() -> str:
    import shutil as _shutil
    return _shutil.which("php") or "/usr/bin/php"

def _instance_php_version(instance: str):
    """The pinned php_version for an instance (from registry/sandbox.local.yml),
    or None when unpinned."""
    entry = _core().registry_find_instance(instance)
    if entry and entry.get("php_version"):
        return entry["php_version"]
    blk = (_load_sandbox_yml().get("instances", {}) or {}).get(instance, {}) or {}
    return blk.get("php_version")

def _herd_php_bin(instance: str) -> str:
    """The PHP binary a herd instance should run CLI under: its pinned
    php_version resolved to Herd's version-specific binary (8.1 → php81).
    Falls back to host php when unpinned or the binary isn't installed."""
    php_v = _instance_php_version(instance)
    if php_v in (None, ""):
        return _host_php_bin()
    digits = _re.sub(r"[^0-9]", "", str(php_v).strip())  # "8.1" → "81"
    if digits:
        cand = _HERD_BIN_DIR / f"php{digits}"
        if cand.exists():
            return str(cand)
    return _host_php_bin()

def _herd_host_env(instance: str) -> dict:
    """Env for arbitrary host shell commands (wp_exec / _wpcli_shell) on a herd
    instance: a per-instance shim dir is prepended to PATH so bare `php` and
    `wp` resolve to the instance's PINNED PHP. Without this, a `php …` typed in
    wp_exec would hit Herd's default php. Idempotent; shims live under runtime/."""
    php = _herd_php_bin(instance)
    env = dict(os.environ)
    if php == _host_php_bin():
        return env  # unpinned — nothing to override
    shim = RUNTIME_DIR / "herd-shims" / instance
    shim.mkdir(parents=True, exist_ok=True)
    php_shim = shim / "php"
    php_shim.write_text(f'#!/bin/sh\nexec "{php}" "$@"\n')
    php_shim.chmod(0o755)
    wp_shim = shim / "wp"
    wp_shim.write_text(f'#!/bin/sh\nexec "{php}" "{_host_wp_bin()}" "$@"\n')
    wp_shim.chmod(0o755)
    env["PATH"] = f"{shim}:{env.get('PATH', '')}"
    return env

_WP_CLI_BUILTIN: dict[str, bool] = {}  # instance → built-in wp present (per process)


def _wp_has_builtin_cli(instance: str) -> bool:
    """True if the instance's running `wp` container has the mounted wp binary
    (built-in wp-cli). Doubles as a running-check; positive results are cached."""
    if _WP_CLI_BUILTIN.get(instance):
        return True
    r = _compose("exec", "-T", "wp", "test", "-f", "/usr/local/bin/wp",
                 instance=instance, timeout=20)
    ok = isinstance(r, dict) and r.get("code") == 0
    if ok:
        _WP_CLI_BUILTIN[instance] = True
    return ok


def _wpcli(args: list[str], instance: str,
           timeout: int = 60) -> dict:
    if _is_herd(instance):
        # Run wp-cli (a phar) under the instance's PINNED PHP, not the phar's
        # default `php`, so wp_cli executes on the project's PHP version.
        return _host_run(
            [_herd_php_bin(instance), _host_wp_bin(),
             f"--path={_wp_root(instance)}", *args],
            timeout=timeout)
    # `wp db ...` shells out to the mysql/mysqldump client, absent from the fpm
    # (nginx) web image — only the dedicated `wpcli` service ships it. Route DB
    # subcommands there so db_query etc. work on every server tier (the
    # exec-into-web path fails with "env: 'mysql': No such file or directory").
    needs_db_client = bool(args) and args[0] == "db"
    # Built-in wp-cli: exec the mounted phar in the running wp container as
    # www-data (no per-call container). Fall back to the one-shot wpcli service.
    if _wp_has_builtin_cli(instance) and not needs_db_client:
        return _compose("exec", "-u", "www-data", "-T", "wp", "wp", *args,
                        instance=instance, timeout=timeout)
    return _compose("run", "--rm", "wpcli", *args,
                    instance=instance, timeout=timeout)

def _wpcli_shell(shell_cmd: str, instance: str,
                 timeout: int = 60) -> dict:
    """Run a wp-cli command through sh -c so $(...) / pipes work. For herd
    instances the shell runs on the host with cwd at the WP root; PATH is
    prepended with a shim dir exposing `php`/`wp` bound to the pinned PHP so a
    bare `wp …`/`php …` inside the command resolves the project's version."""
    if _is_herd(instance):
        return _host_run(["sh", "-c", shell_cmd], timeout=timeout,
                         cwd=_wp_root(instance),
                         env=_herd_host_env(instance))
    if _wp_has_builtin_cli(instance):
        return _compose("exec", "-u", "www-data", "-T", "wp", "sh", "-c", shell_cmd,
                        instance=instance, timeout=timeout)
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


def _run_sandbox_json(command: list[str], timeout: int) -> dict:
    """Run a sandbox subprocess and normalize its JSON boundary."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {
            "timed_out": True, "returncode": None,
            "stdout": "", "stderr": "", "payload": None,
        }

    payload = None
    for line in reversed((result.stdout or "").splitlines()):
        candidate = _safe_json(line)
        if isinstance(candidate, (dict, list)):
            payload = candidate
            break
    return {
        "timed_out": False, "returncode": result.returncode,
        "stdout": result.stdout or "", "stderr": result.stderr or "",
        "payload": payload,
    }

def _mailpit_url(instance: str) -> str:
    port = _resolve_instance(instance)["mailpit_port"]
    return f"http://localhost:{port}"

def _parse_skill_metadata(skill_md: Path) -> dict:
    """Pull `name:` and `description:` out of a SKILL.md's YAML frontmatter.

    Tolerant of missing/malformed frontmatter — returns empty strings then.
    """
    name, description, enable = "", "", True
    try:
        text = skill_md.read_text(errors="replace")
    except OSError:
        return {"name": name, "description": description, "enable": enable}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
                elif line.startswith("enable:"):
                    enable = line.split(":", 1)[1].strip().lower() not in ("false", "0", "no")
    return {"name": name, "description": description, "enable": enable}

VISIT_SCRIPT = SANDBOX_ROOT / "tools" / "visit" / "visit.py"

TOOLS_VENV_PY = RUNTIME_DIR / ".venv-tools" / "bin" / "python"

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
