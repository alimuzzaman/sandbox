from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr



__all__ = ['ACTIVE', 'ASKPASS_HELPER', 'CLI_VENV', 'COMPOSE', 'COMPOSE_DIR', 'CONFIG', 'CONFIG_LOCAL', 'CONNECT_TARGETS', 'DOMAIN_RE', 'ENTRY', 'FOCUS', 'HERD_BIN_DIR', 'HERD_CLI_DEFAULT', 'HERD_DB_HOST', 'HERD_DB_PASSWORD', 'HERD_DB_PORT', 'HERD_DB_USER', 'HOSTS_HELPER', 'INTROSPECT_PHP', 'LAUNCHD_PLIST', 'MCP_DIR', 'MCP_SERVER_NAME', 'MCP_VENV', 'MULTISITE_MARKER', 'PLUGINS_DIR', 'PROJECT_MCP_JSON', 'PROXY_BIND_IP', 'PROXY_CADDYFILE', 'PROXY_CERTS_DIR', 'PROXY_COMPOSE', 'PROXY_DIR', 'PROXY_HELPER', 'PROXY_PROJECT', 'PROXY_SUDOERS', 'PROXY_TLD', 'ROOT', 'SECRETS_ENV', 'SEEDS_DIR', 'SERVERS', 'SNAPSHOTS_DIR', 'SUDOERS_FILE', 'TESTS_DB_NAME', 'TEST_SUITE_DIR', 'TEST_TOOLS_DIR', 'TOOLS_DIR', 'TOOLS_VENV', 'WP_DIR', '_BASE_WP_CONFIG', '_CLAUDE_PRICES', '_COMPOSER_PHAR_URL', '_HTTPS_OFFER_MARKER', '_JobStream', '_PHPUNIT_PHAR_URL', '_POLYFILLS_REPO', '_POLYFILLS_TAG', '_RunResult', '_WEB_BUILDERS', '_WEB_CSS_CACHE', '_WEB_JS_CACHE', '_WEB_PAGE', '_WEB_STREAM', '_WPDEVELOP_REPO', '_active_project_name', '_assign_domains_to_all', '_autologin_mu_plugin', '_build_instance_block', '_build_mcp_entry', '_ca_installed', '_ca_trusted_macos', '_caddy_block', '_cert_paths', '_certs_changed_since_proxy_start', '_claude_projects_dir', '_cli_image', '_compose_no_follow_logs', '_config_extra_php', '_connect_fluentboards', '_connect_github', '_convert_multisite', '_core', '_cost_for', '_curses_suspended', '_cwd_instance', '_dash_draw', '_dash_flash', '_dash_pick', '_dash_prompt', '_dash_run', '_derive_instance_name', '_distinct_tlds', '_dns_flush', '_docker_preflight', '_download', '_ensure_litespeed_htaccess', '_ensure_proxy_up', '_ensure_test_tools', '_ensure_tests_db', '_ensure_url_proxy', '_ensure_wp_test_suite', '_env_config_lines', '_extra_vol_lines', '_force_symlink', '_gh_cli_orgs', '_gh_cli_user', '_git_q', '_global_link_dir', '_herd', '_herd_cli', '_herd_db_name', '_herd_domain', '_herd_isolate', '_herd_isolated_php', '_herd_php', '_herd_php_bin', '_herd_tests_db', '_herd_wp_cmd', '_host_php', '_host_wp', '_hosts_edit', '_hosts_passwordless', '_https_offer_declined', '_install_alias_launchd', '_instance_reachable', '_instance_running', '_is_herd_instance', '_is_server', '_job_snapshot', '_lo0_alias_present', '_local_yaml', '_make_venv', '_merged_wp_config', '_mint_cert', '_multisite_mode', '_next_free_port', '_norm_tld', '_offer_install', '_onboard_instance', '_php_literal', '_php_squote', '_pick_instance_ports', '_pin_db_creds_in_config', '_pin_wp_constants_in_config', '_pkg_manager', '_pkg_slug', '_plugins_home', '_port_busy_by_other', '_price_tier', '_prompt', '_provision_herd', '_provision_test_harness', '_proxy_container_running', '_proxy_started_at', '_proxy_sudoers_installed', '_refresh_env_local', '_relax_perms_for_uid_switch', '_resolve_port_conflicts', '_resolve_setup_tld', '_resolver_present', '_run_cmd_capture', '_run_tests', '_run_tests_herd', '_sandbox_proxy_active', '_secure_at_create', '_server_runtime', '_set_https_offer_declined', '_site_host', '_stale_mcp_servers', '_start_job', '_sudo', '_sudo_env', '_tld', '_valet_available', '_valet_proxy_active', '_valet_tld', '_valid_domain', '_valid_server', '_wait_http', '_wait_reachable', '_warn_version_drift', '_web_apache', '_web_css', '_web_do_action', '_web_image', '_web_job_seq', '_web_jobs_lock', '_web_js', '_web_list_seeds', '_web_list_snapshots', '_web_litespeed', '_web_lock', '_web_nginx', '_web_services', '_wildcard_san', '_wire_project_plugins', '_wire_project_themes', '_wp_debug_env', '_wpcli_service', '_write_env_local', '_write_local_yaml', '_write_mail_muplugin', '_write_multisite_htaccess', '_write_ssl_muplugin', '_write_wp_tests_config', '_write_wp_tests_config_herd', 'active_project_file', 'apply_config', 'claude_usage', 'collect_instance_rows', 'compose', 'compose_file', 'deep_merge', 'die', 'domains_ready', 'ensure_instance', 'ensure_pyyaml', 'ensure_tools_venv', 'expand', 'find_modern_python', 'focus_file', 'info', 'load_config', 'mcp_server_name', 'ok', 'plugins_dir', 'project_name', 'proxy_available', 'proxy_setup', 'proxy_teardown', 'regen_caddyfile', 'register_claude_user_scope', 'reload_proxy', 'render_compose', 'render_proxy_compose', 'resolve_instances', 'run', 'save_local_app_password', 'save_local_autologin_token', 'site_url', 'snapshots_dir', 'valet_proxy_add', 'valet_proxy_remove', 'wp_dir', 'wpcli', 'write_claude_mcp_config', 'write_compose_files', 'write_env_for_compose']
__all__ += ['BRIDGE_PORT', 'save_local_bridge_token', '_write_snapshot_muplugin',
            '_bridge_handle', '_bridge_token_for', '_ensure_bridge_server']
BRIDGE_PORT = 8765  # fixed host port the `sb web` snapshot bridge listens on



ROOT = Path(__file__).resolve().parent.parent

ENTRY = ROOT / "sb"  # the polyglot entry file, one level up from this package

CONFIG = ROOT / "sandbox.yml"

CONFIG_LOCAL = ROOT / "sandbox.local.yml"

COMPOSE = ROOT / "docker-compose.yml"

COMPOSE_DIR = ROOT / "runtime" / "compose"

ACTIVE = ROOT / ".active-project"

FOCUS = ROOT / ".focus"

WP_DIR = ROOT / "runtime" / "wp"

PLUGINS_DIR = WP_DIR / "wp-content" / "plugins"

SNAPSHOTS_DIR = ROOT / "runtime" / "snapshots"

SEEDS_DIR = ROOT / "runtime" / "seeds"

MCP_DIR = ROOT / "mcp" / "wp-server"

MCP_VENV = MCP_DIR / ".venv"

CLI_VENV = ROOT / ".cli-venv"

TOOLS_DIR = ROOT / "tools"

TOOLS_VENV = ROOT / "runtime" / ".venv-tools"

def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)

def info(msg: str) -> None:
    print(f"• {msg}")

def ok(msg: str) -> None:
    print(f"✓ {msg}")

_WEB_STREAM = [False]

class _RunResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

def run(cmd: list[str], check: bool = True, capture: bool = False, **kw):
    if not capture:
        print(f"  $ {' '.join(cmd)}")

    # Web-streaming path: only when not capturing (capture callers want the
    # buffered value back) and the flag is on. Merge stderr into stdout and
    # echo each line as it arrives so the console tails the real output.
    if _WEB_STREAM[0] and not capture:
        kw.pop("capture_output", None)
        proc = subprocess.Popen(cmd, text=True, cwd=str(ROOT),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, **kw)
        collected = []
        for line in proc.stdout:
            collected.append(line)
            print(line, end="")            # → active _JobStream (web console)
        proc.wait()
        if check and proc.returncode != 0:
            sys.exit(proc.returncode)
        return _RunResult(proc.returncode, "".join(collected))

    res = subprocess.run(cmd, check=False, text=True,
                         capture_output=capture, cwd=str(ROOT), **kw)
    if check and res.returncode != 0:
        sys.exit(res.returncode)
    return res

def ensure_pyyaml() -> None:
    """Ensure PyYAML is importable.

    PEP 668 ("externally-managed-environment") prevents pip --user installs
    against system/homebrew Pythons, so we keep the CLI's deps in its own
    venv at .cli-venv/ and re-exec ourselves through it on first run.
    """
    try:
        import yaml  # noqa: F401
        return
    except ImportError:
        pass

    cli_py = CLI_VENV / "bin" / "python"
    if not cli_py.exists():
        info("Creating CLI venv at .cli-venv/ (one-time)…")
        subprocess.check_call([sys.executable, "-m", "venv", str(CLI_VENV)])
        subprocess.check_call(
            [str(CLI_VENV / "bin" / "pip"), "install", "--quiet",
             "--disable-pip-version-check", "pyyaml"]
        )

    # If we're not already running under the CLI venv, re-exec there.
    # Compare sys.prefix (not sys.executable, which resolves to the underlying
    # interpreter binary that the venv symlinks to).
    if Path(sys.prefix).resolve() != CLI_VENV.resolve():
        os.execv(str(cli_py), [str(cli_py), str(ENTRY), *sys.argv[1:]])

def expand(value, vars_: dict) -> object:
    """Recursively expand ${var} references using vars_."""
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: str(vars_.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [expand(v, vars_) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, vars_) for k, v in value.items()}
    return value

def load_config() -> dict:
    ensure_pyyaml()
    import yaml
    if not CONFIG.exists():
        die(f"missing {CONFIG} — run from the sandbox/ directory")
    with CONFIG.open() as f:
        cfg = yaml.safe_load(f) or {}
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            local = yaml.safe_load(f) or {}
        cfg = deep_merge(cfg, local)
    vars_ = cfg.get("defaults", {}) or {}
    return expand(cfg, vars_)

def deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out

SERVERS = ("apache", "nginx", "litespeed", "herd")

def _valid_server(server: str) -> str:
    s = (server or "apache").strip().lower()
    if s not in SERVERS:
        die(f"unknown server '{server}'. Choose from: {', '.join(SERVERS)}.")
    return s

def _server_runtime(server: str) -> dict:
    """Per-server container facts shared by the web service AND the wpcli
    service: where WP files live inside the container, and which uid owns
    them. The official wordpress images (apache/fpm/cli) run as www-data=33
    and serve from /var/www/html; OpenLiteSpeed serves from a different
    docroot and runs as uid 1000.
    """
    if server == "litespeed":
        return {"docroot": "/var/www/vhosts/localhost/html", "uid": "1000:1000"}
    # apache + nginx both use the official wordpress image layout.
    return {"docroot": "/var/www/html", "uid": "33:33"}

HERD_CLI_DEFAULT = (Path.home() / "Library" / "Application Support"
                    / "Herd" / "bin" / "herd")

HERD_BIN_DIR = Path(os.environ.get(
    "SANDBOX_HERD_BIN_DIR",
    str(Path.home() / "Library" / "Application Support" / "Herd" / "bin")))

HERD_DB_HOST = os.environ.get("SANDBOX_HERD_DB_HOST", "127.0.0.1")

HERD_DB_PORT = os.environ.get("SANDBOX_HERD_DB_PORT", "3306")

HERD_DB_USER = os.environ.get("SANDBOX_HERD_DB_USER", "root")

HERD_DB_PASSWORD = os.environ.get("SANDBOX_HERD_DB_PASSWORD", "")

def _herd_cli() -> str:
    return os.environ.get("SANDBOX_HERD_CLI", str(HERD_CLI_DEFAULT))

def _host_wp() -> str:
    wp = shutil.which("wp") or "/usr/local/bin/wp"
    if not Path(wp).exists():
        die("host wp-cli not found (needed for server: herd) — "
            "install it: brew install wp-cli")
    return wp

def _host_php() -> str:
    return shutil.which("php") or "/usr/bin/php"

def _herd_php_bin(php_v) -> str:
    """Resolve the PHP CLI binary for a pinned phpVersion on Herd.

    `8.1` → <Herd bin>/php81. Falls back to the generic host php when the
    version isn't given or its binary isn't installed (so a project with no
    phpVersion, or one pinned to a PHP Herd doesn't ship, still runs rather
    than hard-failing — same degrade-don't-abort posture as web isolation)."""
    if php_v in (None, ""):
        return _host_php()
    # Accept "8.1", 8.1 (float), "8" — normalize to major[.minor] then strip
    # the dot for Herd's binary naming (php81). A bare major maps to php8x only
    # if such a binary exists; otherwise fall through to the default php.
    ver = str(php_v).strip()
    digits = re.sub(r"[^0-9]", "", ver)  # "8.1" → "81", "8" → "8"
    if digits:
        cand = HERD_BIN_DIR / f"php{digits}"
        if cand.exists():
            return str(cand)
    return _host_php()

def _herd_php(instance: str) -> str:
    """The PHP binary a herd instance should run CLI/phpunit under: its pinned
    php_version (stored in the instance block by ensure_instance) resolved to
    the version-specific Herd binary. Falls back to host php when unpinned."""
    php_v = None
    try:
        php_v = (resolve_instances(load_config())
                 .get(instance, {}).get("php_version"))
    except SystemExit:
        raise
    except Exception:
        php_v = None
    return _herd_php_bin(php_v)

def _php_squote(value: str) -> str:
    """Emit a PHP single-quoted string literal whose VALUE is a shell-safe
    token for `value`. Used for WP_PHP_BINARY, which the WP test suite splices
    into a shell command unescaped — the Herd php path has spaces, so the
    stored value must already be shell-quoted. We shell-quote first (Python
    shlex), then PHP-single-quote-escape that for the literal."""
    import shlex
    shell_safe = shlex.quote(value)            # e.g. '/a b/php81'
    php_escaped = shell_safe.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{php_escaped}'"

def _herd_wp_cmd(instance: str) -> list[str]:
    """Argv prefix to run host wp-cli under a herd instance's pinned PHP.

    wp-cli ships as a phar with a `#!/usr/bin/env php` shebang, so invoking it
    as `<pinned-php> <wp.phar>` (instead of executing the phar directly, which
    would pick up the default `php`) is what makes `sb wp …` honor phpVersion."""
    return [_herd_php(instance), _host_wp()]

def _herd(*args: str, cwd=None, check: bool = False):
    """Run the Herd CLI (valet-compatible: link/unlink/secure/unsecure/
    isolate). Quiet capture — herd is chatty; callers surface failures."""
    cli = _herd_cli()
    if not Path(cli).exists():
        die(f"Herd CLI not found at {cli} (needed for server: herd). "
            f"Install Laravel Herd, or set SANDBOX_HERD_CLI.")
    res = subprocess.run([cli, *args], capture_output=True, text=True,
                         cwd=str(cwd) if cwd else None)
    if check and res.returncode != 0:
        die(f"herd {' '.join(args)} failed: "
            f"{(res.stderr or res.stdout or '').strip()[:400]}")
    return res

def _is_herd_instance(instance: str) -> bool:
    """True when `instance` is host-served by Herd. Read fresh from config
    (cheap) — ensure_instance writes the block before any wpcli call."""
    try:
        return (resolve_instances(load_config())
                .get(instance, {}).get("server") == "herd")
    except SystemExit:
        raise
    except Exception:
        return False

def _herd_db_name(instance: str) -> str:
    return "sandbox_" + re.sub(r"[^a-z0-9_]", "_", instance.lower())

def _herd_domain(instance: str) -> str:
    return f"{instance}.test"

def resolve_instances(cfg: dict) -> dict[str, dict]:
    """Return {instance_name: resolved_instance_config}.

    Each resolved dict has: wordpress_port, db_port, mailpit_port,
    admin (dict), wordpress_image, mariadb_image, wpcli_image,
    project (str|None). Values come from per-instance overrides
    first, then the top-level runtime: block, then hardcoded defaults.
    """
    runtime = cfg.get("runtime", {}) or {}
    instances = cfg.get("instances")

    def merged(inst: dict) -> dict:
        # Per-instance values override runtime defaults; admin dict is
        # shallow-merged so a per-instance override (e.g. site_title)
        # doesn't require restating user/password/email.
        rt_admin = (runtime.get("admin") or {})
        inst_admin = (inst.get("admin") or {})
        # Version pins (per-project knobs). The web image is resolved
        # server-aware at render time from these; the wpcli image follows the
        # PHP pin so test runs (composer/phpunit in wpcli) match. Coerce to str
        # — PyYAML parses an unquoted `php_version: 8.1` as a float and `8` as
        # an int, which would crash the litespeed `.replace('.', '')` and yield
        # malformed tags on apache/nginx.
        def _ver(v):
            return str(v) if v not in (None, "") else None
        php_version = _ver(inst.get("php_version", runtime.get("php_version")))
        wp_version = _ver(inst.get("wp_version", runtime.get("wp_version")))
        # The bare "wordpress:cli" is the default sentinel → derive from the PHP
        # pin so tests (composer + phpunit run in the wpcli container) execute
        # under the project's PHP. A non-default explicit image always wins.
        wpcli_explicit = inst.get("wpcli_image", runtime.get("wpcli_image"))
        wpcli_image = (wpcli_explicit
                       if wpcli_explicit and wpcli_explicit != "wordpress:cli"
                       else _cli_image(php_version))
        return {
            "wordpress_port": inst.get("wordpress_port",
                                       runtime.get("wordpress_port", 8088)),
            "db_port": inst.get("db_port",
                                runtime.get("db_port", 3307)),
            "mailpit_port": inst.get("mailpit_port",
                                     runtime.get("mailpit_port", 8025)),
            "wordpress_image": inst.get("wordpress_image",
                                        runtime.get("wordpress_image",
                                                    "wordpress:latest")),
            "mariadb_image": inst.get("mariadb_image",
                                      runtime.get("mariadb_image",
                                                  "mariadb:latest")),
            "wpcli_image": wpcli_image,
            "php_version": php_version,
            "wp_version": wp_version,
            "admin": {**rt_admin, **inst_admin},
            "project": inst.get("project"),
            # Web server stack: apache (default) | nginx | litespeed.
            # Only the compose web tier differs per server (see
            # render_compose); db/mailpit are server-agnostic.
            "server": _valid_server(inst.get("server",
                                             runtime.get("server", "apache"))),
            # Optional custom local domain (e.g. xspeed.tst) mapped to
            # 127.0.0.1 via /etc/hosts. None → plain localhost:<port>.
            "domain": inst.get("domain"),
            # Extra bind-mount sources for plugin repos / mappings outside
            # plugins_home. Written by ensure_instance; injected into compose.
            "extra_mounts": inst.get("extra_mounts",
                                     runtime.get("extra_mounts", [])) or [],
            # Project wp-config constants (sandbox.config.json `config`) and
            # multisite flag (False | True | "subdirectory" | "subdomain").
            # Written by ensure_instance; rendered into WORDPRESS_CONFIG_EXTRA
            # and acted on at install time (multisite-convert).
            "wp_config": inst.get("wp_config",
                                  runtime.get("wp_config", {})) or {},
            "multisite": inst.get("multisite",
                                  runtime.get("multisite", False)),
        }

    # Per-project model: the authoritative instance list is the on-disk registry
    # (project root -> instance). Per-instance config is pulled from the merged
    # `instances:` block (sandbox.local.yml, written by ensure_instance); a stale
    # block with no registry entry (e.g. a legacy `main`) is ignored. There is no
    # synthesized/implicit instance.
    # The registry entry caches each instance's ports/server/domain; overlay the
    # sandbox.local.yml block on top so an instance whose block was lost still
    # resolves to its REAL ports (not the shared hardcoded defaults → collision).
    _RKEYS = ("wordpress_port", "db_port", "mailpit_port", "server", "domain")
    try:
        reg = {e["instance"]: e for e in _core().registry_all().values()
               if e.get("instance")}
    except Exception:
        reg = {}
    reg_names = set(reg) or set(instances or {})

    def _cfg_for(name):
        entry = reg.get(name) or {}
        base = {k: entry[k] for k in _RKEYS if entry.get(k) is not None}
        base.update((instances or {}).get(name) or {})  # local.yml block wins
        return base

    return {name: merged(_cfg_for(name)) for name in sorted(reg_names)}

def compose_file(instance: str) -> Path:
    """Per-instance generated compose file path."""
    return COMPOSE_DIR / f"{instance}.yml"

def wp_dir(instance: str) -> Path:
    """Per-instance WordPress install dir."""
    return ROOT / "runtime" / f"wp-{instance}"

def plugins_dir(instance: str) -> Path:
    return wp_dir(instance) / "wp-content" / "plugins"

def active_project_file(instance: str) -> Path:
    return ROOT / f".active-project.{instance}"

def focus_file(instance: str) -> Path:
    return ROOT / f".focus.{instance}"

def snapshots_dir(instance: str) -> Path:
    return ROOT / "runtime" / "snapshots" / instance

def project_name(instance: str) -> str:
    """docker-compose project name — must be unique per instance."""
    return f"sandbox-{instance}"

MCP_SERVER_NAME = "sandbox"

def mcp_server_name(instance: str = "") -> str:
    """The Claude MCP server name. Per-project rewrite: ONE 'sandbox' server
    routes every project by `project_dir`, so this is a constant now — the old
    per-instance `sandbox-<name>` scheme is gone. Kept as a function so the
    existing call sites don't all need editing."""
    return MCP_SERVER_NAME

HOSTS_HELPER = TOOLS_DIR / "hosts-helper.sh"

SUDOERS_FILE = Path("/etc/sudoers.d/sandbox-hosts")

DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")

PROXY_DIR       = ROOT / "runtime" / "proxy"

PROXY_CERTS_DIR = PROXY_DIR / "certs"

PROXY_CADDYFILE = PROXY_DIR / "Caddyfile"

PROXY_COMPOSE   = PROXY_DIR / "proxy.yml"

PROXY_HELPER    = TOOLS_DIR / "proxy-helper.sh"

ASKPASS_HELPER  = TOOLS_DIR / "askpass.sh"

PROXY_SUDOERS   = Path("/etc/sudoers.d/sandbox-proxy")

PROXY_PROJECT   = "sandbox-proxy"

PROXY_BIND_IP   = "127.0.0.77"

PROXY_TLD       = "tst"   # DEFAULT local TLD; per-project override via sandbox.config.json "tld"

LAUNCHD_PLIST   = Path("/Library/LaunchDaemons/com.sandbox.lo0alias.plist")

def _tld(ic: dict | None = None) -> str:
    """Local domain TLD for an instance — from its `tld` (sandbox.config.json),
    defaulting to PROXY_TLD ('tst'). The proxy is global, but each instance's
    domain is built from — and matched against — its own configured TLD, so a
    custom `tld` in one project never breaks another's domain detection."""
    return (ic or {}).get("tld") or PROXY_TLD

def _distinct_tlds(cfg: dict) -> set:
    """Every TLD in use across instances (for DNS setup); at least {PROXY_TLD}."""
    return {_tld(ic) for ic in resolve_instances(cfg).values()} or {PROXY_TLD}

def _lo0_alias_present() -> bool:
    """True if the proxy's loopback alias is already on lo0 — checkable WITHOUT
    sudo, so we can skip an unnecessary `sudo alias-up`."""
    r = subprocess.run(["ifconfig", "lo0"], capture_output=True, text=True)
    return PROXY_BIND_IP in (r.stdout or "")

def _resolver_present(tld: str) -> bool:
    """True if /etc/resolver/<tld> exists (written by dns-up). Existence needs no
    read permission, so this is checkable WITHOUT sudo."""
    return Path(f"/etc/resolver/{tld}").exists()

def _norm_tld(s) -> str:
    """Normalise a user-supplied TLD: strip leading dots + lowercase. Empty → ''.
    Rejects anything that isn't a single DNS label (letters/digits/hyphens)."""
    t = (s or "").strip().lstrip(".").lower()
    if t and not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", t):
        die(f"invalid TLD {s!r}. Use a single label like 'tst' (letters, digits, hyphens).")
    return t

def _resolve_setup_tld(args):
    """The TLD EXPLICITLY chosen for `domains setup`: the CLI arg, else an
    interactive prompt. Returns None when the user accepts the default (empty
    input) or there's no TTY — so a per-project `tld` config (else PROXY_TLD)
    still wins. A non-None return overrides per-project for the assignment."""
    if getattr(args, "tld", None):
        return _norm_tld(args.tld) or None
    if sys.stdin.isatty():
        try:
            ans = input(f"Local TLD for clean URLs — avoid .sb (a real ccTLD) "
                        f"and .test (Herd/Valet) [{PROXY_TLD}]: ")
        except EOFError:
            ans = ""
        return _norm_tld(ans) or None
    return None

def _valid_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    if not DOMAIN_RE.match(d) or len(d) > 253:
        die(f"invalid domain '{domain}'. Use a hostname like myapp.tst")
    if d.endswith(".dev"):
        info("note: browsers force HTTPS on all .dev domains — http won't work. "
             "Prefer .tst / .test / .local.")
    return d

def _hosts_passwordless() -> bool:
    """True if the passwordless sudoers rule for the hosts-helper is installed."""
    return SUDOERS_FILE.exists()

def _hosts_edit(action: str, domain: str) -> tuple[bool, str]:
    """Add/remove a domain mapping via the helper. ALWAYS uses `sudo -n`
    (non-interactive) so it can NEVER hang on a password prompt — critical for
    the web server, where a blocking sudo would freeze the job forever. With
    the passwordless rule installed it succeeds silently; without it, it fails
    immediately and the caller falls back + tells the user to run
    `./sb domains setup`. Returns (ok, message)."""
    res = subprocess.run(
        ["sudo", "-n", str(HOSTS_HELPER), action, domain],
        capture_output=True, text=True)
    if res.returncode == 0:
        return True, (res.stdout or "").strip()
    if not _hosts_passwordless():
        return False, ("custom domains need a one-time setup: run "
                       "`./sb domains setup` (or `sudo ./tools/hosts-helper.sh "
                       f"{action} {domain}`)")
    return False, (res.stderr or res.stdout or "sudo failed").strip()

def _valet_tld() -> str:
    """The TLD Valet serves (e.g. 'dev' or 'test'). Defaults to 'test' (Valet's
    own default) if the config can't be read."""
    cfg_path = Path.home() / ".config" / "valet" / "config.json"
    try:
        import json
        return (json.loads(cfg_path.read_text()).get("tld") or "test").strip()
    except (OSError, ValueError):
        return "test"

def _valet_available() -> bool:
    """True when Valet is installed AND running (its nginx owns :80). We treat
    presence of the binary + the valet config dir as 'available'; the proxy
    call itself surfaces any 'valet not started' error, and we fall back."""
    return shutil.which("valet") is not None and \
        (Path.home() / ".config" / "valet").is_dir()

def valet_proxy_add(domain: str, port: int) -> bool:
    """Publish a clean http://<domain> via `valet proxy`. Valet handles DNS
    (dnsmasq), the nginx block, and the root reload. Returns True on success;
    False (no-op) if Valet isn't available or the command fails — caller then
    falls back to the per-port URL.

    Valet writes its nginx config + reloads its root nginx via sudo. If Valet
    isn't configured passwordless, that prompts for a password — fine on an
    interactive terminal (let it through so the user can type it once), but in a
    non-interactive context (web UI / CI) we close stdin so it fails fast
    instead of hanging forever on the prompt."""
    if not domain or not _valet_available():
        return False
    cmd = ["valet", "proxy", domain, f"http://127.0.0.1:{port}"]
    if sys.stdin.isatty():
        # Interactive: let valet's own sudo prompt reach the terminal.
        res = subprocess.run(cmd)
    else:
        # Non-interactive: never hang on a password prompt.
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True)
    return res.returncode == 0

def valet_proxy_remove(domain: str) -> None:
    """Remove a Valet proxy (`valet unproxy`), if Valet is available. Same
    interactive/non-interactive handling as valet_proxy_add (it also reloads
    nginx via sudo)."""
    if not (domain and _valet_available()):
        return
    cmd = ["valet", "unproxy", domain]
    if sys.stdin.isatty():
        subprocess.run(cmd)
    else:
        subprocess.run(cmd, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)

def _valet_proxy_active(domain: str) -> bool:
    """True when Valet currently serves a proxy for this domain. Checked from
    Valet's own site dir so site_url() reflects reality (clean vs per-port)."""
    if not domain:
        return False
    # Valet stores per-site nginx configs as ~/.config/valet/Nginx/<domain>.
    return (Path.home() / ".config" / "valet" / "Nginx" / domain).exists()

def proxy_available() -> bool:
    """True when the sandbox proxy CAN serve clean no-port URLs: just Docker.
    The DEFAULT path is plain HTTP (no cert/CA needed) — so this no longer
    requires mkcert trust. HTTPS is the opt-in `./sb secure`."""
    return shutil.which("docker") is not None

_HTTPS_OFFER_MARKER = ROOT / "runtime" / ".https-offer-declined"

def _https_offer_declined() -> bool:
    """True if the user previously declined the one-time HTTPS-setup offer, so
    create doesn't nag on every new instance."""
    return _HTTPS_OFFER_MARKER.exists()

def _set_https_offer_declined() -> None:
    _HTTPS_OFFER_MARKER.parent.mkdir(parents=True, exist_ok=True)
    _HTTPS_OFFER_MARKER.write_text(
        "User declined the trusted-HTTPS offer at instance create. Delete this "
        "file (or run `./sb domains setup`) to enable https://<name>.tst.\n")

def _proxy_container_running() -> bool:
    """True if the sandbox-proxy Caddy container is up."""
    res = subprocess.run(
        ["docker", "compose", "-p", PROXY_PROJECT, "-f", str(PROXY_COMPOSE),
         "--project-directory", str(ROOT), "ps", "-q", "proxy"],
        capture_output=True, text=True)
    return res.returncode == 0 and bool((res.stdout or "").strip())

def _sandbox_proxy_active(domain: str) -> bool:
    """True when the proxy is running AND has a route for this domain — i.e.
    https://<domain> actually serves. Used by site_url()."""
    if not domain or not PROXY_CADDYFILE.exists():
        return False
    txt = PROXY_CADDYFILE.read_text()
    if f"http://{domain} {{" not in txt and f"\n{domain} {{" not in txt:
        return False
    return _proxy_container_running()

def _cert_paths(domain: str) -> tuple[Path, Path]:
    """(cert, key) file paths for a domain's explicit mkcert certificate."""
    return (PROXY_CERTS_DIR / f"{domain}.pem",
            PROXY_CERTS_DIR / f"{domain}-key.pem")

def _ca_installed() -> bool:
    """True only if the mkcert CA is actually TRUSTED by the OS — not merely
    present on disk. (A rootCA.pem on disk that isn't trusted in the keychain is
    exactly what causes the browser's ERR_CERT_AUTHORITY_INVALID while curl with
    --cacert still works.) We verify trust by minting a throwaway cert and
    asking the OS to verify its chain via `security verify-cert` (macOS). On
    non-macOS, fall back to the on-disk check."""
    if shutil.which("mkcert") is None:
        return False
    res = subprocess.run(["mkcert", "-CAROOT"], capture_output=True, text=True)
    if res.returncode != 0 or not (Path(res.stdout.strip()) / "rootCA.pem").exists():
        return False
    if sys.platform != "darwin":
        return True  # Linux: trust check is distro-specific; assume on-disk = ok
    return _ca_trusted_macos()

def _ca_trusted_macos() -> bool:
    """Mint a throwaway leaf cert and ask macOS to verify its chain. Returns
    True only if the OS trusts the mkcert CA (what browsers actually require)."""
    import tempfile
    d = tempfile.mkdtemp()
    cert = Path(d) / "probe.pem"
    key = Path(d) / "probe-key.pem"
    try:
        g = subprocess.run(
            ["mkcert", "-cert-file", str(cert), "-key-file", str(key),
             "sb-trust-probe.tst"], capture_output=True, text=True)
        if g.returncode != 0 or not cert.exists():
            return False
        v = subprocess.run(["security", "verify-cert", "-c", str(cert)],
                           capture_output=True, text=True)
        return v.returncode == 0
    except Exception:
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)

def _mint_cert(domain: str, extra_sans: list[str] | None = None) -> bool:
    """Mint an explicit cert for <domain> signed by the already-trusted mkcert
    CA. No sudo. Idempotent — overwrites. Returns ok.

    `extra_sans` adds additional Subject Alternative Names to the SAME cert
    file (keyed by <domain> via _cert_paths). Used for subdomain multisite:
    a wildcard SAN `*.<domain>.tst` so every sub-site host (sub1.<name>.tst)
    is covered by one cert. Wildcards directly under `.tst` are browser-
    rejected, but `*.<name>.tst` (a level deeper) is a valid SAN."""
    PROXY_CERTS_DIR.mkdir(parents=True, exist_ok=True)
    cert, key = _cert_paths(domain)
    names = [domain, *(extra_sans or [])]
    res = subprocess.run(
        ["mkcert", "-cert-file", str(cert), "-key-file", str(key), *names],
        capture_output=True, text=True)
    return res.returncode == 0 and cert.exists() and key.exists()

def _wildcard_san(domain: str) -> str:
    """The wildcard SAN that covers an instance's sub-sites: `*.<domain>`."""
    return f"*.{domain}"

def render_proxy_compose() -> str:
    """The sandbox-proxy compose file. One Caddy container on a dedicated
    loopback IP serving plain HTTP on :80 (clean no-port URLs, no certs). It
    also publishes :443 so `./sb secure` can add TLS later, but the default
    path uses :80 only."""
    return f"""# Generated by ./sb — sandbox URL proxy. Do not edit by hand.
name: {PROXY_PROJECT}
services:
  proxy:
    image: caddy:2-alpine
    container_name: {PROXY_PROJECT}
    restart: unless-stopped
    ports:
      - "{PROXY_BIND_IP}:80:80"
      - "{PROXY_BIND_IP}:443:443"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./runtime/proxy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./runtime/proxy/certs:/certs:ro
      - proxy_data:/data
      - proxy_config:/config
volumes:
  proxy_data:
  proxy_config:
"""

def _caddy_block(domain: str, port: int, wildcard: bool = False) -> str:
    """One Caddy site block. Default is plain http://<domain> (no port, no cert
    — zero CA-trust fragility, browsers never warn on http). If this domain has
    been secured (a mkcert cert exists), serve https + bounce http→https.

    When `wildcard` is set (subdomain multisite), the site address list also
    includes `*.<domain>` so every sub-site host (sub1.<domain>) reverse-
    proxies to the same instance port. dnsmasq already wildcards `.tst`, and
    the secured cert carries a matching `*.<domain>` SAN (see _mint_cert)."""
    cert, key = _cert_paths(domain)
    # Space-separated address list — Caddy serves all of them from one block.
    hosts = f"{domain} {_wildcard_san(domain)}" if wildcard else domain
    if cert.exists() and key.exists():
        return f"""http://{hosts} {{
    redir https://{{host}}{{uri}} 308
}}

{hosts} {{
    tls /certs/{cert.name} /certs/{key.name}
    reverse_proxy host.docker.internal:{port} {{
        header_up X-Forwarded-Proto https
        header_up Host {{host}}
    }}
}}
"""
    return f"""http://{hosts} {{
    reverse_proxy host.docker.internal:{port} {{
        header_up Host {{host}}
    }}
}}
"""

def regen_caddyfile(cfg: dict) -> None:
    """Rewrite the Caddyfile from current config: a global block + one site
    block per instance whose domain is a .tst name. Mirrors write_compose_files
    — always reflects sandbox.local.yml, so create/delete just call this."""
    PROXY_DIR.mkdir(parents=True, exist_ok=True)
    blocks = [f"""# Generated by ./sb — do not edit by hand. Regenerated on
# instance create/delete.
{{
    auto_https off
    # Serve HTTP/1.1 + HTTP/2 only — no HTTP/3. Caddy's default h3 makes it
    # advertise `alt-svc: h3` (cached 30d), which pins long-running browsers to
    # a QUIC connection holding a STALE cert verdict from before `./sb secure`
    # ran — so the same browser keeps showing "Not Secure" while a freshly
    # opened one trusts the cert fine. QUIC buys nothing for local dev; drop it.
    servers {{
        protocols h1 h2
    }}
}}
"""]
    for name, ic in resolve_instances(cfg).items():
        dom = ic.get("domain")
        if not (dom and dom.endswith(f".{_tld(ic)}")):
            continue
        # No cert minting here — default is plain http. _caddy_block emits an
        # https block only if a cert already exists (i.e. `./sb secure` ran).
        # Subdomain multisite also needs a wildcard `*.<name>.tst` block so each
        # sub-site host proxies to the same port.
        wildcard = _multisite_mode(ic) == "subdomain"
        blocks.append(_caddy_block(dom, ic["wordpress_port"], wildcard=wildcard))
    PROXY_CADDYFILE.write_text("\n".join(blocks))

def _dns_flush() -> None:
    """Self-heal DNS so the user NEVER runs a terminal command: reload the live
    dnsmasq (drops stale cached *.tst records that would shadow the wildcard) and
    flush macOS's resolver cache. Passwordless via the proxy-helper sudoers rule;
    silent no-op if that rule isn't installed. Called after every domain change."""
    if _proxy_sudoers_installed():
        subprocess.run(["sudo", "-n", str(PROXY_HELPER), "dns-flush"],
                       capture_output=True, text=True)

def _proxy_started_at() -> float | None:
    """UNIX timestamp of when the sandbox-proxy container last (re)started, or
    None if it isn't running / can't be read."""
    res = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", PROXY_PROJECT],
        capture_output=True, text=True)
    raw = (res.stdout or "").strip()
    if res.returncode != 0 or not raw:
        return None
    try:
        from datetime import datetime
        s = raw.replace("Z", "+00:00")
        # Docker emits RFC3339 nanoseconds; trim the fraction to 6 digits so
        # datetime.fromisoformat (which maxes at microseconds) can parse it.
        if "." in s:
            base, rest = s.split(".", 1)
            i = 0
            while i < len(rest) and rest[i].isdigit():
                i += 1
            s = f"{base}.{rest[:min(i, 6)]}{rest[i:]}"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None

def _certs_changed_since_proxy_start() -> bool:
    """True if any mkcert cert was (re)minted after the proxy container started.

    `caddy reload` does NOT re-read an explicit `tls <cert> <key>` whose path is
    unchanged but whose bytes changed — and `_mint_cert` overwrites certs in
    place (same path per domain). So after an instance recreate/secure re-mints
    a cert, a hot reload silently keeps serving the stale cert and TLS
    handshakes reset; only a container restart re-reads the file."""
    started = _proxy_started_at()
    if started is None or not PROXY_CERTS_DIR.is_dir():
        return False
    for pem in PROXY_CERTS_DIR.glob("*.pem"):
        try:
            if pem.stat().st_mtime > started + 1:  # 1s slack for clock skew
                return True
        except OSError:
            continue
    return False

def reload_proxy() -> bool:
    """Apply the current proxy config. Always rewrite the compose from the
    template; if it changed (e.g. ports) recreate the container. Otherwise
    hot-reload Caddy with the regenerated Caddyfile — UNLESS a cert was re-minted
    since the proxy started, in which case restart the container so Caddy re-reads
    the changed cert files (a plain `caddy reload` won't). Then self-heal DNS
    (clear stale *.tst cache) so a new/changed domain resolves immediately — no
    manual flush. Non-interactive, never hangs. Returns success of the proxy step."""
    desired = render_proxy_compose()
    changed = (not PROXY_COMPOSE.exists()) or PROXY_COMPOSE.read_text() != desired
    if changed:
        PROXY_COMPOSE.write_text(desired)
    if _proxy_container_running() and not changed:
        if _certs_changed_since_proxy_start():
            # Certs re-minted at an unchanged path (e.g. an instance recreate):
            # `caddy reload` keeps the stale cert and TLS resets — restart so the
            # cert files are re-read from the mounted /certs volume.
            res = subprocess.run(
                ["docker", "compose", "-p", PROXY_PROJECT, "-f", str(PROXY_COMPOSE),
                 "--project-directory", str(ROOT), "restart", "proxy"],
                capture_output=True, text=True)
        else:
            res = subprocess.run(
                ["docker", "exec", PROXY_PROJECT, "caddy", "reload",
                 "--config", "/etc/caddy/Caddyfile"],
                capture_output=True, text=True)
    else:
        # First boot, or the compose changed (ports/image) → (re)create so the
        # new spec takes effect; `up -d` recreates only what differs.
        res = subprocess.run(
            ["docker", "compose", "-p", PROXY_PROJECT, "-f", str(PROXY_COMPOSE),
             "--project-directory", str(ROOT), "up", "-d"],
            capture_output=True, text=True)
    _dns_flush()
    return res.returncode == 0

def _write_ssl_muplugin(instance: str) -> None:
    """Drop a mu-plugin so WP trusts the proxy's TLS termination. Without it WP
    sees plain http inside the container, mismatches its https siteurl, and
    redirect-loops. mu-plugins auto-load with no activation; path is the same
    across apache/nginx/litespeed."""
    mu_dir = wp_dir(instance) / "wp-content" / "mu-plugins"
    mu_dir.mkdir(parents=True, exist_ok=True)
    (mu_dir / "00-sandbox-ssl.php").write_text(
        "<?php\n"
        "/* Sandbox: trust the reverse proxy's TLS termination. "
        "Generated by ./sb. */\n"
        "if ( ! empty( $_SERVER['HTTP_X_FORWARDED_PROTO'] )\n"
        "     && 'https' === $_SERVER['HTTP_X_FORWARDED_PROTO'] ) {\n"
        "    $_SERVER['HTTPS'] = 'on';\n"
        "}\n"
        "if ( ! empty( $_SERVER['HTTP_X_FORWARDED_HOST'] ) ) {\n"
        "    $_SERVER['HTTP_HOST'] = $_SERVER['HTTP_X_FORWARDED_HOST'];\n"
        "}\n"
    )

def _write_mail_muplugin(instance: str) -> None:
    """Drop a mu-plugin so PHP mail is captured by the Mailpit container instead
    of being dropped on the floor. The official wordpress image has no working
    sendmail binary (sendmail_path points at /usr/sbin/sendmail, which is
    absent), so wp_mail() returns false and the captured-mail feature
    (mail_list/mail_get) never sees anything. On phpmailer_init we switch
    PHPMailer to SMTP and point it at the shared `mailpit` service on :1025.

    Lives in the shared runtime/wp-<instance> bind-mount, so it is visible to
    BOTH the web (`wp`) and the `wpcli` tiers — CLI-triggered mail
    (e.g. `./sb wp eval`, cron, tests) is captured too. mu-plugins auto-load
    with no activation and survive container restarts (unlike `wp config set`,
    which the entrypoint wipes). Image-agnostic; mirrors _write_ssl_muplugin."""
    mu_dir = wp_dir(instance) / "wp-content" / "mu-plugins"
    mu_dir.mkdir(parents=True, exist_ok=True)
    (mu_dir / "00-sandbox-mail.php").write_text(
        "<?php\n"
        "/* Sandbox: route all PHP mail to the Mailpit container so it can be\n"
        "   inspected via mail_list / mail_get. Generated by ./sb. */\n"
        "add_action( 'phpmailer_init', function ( $phpmailer ) {\n"
        "    $phpmailer->isSMTP();\n"
        "    $phpmailer->Host       = 'mailpit';\n"
        "    $phpmailer->Port       = 1025;\n"
        "    $phpmailer->SMTPAuth   = false;\n"
        "    $phpmailer->SMTPSecure = '';\n"
        "    $phpmailer->SMTPAutoTLS = false;\n"
        "} );\n"
        "// WP's default From is wordpress@localhost; PHPMailer rejects it as an\n"
        "// invalid address (no TLD), so wp_mail() fails before it ever reaches\n"
        "// SMTP. Give it a valid sandbox sender unless the caller set one.\n"
        "add_filter( 'wp_mail_from', function ( $from ) {\n"
        "    return ( $from && 'wordpress@localhost' !== $from )\n"
        "        ? $from : 'wordpress@sandbox.test';\n"
        "}, 1 );\n"
    )

def site_url(inst_cfg: dict) -> str:
    """Browser URL for an instance. Precedence:
      • https://<domain>        — proxy serves it AND it's been secured (cert)
      • http://<domain>         — proxy serves this .tst domain (clean, no port)
      • http://<domain>         — legacy Valet proxy (no port)
      • http://localhost:<port> — domain set but proxy NOT serving it, or no domain

    Critical: a `.tst` domain only resolves while the proxy + its *.tst DNS are
    up. If a domain is set but the proxy isn't actually serving it (proxy down,
    DNS not installed, or the lo0 alias dropped after a reboot), we must fall
    back to http://localhost:<port> — NOT http://<domain>:<port>. The latter is
    never valid (the proxy serves clean URLs with no port) and points at a host
    that won't resolve on a clean box, so the browser hangs ("loading forever").
    localhost:<port> always works because the WP container publishes that port.
    """
    port = inst_cfg["wordpress_port"]
    dom = inst_cfg.get("domain")
    # herd (host) instances are served by Herd at https://<name>.test — no
    # docker port, no .tst proxy. `herd secure` runs during provisioning.
    if inst_cfg.get("server") == "herd" and dom:
        return f"https://{dom}"
    if dom and dom.endswith(f".{_tld(inst_cfg)}") and _sandbox_proxy_active(dom):
        cert, _ = _cert_paths(dom)
        return (f"https://{dom}" if cert.exists() else f"http://{dom}")
    if dom and _valet_proxy_active(dom):
        return f"http://{dom}"
    return f"http://localhost:{port}"

def _plugins_home(cfg: dict) -> Path:
    """Resolve `defaults.plugins_home` to an absolute path, creating it."""
    defaults = cfg.get("defaults", {}) or {}
    raw = defaults.get("plugins_home", "") or "./plugins"
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def _extra_vol_lines(inst_cfg: dict, indent: int = 6, ro: bool = False) -> str:
    """Extra bind-mount lines for paths in inst_cfg["extra_mounts"].

    Injected into every service tier so symlinks to sources outside
    plugins_home (e.g. a plugin repo at ~/Sites/git/templately) resolve
    inside the container. Without these mounts the symlinks are dangling
    and WP silently skips the plugins."""
    mounts = inst_cfg.get("extra_mounts") or []
    if not mounts:
        return ""
    pad = " " * indent
    suffix = ":ro" if ro else ""
    return "\n" + "\n".join(f"{pad}- {m}:{m}{suffix}" for m in mounts)

_BASE_WP_CONFIG = {
    "WP_DEBUG_LOG": True,
    "WP_DEBUG_DISPLAY": False,
    "SCRIPT_DEBUG": True,
    "WP_ENVIRONMENT_TYPE": "local",
}

MULTISITE_MARKER = ".sandbox-multisite"

def _php_literal(v) -> str:
    """Render a config scalar as a PHP literal for a define()."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "null"
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"

def _merged_wp_config(inst_cfg: dict) -> dict:
    """Sandbox defaults overlaid with the project's `config` dict (project
    wins). WP_DEBUG is excluded: the official image defines it from the
    WORDPRESS_DEBUG env var BEFORE the WORDPRESS_CONFIG_EXTRA eval, so a
    define() here would collide — see _wp_debug_env."""
    merged = {**_BASE_WP_CONFIG, **(inst_cfg.get("wp_config") or {})}
    merged.pop("WP_DEBUG", None)
    return merged

def _wp_debug_env(inst_cfg: dict) -> str:
    """WORDPRESS_DEBUG env value: WP_DEBUG defaults to true in the sandbox;
    only an explicit `"WP_DEBUG": false` in the project config turns it off."""
    return "" if (inst_cfg.get("wp_config") or {}).get("WP_DEBUG") is False else "1"

def _multisite_mode(inst_cfg: dict):
    """None (single site) | 'subdirectory' | 'subdomain'. `true` means
    subdirectory — the baseline that works on localhost:<port> with no
    wildcard DNS."""
    ms = inst_cfg.get("multisite")
    if not ms:
        return None
    return "subdomain" if str(ms).lower() == "subdomain" else "subdirectory"

def _site_host(inst_cfg: dict) -> str:
    """Host[:port] of the instance's URL — DOMAIN_CURRENT_SITE must match
    wp_site.domain byte-for-byte, and `wp core multisite-convert` stores the
    siteurl's full netloc INCLUDING the port (e.g. 'localhost:8191')."""
    from urllib.parse import urlparse
    return urlparse(site_url(inst_cfg)).netloc or "localhost"

def _config_extra_php(inst_cfg: dict, docroot: str = "/var/www/html") -> str:
    """The WORDPRESS_CONFIG_EXTRA PHP block: typed define()s for the merged
    sandbox + project constants, plus (when multisite is configured) the
    network constants gated on the conversion marker file. Every define is
    defined()-guarded so literal constants that wp-cli wrote into
    wp-config.php (multisite-convert, litespeed pinning) never double-define.
    Living in the compose env is what makes the constants survive container
    restarts — the official entrypoint regenerates wp-config.php from env on
    start, wiping anything written via `wp config set`."""
    lines = [f"defined('{k}') || define('{k}', {_php_literal(v)});"
             for k, v in _merged_wp_config(inst_cfg).items()]
    mode = _multisite_mode(inst_cfg)
    if mode:
        sub = "true" if mode == "subdomain" else "false"
        lines += [
            f"if (file_exists('{docroot}/{MULTISITE_MARKER}')) {{",
            "    defined('WP_ALLOW_MULTISITE') || define('WP_ALLOW_MULTISITE', true);",
            "    defined('MULTISITE') || define('MULTISITE', true);",
            f"    defined('SUBDOMAIN_INSTALL') || define('SUBDOMAIN_INSTALL', {sub});",
            f"    defined('DOMAIN_CURRENT_SITE') || define('DOMAIN_CURRENT_SITE', '{_site_host(inst_cfg)}');",
            "    defined('PATH_CURRENT_SITE') || define('PATH_CURRENT_SITE', '/');",
            "    defined('SITE_ID_CURRENT_SITE') || define('SITE_ID_CURRENT_SITE', 1);",
            "    defined('BLOG_ID_CURRENT_SITE') || define('BLOG_ID_CURRENT_SITE', 1);",
            "    $base = '/';",
            "}",
        ]
    return "\n".join(lines)

def _env_config_lines(inst_cfg: dict, docroot: str = "/var/www/html",
                      indent: int = 6) -> str:
    """WORDPRESS_DEBUG + WORDPRESS_CONFIG_EXTRA env entries for a compose
    service. Set on BOTH the web tier and wpcli so `wp eval`/tests see the
    same constants the site runs with (the generated wp-config.php reads both
    from the env of whichever container is executing)."""
    pad = " " * indent
    # Compose interpolates $vars inside the YAML — a PHP `$base` would warn
    # "variable is not set" and land as an empty string. `$$` escapes it.
    php = _config_extra_php(inst_cfg, docroot).replace("$", "$$")
    body = "\n".join(f"{pad}  {ln}" for ln in php.splitlines())
    return (f"{pad}WORDPRESS_DEBUG: \"{_wp_debug_env(inst_cfg)}\"\n"
            f"{pad}WORDPRESS_CONFIG_EXTRA: |\n{body}")

def _web_apache(instance: str, inst_cfg: dict, plugins_host: Path) -> str:
    """The original single-service Apache + mod_php web tier (default).
    Kept byte-identical to the pre-multi-server template so existing
    instances regenerate unchanged."""
    image = _web_image("apache", inst_cfg.get("php_version"),
                       inst_cfg.get("wp_version"), inst_cfg.get("wordpress_image"))
    return f"""  wp:
    image: {image}
    # Reach the host `sb web` snapshot bridge from in-container PHP
    # (auto on Docker Desktop; explicit for Linux/Docker Engine).
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "{inst_cfg["wordpress_port"]}:80"
    # The official wordpress:latest image ships Apache with
    # `AllowOverride None` for /var/www/, which silently breaks pretty
    # permalinks (wp-json/ 404s). Patch the conf on every start, then
    # hand off to the image's entrypoint.
    #
    # Permissions reconciler: on this bind mount wp-content/upgrade and
    # wp-content/uploads can end up owned by root, which the Apache worker
    # (www-data) cannot write — so WordPress plugin/theme installs (and FSI)
    # fail with "Directory listing failed." / "Destination folder already
    # exists." Once WP core is copied, a short background loop keeps those
    # dirs owned by www-data through the provisioning window; once set they
    # persist for the session (WP reuses the upgrade dir across installs).
    command: >
      bash -c "sed -i 's|AllowOverride None|AllowOverride All|g' /etc/apache2/apache2.conf
      ; ( while [ ! -f /var/www/html/wp-load.php ]; do sleep 1; done
      ; for i in 1 2 3 4 5 6 7 8 9 10 11 12; do mkdir -p /var/www/html/wp-content/upgrade /var/www/html/wp-content/upgrade-temp-backup ; chown -R www-data:www-data /var/www/html/wp-content/upgrade /var/www/html/wp-content/upgrade-temp-backup /var/www/html/wp-content/uploads 2>/dev/null || true ; sleep 4 ; done ) &
      docker-entrypoint.sh apache2-foreground"
    environment:
      WORDPRESS_DB_HOST: db:3306
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wp
{_env_config_lines(inst_cfg)}
    volumes:
      - ./runtime/wp-{instance}:/var/www/html
      - ./runtime/seeds:/seeds
      # Bind-mount plugin sources at the same absolute host path so the
      # symlinks ensure_instance creates under wp-content/plugins/ resolve
      # inside the container.
      - {plugins_host}:{plugins_host}{_extra_vol_lines(inst_cfg)}
      - ./config/php-sandbox.ini:/usr/local/etc/php/conf.d/zz-sandbox.ini:ro
"""

def _web_nginx(instance: str, inst_cfg: dict, plugins_host: Path) -> str:
    """nginx + php-fpm: two services sharing the WP bind-mount at the SAME
    path (/var/www/html). The `wp` service is php-fpm (internal :9000, no
    published port); `nginx` publishes the instance's wordpress_port and
    reverse-proxies .php to wp:9000. nginx-sandbox.conf carries the WP
    front-controller rewrite (permalinks + /wp-json/ both fall through to
    index.php). Default image is Apache-specific, so pin the fpm flavor here."""
    fpm_image = _web_image("nginx", inst_cfg.get("php_version"),
                           inst_cfg.get("wp_version"), inst_cfg.get("wordpress_image"))
    return f"""  wp:
    image: {fpm_image}
    extra_hosts:                       # reach the host `sb web` snapshot bridge
      - "host.docker.internal:host-gateway"
    depends_on:
      db:
        condition: service_healthy
    # php-fpm listens on :9000 internally; nginx reaches it as wp:9000.
    # No published port — only nginx is web-facing.
    environment:
      WORDPRESS_DB_HOST: db:3306
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wp
{_env_config_lines(inst_cfg)}
    volumes:
      - ./runtime/wp-{instance}:/var/www/html
      - ./runtime/seeds:/seeds
      - {plugins_host}:{plugins_host}{_extra_vol_lines(inst_cfg)}
      - ./config/php-sandbox.ini:/usr/local/etc/php/conf.d/zz-sandbox.ini:ro

  nginx:
    image: nginx:1.27-alpine
    depends_on:
      - wp
    ports:
      - "{inst_cfg["wordpress_port"]}:80"
    volumes:
      # Same WP files nginx serves statically + computes $document_root from.
      - ./runtime/wp-{instance}:/var/www/html:ro
      # Plugins are symlinked into wp-content/plugins as ABSOLUTE host paths
      # under plugins_host. nginx serves their static assets (js/css/images)
      # itself, so it must resolve those symlinks too — mount plugins_host at
      # the same path it does in the fpm `wp` service, or every symlinked
      # plugin's assets 404 and its admin UI renders blank.
      - {plugins_host}:{plugins_host}:ro{_extra_vol_lines(inst_cfg, ro=True)}
      - ./config/nginx-sandbox.conf:/etc/nginx/conf.d/default.conf:ro
"""

def _web_litespeed(instance: str, inst_cfg: dict, plugins_host: Path) -> str:
    """OpenLiteSpeed: a single container — lsphp runs in-process via LSAPI,
    so there's no separate fpm service. Different docroot
    (/var/www/vhosts/localhost/html) and uid (1000). The image's `docker`
    vhost template already does `autoLoadHtaccess`, so no custom OLS config is
    mounted — instead cmd_install writes the WP .htaccess into the docroot
    (WP itself won't, under OLS). That drives permalinks + REST."""
    rt = _server_runtime("litespeed")
    docroot = rt["docroot"]
    ls_image = _web_image("litespeed", inst_cfg.get("php_version"),
                          inst_cfg.get("wp_version"), inst_cfg.get("wordpress_image"))
    return f"""  wp:
    image: {ls_image}
    extra_hosts:                       # reach the host `sb web` snapshot bridge
      - "host.docker.internal:host-gateway"
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "{inst_cfg["wordpress_port"]}:80"
      - "{inst_cfg["wordpress_port"] + 1000}:7080"  # OLS WebAdmin console
    environment:
      TZ: UTC
    volumes:
      - ./runtime/wp-{instance}:{docroot}
      - ./runtime/seeds:/seeds
      - {plugins_host}:{plugins_host}{_extra_vol_lines(inst_cfg)}
"""

def _wpcli_service(instance: str, inst_cfg: dict, plugins_host: Path) -> str:
    """The wp-cli helper container. Must mount the SAME host WP dir at the
    SAME in-container docroot as the web tier, and run as the matching uid,
    so `./sb wp` operates on the files the web server actually serves."""
    rt = _server_runtime(inst_cfg["server"])
    docroot = rt["docroot"]
    return f"""  wpcli:
    image: {inst_cfg["wpcli_image"]}
    depends_on:
      - wp
    user: "{rt["uid"]}"
    working_dir: {docroot}
    environment:
      WORDPRESS_DB_HOST: db:3306
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wp
      HOME: /tmp
      WP_CLI_CACHE_DIR: /tmp/.wp-cli/cache
{_env_config_lines(inst_cfg, docroot)}
    volumes:
      - ./runtime/wp-{instance}:{docroot}
      - ./runtime/seeds:/seeds
      - {plugins_host}:{plugins_host}{_extra_vol_lines(inst_cfg)}
      - ./config/php-sandbox.ini:/usr/local/etc/php/conf.d/zz-sandbox.ini:ro
    entrypoint: ["wp", "--allow-root"]
    command: ["--info"]
"""

_WEB_BUILDERS = {
    "apache": _web_apache,
    "nginx": _web_nginx,
    "litespeed": _web_litespeed,
}

def render_compose(instance: str, inst_cfg: dict, plugins_host: Path) -> str:
    """Build the docker-compose YAML for one instance.

    The db + mailpit + wpcli tiers are shared; only the web tier (the `wp`
    service, plus an `nginx` sidecar for the nginx server) is chosen by
    inst_cfg["server"]. Kept as Python f-strings (no Jinja2 dependency) —
    the templates are small and stable enough to inline.
    """
    server = inst_cfg.get("server", "apache")
    web = _WEB_BUILDERS[server](instance, inst_cfg, plugins_host)
    wpcli_svc = _wpcli_service(instance, inst_cfg, plugins_host)
    return f"""# Generated by ./sb — do not edit by hand. Edit sandbox.yml + re-run ./sb apply.
# server: {server}
name: {project_name(instance)}

services:
{web}
  db:
    image: {inst_cfg["mariadb_image"]}
    environment:
      MARIADB_DATABASE: wp
      MARIADB_USER: wp
      MARIADB_PASSWORD: wp
      MARIADB_ROOT_PASSWORD: root
    ports:
      - "{inst_cfg["db_port"]}:3306"
    volumes:
      - db_data:/var/lib/mysql
    healthcheck:
      test: ["CMD-SHELL", "mariadb-admin --user=root --password=$$MARIADB_ROOT_PASSWORD ping --silent"]
      interval: 5s
      retries: 20

  mailpit:
    image: axllent/mailpit
    ports:
      - "{inst_cfg["mailpit_port"]}:8025"
      - "1025"

{wpcli_svc}
volumes:
  db_data:
"""

def write_compose_files(cfg: dict) -> None:
    """Regenerate one compose file per instance under runtime/compose/.

    Idempotent: safe to call on every `sb` invocation. Old compose files
    for instances no longer in sandbox.yml are removed so stale stacks
    don't linger.
    """
    COMPOSE_DIR.mkdir(parents=True, exist_ok=True)
    plugins_host = _plugins_home(cfg)
    instances = resolve_instances(cfg)

    # Write current instances. herd (host) instances have no docker stack —
    # no compose file (the orphan sweep below also removes a stale one left
    # over from a docker→herd re-provision).
    current_files = set()
    for name, inst_cfg in instances.items():
        if inst_cfg.get("server") == "herd":
            continue
        path = compose_file(name)
        path.write_text(render_compose(name, inst_cfg, plugins_host))
        current_files.add(path.name)

    # Remove orphaned compose files (instance was deleted from sandbox.yml).
    # Only remove .yml under our managed dir — don't touch user files.
    for existing in COMPOSE_DIR.glob("*.yml"):
        if existing.name not in current_files:
            existing.unlink()

def compose(*args: str, instance: str,
            check: bool = True, capture: bool = False):
    """Run `docker compose` against one instance's stack.

    Uses the per-instance project name (-p sandbox-<instance>) and
    generated compose file. Caller must have run write_compose_files()
    at least once (the CLI entrypoint does this on every invocation).
    """
    return run(
        ["docker", "compose",
         "-p", project_name(instance),
         "-f", str(compose_file(instance)),
         # Resolve the compose file's relative paths (./config, ./runtime)
         # against the sandbox ROOT, not the compose file's own dir
         # (runtime/compose/). Without this, `./config/x` would resolve to
         # runtime/compose/config/x. Load-bearing for the nginx/litespeed
         # server config mounts.
         "--project-directory", str(ROOT),
         *args],
        check=check, capture=capture,
    )

def wpcli(args: list[str], instance: str,
          check: bool = True, capture: bool = False):
    """Run wp-cli against an instance. Docker instances exec in the wpcli
    container; herd instances run the HOST wp with --path at the same WP dir —
    this single seam is what makes every provisioning step (install, constants,
    multisite, plugins, themes) work identically on both runtimes."""
    if _is_herd_instance(instance):
        # Run wp-cli under the instance's PINNED PHP (php_version), not the
        # phar's default `php` — so plugin code, migrations, and `wp eval`
        # execute on the same PHP the web tier serves.
        return run([*_herd_wp_cmd(instance), f"--path={wp_dir(instance)}", *args],
                   check=check, capture=capture)
    return compose("run", "--rm", "wpcli", *args,
                   instance=instance, check=check, capture=capture)

def write_env_for_compose(cfg: dict) -> None:
    """Legacy: materialize values for the checked-in docker-compose.yml.

    Pre-multi-instance, this wrote a `.env` file consumed by
    `docker-compose.yml` (the checked-in file). The new flow ignores
    that file entirely — each instance has its own generated compose
    file with values baked in. We keep this function so anyone calling
    it from out-of-tree code (or older skills) still gets a valid
    `.env`, but the multi-instance code path doesn't depend on it.
    """
    rt = cfg.get("runtime", {})
    plugins_home = _plugins_home(cfg)
    env = ROOT / ".env"
    env.write_text(
        f"WP_PORT={rt.get('wordpress_port', 8088)}\n"
        f"DB_PORT={rt.get('db_port', 3307)}\n"
        f"MAILPIT_PORT={rt.get('mailpit_port', 8025)}\n"
        f"SANDBOX_PLUGINS_HOST={plugins_home}\n"
    )

def _web_services(server: str) -> list[str]:
    """Long-running services to boot for an instance. The nginx server adds
    an `nginx` sidecar in front of the php-fpm `wp` service; apache and
    litespeed are single web containers (`wp`)."""
    base = ["wp", "db", "mailpit"]
    if server == "nginx":
        base.insert(1, "nginx")
    return base

def _ensure_litespeed_htaccess(inst: str) -> None:
    """Write the canonical WP rewrite .htaccess into the docroot and reload
    OpenLiteSpeed so it honors it. WordPress only writes a physical .htaccess
    under Apache — under OLS it writes none, so pretty permalinks + /wp-json/
    404 without this. The image's vhost template does `autoLoadHtaccess`, but
    only re-reads on reload. Idempotent: keeps an existing .htaccess as-is.
    Called on install AND when switching an instance to the litespeed server."""
    htaccess = wp_dir(inst) / ".htaccess"
    if not htaccess.exists():
        info("litespeed: writing WordPress .htaccess (OLS autoloads it for rewrites)…")
        htaccess.write_text(
            "# BEGIN WordPress\n"
            "<IfModule mod_rewrite.c>\n"
            "RewriteEngine On\n"
            "RewriteBase /\n"
            "RewriteRule ^index\\.php$ - [L]\n"
            "RewriteCond %{REQUEST_FILENAME} !-f\n"
            "RewriteCond %{REQUEST_FILENAME} !-d\n"
            "RewriteRule . /index.php [L]\n"
            "</IfModule>\n"
            "# END WordPress\n"
        )
    # OLS cached the vhost on boot BEFORE the .htaccess existed, so it won't
    # honor it until reloaded. Graceful reload picks it up.
    info("litespeed: reloading OpenLiteSpeed to load the new .htaccess…")
    compose("exec", "-T", "wp", "/usr/local/lsws/bin/lswsctrl", "restart",
            instance=inst, check=False)

def _pin_db_creds_in_config(inst: str) -> None:
    """Write the DB credentials as LITERAL constants into wp-config.php.

    The default wp-config resolves creds via getenv_docker('WORDPRESS_DB_*').
    That works under the official wordpress image (apache/nginx) because its
    entrypoint exports those env vars to php-fpm — but OpenLiteSpeed's lsphp
    runs via suExec and does NOT inherit the container environment, so getenv()
    returns empty and WP 500s with "Error establishing a database connection"
    the moment an instance is switched to litespeed. Pinning literal values
    makes the config server-agnostic: correct under apache, nginx, AND OLS.
    Idempotent — `wp config set` overwrites the constant in place."""
    info("pinning DB credentials into wp-config.php (server-agnostic)…")
    for const, val in (("DB_HOST", "db:3306"), ("DB_USER", "wp"),
                       ("DB_PASSWORD", "wp"), ("DB_NAME", "wp")):
        wpcli(["config", "set", const, val], instance=inst, check=False)

def _pin_wp_constants_in_config(inst: str, inst_cfg: dict) -> None:
    """litespeed: write the merged wp-config constants as LITERALS via
    `wp config set` — same rationale as _pin_db_creds_in_config: lsphp can't
    see the container env, so the WORDPRESS_CONFIG_EXTRA mechanism that
    apache/nginx use is invisible to it. The OLS image doesn't regenerate
    wp-config.php on start, so the literals persist there."""
    constants = {"WP_DEBUG": _wp_debug_env(inst_cfg) == "1",
                 **_merged_wp_config(inst_cfg)}
    # An already-converted multisite needs its network constants literal too
    # (the marker-gated block in WORDPRESS_CONFIG_EXTRA never runs under OLS).
    # On a fresh install the marker doesn't exist yet — multisite-convert
    # writes the constants into wp-config.php itself, which persists under OLS.
    mode = _multisite_mode(inst_cfg)
    if mode and (wp_dir(inst) / MULTISITE_MARKER).exists():
        constants.update({
            "WP_ALLOW_MULTISITE": True,
            "MULTISITE": True,
            "SUBDOMAIN_INSTALL": mode == "subdomain",
            "DOMAIN_CURRENT_SITE": _site_host(inst_cfg),
            "PATH_CURRENT_SITE": "/",
            "SITE_ID_CURRENT_SITE": 1,
            "BLOG_ID_CURRENT_SITE": 1,
        })
    info("pinning wp-config constants into wp-config.php (literal)…")
    for k, v in constants.items():
        if isinstance(v, str):
            wpcli(["config", "set", k, v], instance=inst, check=False)
        else:
            wpcli(["config", "set", k, _php_literal(v), "--raw"],
                  instance=inst, check=False)

def _write_multisite_htaccess(inst: str, mode: str) -> None:
    """The WP network .htaccess. WordPress never writes it itself (network
    setup only DISPLAYS the rules to paste), so without this /site2/wp-admin/
    and core assets under a subdirectory site 404. The same rules serve
    apache and OpenLiteSpeed (autoLoadHtaccess); nginx carries its equivalent
    in config/nginx-sandbox.conf."""
    strip = "" if mode == "subdomain" else "([_0-9a-zA-Z-]+/)?"
    ref = "$1" if mode == "subdomain" else "$2"
    (wp_dir(inst) / ".htaccess").write_text(
        "# BEGIN WordPress Multisite — generated by ./sb\n"
        "RewriteEngine On\n"
        "RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]\n"
        "RewriteBase /\n"
        "RewriteRule ^index\\.php$ - [L]\n"
        f"RewriteRule ^{strip}wp-admin$ {'$1' if strip else ''}wp-admin/ [R=301,L]\n"
        "RewriteCond %{REQUEST_FILENAME} -f [OR]\n"
        "RewriteCond %{REQUEST_FILENAME} -d\n"
        "RewriteRule ^ - [L]\n"
        f"RewriteRule ^{strip}(wp-(content|admin|includes).*) {ref} [L]\n"
        f"RewriteRule ^{strip}(.*\\.php)$ {ref} [L]\n"
        "RewriteRule . index.php [L]\n"
        "# END WordPress Multisite\n"
    )

def _convert_multisite(inst: str, inst_cfg: dict) -> None:
    """Convert a freshly installed single site to multisite, per the project's
    `multisite` config. Idempotent: skips the convert when the network tables
    already exist. The marker file written here is what turns on the MULTISITE
    constants in WORDPRESS_CONFIG_EXTRA (see _config_extra_php) — wp-cli also
    writes them into wp-config.php, but the official entrypoint wipes that on
    the next container start."""
    mode = _multisite_mode(inst_cfg)
    if not mode:
        return
    chk = wpcli(["core", "is-installed", "--network"],
                instance=inst, check=False, capture=True)
    if chk.returncode != 0:
        info(f"converting to multisite ({mode})…")
        cmd = ["core", "multisite-convert"]
        if mode == "subdomain":
            cmd.append("--subdomains")
        # apache/nginx get the constants from WORDPRESS_CONFIG_EXTRA (marker-
        # gated) — letting wp-cli ALSO write them into wp-config.php would
        # double-define ("already defined" warnings on every request until the
        # next restart wipes the file). litespeed/herd are the opposite: no
        # container env reaches PHP there, so the literal constants wp-cli
        # writes are the only ones that work (and their wp-config is stable).
        if inst_cfg.get("server") not in ("litespeed", "herd"):
            cmd.append("--skip-config")
        wpcli(cmd, instance=inst)
    (wp_dir(inst) / MULTISITE_MARKER).write_text(
        "Generated by ./sb after `wp core multisite-convert`. Gates the\n"
        "MULTISITE constants in WORDPRESS_CONFIG_EXTRA — delete only if you\n"
        "want the instance to fall back to single-site mode.\n")
    _write_multisite_htaccess(inst, mode)
    if inst_cfg.get("server") == "litespeed":
        # OLS only re-reads the .htaccess on reload.
        compose("exec", "-T", "wp", "/usr/local/lsws/bin/lswsctrl", "restart",
                instance=inst, check=False)

def _ensure_proxy_up(cfg: dict) -> None:
    """Restore the lo0 alias (dropped on reboot) and start the proxy if it's
    not running. Best-effort, passwordless, silent on success."""
    if _proxy_sudoers_installed():
        subprocess.run(["sudo", "-n", str(PROXY_HELPER), "alias-up"],
                       capture_output=True, text=True)
    if not _proxy_container_running():
        regen_caddyfile(cfg)
        reload_proxy()

def save_local_app_password(app_pw: str, instance: str) -> None:
    """Persist the WP Application Password into sandbox.local.yml so the
    MCP server picks it up on next launch.

    Written to `instances.<name>.app_password` so each instance has its own
    secret without colliding. (Per-project model — there is no global key.)
    """
    ensure_pyyaml()
    import yaml
    local = {}
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            local = yaml.safe_load(f) or {}
    local.setdefault("instances", {}).setdefault(instance, {})["app_password"] = app_pw
    with CONFIG_LOCAL.open("w") as f:
        yaml.safe_dump(local, f, default_flow_style=False, sort_keys=False)

def save_local_autologin_token(token: str, instance: str) -> None:
    """Persist the sandbox autologin token in sandbox.local.yml so it can be
    included in the ensure_instance return value as login_url."""
    ensure_pyyaml()
    import yaml
    local = {}
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            local = yaml.safe_load(f) or {}
    local.setdefault("instances", {}).setdefault(instance, {})["autologin_token"] = token
    with CONFIG_LOCAL.open("w") as f:
        yaml.safe_dump(local, f, default_flow_style=False, sort_keys=False)

def save_local_bridge_token(token: str, instance: str) -> None:
    """Persist the per-instance snapshot-bridge token in sandbox.local.yml.
    The sb web bridge authenticates dashboard snapshot calls against it."""
    ensure_pyyaml()
    import yaml
    local = {}
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            local = yaml.safe_load(f) or {}
    local.setdefault("instances", {}).setdefault(instance, {})["bridge_token"] = token
    with CONFIG_LOCAL.open("w") as f:
        yaml.safe_dump(local, f, default_flow_style=False, sort_keys=False)


def _write_snapshot_muplugin(instance: str, token: str) -> None:
    """Drop 00-sandbox-snapshots.php into the instance — a sandbox-only wp-admin
    screen (Tools → Sandbox Snapshots) that takes/restores/lists/deletes snapshots
    by calling the host `sb web` bridge. PHP admin-ajax handlers enforce nonce +
    manage_options, then call the bridge over host.docker.internal with the
    per-instance Bearer token (the bridge runs the host-level sb snapshot/restore
    out-of-band). Regenerated on every install/recreate."""
    mu_dir = wp_dir(instance) / "wp-content" / "mu-plugins"
    mu_dir.mkdir(parents=True, exist_ok=True)
    url = f"http://host.docker.internal:{BRIDGE_PORT}"
    php = _SNAPSHOT_MU_TEMPLATE.replace("%URL%", url) \
                               .replace("%TOKEN%", token) \
                               .replace("%INSTANCE%", instance)
    (mu_dir / "00-sandbox-snapshots.php").write_text(php)


_SNAPSHOT_MU_TEMPLATE = r'''<?php
/**
 * Sandbox Snapshots - local dev only. Generated by ./sb; regenerated on recreate.
 * Tools -> Sandbox Snapshots: take/restore/list/delete instance snapshots via the
 * host `sb web` bridge. Never ships to / affects a real site (sandbox-only guard).
 */
if ( ! defined( 'ABSPATH' ) ) { return; }
define( 'SANDBOX_BRIDGE_URL', '%URL%' );
define( 'SANDBOX_BRIDGE_TOKEN', '%TOKEN%' );
define( 'SANDBOX_INSTANCE', '%INSTANCE%' );

add_action( 'admin_menu', function () {
	add_management_page(
		'Sandbox Snapshots', 'Sandbox Snapshots',
		'manage_options', 'sandbox-snapshots', 'sandbox_snapshots_render'
	);
} );

/** Server-side proxy to the host bridge (nonce + capability enforced here). */
function sandbox_snapshots_bridge( $method, $path, $body = null ) {
	$args = array(
		'method'  => $method,
		'timeout' => 30,
		'headers' => array(
			'Authorization' => 'Bearer ' . SANDBOX_BRIDGE_TOKEN,
			'Content-Type'  => 'application/json',
		),
	);
	if ( null !== $body ) { $args['body'] = wp_json_encode( $body ); }
	$url = SANDBOX_BRIDGE_URL . '/api/instance/' . rawurlencode( SANDBOX_INSTANCE ) . $path;
	$res = wp_remote_request( $url, $args );
	if ( is_wp_error( $res ) ) {
		return array( 'ok' => false, 'error' => $res->get_error_message() );
	}
	$code = wp_remote_retrieve_response_code( $res );
	$data = json_decode( wp_remote_retrieve_body( $res ), true );
	if ( ! is_array( $data ) ) { $data = array( 'ok' => false, 'error' => 'bad bridge response' ); }
	$data['_status'] = $code;
	return $data;
}

add_action( 'wp_ajax_sandbox_snap', function () {
	if ( ! current_user_can( 'manage_options' )
		|| ! check_ajax_referer( 'sandbox_snapshots', 'nonce', false ) ) {
		wp_send_json( array( 'ok' => false, 'error' => 'unauthorized' ), 403 );
	}
	$op = isset( $_POST['op'] ) ? sanitize_text_field( wp_unslash( $_POST['op'] ) ) : '';
	$name = isset( $_POST['name'] ) ? sanitize_text_field( wp_unslash( $_POST['name'] ) ) : '';
	if ( $name !== '' && ! preg_match( '/^[A-Za-z0-9._-]+$/', $name ) ) {
		wp_send_json( array( 'ok' => false, 'error' => 'invalid snapshot name' ), 400 );
	}
	if ( 'list' === $op ) {
		wp_send_json( sandbox_snapshots_bridge( 'GET', '/snapshots' ) );
	} elseif ( 'take' === $op ) {
		wp_send_json( sandbox_snapshots_bridge( 'POST', '/snapshot', array( 'name' => $name, 'force' => ! empty( $_POST['force'] ) ) ) );
	} elseif ( 'restore' === $op ) {
		wp_send_json( sandbox_snapshots_bridge( 'POST', '/restore', array( 'name' => $name ) ) );
	} elseif ( 'delete' === $op ) {
		wp_send_json( sandbox_snapshots_bridge( 'DELETE', '/snapshot/' . rawurlencode( $name ) ) );
	} elseif ( 'job' === $op ) {
		$jid = isset( $_POST['job_id'] ) ? sanitize_text_field( wp_unslash( $_POST['job_id'] ) ) : '';
		wp_send_json( sandbox_snapshots_bridge( 'GET', '/job/' . rawurlencode( $jid ) ) );
	}
	wp_send_json( array( 'ok' => false, 'error' => 'unknown op' ), 400 );
} );

function sandbox_snapshots_render() {
	if ( ! current_user_can( 'manage_options' ) ) { wp_die( 'Forbidden' ); }
	$nonce = wp_create_nonce( 'sandbox_snapshots' );
	echo '<div class="wrap"><h1>Sandbox Snapshots &mdash; <code>' . esc_html( SANDBOX_INSTANCE ) . '</code></h1>';
	echo '<p>Capture or roll back this instance\'s database + uploads (runs on the sandbox host).</p>';
	echo '<p><input type="text" id="sbx-name" class="regular-text" placeholder="snapshot name (optional)"> ';
	echo '<button class="button button-primary" id="sbx-take">Take snapshot</button> ';
	echo '<label><input type="checkbox" id="sbx-force"> overwrite</label></p>';
	echo '<div id="sbx-msg" style="margin:8px 0"></div>';
	echo '<table class="widefat striped" id="sbx-table"><thead><tr><th>Name</th><th>Size</th><th>Meta</th><th></th></tr></thead><tbody></tbody></table>';
	echo '</div>';
	$ajax = esc_url( admin_url( 'admin-ajax.php' ) );
	?>
<script>
(function(){
  var AJAX=<?php echo wp_json_encode( $ajax ); ?>, NONCE=<?php echo wp_json_encode( $nonce ); ?>;
  var msg=document.getElementById('sbx-msg'), tb=document.querySelector('#sbx-table tbody');
  function call(op, extra){ var d=new URLSearchParams(Object.assign({action:'sandbox_snap',nonce:NONCE,op:op},extra||{}));
    return fetch(AJAX,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:d}).then(function(r){return r.json();}); }
  function say(t,err){ msg.textContent=t; msg.style.color=err?'#b32d2e':'#2271b1'; }
  function poll(jid){ return call('job',{job_id:jid}).then(function(j){
    if(j.status==='succeeded'){say('Done.');return refresh();}
    if(j.status==='failed'){say('Failed: '+(j.detail||''),true);return;}
    say('Working… ('+(j.status||'running')+')'); return new Promise(function(res){setTimeout(res,1500);}).then(function(){return poll(jid);}); }); }
  function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function refresh(){ return call('list').then(function(r){ tb.innerHTML='';
    (r.snapshots||[]).forEach(function(s){ var tr=document.createElement('tr'); var n=esc(s.name);
      tr.innerHTML='<td>'+n+'</td><td>'+(parseInt(s.size_kb)||0)+' KB</td><td>'+esc(s.meta)+'</td>'+
        '<td><button class="button" data-r="'+n+'">Restore</button> <button class="button" data-d="'+n+'">Delete</button></td>';
      tb.appendChild(tr); }); }); }
  document.getElementById('sbx-take').onclick=function(){ var n=document.getElementById('sbx-name').value;
    say('Taking snapshot…'); call('take',{name:n,force:document.getElementById('sbx-force').checked?1:''}).then(function(r){
      if(r.job_id){return poll(r.job_id);} say(r.error||'error',true); }); };
  tb.addEventListener('click',function(e){ var r=e.target.getAttribute('data-r'), d=e.target.getAttribute('data-d');
    if(r&&confirm('Restore '+r+'? This REPLACES the current DB + uploads.')){ say('Restoring…');
      call('restore',{name:r}).then(function(x){ if(x.job_id){return poll(x.job_id);} say(x.error||'error',true); }); }
    if(d&&confirm('Delete snapshot '+d+'?')){ call('delete',{name:d}).then(function(){refresh();}); } });
  refresh();
})();
</script>
	<?php
}
'''


def _autologin_mu_plugin(token: str) -> str:
    """Render the autologin mu-plugin with the token embedded directly in the
    file. Storing the token here (not in wp-config.php) means it survives
    container restarts — the WordPress Docker entrypoint regenerates wp-config.php
    from env-vars on every start, wiping any constants we set via `wp config set`."""
    return f"""\
<?php
/**
 * Sandbox autologin — local dev only. Generated by ./sb; regenerated on recreate.
 * Visit /?sandbox_autologin=<token> to log in as admin without a password.
 * Token is in sandbox.local.yml (gitignored); never committed.
 */
// Token embedded in file so it survives wp-config.php regeneration on restart.
define( 'SANDBOX_AUTOLOGIN_TOKEN', '{token}' );

add_action( 'init', function () {{
    if ( empty( $_GET['sandbox_autologin'] ) ) {{
        return;
    }}
    if ( ! hash_equals( SANDBOX_AUTOLOGIN_TOKEN, (string) $_GET['sandbox_autologin'] ) ) {{
        return;
    }}
    $user = get_user_by( 'login', 'admin' );
    if ( $user ) {{
        wp_set_auth_cookie( $user->ID, true, is_ssl() );
        wp_safe_redirect( admin_url() );
        exit;
    }}
}}, 1 );
"""

def _force_symlink(link: Path, src: Path) -> None:
    """Replace whatever is at `link` with a symlink to `src`.

    `link.unlink()` fails with EPERM on a real (non-symlink) directory, which
    is exactly the state we hit when a wp.org install of the same slug already
    sits in the plugins folder. Detect that case and remove the directory tree
    instead. Symlinks (even dangling ones) get unlinked normally.
    """
    if link.is_symlink():
        link.unlink()
    elif link.is_dir():
        # Plain `rmtree` follows nothing here — link is a real dir, not a link.
        shutil.rmtree(link)
    elif link.exists():
        link.unlink()
    link.symlink_to(src)

def _active_project_name(instance: str) -> str | None:
    apf = active_project_file(instance)
    return apf.read_text().strip() if apf.exists() else None

def ensure_tools_venv() -> Path:
    """Build the headless-browser venv on first use and return its python path.

    Lives under runtime/.venv-tools/ so it sits next to other auto-managed
    state and is wiped by `./sb clean`. The Chromium binary that Playwright
    downloads lands in the playwright cache under the venv.
    """
    py = TOOLS_VENV / "bin" / "python"
    req = TOOLS_DIR / "visit" / "requirements.txt"
    stamp = TOOLS_VENV / ".installed"

    if py.exists() and stamp.exists() and stamp.read_text().strip() == req.read_text().strip():
        return py

    if not py.exists():
        info("Creating tools venv at runtime/.venv-tools/ (one-time)…")
        TOOLS_VENV.parent.mkdir(parents=True, exist_ok=True)
        _make_venv(find_modern_python(), TOOLS_VENV)

    pip = TOOLS_VENV / "bin" / "pip"
    info("Installing Playwright (one-time)…")
    subprocess.check_call([str(pip), "install", "--quiet",
                           "--disable-pip-version-check", "-r", str(req)])
    info("Downloading headless Chromium (one-time, ~150 MB)…")
    # Pin to chromium only — we don't need firefox/webkit and don't want
    # to wait for 3x the download on first run.
    subprocess.check_call([str(py), "-m", "playwright", "install", "chromium"])
    stamp.write_text(req.read_text())
    ok("Tools venv ready.")
    return py

def find_modern_python() -> str:
    """Pick a Python >= 3.10 that can actually build a working venv. We've seen
    a Homebrew python3.13 with a broken pyexpat (libexpat symbol mismatch) that
    passes a version check but fails ensurepip — so each candidate is validated
    by importing the stdlib modules venv/ensurepip need, not just its version.
    Prefers the highest usable version; includes python3/3.14 in the list."""
    candidates = [
        "python3.14", "python3.13", "python3.12", "python3.11", "python3.10",
        "python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3",
    ]
    fallback = None
    for c in candidates:
        if not shutil.which(c) and not Path(c).exists():
            continue
        try:
            v = subprocess.check_output(
                [c, "-c", "import sys;print(sys.version_info[:2])"], text=True
            ).strip()
            if eval(v) < (3, 10):
                continue
            fallback = fallback or c
            # Validate the interpreter is actually usable for a venv: the
            # modules ensurepip pulls in (pyexpat via xml, ssl, ensurepip) must
            # import cleanly. A broken pyexpat here is what fails `-m venv`.
            chk = subprocess.run(
                [c, "-c", "import ensurepip, ssl, pyexpat, xml.parsers.expat"],
                capture_output=True, text=True)
            if chk.returncode == 0:
                return c
        except Exception:
            continue
    # No fully-validated interpreter — return the best version-only match (the
    # venv builder has its own --without-pip + get-pip fallback) or python3.
    return fallback or "python3"

def _make_venv(py: str, path: Path) -> None:
    """Create a venv robustly. Some interpreters (e.g. Homebrew python3.13) fail
    `python -m venv` at the internal ensurepip step. Fall back to building the
    venv WITHOUT pip, then bootstrap pip via ensurepip → get-pip."""
    r = subprocess.run([py, "-m", "venv", str(path)],
                       capture_output=True, text=True)
    vpy = path / "bin" / "python"
    if r.returncode == 0 and vpy.exists():
        return
    # Fallback: pip-less venv + bootstrap pip.
    info("venv+pip failed; retrying without pip, then bootstrapping pip…")
    shutil.rmtree(path, ignore_errors=True)
    run([py, "-m", "venv", "--without-pip", str(path)])
    # 1) try ensurepip inside the venv
    if subprocess.run([str(vpy), "-m", "ensurepip", "--upgrade"],
                      capture_output=True, text=True).returncode == 0:
        return
    # 2) last resort: get-pip.py
    import urllib.request, tempfile
    info("ensurepip unavailable; fetching get-pip.py…")
    with tempfile.NamedTemporaryFile("wb", suffix=".py", delete=False) as f:
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", f.name)
        gp = f.name
    run([str(vpy), gp])
    Path(gp).unlink(missing_ok=True)

PROJECT_MCP_JSON = ROOT / ".mcp.json"   # Claude Code auto-loads this when run from ROOT

def _build_mcp_entry(cfg: dict | None = None) -> dict:
    """The SINGLE 'sandbox' MCP server registration entry. One server for all
    projects: it takes NO per-instance env — every tool routes by `project_dir`
    and resolves the instance from the on-disk registry per call. Launched via
    `<sb> mcp` (the stdio entrypoint), which execs the venv server.py.

    Prefer the PATH-resolved `sb` name (set by `./sb global`) so the
    registration survives the repo being moved or re-cloned — same as how
    @wordpress/env uses a PATH-based npm bin. Fall back to the absolute path
    when `sb` isn't on PATH yet."""
    sb_on_path = shutil.which("sb")
    command = "sb" if sb_on_path else str(ROOT / "sb")
    return {"command": command, "args": ["mcp"]}

def _stale_mcp_servers(claude_bin: str) -> list[str]:
    """Per-instance server names (`sandbox-<name>`) left by pre-rewrite
    registrations, so the single-server migration can clean them up."""
    res = subprocess.run([claude_bin, "mcp", "list"],
                         capture_output=True, text=True)
    out = []
    for line in (res.stdout or "").splitlines():
        tok = line.split(":")[0].strip()
        if tok.startswith("sandbox-"):
            out.append(tok)
    return out

def register_claude_user_scope(cfg: dict) -> None:
    """Register the SINGLE 'sandbox' MCP server at user scope so every `claude`
    session reaches it from any directory. The server routes by `project_dir`,
    so one registration serves every project — no per-instance servers.

    Idempotent (wipe-then-add). Also removes legacy registrations: the
    pre-rename `wp-sandbox`, and any `sandbox-<name>` per-instance servers from
    the pre-rewrite multi-instance model.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        info("claude CLI not in PATH — skipping user-scope MCP registration.")
        return

    # Clean up legacy / stale registrations (pre-rename + per-instance).
    for stale in ["wp-sandbox", *_stale_mcp_servers(claude_bin)]:
        subprocess.run([claude_bin, "mcp", "remove", "--scope", "user", stale],
                       capture_output=True, text=True)

    entry = _build_mcp_entry(cfg)
    subprocess.run([claude_bin, "mcp", "remove", "--scope", "user", MCP_SERVER_NAME],
                   capture_output=True, text=True)
    cmd = [claude_bin, "mcp", "add", "--scope", "user", MCP_SERVER_NAME, "--",
           entry["command"], *entry.get("args", [])]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        ok(f"Registered MCP server '{MCP_SERVER_NAME}' at user scope "
           f"(tools: mcp__{MCP_SERVER_NAME}__*; routes by project_dir).")
    else:
        info(f"MCP registration failed: {res.stderr.strip()}")
        info("Project-local .mcp.json still works when cwd is the sandbox.")

def write_claude_mcp_config(cfg: dict) -> tuple[Path, bool]:
    """Write the project-local .mcp.json with the SINGLE 'sandbox' server.

    Claude Code auto-loads .mcp.json from the working directory; the user-scope
    registration (register_claude_user_scope) is what makes it reachable outside
    the sandbox folder. Drops any stale `sandbox-<name>` entries from the old
    per-instance model.
    """
    existing = {}
    created = not PROJECT_MCP_JSON.exists()
    if not created:
        try:
            existing = json.loads(PROJECT_MCP_JSON.read_text()) or {}
        except json.JSONDecodeError:
            backup = PROJECT_MCP_JSON.with_suffix(".json.bak")
            PROJECT_MCP_JSON.rename(backup)
            info(f"existing .mcp.json was invalid JSON — backed up to {backup}")
            existing = {}
            created = True
    servers = existing.setdefault("mcpServers", {})
    # Drop stale per-instance entries from the pre-rewrite model.
    for name in list(servers):
        if name.startswith("sandbox-"):
            del servers[name]
    servers[MCP_SERVER_NAME] = _build_mcp_entry(cfg)
    PROJECT_MCP_JSON.write_text(json.dumps(existing, indent=2) + "\n")
    return PROJECT_MCP_JSON, created

SECRETS_ENV = ROOT / ".env.local"

def _local_yaml() -> dict:
    ensure_pyyaml()
    import yaml
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            return yaml.safe_load(f) or {}
    return {}

def _write_local_yaml(local: dict) -> None:
    ensure_pyyaml()
    import yaml
    with CONFIG_LOCAL.open("w") as f:
        yaml.safe_dump(local, f, default_flow_style=False, sort_keys=False)

def _write_env_local(values: dict) -> None:
    """Write/merge KEY=VAL pairs into .env.local. Existing keys are replaced;
    others preserved. Empty values are skipped."""
    existing: dict[str, str] = {}
    if SECRETS_ENV.exists():
        for ln in SECRETS_ENV.read_text().splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                existing[k.strip()] = v
    for k, v in values.items():
        if v:
            existing[k] = v
    lines = ["# Personal secrets for the sandbox — gitignored, never commit.",
             "# Source from your shell or let skills read directly.", ""]
    for k, v in existing.items():
        lines.append(f"{k}={v}")
    SECRETS_ENV.write_text("\n".join(lines) + "\n")
    try:
        SECRETS_ENV.chmod(0o600)
    except OSError:
        pass

def _pkg_manager() -> tuple[str, str] | tuple[None, None]:
    """Detect the platform package manager. Returns (name, sudo_prefix) where
    sudo_prefix is '' for brew (never sudo) or 'sudo ' for apt/dnf."""
    if shutil.which("brew"):
        return ("brew", "")
    if shutil.which("apt-get"):
        return ("apt", "sudo ")
    if shutil.which("dnf"):
        return ("dnf", "sudo ")
    return (None, None)

def _offer_install(label: str, cmd: str, *, verb: str = "Install") -> bool:
    """Offer to run a fix command for a missing/blocked prerequisite. Prompts
    (default No); on 'y' runs it and returns True on success. Non-interactive
    (no TTY) never runs — just prints the command and returns False, so CI/web
    contexts fall back to the printed hint. The user types any sudo password at
    the real prompt. `verb` tailors the wording (e.g. "Install", "Start")."""
    if not sys.stdin.isatty():
        print(f"      → run: {cmd}")
        return False
    try:
        ans = input(f"      {verb} now? [y/N] ({cmd}): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if ans not in ("y", "yes"):
        print(f"      → skipped. Run when ready: {cmd}")
        return False
    print(f"      running: {cmd}")
    # Run through the shell so pipes/&& and sudo prompts work normally.
    res = subprocess.run(cmd, shell=True)
    if res.returncode == 0:
        ok(f"{verb.lower().rstrip('e')}ed {label}")
        return True
    info(f"{verb.lower()} failed (exit {res.returncode}) — run manually: {cmd}")
    return False

def _docker_preflight() -> int:
    """Check prerequisites are usable. Returns problem count. For each missing
    prerequisite, offer to install it (interactive, default No)."""
    problems = 0
    print("\nPrerequisites:")
    pm, sudo = _pkg_manager()

    # Python — script wouldn't have started without it, but warn on too-old
    # versions and on missing `venv` module (Debian/Ubuntu split it out, which
    # silently breaks .cli-venv and mcp/wp-server/.venv).
    py_ver = sys.version_info
    if (py_ver.major, py_ver.minor) < (3, 9):
        problems += 1
        print(f"  ✗ Python {py_ver.major}.{py_ver.minor} is too old (need 3.9+)")
        cmd = ({"brew": "brew install python@3.12",
                "apt": "sudo apt-get install -y python3.12 python3.12-venv",
                "dnf": "sudo dnf install -y python3.12"}.get(pm))
        if cmd:
            print("      A newer python3 lives alongside the old one — install it,")
            print("      then re-run `./sb setup` (it'll use the newer version).")
            _offer_install("python 3.12", cmd)
        else:
            print("      → install Python 3.9+ (or use pyenv: https://github.com/pyenv/pyenv)")
    else:
        print(f"  ✓ python {py_ver.major}.{py_ver.minor}.{py_ver.micro}")

    r = subprocess.run([sys.executable, "-m", "venv", "--help"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        problems += 1
        print("  ✗ python3 `venv` module not available")
        print("      (needed to build .cli-venv and mcp/wp-server/.venv)")
        # Almost always Debian/Ubuntu splitting venv out. Offer the apt install.
        if pm == "apt":
            if _offer_install("python3-venv", "sudo apt-get install -y python3-venv"):
                problems -= 1  # fixed in place
        else:
            print("      → install your distro's python3 venv package")
    else:
        print("  ✓ python3 -m venv works")

    docker_bin = shutil.which("docker")
    if not docker_bin:
        problems += 1
        print("  ✗ docker not found in PATH")
        if pm == "brew":
            print("      Docker Desktop installs the app; you then OPEN it once")
            print("      (accept the license) before `docker` works.")
            if _offer_install("Docker Desktop", "brew install --cask docker"):
                print("      → now OPEN Docker Desktop once, then re-run `./sb setup`.")
        else:
            print("      → install Docker: https://docs.docker.com/get-docker/")
        return problems
    print(f"  ✓ docker found ({docker_bin})")

    r = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if r.returncode != 0:
        # On Linux, distinguish "daemon down" from "I'm not in the docker group"
        # (daemon up, but this user can't reach the socket) — different fix.
        if sys.platform != "darwin":
            sudo_ok = subprocess.run(["sudo", "-n", "docker", "info"],
                                     capture_output=True).returncode == 0
            err = (r.stderr or "")
            if sudo_ok or "permission denied" in err.lower():
                problems += 1
                user = os.environ.get("USER", "$USER")
                print("  ✗ docker is running, but this user can't access it")
                print(f"      → add yourself to the docker group, then re-login:")
                print(f"          sudo usermod -aG docker {user}")
                print(f"          newgrp docker   # or log out/in")
                print(f"        then re-run:  ./sb setup")
                return problems
        problems += 1
        print("  ✗ docker is installed but not running")
        start_cmd = ("open -a Docker" if sys.platform == "darwin"
                     else "sudo systemctl start docker")
        if _offer_install("Docker", start_cmd, verb="Start"):
            # Give the daemon a moment, then re-check so setup can continue.
            import time
            print("      waiting for the Docker daemon…")
            for _ in range(20):
                time.sleep(1)
                if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
                    print("  ✓ docker daemon running")
                    problems -= 1
                    break
            else:
                print("      still not ready — wait a few seconds, then re-run `./sb setup`.")
        if problems:
            return problems
    else:
        print("  ✓ docker daemon running")

    r = subprocess.run(["docker", "compose", "version"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        problems += 1
        print("  ✗ `docker compose` plugin not available")
        print("      (it ships with Docker Desktop — usually means an old/CE")
        print("       Docker without the v2 plugin)")
        print("      → install Compose v2: https://docs.docker.com/compose/install/")
    else:
        print(f"  ✓ {r.stdout.strip()}")
    return problems

def _prompt(label: str, default: str = "", secret: bool = False) -> str:
    """Prompt the user. Empty input keeps the default (or skips if no default)."""
    if default:
        hint = "•••••• (saved)" if secret else default
        suffix = f" [{hint}] (Enter to keep)"
    else:
        suffix = " (Enter to skip)"
    if secret:
        import getpass
        val = getpass.getpass(f"  {label}{suffix}: ").strip()
    else:
        val = input(f"  {label}{suffix}: ").strip()
    return val or default

CONNECT_TARGETS = {
    "fb": "fluentboards", "fluentboards": "fluentboards",
    "gh": "github", "github": "github",
}

def _refresh_env_local() -> None:
    """Mirror current sandbox.local.yml secrets into .env.local."""
    local = _local_yaml()
    fb = local.get("fluentboards", {}) or {}
    _write_env_local({
        "GITHUB_ORG": (local.get("defaults", {}) or {}).get("github_org", ""),
        "FLUENTBOARDS_URL": fb.get("url", ""),
        "FLUENTBOARDS_EMAIL": fb.get("email", ""),
        "FLUENTBOARDS_APP_PASSWORD": fb.get("app_password", ""),
    })

def _connect_fluentboards(cfg, non_interactive: bool = False) -> None:
    local = _local_yaml()
    fb = local.setdefault("fluentboards", {})

    if non_interactive:
        # Read from environment; require at least URL + app password.
        url = os.environ.get("FLUENTBOARDS_URL", "").strip()
        email = os.environ.get("FLUENTBOARDS_EMAIL", "").strip()
        pw = os.environ.get("FLUENTBOARDS_APP_PASSWORD", "").strip()
        if not url or not pw:
            die("--non-interactive requires FLUENTBOARDS_URL and "
                "FLUENTBOARDS_APP_PASSWORD to be set in the environment.")
        fb["url"] = url
        fb["email"] = email
        fb["app_password"] = pw
        _write_local_yaml(local)
        _refresh_env_local()
        ok(f"FluentBoards credentials saved (non-interactive) to "
           f"{CONFIG_LOCAL.name} + {SECRETS_ENV.name}")
        return

    print("\nFluentBoards")
    print("  Used by the standup/report skills to read your My Day cards.")
    print("  Generate an Application Password at:")
    print("    https://projects.startise.com/wp-admin/profile.php#application-passwords-section")
    print("  Press Enter at any prompt to skip / keep existing value.")
    fb["url"] = _prompt("Site URL",
                        fb.get("url") or "https://projects.startise.com")
    fb["email"] = _prompt("Login email", fb.get("email", ""))
    fb["app_password"] = _prompt("Application password",
                                 fb.get("app_password", ""), secret=True)
    _write_local_yaml(local)
    _refresh_env_local()
    ok(f"Saved to {CONFIG_LOCAL.name} + {SECRETS_ENV.name}")

def _gh_cli_user() -> str | None:
    """Return the GitHub username if `gh` is installed AND authenticated."""
    if not shutil.which("gh"):
        return None
    r = subprocess.run(["gh", "auth", "status"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    # `gh api user` is the cleanest source-of-truth for the logged-in handle.
    r = subprocess.run(["gh", "api", "user", "-q", ".login"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None

def _gh_cli_orgs() -> list[str]:
    """Return GitHub orgs the gh-authenticated user belongs to (or [] if none/no-gh)."""
    if not shutil.which("gh"):
        return []
    r = subprocess.run(["gh", "api", "user/orgs", "--jq", ".[].login"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]

def _connect_github(cfg, non_interactive: bool = False) -> None:
    local = _local_yaml()
    defaults = local.setdefault("defaults", {})
    cur = (defaults.get("github_org")
           or (cfg.get("defaults", {}) or {}).get("github_org") or "")

    if non_interactive:
        org = os.environ.get("GITHUB_ORG", "").strip()
        if not org:
            die("--non-interactive requires GITHUB_ORG to be set in the environment.")
        defaults["github_org"] = org
        _write_local_yaml(local)
        _refresh_env_local()
        ok(f"github_org='{org}' saved (non-interactive) to "
           f"{CONFIG_LOCAL.name} + {SECRETS_ENV.name}")
        return

    print("\nGitHub")
    print("  Sets defaults.github_org + ensures the `gh` CLI can read private")
    print("  repos (Pro plugins, private mappings) during git/composer operations.")
    print()

    gh_user = _gh_cli_user()
    orgs = _gh_cli_orgs() if gh_user else []

    if gh_user:
        ok(f"`gh` CLI authenticated as: {gh_user}")

    # Build a numbered menu when we have anything to suggest. Otherwise
    # fall back to a free-form prompt. The current value (if any) is
    # always offered as one of the choices, never silently re-confirmed —
    # so a wrong saved value (a common state for new WPDev hires whose
    # gh login is their personal handle) can't shadow the right answer.
    choices: list[str] = []
    if "WPDevelopers" in orgs:
        choices.append("WPDevelopers")
    for o in orgs:
        if o not in choices:
            choices.append(o)
    if gh_user and gh_user not in choices:
        choices.append(gh_user)
    if cur and cur not in choices:
        choices.append(cur)

    entered = ""
    if choices:
        print()
        print("  Pick a GitHub org/user (default repo resolution falls back here):")
        for i, c in enumerate(choices, 1):
            marker = "  (current)" if c == cur else ("  (recommended for WPDev team)"
                                                    if c == "WPDevelopers" else "")
            print(f"    {i})  {c}{marker}")
        print(f"    {len(choices)+1})  other (type a value)")
        try:
            raw = input("  Pick: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(choices):
                entered = choices[idx - 1]
            elif idx == len(choices) + 1:
                entered = _prompt("Enter org/user", "").strip()
        else:
            entered = raw  # treat as a typed org name
    else:
        entered = _prompt("GitHub org/user", cur).strip()

    if not entered:
        info("(left blank — set later with `./sb connect gh`)")
        return

    defaults["github_org"] = entered
    _write_local_yaml(local)
    _refresh_env_local()
    ok(f"Saved github_org='{entered}' to {CONFIG_LOCAL.name} + {SECRETS_ENV.name}")

def _is_server(args) -> bool:
    """Headless/server mode: skip the desktop-only steps (clean-URL proxy +
    mkcert + GUI askpass, Claude MCP wiring, browser auto-open). True when
    --server is passed, SANDBOX_SERVER=1 is set, or we auto-detect a headless
    box (not macOS, no claude CLI, and no controlling terminal)."""
    if getattr(args, "server", False):
        return True
    if os.environ.get("SANDBOX_SERVER") == "1":
        return True
    return (sys.platform != "darwin"
            and shutil.which("claude") is None
            and not sys.stdin.isatty())

INTROSPECT_PHP = {
"blocks": r"""<?php
$reg = WP_Block_Type_Registry::get_instance();
$out = [];
foreach ($reg->get_all_registered() as $name => $b) {
    $out[] = [
        'name'       => $name,
        'title'      => $b->title ?? '',
        'category'   => $b->category ?? '',
        'attributes' => $b->attributes ?? [],
        'supports'   => $b->supports ?? [],
        'dynamic'    => !empty($b->render_callback),
        'parent'     => $b->parent ?? null,
        'ancestor'   => $b->ancestor ?? null,
    ];
}
echo wp_json_encode(['count' => count($out), 'blocks' => $out], JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
""",

"widgets": r"""<?php
if (!class_exists('\Elementor\Plugin')) {
    echo wp_json_encode(['error' => 'Elementor not active']);
    return;
}
$mgr = \Elementor\Plugin::$instance->widgets_manager;
$out = [];
foreach ($mgr->get_widget_types() as $name => $w) {
    // get_controls() / get_title() avoid get_config()'s full bootstrap which
    // sometimes faults outside an editor request context.
    $controls = [];
    try { $raw = $w->get_controls(); } catch (\Throwable $e) { $raw = []; }
    foreach ((array)$raw as $cname => $c) {
        if (!is_array($c)) continue;
        $entry = ['type' => $c['type'] ?? null];
        if (isset($c['default']))   $entry['default']  = $c['default'];
        if (isset($c['options']))   $entry['options']  = is_array($c['options']) ? array_keys($c['options']) : $c['options'];
        if (isset($c['label']))     $entry['label']    = is_string($c['label']) ? wp_strip_all_tags($c['label']) : '';
        if (isset($c['fields']))    $entry['fields']   = array_keys((array)$c['fields']);
        if (!empty($c['condition'])) $entry['condition'] = $c['condition'];
        if (!empty($c['classes']))  $entry['classes']  = $c['classes'];   // surfaces Pro-only flags
        $controls[$cname] = $entry;
    }
    try { $title = $w->get_title(); } catch (\Throwable $e) { $title = ''; }
    try { $cats  = $w->get_categories(); } catch (\Throwable $e) { $cats = []; }
    $out[] = [
        'name'       => $name,
        'title'      => is_string($title) ? wp_strip_all_tags($title) : '',
        'categories' => $cats,
        'controls'   => $controls,
    ];
}
echo wp_json_encode(['count' => count($out), 'widgets' => $out], JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
""",

"shortcodes": r"""<?php
$out = [];
foreach ($GLOBALS['shortcode_tags'] ?? [] as $tag => $cb) {
    $callback = '';
    if (is_string($cb))         $callback = $cb;
    elseif (is_array($cb))      $callback = (is_object($cb[0]) ? get_class($cb[0]) : (string)$cb[0]) . '::' . $cb[1];
    elseif ($cb instanceof Closure) $callback = 'Closure';
    $out[] = ['tag' => $tag, 'callback' => $callback];
}
echo wp_json_encode(['count' => count($out), 'shortcodes' => $out], JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
""",
}

def _global_link_dir() -> tuple[Path, bool]:
    """Pick where to drop the global `sb` symlink. Returns (dir, needs_sudo).

    Preference order, first that's on PATH:
      1. a user dir we can write without sudo  (~/.local/bin, ~/bin)
      2. Homebrew bin                          (/opt/homebrew/bin, /usr/local/bin)
      3. /usr/local/bin with sudo
    Falling back to ~/.local/bin (creating it) if nothing on PATH is writable —
    the caller then tells the user to add it to PATH."""
    path_dirs = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    on_path = lambda d: d in path_dirs
    home = Path.home()
    # 1. user-writable dirs already on PATH (no sudo)
    for d in (home / ".local" / "bin", home / "bin",
              Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        if on_path(d) and os.access(d, os.W_OK):
            return d, False
    # 2. /usr/local/bin on PATH but not writable → sudo
    ulb = Path("/usr/local/bin")
    if on_path(ulb) and ulb.exists():
        return ulb, True
    # 3. last resort: ~/.local/bin (create it), warn about PATH
    return home / ".local" / "bin", False

def _next_free_port(start: int, used: set[int]) -> int:
    """Find the next port >= start that is not in `used` and not bound by
    another local listener. Avoids handing out a port the OS will reject."""
    import socket
    p = start
    while True:
        if p not in used:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", p))
                    return p
                except OSError:
                    pass
        p += 1

def _pick_instance_ports(cfg: dict) -> dict[str, int]:
    """Pick wordpress_port, db_port, mailpit_port for a new instance.

    Walks every defined instance's resolved config to collect ports
    already claimed, then bumps each base by 1 until we find a free trio.
    Bases are picked off `main`'s defaults so new instances stay in the
    same numeric neighborhood (8188 → 8189 → 8190 …).
    """
    instances = resolve_instances(cfg)
    used: set[int] = set()
    for inst in instances.values():
        used.update({inst["wordpress_port"], inst["db_port"], inst["mailpit_port"]})
    # Base ports for the instance neighborhood (8188, 8189, …); overridable via
    # the top-level runtime: block. The base itself is assignable now that there
    # is no `main` instance occupying it (start AT the base, not base+1).
    runtime = cfg.get("runtime", {}) or {}
    base_wp = runtime.get("wordpress_port", 8188)
    base_db = runtime.get("db_port", 3318)
    base_mp = runtime.get("mailpit_port", 8125)
    return {
        "wordpress_port": _next_free_port(base_wp, used),
        "db_port": _next_free_port(base_db, used),
        "mailpit_port": _next_free_port(base_mp, used),
    }

def _port_busy_by_other(port: int, own_project: str) -> bool:
    """True if `port` is bound by something OTHER than this instance's own
    compose project. A port published by our own (already-running) container is
    NOT a conflict — re-running setup on a healthy install must not churn ports.
    """
    import socket
    # Free to bind → not busy at all.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port)); return False
        except OSError:
            pass
    # Busy — is it OUR container holding it? Check docker for a published port
    # owned by own_project. If yes, not a conflict.
    res = subprocess.run(
        ["docker", "ps", "--filter", f"publish={port}",
         "--format", "{{.Names}}"], capture_output=True, text=True)
    names = (res.stdout or "")
    return own_project not in names

def _resolve_port_conflicts(cfg: dict) -> dict:
    """Before booting, ensure each instance's ports are free (or already ours).
    If a port collides with another listener, bump the whole instance to a free
    trio and persist to sandbox.local.yml. Returns the (possibly reloaded) cfg.
    """
    instances = resolve_instances(cfg)
    used: set[int] = set()
    for ic in instances.values():
        used.update({ic["wordpress_port"], ic["db_port"], ic["mailpit_port"]})
    changed = False
    local = _local_yaml()
    for name, ic in instances.items():
        proj = project_name(name)
        wp, db, mp = ic["wordpress_port"], ic["db_port"], ic["mailpit_port"]
        conflict = (_port_busy_by_other(wp, proj)
                    or _port_busy_by_other(db, proj)
                    or _port_busy_by_other(mp, proj))
        if not conflict:
            continue
        # Pick a fresh free trio (avoid all currently-claimed ports).
        used.discard(wp); used.discard(db); used.discard(mp)
        new_wp = _next_free_port(max(wp, 8188), used); used.add(new_wp)
        new_db = _next_free_port(max(db, 3318), used); used.add(new_db)
        new_mp = _next_free_port(max(mp, 8125), used); used.add(new_mp)
        info(f"port conflict for '{name}' (WP {wp}/DB {db}/mail {mp} busy) → "
             f"using WP {new_wp}/DB {new_db}/mail {new_mp}")
        blk = local.setdefault("instances", {}).setdefault(name, {})
        blk["wordpress_port"], blk["db_port"], blk["mailpit_port"] = \
            new_wp, new_db, new_mp
        changed = True
    if changed:
        _write_local_yaml(local)
        cfg = load_config()
        write_compose_files(cfg)
        ok("adjusted ports to avoid conflicts (saved to sandbox.local.yml)")
    return cfg

def _relax_perms_for_uid_switch(inst: str) -> None:
    """Make the instance's WP files readable+writable across web-server uids.

    apache/nginx serve as www-data (uid 33); OpenLiteSpeed runs lsphp as uid
    1000. The WP files are the SAME bind-mounted host dir (runtime/wp-<inst>) —
    switching servers doesn't move data, but files written under one uid must
    stay readable (and uploads writable) under the other. The real fix for a
    bind-mounted dev tree is permissive group/other bits: a+rX everywhere, and
    a+rwX on the writable trees (uploads, cache, upgrade). No sudo, no chown —
    works whoever owns the files on the host."""
    root = wp_dir(inst)
    if not root.exists():
        return
    info(f"relaxing file perms on {root.name} so both web-server uids can read/write…")
    for p in root.rglob("*"):
        try:
            if p.is_dir():
                p.chmod(p.stat().st_mode | 0o055)        # a+rx on dirs
            else:
                p.chmod(p.stat().st_mode | 0o044)        # a+r on files
        except OSError:
            pass
    # The trees WP writes to at runtime need group/other write too.
    for sub in ("wp-content/uploads", "wp-content/cache", "wp-content/upgrade"):
        d = root / sub
        for p in [d, *d.rglob("*")] if d.exists() else []:
            try:
                p.chmod(p.stat().st_mode | (0o022 if p.is_dir() else 0o022))
            except OSError:
                pass

def _onboard_instance(cfg: dict, name: str, args) -> None:
    from sandbox.commands.instances_cmd import cmd_focus
    from sandbox.commands.wp import cmd_seed
    """Post-install onboarding for a freshly created instance: install plugins/
    projects, enable WP_DEBUG, activate a theme, import seed content. Driven by
    flags (--project/--plugin/--seed/--theme/--wp-debug) for non-interactive
    callers (web UI, CI); on a terminal with NO such flags and without
    --minimal, prompt for each (like `./sb setup`'s post-setup picker). Every
    step is best-effort so one failure never leaves the instance half-made."""
    import types as _t
    interactive = sys.stdin.isatty() and not getattr(args, "minimal", False)

    # ---- collect choices (from flags, else prompt) ----
    slugs = list(getattr(args, "plugins", None) or [])
    seed = getattr(args, "seed", None)
    theme = getattr(args, "theme", None)
    wp_debug = bool(getattr(args, "wp_debug", False))
    flags_given = bool(slugs or seed or theme or wp_debug
                       or getattr(args, "site_title", None))

    if interactive and not flags_given:
        # Per-project model: there's no catalog to pick from. Install wp.org
        # plugins by slug below (or pass --plugin), and set up dev plugins by
        # cd-ing into their repo and running `./sb init`.
        try:
            raw = input("  Install wp.org plugins now? (space-separated slugs, "
                        "blank to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        slugs.extend(tok for tok in raw.replace(",", " ").split() if tok)
        seeds = _web_list_seeds()
        if seeds:
            print(f"\n  Seed demo content? [{', '.join(seeds)}] (blank to skip):")
            try:
                s = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                s = ""
            if s in seeds:
                seed = s
        try:
            d = input("  Enable WP_DEBUG? [y/N]: ").strip().lower()
            wp_debug = d in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            pass

    # ---- apply ----
    if slugs:
        # Per-project model: slugs are wp.org plugins installed into this
        # instance. Dev plugins live in their own repos (cd in + `./sb init`).
        print(f"\n▸ Installing wp.org plugins on '{name}': {', '.join(slugs)}")
        wpcli(["plugin", "install", *slugs, "--activate"],
              instance=name, check=False)
        # Default Claude's focus to the first plugin.
        try:
            cmd_focus(cfg, _t.SimpleNamespace(
                resolved_instance=name, slug=slugs[0], clear=False))
        except Exception:
            pass

    if wp_debug:
        info("enabling WP_DEBUG")
        wpcli(["config", "set", "WP_DEBUG", "true", "--raw",
               "--type=constant"], instance=name, check=False)

    if theme:
        info(f"activating theme {theme}")
        r = wpcli(["theme", "activate", theme], instance=name, check=False,
                  capture=True)
        if getattr(r, "returncode", 1) != 0:
            wpcli(["theme", "install", theme, "--activate"],
                  instance=name, check=False)

    if seed:
        print(f"\n▸ Importing seed content '{seed}'…")
        try:
            cmd_seed(cfg, _t.SimpleNamespace(resolved_instance=name, file=seed))
        except Exception as e:
            info(f"seed import failed: {e}")

def _core():
    """Lazy import of the shared sandbox_core module (CLI + MCP share it).
    ROOT is the resolved sandbox dir, so this works even via the global symlink."""
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import sandbox_core
    return sandbox_core

def _cwd_instance() -> str | None:
    """Resolve the instance owning the current working directory's project via
    the on-disk registry. Returns the instance name, or None when cwd isn't a
    registered project (the caller then errors — there is no fallback instance).

    This is what lets `sb <cmd>` (no --instance) target the project you're
    standing in — mirroring how the MCP tools route by project_dir."""
    sc = _core()
    try:
        root = sc.find_project_root(Path.cwd())
    except Exception:
        return None
    entry = sc.registry_get(str(root))
    return entry.get("instance") if entry else None

def _derive_instance_name(root: str, taken: set) -> str:
    """A valid, unique instance name from a project dir basename."""
    # Truncate to 24 first, THEN strip dashes, so a cut that lands on a hyphen
    # (e.g. "templately-nav-menu-url-replace"[:24] → "templately-nav-menu-url-")
    # doesn't leave an invalid trailing hyphen in the instance name / domain.
    base = re.sub(r"[^a-z0-9]+", "-", Path(root).name.lower())[:24].strip("-") or "proj"
    if not re.match(r"^[a-z0-9]", base):
        base = "p-" + base
    name, i = base, 2
    while name in taken:
        name, i = f"{base}-{i}", i + 1
    return name

def _web_image(server: str, php=None, wp=None, explicit=None) -> str:
    """Resolve the web-tier container image for a server stack + version pins.

    WordPress version is intentionally excluded from the image tag — following
    @wordpress/env's approach: use a PHP-only base image and download the exact
    WP version inside the container via `wp core download --version=X.Y.Z`.
    This avoids 'manifest unknown' errors when Docker Hub hasn't published a
    patch-level tag yet (e.g. wordpress:6.9.4-php8.1 may not exist, but
    wordpress:php8.1 always does).

    An explicit, non-default `wordpress_image` from sandbox.yml always wins.
    No pins → the per-server default (latest)."""
    if explicit and explicit not in ("wordpress:latest", "wordpress"):
        return explicit
    if server == "litespeed":
        return f"litespeedtech/openlitespeed:1.8.2-lsphp{(php or '8.3').replace('.', '')}"
    fpm = "-fpm" if server == "nginx" else ""
    if php:
        return f"wordpress:php{php}{fpm}"
    return "wordpress:php8.3-fpm" if server == "nginx" else "wordpress:latest"

def _cli_image(php=None) -> str:
    """The wp-cli helper image. Matches the pinned PHP so composer + phpunit
    (which run in the wpcli container) execute under the project's PHP."""
    return f"wordpress:cli-php{php}" if php else "wordpress:cli"

def _warn_version_drift(cfg: dict, instance_name, pconf: dict) -> None:
    """Warn when a project's config version pins no longer match the running
    instance's image. ensure_instance returns the live instance unchanged, so
    without this the skew is silent (tests can run against a different WP/PHP
    than the live site)."""
    if not instance_name:
        return
    inst = resolve_instances(cfg).get(instance_name, {})
    norm = lambda v: str(v) if v not in (None, "") else None
    cur = (norm(inst.get("php_version")), norm(inst.get("wp_version")))
    want = (norm(pconf.get("phpVersion")), norm(pconf.get("wpVersion")))
    if cur == want:
        return
    info(f"⚠ '{instance_name}' is running php={cur[0] or 'latest'}/wp={cur[1] or 'latest'} "
         f"but config now pins php={want[0] or 'latest'}/wp={want[1] or 'latest'}. "
         f"Recreate to apply: ./sb instance delete {instance_name}, then re-run.")

def _wait_http(port: int, timeout: int = 30) -> bool:
    import urllib.request
    import time
    for _ in range(timeout):
        try:
            urllib.request.urlopen(f"http://localhost:{port}", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False

def _wait_reachable(inst_cfg: dict, timeout: int = 30) -> bool:
    """Wait until WP resolves a site at its REAL URL — the proxy
    https://<name>.<tld> when secured, else localhost:<port>. Unlike _wait_http
    (port-only), this is correct for a secured multisite, whose
    DOMAIN_CURRENT_SITE is the .tld host so localhost:<port> 500s 'Site not
    found'. A 4xx (login redirect) counts as up; 5xx keeps waiting."""
    import ssl
    import time
    import urllib.error
    import urllib.request
    url = site_url(inst_cfg)
    ctx = ssl._create_unverified_context()
    for _ in range(timeout):
        try:
            urllib.request.urlopen(url, timeout=2, context=ctx)
            return True
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

def _instance_reachable(entry: dict) -> bool:
    """Probe the instance's canonical URL — localhost:<port> for a plain docker
    instance, or its https://<name>.<tld> proxy URL when secured. A secured
    MULTISITE 500s on localhost (its DOMAIN_CURRENT_SITE is the .tld host), so we
    must probe the real URL, not the port. The mkcert CA may be OS-trusted but
    isn't in Python's bundle, so skip cert verification; we only ask 'does WP
    resolve a site here'. A 4xx (e.g. a login redirect) still counts as up."""
    import ssl
    import urllib.error
    import urllib.request
    url = entry.get("url")
    if not url:
        port = entry.get("wordpress_port")
        if not port:
            return False
        url = f"http://localhost:{port}"
    try:
        urllib.request.urlopen(
            url, timeout=3, context=ssl._create_unverified_context())
        return True
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False

def _wire_project_plugins(name: str, root: str, pconf: dict) -> None:
    """Symlink the project's plugins + mappings into the instance, then activate.

    Relative mapping sources ("." or "../sibling") are resolved relative to
    root_path (the project dir), matching wp-env's convention.
    NOTE: absolute symlinks only resolve inside the container when the target is
    under a bind-mounted host path. Sources under defaults.plugins_home work
    today; a project root outside it needs its own bind mount (T0.4 follow-up)."""
    pdir = plugins_dir(name)
    pdir.mkdir(parents=True, exist_ok=True)
    root_path = Path(root)
    activate, zips, slugs = [], [], []
    for entry in pconf.get("plugins") or ["."]:
        if entry == ".":
            slug = root_path.name
            _force_symlink(pdir / slug, root_path)
            activate.append(slug)
        elif isinstance(entry, str) and entry.startswith("http") and entry.endswith(".zip"):
            zips.append(entry)
        elif isinstance(entry, str) and ("/" in entry or entry.startswith((".", "~"))):
            src = Path(entry).expanduser()
            if not src.is_absolute():
                src = (root_path / entry).resolve()
            if src.exists():
                _force_symlink(pdir / src.name, src)
                activate.append(src.name)
        elif entry:
            slugs.append(entry)
    wp_root = wp_dir(name)
    map_activate: list[str] = []
    for wp_path, src in (pconf.get("mappings") or {}).items():
        src_p = Path(str(src)).expanduser()
        if not src_p.is_absolute():
            src_p = (root_path / src_p).resolve()
        if src_p.exists():
            dest = wp_root / wp_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            _force_symlink(dest, src_p)
            # Activate if this is a plugin mapping (wp-content/plugins/<slug>).
            parts = wp_path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "wp-content" and parts[1] == "plugins":
                map_activate.append(parts[2])
    # mappings_inactive: mount the symlink but do NOT activate.
    for wp_path, src in (pconf.get("mappings_inactive") or {}).items():
        src_p = Path(str(src)).expanduser()
        if not src_p.is_absolute():
            src_p = (root_path / src_p).resolve()
        if src_p.exists():
            dest = wp_root / wp_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            _force_symlink(dest, src_p)
    # Install only what isn't already present; reinstalling an existing plugin
    # just emits "Plugin already installed." noise. Already-present ones still
    # get activated (install --activate only activates the freshly installed).
    install, reactivate = [], []
    for entry in zips + slugs:
        (reactivate if (pdir / _pkg_slug(entry)).exists() else install).append(entry)
    if install:
        wpcli(["plugin", "install", *install, "--activate"],
              instance=name, check=False)
    if reactivate:
        wpcli(["plugin", "activate", *[_pkg_slug(e) for e in reactivate]],
              instance=name, check=False)
    if activate:
        wpcli(["plugin", "activate", *activate], instance=name, check=False)
    if map_activate:
        wpcli(["plugin", "activate", *map_activate], instance=name, check=False)

def _pkg_slug(entry: str) -> str:
    """Best-effort slug from a plugin/theme entry: wp.org slug as-is; zip URL by
    basename minus the version tail (twentytwentyfour.1.5.zip →
    twentytwentyfour); local path by directory name."""
    if entry.startswith("http"):
        base = entry.rstrip("/").rsplit("/", 1)[-1]
        base = base[:-4] if base.endswith(".zip") else base
        return re.sub(r"\.\d[\d.]*$", "", base)
    if "/" in entry or entry.startswith((".", "~")):
        return Path(entry).expanduser().name
    return entry

def _wire_project_themes(name: str, root: str, pconf: dict) -> None:
    """Install the project's `themes` and activate the FIRST one. Same entry
    forms as plugins: wp.org slug, zip URL, or local path (symlinked in —
    needs an extra_mounts bind like plugin paths do). Activation is a
    separate explicit step: `theme install --activate` is silently ignored
    on multisite installs."""
    themes = [t for t in (pconf.get("themes") or []) if t and isinstance(t, str)]
    if not themes:
        return
    tdir = wp_dir(name) / "wp-content" / "themes"
    tdir.mkdir(parents=True, exist_ok=True)
    root_path = Path(root)
    for entry in themes:
        if entry.startswith("http") and entry.endswith(".zip"):
            if (tdir / _pkg_slug(entry)).exists():
                continue  # already present (bundled / prior provision) — skip noisy reinstall
            wpcli(["theme", "install", entry], instance=name, check=False)
        elif "/" in entry or entry.startswith((".", "~")):
            src = Path(entry).expanduser()
            if not src.is_absolute():
                src = (root_path / entry).resolve()
            if src.exists():
                _force_symlink(tdir / src.name, src)
        elif (tdir / _pkg_slug(entry)).exists():
            # Already present (e.g. a default theme bundled with WP core, or a
            # prior provision). Reinstalling only emits "Destination folder
            # already exists" + "Error: No themes installed." noise — skip it.
            continue
        else:
            wpcli(["theme", "install", entry], instance=name, check=False)
    wpcli(["theme", "activate", _pkg_slug(themes[0])],
          instance=name, check=False)

def _herd_isolated_php(name: str) -> str | None:
    """The PHP version Herd reports as serving <name>.test, parsed from
    `herd isolated` (the table-of-truth for web-tier isolation). Returns e.g.
    "8.1", or None if the site isn't listed / output can't be parsed. Used to
    VERIFY isolation took, since `isolate` can exit 0 yet not stick on a fresh
    link."""
    domain = _herd_domain(name)
    res = _herd("isolated")
    if res.returncode != 0:
        return None
    for line in (res.stdout or "").splitlines():
        # rows look like: | tmpl-foo.test | 8.1 |
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) == 2 and cells[0] == domain:
            m = re.search(r"\d+\.\d+", cells[1])
            if m:
                return m.group(0)
    return None

def _herd_isolate(name: str, php_v: str) -> bool:
    """Isolate <name>.test to php@<php_v> DETERMINISTICALLY and VERIFY.

    The site must already be registered (link + secure done) — on a fresh link
    Herd's site list isn't populated and isolate fails with "site could not be
    found". We target the site explicitly with --site=<name> (the linked alias,
    not the cwd basename `wp-<name>` that a bare `isolate` would resolve), then
    re-query `herd isolated` and retry once. Returns True iff Herd confirms the
    site serves the requested version."""
    want = re.search(r"\d+\.\d+", str(php_v))
    want = want.group(0) if want else str(php_v)
    for attempt in (1, 2):
        r = _herd("isolate", f"php@{php_v}", "--site", name)
        got = _herd_isolated_php(name)
        if got == want:
            return True
        if attempt == 1:
            info(f"herd: isolate not yet confirmed (got {got or 'default'}), "
                 f"retrying…")
            if r.returncode != 0:
                info(f"  isolate said: {(r.stderr or r.stdout or '').strip()[:200]}")
    return False

def _provision_herd(name: str, pconf: dict) -> None:
    """Host-side counterpart of `compose up`: make runtime/wp-<name>/ a Herd
    site at https://<name>.test. `herd link` serves the dir, `secure` mints +
    trusts the .test TLS cert, then `isolate` pins the site's PHP to the
    project's phpVersion. Isolate runs LAST (after the site is registered) and
    is verified+retried — on a fresh link it otherwise fails ("site could not
    be found"). CLI/phpunit run the same pinned PHP via _herd_php().
    Everything is idempotent; isolate/secure failures degrade, not abort."""
    _host_wp()   # fail fast with a clear message if host wp-cli is missing
    wpd = wp_dir(name)
    wpd.mkdir(parents=True, exist_ok=True)
    info(f"herd: linking {wpd.name}/ → {_herd_domain(name)} …")
    res = _herd("link", name, cwd=wpd)
    if res.returncode != 0:
        die(f"herd link failed: {(res.stderr or res.stdout or '').strip()[:400]}")
    info(f"herd: securing https://{_herd_domain(name)} …")
    r = _herd("secure", name)
    if r.returncode != 0:
        info(f"⚠ herd secure failed (site stays on http): "
             f"{(r.stderr or r.stdout or '').strip()[:200]}")
    # Isolate AFTER secure: the site is now in Herd's list, so isolate resolves
    # and sticks. Verified against `herd isolated` so the web tier really serves
    # the pinned PHP (a silent default would defeat the point of the pin).
    php_v = pconf.get("phpVersion")
    if php_v:
        info(f"herd: isolating PHP {php_v} for {name} …")
        if _herd_isolate(name, str(php_v)):
            ok(f"herd: {name}.test pinned to PHP {php_v} (web tier)")
        else:
            info(f"⚠ herd isolate php@{php_v} could not be confirmed — the web "
                 f"tier may run Herd's default PHP. CLI/phpunit still use the "
                 f"pinned binary. Try: herd isolate php@{php_v} --site {name}")

def _build_instance_block(cfg: dict, name: str, root: str, pconf: dict,
                          ports: dict, server: str) -> dict:
    """Construct the sandbox.local.yml `instances.<name>` block from a project's
    resolved config. Captures ports, server, version pins, wp-config constants,
    the multisite flag, and any extra bind-mounts. Shared by ensure_instance
    (first boot) and cmd_apply_config (in-place reconcile) so both render the
    SAME block — the single source of truth for what compose generates."""
    php_v = pconf.get("phpVersion")
    wp_v = pconf.get("wpVersion")
    block = {
        "wordpress_port": ports["wordpress_port"],
        "db_port": ports["db_port"],
        "mailpit_port": ports["mailpit_port"],
        "server": server,
        "admin": {"site_title": f"Sandbox {name}"},
    }
    if server == "herd":
        # Herd serves the linked dir at <name>.test — that domain IS the URL.
        block["domain"] = _herd_domain(name)
    # Store version pins (not a baked image) so the image resolves
    # server-aware at compose time and `wp core download` honors wpVersion.
    if php_v:
        block["php_version"] = str(php_v)
    if wp_v:
        block["wp_version"] = str(wp_v)
    # Project wp-config constants + multisite flag live in the instance
    # block so compose regeneration (every `sb` invocation) keeps rendering
    # them into WORDPRESS_CONFIG_EXTRA — that's what survives down/up.
    if pconf.get("config"):
        block["wp_config"] = pconf["config"]
    if pconf.get("multisite"):
        block["multisite"] = pconf["multisite"]
    # Local proxy TLD (sandbox.config.json `tld`, default tst). Persisted so the
    # MCP server + every `sb` invocation match a domain against its own TLD.
    block["tld"] = pconf.get("tld") or PROXY_TLD

    # Preserve a domain already assigned to this instance (by `domains setup` or
    # secure-at-create) so a re-ensure/apply doesn't drop it — otherwise the
    # proxy route + WP's https URL silently revert to localhost on the next run.
    if server != "herd":
        _prev = _local_yaml().get("instances", {}).get(name, {})
        if _prev.get("domain"):
            block["domain"] = _prev["domain"]
            if _prev.get("tld"):
                block["tld"] = _prev["tld"]

    # Collect source paths that need extra Docker bind-mounts — any mapping
    # source or local plugin path outside plugins_home won't be visible
    # inside the container, so symlinks there are dangling and WP skips them.
    plugins_home_p = _plugins_home(cfg).resolve()
    root_p = Path(root)
    _extra: list[str] = []
    for _entry in list(pconf.get("plugins") or []) + list(pconf.get("themes") or []):
        if _entry == ".":
            _src = root_p
        elif isinstance(_entry, str) and ("/" in _entry or _entry.startswith((".", "~"))):
            _src = Path(_entry).expanduser()
            if not _src.is_absolute():
                _src = (root_p / _entry).resolve()
            _src = _src.resolve()
        else:
            continue
        if _src.exists() and not _src.resolve().is_relative_to(plugins_home_p):
            _extra.append(str(_src))
    for _src_raw in (pconf.get("mappings") or {}).values():
        _src = Path(str(_src_raw)).expanduser()
        if not _src.is_absolute():
            _src = (root_p / _src).resolve()
        _src = _src.resolve()
        if _src.exists() and not _src.is_relative_to(plugins_home_p):
            _extra.append(str(_src))
    for _src_raw in (pconf.get("mappings_inactive") or {}).values():
        _src = Path(str(_src_raw)).expanduser()
        if not _src.is_absolute():
            _src = (root_p / _src).resolve()
        _src = _src.resolve()
        if _src.exists() and not _src.is_relative_to(plugins_home_p):
            _extra.append(str(_src))
    extra_mounts = list(dict.fromkeys(_extra))  # deduplicate, preserve order
    if extra_mounts:
        block["extra_mounts"] = extra_mounts
    return block

def ensure_instance(cfg: dict, project_dir: str) -> dict:
    from sandbox.commands.lifecycle import cmd_up, cmd_install
    """Create-if-missing: boot a per-directory instance for the project at
    `project_dir`, keyed by its canonical root in the registry. Idempotent — a
    second call for a ready project returns the existing record."""
    import types
    sc = _core()
    pconf = sc.load_project_config(project_dir)
    root = pconf["root"]

    with sc.project_lock(root):
        existing = sc.registry_get(root)
        if existing and existing.get("status") == "ready" \
                and _instance_reachable(existing):
            # Already up. If the config's version pins no longer match the
            # running instance's image, say so loudly — silently returning the
            # stale record would let tests run against a different WP/PHP than
            # the live site. Re-versioning in place is a tracked follow-up; for
            # now the instance must be recreated to apply a changed pin.
            _warn_version_drift(cfg, existing.get("instance"), pconf)
            return existing

        # Resume a prior record for this root (a partial/failed boot, or a
        # stopped instance) by REUSING its name + ports rather than deriving a
        # fresh `<name>-2` and orphaning the half-built stack. Only when there's
        # no record at all do we allocate a new name + ports.
        if existing and existing.get("instance"):
            name = existing["instance"]
            ports = {
                "wordpress_port": existing["wordpress_port"],
                "db_port": existing["db_port"],
                "mailpit_port": existing["mailpit_port"],
            }
        else:
            taken = set(resolve_instances(cfg).keys())
            taken |= {e.get("instance") for e in sc.registry_all().values()
                      if e.get("instance")}
            name = _derive_instance_name(root, taken)
            ports = _pick_instance_ports(cfg)

        server = _valid_server(pconf.get("server") or "apache")
        php_v = pconf.get("phpVersion")
        wp_v = pconf.get("wpVersion")
        info(f"ensure_instance: {root} → instance '{name}' "
             f"(WP={ports['wordpress_port']} server={server}"
             f"{f' php={php_v}' if php_v else ''}{f' wp={wp_v}' if wp_v else ''})")

        block = _build_instance_block(cfg, name, root, pconf, ports, server)

        local = _local_yaml()
        local.setdefault("instances", {})[name] = block
        _write_local_yaml(local)

        # Record a 'pending' mapping BEFORE booting so a mid-boot crash leaves a
        # resumable record (the reuse branch above finds it) instead of an
        # orphan that forces the next run to a duplicate `<name>-2`.
        sc.registry_put(root, instance=name, status="pending",
                        wordpress_port=ports["wordpress_port"],
                        db_port=ports["db_port"],
                        mailpit_port=ports["mailpit_port"],
                        server=server)

        cfg = load_config()
        write_compose_files(cfg)

        ns = types.SimpleNamespace(resolved_instance=name)
        if server == "herd":
            # Host driver: link + isolate + secure replace the docker boot.
            _provision_herd(name, pconf)
        else:
            cmd_up(cfg, ns)
            _wait_http(ports["wordpress_port"])
            # Secure-at-create: when the clean-URL proxy is already set up, give
            # the instance its https://<name>.<tld> BEFORE install so WP never
            # stores an http localhost URL (whose port leaks into redirects).
            # Single-site only; falls back to localhost otherwise.
            if _proxy_sudoers_installed() and _secure_at_create(cfg, name):
                cfg = load_config()
        cmd_install(cfg, ns)
        # Multisite goes live only when the web tier reboots WITH the MULTISITE
        # constants that multisite-convert's marker (written inside cmd_install)
        # just enabled — and, when secured, with DOMAIN_CURRENT_SITE = <name>.
        # <tld> matching the network domain convert stored. Re-render compose +
        # recreate the web tier so multisite resolves in THIS pass (otherwise
        # wp-cli + HTTP 500 'Site not found' until the next boot). Plugin/theme
        # wiring below then runs against a working network.
        if server != "herd" and _multisite_mode(resolve_instances(cfg)[name]):
            write_compose_files(cfg)
            compose("up", "-d", "--force-recreate", "wp",
                    *(["nginx"] if server == "nginx" else []),
                    instance=name, check=False)
            _wait_reachable(resolve_instances(cfg)[name])
        _wire_project_plugins(name, root, pconf)
        _wire_project_themes(name, root, pconf)

        # Read the autologin token that cmd_install just generated so we can
        # include login_url in the return value for the agent / human.
        _local_data = _local_yaml()
        _autologin = (_local_data.get("instances", {}).get(name, {})
                      .get("autologin_token", ""))
        # Report the instance's real browser URL: its clean https://<name>.<tld>
        # when secured (herd, or secure-at-create above), else localhost:<port>.
        _base_url = site_url(resolve_instances(cfg)[name])
        _login_url = f"{_base_url}/?sandbox_autologin={_autologin}" if _autologin else ""

        return sc.registry_put(
            root,
            instance=name,
            url=_base_url,
            login_url=_login_url,
            admin_url=f"{_base_url}/wp-admin/",
            wordpress_port=ports["wordpress_port"],
            db_port=ports["db_port"],
            mailpit_port=ports["mailpit_port"],
            server=server,
            wp_version=pconf.get("wpVersion"),
            source=pconf.get("source"),
            status="ready",
        )

def apply_config(cfg: dict, project_dir: str) -> dict:
    """Reconcile a RUNNING instance with its project config — WITHOUT dropping
    the DB or uploads. This is the in-place counterpart to recreate_instance
    (which wipes data). Use it after editing sandbox.config.json (toggling
    TEMPLATELY_DEV_API / WP_DEBUG, adding a plugin/theme, enabling multisite).

    Steps:
      1. Rebuild the instance block from the current project config and persist
         it to sandbox.local.yml (constants land in WORDPRESS_CONFIG_EXTRA).
      2. Regenerate compose + `compose up -d --force-recreate` the web tier so
         the new env/mounts take effect. The DB volume is untouched, so no data
         loss; the WP entrypoint re-renders wp-config.php from the new env.
      3. Re-sync plugin/theme symlinks + installs (idempotent).
      4. Run multisite-convert if multisite was newly enabled (idempotent —
         _convert_multisite skips an already-converted network).

    Version pins (php/wp) that change are reflected in compose, but an ALREADY
    BOOTED container keeps its image until recreated; --force-recreate here
    re-pulls/recreates the web tier, so a new php_version pin DOES take effect.
    A changed wp_version needs `wp core download --force` (not done here to
    avoid surprising core swaps); we warn instead.

    Returns the updated registry record. Errors if the project has no instance
    yet (caller should ensure_instance first)."""
    import types
    sc = _core()
    pconf = sc.load_project_config(project_dir)
    root = pconf["root"]

    with sc.project_lock(root):
        existing = sc.registry_get(root)
        if not (existing and existing.get("instance")):
            raise sc.ConfigError(
                f"no instance for {root} yet — run ensure_instance first.")
        name = existing["instance"]
        ports = {
            "wordpress_port": existing["wordpress_port"],
            "db_port": existing["db_port"],
            "mailpit_port": existing["mailpit_port"],
        }
        server = _valid_server(pconf.get("server") or existing.get("server")
                               or "apache")

        # Detect whether multisite is being turned on now (was off in the live
        # block) so we can run the convert after the recreate.
        prev_block = (_local_yaml().get("instances", {}).get(name, {}))
        prev_ms = _multisite_mode(prev_block)

        # 1. Rewrite the instance block from the current config.
        block = _build_instance_block(cfg, name, root, pconf, ports, server)
        local = _local_yaml()
        local.setdefault("instances", {})[name] = block
        _write_local_yaml(local)

        # 2. Regenerate compose + recreate the web tier in place (no DB drop).
        #    herd has no web tier to recreate — constants are literal in the
        #    host wp-config, so re-pin them instead.
        cfg = load_config()
        write_compose_files(cfg)
        inst_cfg = resolve_instances(cfg)[name]
        info(f"apply_config: reconciling '{name}' in place (no data loss)…")
        if server == "herd":
            _pin_wp_constants_in_config(name, inst_cfg)
        else:
            compose("up", "-d", "--force-recreate",
                    *_web_services(inst_cfg.get("server", "apache")),
                    instance=name, check=False)
            _wait_http(ports["wordpress_port"])
            # Re-assert the SSL + mail mu-plugins (recreate may have reset
            # nothing, but keep them guaranteed-present like cmd_up does).
            if wp_dir(name).exists():
                _write_mail_muplugin(name)

        # 3. Re-sync plugins + themes (idempotent symlinks + installs).
        _wire_project_plugins(name, root, pconf)
        _wire_project_themes(name, root, pconf)

        # 4. Multisite: convert if newly enabled. Skip if it was already a
        #    network (idempotent) or if the config still disables multisite.
        cur_ms = _multisite_mode(inst_cfg)
        if cur_ms and not prev_ms:
            info(f"apply_config: multisite newly enabled ({cur_ms}) — converting…")
            _convert_multisite(name, inst_cfg)
        elif cur_ms and prev_ms and cur_ms != prev_ms:
            info(f"⚠ multisite mode change {prev_ms}→{cur_ms} can't be applied "
                 f"in place — recreate the instance to switch network type.")

        # Warn (don't act) on a wp_version pin change — swapping core under a
        # live DB is destructive-adjacent; leave it to an explicit recreate.
        _warn_version_drift(cfg, name, pconf)

        base_url = existing.get("url") or f"http://localhost:{ports['wordpress_port']}"
        return sc.registry_put(
            root,
            instance=name,
            url=base_url,
            status="ready",
            server=server,
            wp_version=pconf.get("wpVersion"),
            source=pconf.get("source"),
        )

TEST_SUITE_DIR = ROOT / "runtime" / "test-suite"     # wordpress-develop checkout

TEST_TOOLS_DIR = ROOT / "runtime" / "test-tools"     # phpunit/composer phars + polyfills

_WPDEVELOP_REPO = "https://github.com/WordPress/wordpress-develop.git"

_PHPUNIT_PHAR_URL = "https://phar.phpunit.de/phpunit-9.phar"

_COMPOSER_PHAR_URL = "https://getcomposer.org/composer-stable.phar"

_POLYFILLS_REPO = "https://github.com/Yoast/PHPUnit-Polyfills.git"

_POLYFILLS_TAG = "2.0.1"      # supports PHPUnit 5.7–10 → good for phpunit 9 on PHP 8.3

TESTS_DB_NAME = "wp_tests"    # isolated test DB; the installer wipes wptests_ tables

def _git_q(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)

def _ensure_wp_test_suite(wp_version) -> Path:
    """Sparse-clone wordpress-develop's tests/phpunit at the WP version (trunk
    for latest/unpinned). Cached; returns the tests/phpunit dir."""
    ref = "trunk"
    if wp_version and re.match(r"^\d+\.\d+", str(wp_version)):
        v = str(wp_version)
        ref = v if v.count(".") >= 2 else v + ".0"
    phpunit_dir = TEST_SUITE_DIR / "tests" / "phpunit"
    marker = TEST_SUITE_DIR / ".ref"
    if phpunit_dir.exists() and marker.exists() and marker.read_text().strip() == ref:
        return phpunit_dir
    if not (TEST_SUITE_DIR / ".git").exists():
        TEST_SUITE_DIR.mkdir(parents=True, exist_ok=True)
        info("cloning wordpress-develop test suite (sparse, depth 1)…")
        _git_q("clone", "--depth", "1", "--filter=blob:none", "--no-checkout",
               _WPDEVELOP_REPO, str(TEST_SUITE_DIR))
        _git_q("-C", str(TEST_SUITE_DIR), "sparse-checkout", "set", "--cone",
               "tests/phpunit")
    if ref != "trunk":
        got = _git_q("-C", str(TEST_SUITE_DIR), "fetch", "origin", "tag", ref,
                     "--depth", "1", check=False)
        if got.returncode != 0 or _git_q("-C", str(TEST_SUITE_DIR), "checkout",
                                          ref, check=False).returncode != 0:
            info(f"WP suite tag {ref} unavailable — using trunk")
            ref = "trunk"
    if ref == "trunk":
        _git_q("-C", str(TEST_SUITE_DIR), "fetch", "origin", "trunk", "--depth", "1")
        _git_q("-C", str(TEST_SUITE_DIR), "checkout", "trunk")
    info(f"WP test suite → {ref}")
    marker.write_text(ref)
    return phpunit_dir

def _download(url: str, dest: Path) -> None:
    """Stream a URL to dest with a real User-Agent (phar.phpunit.de etc. 403 the
    default urllib UA)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "sandbox-sb/1.0"})
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(tmp, dest)

def _ensure_test_tools() -> dict:
    """Download phpunit.phar + composer.phar and clone polyfills (cached)."""
    TEST_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    phpunit = TEST_TOOLS_DIR / "phpunit.phar"
    if not phpunit.exists():
        info("downloading phpunit.phar (9.x)…")
        _download(_PHPUNIT_PHAR_URL, phpunit)
    composer = TEST_TOOLS_DIR / "composer.phar"
    if not composer.exists():
        info("downloading composer.phar…")
        _download(_COMPOSER_PHAR_URL, composer)
    poly = TEST_TOOLS_DIR / "phpunit-polyfills"
    if not (poly / "phpunitpolyfills-autoload.php").exists():
        if poly.exists():
            shutil.rmtree(poly)
        info("cloning phpunit-polyfills…")
        _git_q("clone", "--depth", "1", "--branch", _POLYFILLS_TAG,
               _POLYFILLS_REPO, str(poly))
    return {"phpunit": phpunit, "composer": composer, "polyfills": poly}

def _herd_tests_db(instance: str) -> str:
    return _herd_db_name(instance) + "_tests"

def _ensure_tests_db(instance: str) -> None:
    """Create the isolated tests DB (idempotent). NEVER the dev DB — the WP
    installer drops every wptests_ table on each run. Docker instances each
    have their own DB server, so they share the name `wp_tests`; herd
    instances share the ONE host MySQL, so the tests DB is per-instance."""
    if _is_herd_instance(instance):
        # Routed through the site's own (root) connection — no host mysql
        # client dependency.
        wpcli(["db", "query",
               f"CREATE DATABASE IF NOT EXISTS {_herd_tests_db(instance)}"],
              instance=instance, check=False, capture=True)
        return
    sql = (f"CREATE DATABASE IF NOT EXISTS {TESTS_DB_NAME}; "
           f"GRANT ALL ON {TESTS_DB_NAME}.* TO 'wp'@'%'; FLUSH PRIVILEGES;")
    compose("exec", "-T", "db", "mariadb", "-uroot", "-proot", "-e", sql,
            instance=instance, check=False)

def _write_wp_tests_config() -> Path:
    """Sandbox-owned wp-tests-config.php matching the sandbox DB topology
    (host db / user wp / pass wp / db wp_tests). Cached under test-tools."""
    cfg_php = (
        "<?php\n"
        "define( 'ABSPATH', '/var/www/html/' );\n"
        "define( 'WP_DEFAULT_THEME', 'default' );\n"
        f"define( 'DB_NAME', '{TESTS_DB_NAME}' );\n"
        "define( 'DB_USER', 'wp' );\n"
        "define( 'DB_PASSWORD', 'wp' );\n"
        "define( 'DB_HOST', 'db' );\n"
        "define( 'DB_CHARSET', 'utf8' );\n"
        "define( 'DB_COLLATE', '' );\n"
        "$table_prefix = 'wptests_';\n"
        # The WP Core bootstrap wants the polyfills path as a CONSTANT (not an
        # env var). /wp-phpunit-polyfills is where _run_tests mounts them.
        "define( 'WP_TESTS_PHPUNIT_POLYFILLS_PATH', '/wp-phpunit-polyfills' );\n"
        "define( 'WP_TESTS_DOMAIN', 'localhost' );\n"
        "define( 'WP_TESTS_EMAIL', 'admin@example.org' );\n"
        "define( 'WP_TESTS_TITLE', 'Sandbox Tests' );\n"
        "define( 'WP_PHP_BINARY', 'php' );\n"
        "define( 'WP_TESTS_MULTISITE', false );\n"
        "foreach ( array('AUTH_KEY','SECURE_AUTH_KEY','LOGGED_IN_KEY','NONCE_KEY',"
        "'AUTH_SALT','SECURE_AUTH_SALT','LOGGED_IN_SALT','NONCE_SALT') as $k ) "
        "{ if ( ! defined($k) ) define($k, 'test'); }\n"
    )
    path = TEST_TOOLS_DIR / "wp-tests-config.php"
    path.write_text(cfg_php)
    return path

def _write_wp_tests_config_herd(instance: str) -> Path:
    """Host-path twin of _write_wp_tests_config: real ABSPATH (the herd
    site's WP dir), host MySQL creds, per-instance tests DB. Passed to the
    suite via the WP_TESTS_CONFIG_FILE_PATH env var, which takes precedence
    over the container-path wp-tests-config.php copied next to the suite."""
    cfg_php = (
        "<?php\n"
        f"define( 'ABSPATH', '{wp_dir(instance)}/' );\n"
        "define( 'WP_DEFAULT_THEME', 'default' );\n"
        f"define( 'DB_NAME', '{_herd_tests_db(instance)}' );\n"
        f"define( 'DB_USER', '{HERD_DB_USER}' );\n"
        f"define( 'DB_PASSWORD', '{HERD_DB_PASSWORD}' );\n"
        f"define( 'DB_HOST', '{HERD_DB_HOST}:{HERD_DB_PORT}' );\n"
        "define( 'DB_CHARSET', 'utf8' );\n"
        "define( 'DB_COLLATE', '' );\n"
        "$table_prefix = 'wptests_';\n"
        f"define( 'WP_TESTS_PHPUNIT_POLYFILLS_PATH', "
        f"'{TEST_TOOLS_DIR / 'phpunit-polyfills'}' );\n"
        f"define( 'WP_TESTS_DOMAIN', '{_herd_domain(instance)}' );\n"
        "define( 'WP_TESTS_EMAIL', 'admin@example.org' );\n"
        "define( 'WP_TESTS_TITLE', 'Sandbox Tests' );\n"
        # WP_PHP_BINARY is used by the suite for sub-process PHP calls — pin it
        # to the instance's PHP so those also run the project's version. The
        # suite splices it UNESCAPED into a shell command
        # (system(WP_PHP_BINARY . ' ' . …)), and Herd's binary path contains
        # spaces, so store a shell-quoted token (PHP single-quote escaping) —
        # not the raw path, which would shell-split and fail with 127.
        f"define( 'WP_PHP_BINARY', {_php_squote(_herd_php(instance))} );\n"
        "define( 'WP_TESTS_MULTISITE', false );\n"
        "foreach ( array('AUTH_KEY','SECURE_AUTH_KEY','LOGGED_IN_KEY','NONCE_KEY',"
        "'AUTH_SALT','SECURE_AUTH_SALT','LOGGED_IN_SALT','NONCE_SALT') as $k ) "
        "{ if ( ! defined($k) ) define($k, 'test'); }\n"
    )
    path = TEST_TOOLS_DIR / f"wp-tests-config-{instance}.php"
    path.write_text(cfg_php)
    return path

def _run_tests_herd(inst: str, root: str, suite: Path, tools: dict,
                    extra: list) -> int:
    """Host twin of _run_tests: composer install (if the plugin has a
    composer.json) and phpunit both run on the instance's PINNED PHP
    (php_version) — no containers. The suite/polyfills/phpunit live at real
    host paths already (runtime/), so no mounts are needed; only the env
    wiring differs. Using the pinned binary (not the generic `php`) is what
    makes test runs execute on the project's PHP, matching CI/production."""
    php = _herd_php(inst)
    root_p = Path(root)
    if (root_p / "composer.json").exists() \
            and not (root_p / "vendor" / "autoload.php").exists():
        info(f"composer install (plugin dev deps, host PHP {php})…")
        r = subprocess.run([php, str(tools["composer"]), "install",
                            "--no-interaction", "--no-progress", "--no-plugins"],
                           cwd=root)
        if r.returncode != 0:
            info("locked install failed (stale lock) — running composer update…")
            subprocess.run([php, str(tools["composer"]), "update",
                            "--no-interaction", "--no-progress", "--no-plugins"],
                           cwd=root)
    info(f"running phpunit (host PHP {php})…")
    env = {**os.environ,
           "WP_TESTS_DIR": str(suite),
           "WP_TESTS_PHPUNIT_POLYFILLS_PATH": str(tools["polyfills"])}
    r = subprocess.run([php, str(tools["phpunit"]), *extra],
                       cwd=root, env=env)
    return r.returncode

def _run_tests(inst: str, root: str, suite: Path, tools: dict, extra: list) -> int:
    """composer install (the plugin's OWN dev deps) if needed, then run phpunit
    with the external harness mounted. The project root is bind-mounted at the
    identical path in-container, so it doubles as the phpunit workdir. Streams
    output; returns the phpunit exit code."""
    plug = str(root)
    if not (Path(root) / "vendor" / "autoload.php").exists():
        info("composer install (plugin dev deps)…")
        # --no-plugins: skip composer/installers etc. (not needed to build the
        #   test vendor, and they trip composer 2.2's allow-plugins gate).
        # COMPOSER_ALLOW_SUPERUSER: the container runs composer as root.
        base = ["run", "--rm",
                "-e", "COMPOSER_HOME=/tmp/composer",
                "-e", "COMPOSER_ALLOW_SUPERUSER=1",
                "-v", f"{tools['composer']}:/composer.phar:ro",
                "-w", plug, "--entrypoint", "php", "wpcli", "/composer.phar"]
        flags = ["--no-interaction", "--no-progress", "--no-plugins"]
        r = compose(*base, "install", *flags,
                    instance=inst, check=False, capture=True)
        if getattr(r, "returncode", 1) != 0:
            # Stale/incompatible composer.lock (common: a lock pinned for PHP 7
            # against a PHP 8.x container) — regenerate it for the live PHP.
            info("locked install failed (stale lock) — running composer update…")
            compose(*base, "update", *flags, instance=inst, check=False)
    info("running phpunit…")
    r = compose("run", "--rm",
                "-v", f"{suite}:/wordpress-phpunit",
                "-v", f"{tools['polyfills']}:/wp-phpunit-polyfills:ro",
                "-v", f"{tools['phpunit']}:/phpunit.phar:ro",
                "-e", "WP_TESTS_DIR=/wordpress-phpunit",
                "-e", "WP_TESTS_PHPUNIT_POLYFILLS_PATH=/wp-phpunit-polyfills",
                "-w", plug, "--entrypoint", "php", "wpcli",
                "/phpunit.phar", *extra,
                instance=inst, check=False)
    return getattr(r, "returncode", 1)

def _provision_test_harness(inst: str, pconf: dict) -> dict:
    """Provision (cached) the external WP test harness for `inst`: the WP suite
    at the project's wpVersion, phpunit + composer phars + polyfills, the
    isolated wp_tests DB, and the sandbox wp-tests-config.php (copied alongside
    the suite so the WP bootstrap auto-discovers it). Idempotent. Returns
    {suite, tools, config}."""
    suite = _ensure_wp_test_suite(pconf.get("wpVersion"))
    tools = _ensure_test_tools()
    _ensure_tests_db(inst)
    # The suite bootstrap only reads wp-tests-config.php ADJACENT to itself
    # (WP_TESTS_CONFIG_FILE_PATH is a wp-phpunit convention, not wordpress-
    # develop's) — so copy the runtime-appropriate config there on every run:
    # container paths for docker instances, host paths for herd.
    if _is_herd_instance(inst):
        config = _write_wp_tests_config_herd(inst)
    else:
        config = _write_wp_tests_config()
    shutil.copy(config, suite / "wp-tests-config.php")
    return {"suite": suite, "tools": tools, "config": config}

def _instance_running(name: str) -> bool:
    """True if the instance's `wp` web container reports running."""
    ps = compose("ps", "--format", "json", instance=name,
                 check=False, capture=True)
    for ln in (ps.stdout or "").splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if row.get("Service") == "wp" and row.get("State") == "running":
            return True
    return False

def collect_instance_rows(cfg: dict) -> list[dict]:
    """Per-instance view-model shared by `cmd_instances` (static print) and the
    `dashboard` TUI, so the two never drift. One dict per instance with status,
    URLs, server, MCP server name, project, and focus.
    """
    sc = _core()
    rows = []
    local_cfg = _local_yaml()
    for name, inst_cfg in resolve_instances(cfg).items():
        ff = focus_file(name)
        # Per-project model: the project is the registry root this instance
        # serves (its dir basename), not the vestigial .active-project file.
        owner = sc.registry_find_instance(name)
        project = Path(owner["root"]).name if owner and owner.get("root") else "—"
        _base = site_url(inst_cfg)
        _token = local_cfg.get("instances", {}).get(name, {}).get("autologin_token", "")
        rows.append({
            "name": name,
            "running": _instance_running(name),
            "wordpress_port": inst_cfg["wordpress_port"],
            "mailpit_port": inst_cfg["mailpit_port"],
            "url": _base,
            "admin_url": f"{_base}/wp-admin/",
            "login_url": f"{_base}/?sandbox_autologin={_token}" if _token else "",
            "domain": inst_cfg.get("domain"),
            "server": inst_cfg.get("server", "apache"),
            "mcp_server": mcp_server_name(name),
            "project": project,
            "focus": ff.read_text().strip() if ff.exists() else "—",
        })
    return rows

def domains_ready() -> bool:
    """True once custom domains can be applied without a password. Primary path
    is the sandbox HTTPS proxy (cert generated). Fallbacks: Valet, or the
    one-time passwordless /etc/hosts sudoers rule."""
    return proxy_available() or _valet_available() or _hosts_passwordless()

def _proxy_sudoers_installed() -> bool:
    """True if the passwordless rule for proxy-helper.sh is installed."""
    if not PROXY_SUDOERS.exists():
        return False
    try:
        return str(PROXY_HELPER) in PROXY_SUDOERS.read_text()
    except PermissionError:
        # The rule lands as 0440 root:wheel, so a non-root process can't read
        # its contents — but only our setup creates this exact file, so its
        # mere existence means the rule is installed. (Without this, every
        # non-root `sb` run thinks setup never happened and demands a TTY.)
        return True
    except OSError:
        return False

def _sudo_env():
    """Environment for interactive sudo that pops a native macOS password dialog
    (via SUDO_ASKPASS) instead of prompting in the terminal. Falls back silently
    to the terminal if the helper/osascript isn't usable."""
    env = dict(os.environ)
    if sys.platform == "darwin" and ASKPASS_HELPER.exists():
        env["SUDO_ASKPASS"] = str(ASKPASS_HELPER)
    return env

def _sudo(cmd, reason=None, **kw):
    """Run `sudo <cmd>` using the GUI password dialog (sudo -A) when available.
    `cmd` is the argv AFTER 'sudo'. Use for INTERACTIVE sudo (first-time setup);
    passwordless calls keep using `sudo -n` directly.

    `reason` is a human explanation of WHY the password is needed. It's passed via
    `sudo -p` so it becomes the prompt the askpass dialog shows — the user sees a
    concrete sentence ("Sandbox needs admin rights to …") instead of a bare
    "Password:". Keep it one line; the dialog renders it verbatim."""
    flag = ["-A"] if (sys.platform == "darwin" and ASKPASS_HELPER.exists()) else []
    prompt = ["-p", reason] if reason else []
    return subprocess.run(["sudo", *flag, *prompt, *cmd], env=_sudo_env(), **kw)

def _ensure_url_proxy(cfg, *, quiet: bool = False, tld=None):
    """Ensure the clean-URL HTTP proxy infra is up (no certs): the passwordless
    sudoers rule, the lo0 alias, dnsmasq/resolver for *.tst, the boot LaunchDaemon,
    and the running Caddy container. This is the DEFAULT path — plain http://
    <name>.tst, no mkcert, no 'Not Secure'. One-time sudo for the sudoers rule;
    after that it's password-free. Returns (ok, cfg). Requires an interactive
    terminal the first time (sudoers install)."""
    if shutil.which("docker") is None:
        if not quiet:
            info("Docker not found — clean URLs need it. Using localhost:<port>.")
        return False, cfg

    # 1. Passwordless sudoers rule for proxy-helper.sh (alias + dnsmasq). One
    #    sudo prompt, once. Skipped if already installed.
    if not _proxy_sudoers_installed():
        if not sys.stdin.isatty():
            if not quiet:
                info("clean URLs need a one-time setup (a password) — run "
                     "`./sb domains setup` in your terminal. Using localhost.")
            return False, cfg
        import getpass
        user = getpass.getuser()
        rule = (f"# Installed by the sandbox — lets it manage the lo0 alias and "
                f"dnsmasq/resolver for *.{PROXY_TLD} without a password.\n"
                f"{user} ALL=(root) NOPASSWD: {PROXY_HELPER}\n")
        info("One-time setup for clean http://<name>.tst URLs — your password "
             "ONCE (no certificate, no browser warning).")
        tmp = ROOT / "runtime" / "sandbox-proxy.sudoers"
        tmp.write_text(rule)
        _SUDOERS_REASON = (
            "Sandbox would like to set up clean local URLs so your sites open at "
            "http://<name>.tst instead of localhost:8188. This one-time step lets "
            "it manage local DNS for *.tst without asking again — all local, and "
            "undoable anytime with ./sb uninstall.")
        chk = _sudo(["visudo", "-cf", str(tmp)], reason=_SUDOERS_REASON,
                    capture_output=True, text=True)
        if chk.returncode != 0:
            tmp.unlink(missing_ok=True)
            info(f"sudoers rule failed validation: {chk.stderr.strip()}")
            return False, cfg
        inst = _sudo(
            ["install", "-m", "0440", "-o", "root", "-g",
             "wheel" if sys.platform == "darwin" else "root",
             str(tmp), str(PROXY_SUDOERS)], reason=_SUDOERS_REASON,
            capture_output=True, text=True)
        tmp.unlink(missing_ok=True)
        if inst.returncode != 0:
            info(f"failed to install sudoers rule: {inst.stderr.strip()}")
            return False, cfg
        ok("clean-URL host actions are now password-free.")

    # 2. lo0 alias + dnsmasq/resolver for each configured TLD. Only sudo for what
    #    is MISSING: the alias + resolver persist (the LaunchDaemon re-adds the
    #    alias on boot, the resolver/dnsmasq files stay), so once the one-time
    #    `domains setup` ran, securing needs NO sudo per ensure. That's what lets
    #    secure-at-create work from the MCP server's subprocess, which can't
    #    `sudo -n` (no controlling tty/session) — the cause of MCP-created
    #    instances falling back to localhost.
    ok_all = True
    if not _lo0_alias_present():
        ok_all = subprocess.run(["sudo", "-n", str(PROXY_HELPER), "alias-up"],
                                capture_output=True, text=True).returncode == 0
    tlds = _distinct_tlds(cfg) | ({tld} if tld else set())
    for t in tlds:
        if not _resolver_present(t):
            r = subprocess.run(["sudo", "-n", str(PROXY_HELPER), "dns-up", t],
                               capture_output=True, text=True)
            ok_all = ok_all and r.returncode == 0
    if not ok_all:
        info(f"could not set up *.{'/'.join(sorted(tlds))} "
             "resolution — using localhost for now.")
        return False, cfg
    PROXY_CERTS_DIR.mkdir(parents=True, exist_ok=True)

    # 3. Boot-time alias restore + start the proxy with current routes.
    _install_alias_launchd()
    PROXY_COMPOSE.write_text(render_proxy_compose())
    regen_caddyfile(cfg)
    if not reload_proxy():
        info("proxy container did not start (is Docker running?).")
        return False, cfg
    return True, cfg

def proxy_setup(cfg, tld=None) -> bool:
    """OPT-IN: upgrade clean URLs to trusted HTTPS (https://<name>.<tld>). This is
    `./sb secure` / `./sb domains setup`. It first ensures the HTTP URL proxy is
    up (_ensure_url_proxy), then installs + trusts the mkcert CA and mints a cert
    per proxy instance, switching them to https. Interactive (password once for the
    CA). `tld` overrides the per-project default for newly-assigned domains.
    The DEFAULT install path does NOT call this — plain http needs no cert."""
    # 1. Ensure the base HTTP proxy infra (sudoers, alias, dnsmasq, container).
    up, cfg = _ensure_url_proxy(cfg, tld=tld)
    if not up:
        return False
    if shutil.which("brew") is None:
        info("Homebrew not found — needed to install mkcert. See brew.sh.")
        return False

    # 2. mkcert + trust the CA (interactive), and VERIFY the OS really trusts it.
    if shutil.which("mkcert") is None:
        info("installing mkcert + nss via Homebrew\u2026")
        if subprocess.run(["brew", "install", "mkcert", "nss"]).returncode != 0:
            info("brew install mkcert failed.")
            return False
    if sys.platform == "darwin":
        info("macOS will ask for Touch ID (or your password) to trust the local "
             "HTTPS certificate \u2014 that's expected, and only happens once.")
    else:
        info("trusting the local HTTPS certificate \u2014 enter your password if "
             "prompted (once).")
    r = subprocess.run(["mkcert", "-install"], capture_output=True, text=True)
    if r.returncode != 0:
        info(f"mkcert -install failed: {(r.stderr or r.stdout).strip()[:200]}")
        info("  run `mkcert -install` yourself in a terminal, then retry.")
        return False
    if sys.platform == "darwin" and not _ca_trusted_macos():
        info("mkcert ran but the OS still doesn't trust the CA \u2014 likely stale/")
        info("duplicate mkcert CAs. Fix with:  ./sb domains repair-ca")
        return False
    ok("mkcert local CA is trusted (verified).")
    PROXY_CERTS_DIR.mkdir(parents=True, exist_ok=True)

    # 3. Mint a cert per proxy instance + point WP at https, then reload.
    cfg = _assign_domains_to_all(cfg, tld)
    for name, ic in resolve_instances(cfg).items():
        dom = ic.get("domain")
        if dom and dom.endswith(f".{_tld(ic)}"):
            sans = [_wildcard_san(dom)] if _multisite_mode(ic) == "subdomain" else None
            _mint_cert(dom, extra_sans=sans)
    regen_caddyfile(cfg)
    if not reload_proxy():
        info("proxy reload failed (is Docker running?).")
        return False
    return True

def _secure_at_create(cfg: dict, name: str) -> bool:
    """Give a FRESH instance its clean https://<name>.<tld> BEFORE `core install`,
    so WP's siteurl/home are never http (no localhost:<port> leaking into
    redirects). Assigns the domain, ensures the proxy + DNS, mints the trusted
    cert, and wires the Caddy TLS route. Returns True when the instance can now
    be installed at its https URL; False (caller falls back to localhost) if
    mkcert/CA/proxy aren't ready. Non-interactive — only meant to run when the
    one-time `./sb domains setup` already happened.

    Works for multisite too: it secures the apex (subdomain mode also gets a
    wildcard *.<name>.<tld> SAN). The caller must, after multisite-convert,
    re-render compose + recreate the web tier so DOMAIN_CURRENT_SITE matches the
    network domain convert stored (see ensure_instance)."""
    ic = resolve_instances(cfg).get(name, {})
    if ic.get("server") == "herd":
        return False
    ca_ok = _ca_trusted_macos() if sys.platform == "darwin" else True
    if not (shutil.which("mkcert") and ca_ok):
        return False
    tld = _tld(ic)
    domain = f"{name}.{tld}"
    # 1. Persist the domain so site_url() resolves to it for the install URL.
    local = _local_yaml()
    blk = local.setdefault("instances", {}).setdefault(name, {})
    blk["domain"] = domain
    blk["tld"] = tld
    _write_local_yaml(local)
    cfg = load_config()
    # 2. Proxy + DNS for this tld (passwordless once the sudoers rule exists).
    up, cfg = _ensure_url_proxy(cfg, quiet=True, tld=tld)
    if not up:
        # Roll the domain back so the instance installs cleanly at localhost.
        local = _local_yaml()
        local.get("instances", {}).get(name, {}).pop("domain", None)
        _write_local_yaml(local)
        return False
    # 3. Mint the trusted cert + wire the route so https://<name>.<tld> serves.
    #    Subdomain multisite needs a wildcard SAN so every sub-site host is
    #    covered by the one cert.
    ic = resolve_instances(cfg)[name]
    sans = [_wildcard_san(domain)] if _multisite_mode(ic) == "subdomain" else None
    _mint_cert(domain, extra_sans=sans)
    regen_caddyfile(cfg)
    reload_proxy()
    return True

def _assign_domains_to_all(cfg: dict, tld=None):
    """Assign <name>.<tld> to every instance lacking a domain (including the
    implicit `main`), persist it to config, and point each running instance's WP
    siteurl/home at the clean URL. Returns the reloaded cfg. Idempotent.

    `tld` (from `./sb domains setup <tld>`) overrides the per-project default for
    this assignment; when None, each instance uses its own `_tld(ic)`."""
    local = _local_yaml()
    insts = local.setdefault("instances", {})
    changed = []
    for name, ic in resolve_instances(cfg).items():
        block = insts.get(name)
        if block is None:
            # `main` (and any synthesized instance) has no explicit block yet —
            # create a minimal one carrying just the domain; resolve_instances
            # fills the rest from the runtime defaults.
            block = {}
            insts[name] = block
        if not block.get("domain"):
            chosen = tld or _tld(ic)
            block["domain"] = f"{name}.{chosen}"
            block["tld"] = chosen
            changed.append(block["domain"])
    if changed:
        _write_local_yaml(local)
        ok(f"assigned domains: {', '.join(changed)}")
    cfg = load_config()

    # Point each running instance's WP siteurl/home at its clean URL. Uses
    # site_url() so it's http://<name>.tst by default, or https when secured.
    # Skip stopped ones — they get it on next up/install.
    for name, ic in resolve_instances(cfg).items():
        dom = ic.get("domain")
        if not (dom and dom.endswith(f".{_tld(ic)}")):
            continue
        if not _instance_running(name):
            continue
        url = site_url(ic)
        cur = wpcli(["option", "get", "siteurl"], instance=name,
                    check=False, capture=True)
        if (getattr(cur, "stdout", "") or "").strip() == url:
            continue
        if url.startswith("https://"):
            _write_ssl_muplugin(name)  # trust the proxy's TLS before switching
        wpcli(["option", "update", "siteurl", url], instance=name, check=False)
        wpcli(["option", "update", "home", url], instance=name, check=False)
        info(f"{name}: WP url → {url}")
    return cfg

def _install_alias_launchd() -> None:
    """Install a LaunchDaemon that re-adds the lo0 alias on boot (the alias is
    not persistent). Best-effort — failure just means `./sb domains up` (or the
    lazy alias-up in cmd_up) restores it after a reboot."""
    # Idempotent: this install needs an INTERACTIVE sudo (it's not covered by the
    # passwordless proxy-helper rule), so skip it once the plist exists —
    # otherwise every `ensure` (now secured at create) re-prompts for the Mac
    # password. .exists() needs no read permission on the root-owned file.
    if LAUNCHD_PLIST.exists():
        return
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sandbox.lo0alias</string>
  <key>RunAtLoad</key><true/>
  <key>ProgramArguments</key>
  <array>
    <string>/sbin/ifconfig</string><string>lo0</string>
    <string>alias</string><string>{PROXY_BIND_IP}</string><string>up</string>
  </array>
</dict>
</plist>
"""
    tmp = ROOT / "runtime" / "com.sandbox.lo0alias.plist"
    tmp.write_text(plist)
    _LAUNCHD_REASON = (
        "Sandbox would like to keep your clean URLs working after a reboot. It "
        "adds a small startup item that re-enables local *.tst sites. You can "
        "remove it anytime with ./sb uninstall.")
    res = _sudo(
        ["install", "-m", "0644", "-o", "root", "-g",
         "wheel" if sys.platform == "darwin" else "root",
         str(tmp), str(LAUNCHD_PLIST)], reason=_LAUNCHD_REASON,
        capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    if res.returncode == 0:
        _sudo(["launchctl", "load", "-w", str(LAUNCHD_PLIST)],
              reason=_LAUNCHD_REASON, capture_output=True, text=True)
        ok("installed boot-time loopback-alias LaunchDaemon")
    else:
        info("skipped LaunchDaemon (alias restored by `./sb domains up` after "
             "reboot)")

def proxy_teardown(cfg) -> None:
    """Reverse proxy_setup: stop the proxy, untrust the CA, remove dnsmasq/
    resolver + the lo0 alias + the LaunchDaemon + the sudoers rule. Each step is
    best-effort so a partial state still cleans up."""
    subprocess.run(["docker", "compose", "-p", PROXY_PROJECT, "-f",
                    str(PROXY_COMPOSE), "--project-directory", str(ROOT),
                    "down"], capture_output=True, text=True)
    if _proxy_sudoers_installed():
        for tld in _distinct_tlds(cfg):
            subprocess.run(["sudo", "-n", str(PROXY_HELPER), "dns-down", tld],
                           capture_output=True, text=True)
        subprocess.run(["sudo", "-n", str(PROXY_HELPER), "alias-down"],
                       capture_output=True, text=True)
    if shutil.which("mkcert"):
        subprocess.run(["mkcert", "-uninstall"], capture_output=True, text=True)
    _UNINSTALL_REASON = (
        "Sandbox is cleaning up its clean-URL setup — removing the startup item "
        "and the local DNS rule it added. Your Mac password confirms this final "
        "step.")
    _sudo(["launchctl", "unload", "-w", str(LAUNCHD_PLIST)],
          reason=_UNINSTALL_REASON, capture_output=True, text=True)
    _sudo(["rm", "-f", str(LAUNCHD_PLIST), str(PROXY_SUDOERS)],
          reason=_UNINSTALL_REASON, capture_output=True, text=True)
    ok("HTTPS proxy torn down (certs left in runtime/proxy/certs — delete "
       "manually if desired).")

@contextmanager
def _curses_suspended(stdscr):
    """Drop out of curses to run normal terminal I/O (a cmd_* with its prints,
    or an input() prompt), then restore the full-screen UI."""
    import curses
    curses.def_prog_mode()      # save curses tty state
    curses.endwin()             # back to normal terminal
    try:
        yield
    finally:
        stdscr.refresh()        # restore saved screen
        curses.reset_prog_mode()
        stdscr.clear()

def _dash_prompt(stdscr, label: str) -> str:
    """One-line text prompt at the bottom of the screen. Returns the entry
    (stripped); empty string if cancelled with Esc/blank."""
    import curses
    h, w = stdscr.getmaxyx()
    curses.echo()
    curses.curs_set(1)
    stdscr.move(h - 1, 0)
    stdscr.clrtoeol()
    stdscr.addstr(h - 1, 0, label[:w - 1])
    stdscr.refresh()
    try:
        raw = stdscr.getstr(h - 1, len(label), max(1, w - len(label) - 1))
        val = raw.decode("utf-8", "replace").strip() if raw else ""
    except Exception:
        val = ""
    finally:
        curses.noecho()
        curses.curs_set(0)
    return val

def _dash_pick(stdscr, label: str, options: list[str]) -> str | None:
    """Inline single-key picker: shows `label: [a]pache [n]ginx ...` and
    returns the option whose first letter is pressed, or None on Esc."""
    import curses
    h, w = stdscr.getmaxyx()
    hint = label + "  " + "  ".join(f"[{o[0]}]{o[1:]}" for o in options)
    stdscr.move(h - 1, 0)
    stdscr.clrtoeol()
    stdscr.addstr(h - 1, 0, hint[:w - 1])
    stdscr.refresh()
    while True:
        c = stdscr.getch()
        if c in (27, ord("q")):
            return None
        for o in options:
            if c == ord(o[0]):
                return o

def _dash_flash(stdscr, msg: str):
    """Transient status line message (shown until next redraw)."""
    import curses
    h, w = stdscr.getmaxyx()
    try:
        stdscr.move(h - 1, 0)
        stdscr.clrtoeol()
        stdscr.addstr(h - 1, 0, msg[:w - 1], curses.A_BOLD)
        stdscr.refresh()
    except Exception:
        pass

def _dash_draw(stdscr, rows: list[dict], selected: int):
    import curses
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    title = f" Sandbox Dashboard — {len(rows)} instance(s) "
    stdscr.addstr(0, 0, title[:w - 1], curses.A_BOLD)
    header = (f"  {'STATUS':<9}{'NAME':<12}{'URL':<26}{'SERVER':<11}"
              f"{'MCP SERVER':<19}{'PROJECT':<12}FOCUS")
    stdscr.addstr(2, 0, header[:w - 1], curses.A_UNDERLINE)
    for i, r in enumerate(rows):
        y = 3 + i
        if y >= h - 2:
            break
        dot = "● run " if r["running"] else "○ stop"
        line = (f"  {dot:<9}{r['name']:<12}{r['url']:<26}{r['server']:<11}"
                f"{r['mcp_server']:<19}{r['project']:<12}{r['focus']}")
        attr = curses.A_REVERSE if i == selected else 0
        if not r["running"]:
            attr |= curses.A_DIM
        stdscr.addstr(y, 0, line[:w - 1], attr)
    legend = ("↑↓ move · s start · x stop · R restart · o open · f focus · "
              "n new · d delete · r refresh · q quit")
    try:
        stdscr.addstr(h - 2, 0, legend[:w - 1], curses.A_DIM)
    except Exception:
        pass
    stdscr.refresh()

def _dash_run(stdscr, cfg):
    from sandbox.commands.lifecycle import cmd_up, cmd_down, cmd_open
    from sandbox.commands.instances_cmd import cmd_focus, cmd_instance
    import curses
    curses.curs_set(0)
    stdscr.timeout(2000)        # getch returns -1 every ~2s → auto-refresh
    selected = 0
    rows = collect_instance_rows(cfg)

    def reload_rows():
        nonlocal cfg, rows
        cfg = load_config()
        rows = collect_instance_rows(cfg)

    while True:
        if rows:
            selected = max(0, min(selected, len(rows) - 1))
        _dash_draw(stdscr, rows, selected)
        c = stdscr.getch()

        if c == -1:                       # timeout tick → refresh
            reload_rows()
            continue
        if c in (ord("q"), 27):
            return
        if c in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
            continue
        if c in (curses.KEY_DOWN, ord("j")):
            selected = min(len(rows) - 1, selected + 1) if rows else 0
            continue
        if c in (ord("r"),):
            reload_rows()
            continue
        if c == curses.KEY_RESIZE:
            continue

        sel = rows[selected] if rows else None

        if c == ord("n"):                 # new instance → per-project model
            _dash_flash(stdscr, "Create instances with `./sb init` inside a "
                                "plugin repo (per-project). Press a key…")
            stdscr.getch()
            continue

        if not sel:
            continue

        name = sel["name"]
        if c == ord("s"):                 # start
            with _curses_suspended(stdscr):
                cmd_up(cfg, _types.SimpleNamespace(resolved_instance=name))
            reload_rows()
        elif c == ord("x"):               # stop
            with _curses_suspended(stdscr):
                cmd_down(cfg, _types.SimpleNamespace(resolved_instance=name))
            reload_rows()
        elif c == ord("R"):               # restart
            with _curses_suspended(stdscr):
                compose("restart", "wp", instance=name, check=False)
            reload_rows()
        elif c == ord("o"):               # open admin/site/mail
            what = _dash_pick(stdscr, "open:",
                              ["admin", "site", "mail"])
            if what:
                cmd_open(cfg, _types.SimpleNamespace(
                    resolved_instance=name, what=what))
        elif c == ord("f"):               # set focus
            slug = _dash_prompt(stdscr, f"focus plugin for '{name}': ")
            if slug:
                with _curses_suspended(stdscr):
                    cmd_focus(load_config(), _types.SimpleNamespace(
                        resolved_instance=name, slug=slug, clear=False))
                    input("\n[enter] to return…")
                reload_rows()
        elif c == ord("d"):               # delete (confirm)
            typed = _dash_prompt(stdscr, f"type '{name}' to delete: ")
            if typed == name:
                with _curses_suspended(stdscr):
                    cmd_instance(load_config(), _types.SimpleNamespace(
                        action="delete", name=name, yes=True,
                        resolved_instance=name))
                    input("\n[enter] to return…")
                selected = 0
                reload_rows()

_web_lock = threading.Lock()          # serialize mutating actions

_web_jobs: dict = {}                  # job_id -> {status, output, done}

_web_job_seq = [0]

def _run_cmd_capture(fn, args_ns) -> tuple[bool, str]:
    """Run a cmd_* handler, capturing its stdout/stderr and turning a die()
    (SystemExit) into a failed result instead of killing the server."""
    buf = io.StringIO()
    ok_flag = True
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            fn(load_config(), args_ns)
    except SystemExit as e:            # die() → non-zero exit
        ok_flag = (str(e) in ("0", "None"))
    except Exception as e:             # never let one action crash the server
        ok_flag = False
        buf.write(f"\nerror: {e}\n")
    return ok_flag, buf.getvalue()

class _JobStream:
    """File-like sink that appends every write to a job's output buffer under
    lock, so the web console can poll incremental output while a command runs.
    """
    def __init__(self, job):
        self._job = job

    def write(self, s):
        if s:
            with _web_jobs_lock:
                self._job["output"] += s
        return len(s)

    def flush(self):
        pass

_web_jobs_lock = threading.Lock()

def _start_job(label: str, fn) -> str:
    """Run `fn(stream)` in a background thread, streaming its output into a new
    job. `fn` returns True/False for ok. Returns the job_id immediately so the
    page can poll /api/job/<id>?offset=N for live output."""
    _web_job_seq[0] += 1
    job_id = str(_web_job_seq[0])
    job = {"status": label, "output": "", "done": False, "ok": None}
    _web_jobs[job_id] = job
    stream = _JobStream(job)

    def worker():
        ok_flag = True
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                _WEB_STREAM[0] = True       # stream real subprocess output → console
                try:
                    ok_flag = fn() is not False
                finally:
                    _WEB_STREAM[0] = False
        except SystemExit as e:
            ok_flag = (str(e) in ("0", "None"))
        except Exception as e:
            ok_flag = False
            stream.write(f"\nerror: {e}\n")
        with _web_jobs_lock:
            job["done"] = True
            job["ok"] = ok_flag
            job["status"] = label + (" ✓" if ok_flag else " ✗")

    threading.Thread(target=worker, daemon=True).start()
    return job_id

def _job_snapshot(job_id: str, offset: int = 0) -> dict | None:
    """Return a job's state with only output past `offset` (incremental)."""
    job = _web_jobs.get(job_id)
    if job is None:
        return None
    with _web_jobs_lock:
        full = job["output"]
        return {"status": job["status"], "done": job["done"], "ok": job["ok"],
                "offset": len(full), "chunk": full[offset:] if offset < len(full) else ""}

def _compose_no_follow_logs(instance: str, tail: int = 200) -> None:
    """Print the last `tail` lines of wp+db logs WITHOUT -f (cmd_logs follows
    forever, which would hang a web job). Prints to current stdout (captured
    by the job stream)."""
    compose("logs", "--no-color", f"--tail={tail}", "wp", "db",
            instance=instance, check=False)

def _web_list_snapshots(instance: str) -> list[str]:
    """Snapshot names saved for an instance (for the restore picker)."""
    d = snapshots_dir(instance)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())

def _web_list_seeds() -> list[str]:
    """WXR files available under runtime/seeds/ (for the seed picker)."""
    if not SEEDS_DIR.exists():
        return []
    return sorted(p.name for p in SEEDS_DIR.iterdir()
                  if p.is_file() and p.suffix in (".xml", ".wxr"))

_CLAUDE_PRICES = {
    "opus":   {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.50},
    "sonnet": {"in": 3.0,  "out": 15.0, "cw": 3.75,  "cr": 0.30},
    "haiku":  {"in": 0.80, "out": 4.0,  "cw": 1.00,  "cr": 0.08},
}

def _price_tier(model: str) -> str:
    m = (model or "").lower()
    if "opus" in m: return "opus"
    if "haiku" in m: return "haiku"
    return "sonnet"   # default/unknown → sonnet rates

def _cost_for(tier: str, u: dict) -> float:
    p = _CLAUDE_PRICES[tier]
    return (u["in"]*p["in"] + u["out"]*p["out"]
            + u["cw"]*p["cw"] + u["cr"]*p["cr"]) / 1_000_000

def _claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"

def claude_usage(known_instances: list[str]) -> dict:
    """Aggregate Claude token usage + estimated cost across all session
    transcripts, with a best-effort per-instance breakdown.

    Per-project model: there's ONE `sandbox` MCP server, so the tool NAMESPACE
    no longer encodes the instance — attribution is by each sandbox tool call's
    `project_dir` argument, mapped to an instance via the registry.

    Returns {total, by_model, per_instance, sessions:[...recent...], generated}.
    Resilient: skips unreadable lines/files; never raises."""
    pdir = _claude_projects_dir()
    blank = lambda: {"in": 0, "out": 0, "cw": 0, "cr": 0}
    # project root (canonical) -> instance, for mapping a tool's project_dir.
    sc = _core()
    root_to_inst = {r: e.get("instance")
                    for r, e in sc.registry_all().items() if e.get("instance")}

    def _pd_to_inst(pd):
        if not pd:
            return None
        try:
            root = str(sc.find_project_root(pd))
        except Exception:
            try:
                root = str(Path(pd).expanduser().resolve())
            except Exception:
                return None
        return root_to_inst.get(root)

    total = blank(); by_model = {}; per_instance = {}; sessions = []
    if not pdir.exists():
        return {"total": total, "tokens": 0, "cost": 0.0, "by_model": {},
                "per_instance": {}, "sessions": [], "available": False}

    for proj in pdir.iterdir():
        if not proj.is_dir():
            continue
        for tf in proj.glob("*.jsonl"):
            su = blank(); s_models = set(); s_dirs = set(); s_used = False
            s_mtime = tf.stat().st_mtime
            try:
                for line in tf.open(errors="ignore"):
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    msg = o.get("message") or {}
                    u = msg.get("usage")
                    if u:
                        su["in"] += u.get("input_tokens", 0) or 0
                        su["out"] += u.get("output_tokens", 0) or 0
                        su["cw"] += u.get("cache_creation_input_tokens", 0) or 0
                        su["cr"] += u.get("cache_read_input_tokens", 0) or 0
                        if msg.get("model"):
                            s_models.add(msg["model"])
                    for blk in (msg.get("content") or []):
                        if isinstance(blk, dict) and blk.get("type") == "tool_use" \
                                and str(blk.get("name", "")).startswith("mcp__sandbox"):
                            s_used = True
                            pd = (blk.get("input") or {}).get("project_dir")
                            if pd:
                                s_dirs.add(pd)
            except OSError:
                continue
            if not any(su.values()):
                continue
            # Only count sessions that touched a sandbox tool OR ran from a
            # sandbox project dir (transcript dir name encodes the cwd).
            touched_sandbox = s_used or "sandbox" in proj.name.lower()
            if not touched_sandbox:
                continue

            tier = _price_tier(next(iter(s_models), ""))
            cost = _cost_for(tier, su)
            for k in total: total[k] += su[k]
            bm = by_model.setdefault(tier, blank())
            for k in bm: bm[k] += su[k]
            # Attribute to each instance whose project_dir this session drove;
            # sandbox-touching sessions with no resolvable project go to
            # 'unattributed' rather than silently onto one instance.
            insts = sorted({i for i in (_pd_to_inst(d) for d in s_dirs) if i})
            targets = insts or ["unattributed"]
            for inst in targets:
                pi = per_instance.setdefault(inst, {**blank(), "cost": 0.0})
                for k in su: pi[k] += su[k]
                pi["cost"] += cost / len(targets)
            sessions.append({
                "id": tf.stem[:8], "model": tier, "mtime": s_mtime,
                "tokens": sum(su.values()), "cost": round(cost, 4),
                "instances": insts,
            })

    total_tokens = sum(total.values())
    total_cost = sum(_cost_for(t, by_model[t]) for t in by_model)
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    by_model_out = {t: {**by_model[t], "cost": round(_cost_for(t, by_model[t]), 4)}
                    for t in by_model}
    per_instance_out = {i: {**v, "cost": round(v["cost"], 4)}
                        for i, v in per_instance.items()}
    return {
        "available": True,
        "total": total, "tokens": total_tokens, "cost": round(total_cost, 4),
        "by_model": by_model_out, "per_instance": per_instance_out,
        "sessions": sessions[:25],
    }

def _bridge_port_up() -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", BRIDGE_PORT)) == 0
    finally:
        s.close()


def _ensure_bridge_server() -> None:
    """Start the `sb web` snapshot bridge on BRIDGE_PORT if it isn't already
    running (idempotent, FR-014). Verifies any existing listener is actually our
    bridge (not a foreign service squatting the port — in which case we leave it
    alone rather than spawn over it / trust it), and serializes startup with a
    stale-aware lock so two concurrent `sb up`s don't double-spawn."""
    import time, urllib.request, json as _json
    if _bridge_port_up():
        try:  # confirm it's OUR bridge (dashboard route returns {"instances":…})
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{BRIDGE_PORT}/api/instances", timeout=0.5) as r:
                _json.loads(r.read() or b"{}")
        except Exception:
            pass
        return  # something is serving the port — don't spawn a competing one
    lock = ROOT / "runtime" / "locks" / "bridge-web.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and (time.time() - lock.stat().st_mtime) > 30:
            lock.unlink(missing_ok=True)  # stale (a previous start crashed)
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return  # another start is already in flight
    try:
        subprocess.Popen(
            [str(ENTRY), "web", "--port", str(BRIDGE_PORT), "--exact-port"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        for _ in range(20):           # hold the lock until it's accepting (≤~6s)
            time.sleep(0.3)
            if _bridge_port_up():
                break
    finally:
        lock.unlink(missing_ok=True)


def _bridge_token_for(instance: str) -> str | None:
    return ((_local_yaml().get("instances") or {}).get(instance) or {}).get("bridge_token")


def _bridge_handle(method: str, instance: str, subpath: str,
                   body: dict, auth: str) -> tuple[int, dict]:
    """Token-authed snapshot bridge for the wp-admin mu-plugin (spec 002).

    Only these verbs, only for `instance`, only with the matching Bearer token:
      GET /snapshots · POST /snapshot {name,force} · POST /restore {name}
      DELETE /snapshot/<name> · GET /job/<id>
    Snapshot/restore run out-of-band via the existing job machinery so a restore
    never severs the caller's request. NO arbitrary host commands (FR-010)."""
    import time, shutil as _sh, types as _types, hmac
    tok = _bridge_token_for(instance)
    if not tok:
        return 404, {"ok": False, "error": "unknown instance"}
    presented = (auth or "").removeprefix("Bearer ").strip()
    if not presented or not hmac.compare_digest(presented, tok):
        return 403, {"ok": False, "error": "unauthorized"}
    if _is_herd_instance(instance):
        return 409, {"ok": False, "error": "unsupported", "reason": "herd"}
    # Snapshot name guard — must be an isolated, alnum-led token (no `.`/`..`/`/`
    # path traversal). Applied to EVERY name the bridge turns into a filesystem
    # path, since the bridge — not the mu-plugin — is the trust boundary.
    def _ok_name(n):
        return bool(re.fullmatch(r"[A-Za-z0-9][\w.-]*", n or "")) and ".." not in (n or "")
    from sandbox.commands.data import cmd_snapshot, cmd_restore  # late: avoid cycle
    cfg = load_config()

    if method == "GET" and subpath == "/snapshots":
        root = snapshots_dir(instance)
        snaps = []
        if root.exists():
            for e in sorted(root.iterdir()):
                if not e.is_dir():
                    continue
                meta = ((e / "META").read_text().strip().replace("\n", " ")
                        if (e / "META").exists() else "")
                size = sum(f.stat().st_size for f in e.rglob("*") if f.is_file())
                snaps.append({"name": e.name, "size_kb": size // 1024, "meta": meta})
        return 200, {"ok": True, "snapshots": snaps}

    if method == "POST" and subpath == "/snapshot":
        name = (body.get("name") or "").strip() or time.strftime("snap-%Y%m%d-%H%M%S")
        if not _ok_name(name):
            return 400, {"ok": False, "error": "invalid snapshot name"}
        ns = _types.SimpleNamespace(resolved_instance=instance, name=name,
                                    force=bool(body.get("force")))
        jid = _start_job(f"snapshot {name}", lambda: cmd_snapshot(cfg, ns))
        return 202, {"ok": True, "job_id": jid, "name": name}

    if method == "POST" and subpath == "/restore":
        name = (body.get("name") or "").strip()
        if not _ok_name(name):
            return 400, {"ok": False, "error": "invalid snapshot name"}
        if not (snapshots_dir(instance) / name).exists():
            return 404, {"ok": False, "error": "no such snapshot"}
        ns = _types.SimpleNamespace(resolved_instance=instance, name=name)
        jid = _start_job(f"restore {name}", lambda: cmd_restore(cfg, ns))
        return 202, {"ok": True, "job_id": jid, "name": name}

    if method == "DELETE" and subpath.startswith("/snapshot/"):
        from urllib.parse import unquote
        name = unquote(subpath[len("/snapshot/"):])
        if not _ok_name(name):
            return 400, {"ok": False, "error": "invalid snapshot name"}
        d = snapshots_dir(instance) / name
        if not d.exists():
            return 404, {"ok": False, "error": "no such snapshot"}
        _sh.rmtree(d)
        return 200, {"ok": True}

    if method == "GET" and subpath.startswith("/job/"):
        snap = _job_snapshot(subpath[len("/job/"):])
        if snap is None:
            return 404, {"ok": False, "error": "no such job"}
        status = ("running" if not snap["done"]
                  else ("succeeded" if snap["ok"] else "failed"))
        return 200, {"ok": True, "status": status, "detail": snap["status"]}

    return 404, {"ok": False, "error": "not found"}


def _web_do_action(payload: dict) -> dict:
    from sandbox.commands.lifecycle import cmd_up, cmd_down, cmd_status, cmd_update, cmd_doctor
    from sandbox.commands.instances_cmd import cmd_focus, cmd_instance
    from sandbox.commands.debug import cmd_introspect, cmd_xdebug
    from sandbox.commands.data import cmd_restore, cmd_snapshot
    from sandbox.commands.wp import cmd_seed, cmd_wp
    from sandbox.commands.net import cmd_server
    """Dispatch a UI action to the matching cmd_*. Fast actions return output
    inline; create/delete (and the all-* sweeps) return a job_id and run in a
    background thread."""
    action = payload.get("action")
    name = (payload.get("instance") or "").strip()
    valid_fast = {"start", "stop", "restart", "focus", "unfocus"}

    if action in valid_fast:
        if not name:
            return {"ok": False, "output": "missing instance"}
        with _web_lock:
            if action == "start":
                ok_f, out = _run_cmd_capture(
                    cmd_up, _types.SimpleNamespace(resolved_instance=name))
            elif action == "stop":
                ok_f, out = _run_cmd_capture(
                    cmd_down, _types.SimpleNamespace(resolved_instance=name))
            elif action == "restart":
                out_buf = io.StringIO()
                with redirect_stdout(out_buf), redirect_stderr(out_buf):
                    compose("restart", "wp", instance=name, check=False)
                ok_f, out = True, out_buf.getvalue()
            elif action == "focus":
                slug = (payload.get("slug") or "").strip()
                if not slug:
                    return {"ok": False, "output": "missing plugin slug"}
                ok_f, out = _run_cmd_capture(cmd_focus, _types.SimpleNamespace(
                    resolved_instance=name, slug=slug, clear=False))
            elif action == "unfocus":
                ok_f, out = _run_cmd_capture(cmd_focus, _types.SimpleNamespace(
                    resolved_instance=name, slug=None, clear=True))
        return {"ok": ok_f, "output": out}

    # Sweep actions over every instance — backgrounded (booting all stacks is
    # slow). The page polls the job and refreshes when done.
    if action in ("start-all", "stop-all"):
        _web_job_seq[0] += 1
        job_id = str(_web_job_seq[0])
        _web_jobs[job_id] = {"status": f"{action}…", "output": "",
                             "done": False, "ok": None}

        def sweep():
            with _web_lock:
                cfg = load_config()
                names = list(resolve_instances(cfg).keys())
                fn = cmd_up if action == "start-all" else cmd_down
                buf = io.StringIO()
                allok = True
                for n in names:
                    with redirect_stdout(buf), redirect_stderr(buf):
                        try:
                            fn(cfg, _types.SimpleNamespace(resolved_instance=n))
                        except Exception as e:
                            allok = False
                            buf.write(f"\n{n}: error {e}\n")
                    buf.write(f"— {action} {n} done\n")
                _web_jobs[job_id].update(
                    output=buf.getvalue(), done=True, ok=allok,
                    status=f"{action} {'✓' if allok else '✗'}")

        threading.Thread(target=sweep, daemon=True).start()
        return {"ok": True, "job_id": job_id}

    # Ops / terminal actions — each streams output into a job the console
    # panel tails. All read or scoped to one instance; destructive ones
    # (restore) are confirmed client-side. `shell`/`claude` are intentionally
    # NOT exposed (interactive, can't work over HTTP).
    OPS = {"logs", "status", "doctor", "snapshot", "restore", "seed",
           "update", "xdebug", "wp", "introspect", "install", "term"}
    if action in OPS:
        if not name:
            return {"ok": False, "output": "missing instance"}
        ns_base = {"resolved_instance": name}

        def run_op():
            cfg = load_config()
            if action == "logs":
                # Non-following snapshot of recent logs (the -f variant would
                # never return). Tail the last N lines of wp+db.
                _compose_no_follow_logs(name)
            elif action == "status":
                cmd_status(cfg, _types.SimpleNamespace(**ns_base))
            elif action == "doctor":
                cmd_doctor(cfg, _types.SimpleNamespace(**ns_base))
            elif action == "update":
                cmd_update(cfg, _types.SimpleNamespace(**ns_base))
            elif action == "introspect":
                cmd_introspect(cfg, _types.SimpleNamespace(
                    target=payload.get("target") or "all", **ns_base))
            elif action == "xdebug":
                state = payload.get("state") or "status"
                if state not in ("on", "off", "status"):
                    print("invalid xdebug state"); return False
                cmd_xdebug(cfg, _types.SimpleNamespace(state=state, **ns_base))
            elif action == "snapshot":
                snap = (payload.get("name") or "").strip()
                if not re.match(r"^[a-z0-9][a-z0-9_-]{0,40}$", snap):
                    print("invalid snapshot name"); return False
                cmd_snapshot(cfg, _types.SimpleNamespace(
                    name=snap, force=bool(payload.get("force")), **ns_base))
            elif action == "restore":
                snap = (payload.get("name") or "").strip()
                if not snap:
                    print("missing snapshot name"); return False
                cmd_restore(cfg, _types.SimpleNamespace(name=snap, **ns_base))
            elif action == "seed":
                f = (payload.get("file") or "").strip()
                if not f:
                    print("missing seed file"); return False
                cmd_seed(cfg, _types.SimpleNamespace(file=f, **ns_base))
            elif action == "wp":
                argstr = (payload.get("args") or "").strip()
                if not argstr:
                    print("missing wp-cli args"); return False
                import shlex as _shlex
                cmd_wp(cfg, _types.SimpleNamespace(
                    passthrough=_shlex.split(argstr), **ns_base))
            elif action == "install":
                slug = (payload.get("slug") or "").strip()
                if not re.match(r"^[a-z0-9][a-z0-9.-]{0,60}$", slug):
                    print("invalid plugin slug"); return False
                # Install from the wp.org directory + activate (streamed).
                wpcli(["plugin", "install", slug, "--activate"], instance=name)
            elif action == "term":
                # Interactive terminal: run a command INSIDE the instance's
                # container (not the host). `wp ...` → wpcli container; anything
                # else → shell in the wp container. Streamed live.
                line = (payload.get("cmd") or "").strip()
                if not line:
                    print("(empty)"); return True
                import shlex as _shlex
                if line == "wp" or line.startswith("wp "):
                    rest = line[2:].strip()
                    try:
                        wpcli(_shlex.split(rest), instance=name, check=False)
                    except Exception as e:
                        print(f"error: {e}"); return False
                else:
                    # shell in the wp container (sh -c "<line>")
                    compose("exec", "-T", "wp", "sh", "-c", line,
                            instance=name, check=False)
            return True

        label = f"{action}" + (f" {name}" if action != "wp"
                               else f" {name}: {payload.get('args','')}")
        # Serialize against other mutating actions via the lock inside the job.
        def locked():
            with _web_lock:
                return run_op()
        return {"ok": True, "job_id": _start_job(label, locked)}

    # Switch an instance's web server in place. Backgrounded + streamed because
    # it recreates the web tier and may pull the OpenLiteSpeed image (slow).
    if action == "server":
        if not name:
            return {"ok": False, "output": "missing instance"}
        try:
            target = _valid_server(payload.get("server"))
        except SystemExit:
            return {"ok": False, "output": "invalid server (apache|nginx|litespeed)"}
        label = f"server {name} → {target}"

        def do_server():
            with _web_lock:
                cmd_server(load_config(), _types.SimpleNamespace(
                    name=name, server_type=target, resolved_instance=name))
        return {"ok": True, "job_id": _start_job(label, do_server)}

    if action == "create":
        # Per-project model: instances are created by `./sb init` / `./sb ensure`
        # inside a plugin repo (keyed to the project dir), not by name here.
        return {"ok": False, "output":
                "Create an instance by running `./sb init` (or `./sb ensure`) "
                "inside a plugin repo — not from the dashboard."}

    if action == "delete":
        if payload.get("confirm") != name:
            return {"ok": False,
                    "output": "delete requires confirm == instance name"}

        def do_inst():
            with _web_lock:
                cmd_instance(load_config(), _types.SimpleNamespace(
                    action="delete", name=name, yes=True, resolved_instance=name))
        return {"ok": True, "job_id": _start_job(f"Deleting {name}", do_inst)}

    return {"ok": False, "output": f"unknown action '{action}'"}

_WEB_CSS_CACHE = [None]

def _web_css() -> str:
    """Vendored, pre-built Tailwind CSS (config/sandbox-web.css). Inlined into
    the page so the UI is fully self-contained — no CDN, works offline.
    Rebuild after editing classes: scripts/build-web-css.sh."""
    if _WEB_CSS_CACHE[0] is None:
        css_path = ROOT / "config" / "sandbox-web.css"
        try:
            _WEB_CSS_CACHE[0] = css_path.read_text()
        except OSError:
            _WEB_CSS_CACHE[0] = ""   # graceful: unstyled but functional
    return _WEB_CSS_CACHE[0]

_WEB_JS_CACHE = [None]

def _web_js() -> str:
    """Vendored, pre-built dashboard bundle (config/sandbox-web.js) — compiled
    from the TypeScript source in src/web by Vite. Inlined into the page so the
    UI is fully self-contained (no node/CDN at runtime). Rebuild after editing
    src/web: scripts/build-web-js.sh."""
    if _WEB_JS_CACHE[0] is None:
        js_path = ROOT / "config" / "sandbox-web.js"
        try:
            _WEB_JS_CACHE[0] = js_path.read_text()
        except OSError:
            _WEB_JS_CACHE[0] = "console.error('sandbox-web.js missing — run scripts/build-web-js.sh');"
    return _WEB_JS_CACHE[0]

_WEB_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sandbox</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Vendored, pre-built Tailwind CSS (no CDN, works offline). Rebuild with
     scripts/build-web-css.sh after changing classes in this page. -->
<style>__SANDBOX_WEB_CSS__</style>
<style>
  /* ---- desktop-app feel (overrides on top of Tailwind) ---- */
  html, body { -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
  * { transition: background-color .14s ease, border-color .14s ease, color .12s ease,
        box-shadow .14s ease, transform .08s ease; }
  ::-webkit-scrollbar { width: 9px; height: 9px; }
  ::-webkit-scrollbar-thumb { background: #8884; border-radius: 9999px; border: 2px solid transparent;
    background-clip: padding-box; }
  ::-webkit-scrollbar-thumb:hover { background: #8888; background-clip: padding-box; }
  .spin { animation: sp 0.7s linear infinite; }
  @keyframes sp { to { transform: rotate(360deg); } }

  /* Flatten the pill buttons into crisp desktop controls + give them depth.
     Targets the action buttons rendered with rounded-full / rounded in the JS. */
  button[disabled] { opacity: .4; cursor: default; }
  main button:not([disabled]):active, footer button:not([disabled]):active { transform: translateY(0.5px); }
  /* pill action buttons → desktop radius + subtle shadow */
  .rounded-full { border-radius: 7px !important; }
  main .rounded-full, footer .rounded-full, aside .rounded-full {
    box-shadow: 0 1px 0 rgba(0,0,0,.03); }
  /* primary (accent) buttons get a soft raised shadow */
  .bg-accent { box-shadow: 0 1px 2px rgba(37,99,235,.35), inset 0 1px 0 rgba(255,255,255,.12); }

  /* sidebar rows: tighter, app-like selection */
  #list button { border-radius: 7px; }

  /* console drawer: terminal vibe */
  #conBody { background:
    linear-gradient(180deg, rgba(255,255,255,.015), transparent 120px); }

  /* fade-in for panel content swaps */
  #detail > * { animation: fadein .18s ease; }
  @keyframes fadein { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }
</style></head>
<body class="font-sans bg-page dark:bg-page-dark text-ink dark:text-ink-dark antialiased h-screen overflow-hidden flex flex-col">

<!-- Desktop title bar (window chrome) -->
<div class="h-9 shrink-0 flex items-center px-3.5 gap-2 border-b border-brd dark:border-brd-dark
     bg-neutral-100/80 dark:bg-neutral-900/80 backdrop-blur select-none">
  <span class="w-3 h-3 rounded-full" style="background:#ff5f57"></span>
  <span class="w-3 h-3 rounded-full" style="background:#febc2e"></span>
  <span class="w-3 h-3 rounded-full" style="background:#28c840"></span>
  <div class="flex-1 text-center text-[12px] font-medium text-neutral-500 dark:text-neutral-400">
    Sandbox — WordPress dev environments</div>
  <span class="w-12"></span>
</div>

<div class="flex flex-1 min-h-0">
  <!-- Sidebar: instance list (Local-style) -->
  <aside class="w-60 shrink-0 border-r border-brd dark:border-brd-dark
                bg-neutral-100/60 dark:bg-neutral-950 flex flex-col">
    <button onclick="goHome()" title="What is this?"
      class="h-12 px-3.5 flex items-center gap-2 border-b border-brd dark:border-brd-dark
             w-full hover:bg-neutral-200/50 dark:hover:bg-neutral-900 text-left">
      <div class="w-5 h-5 rounded-md bg-accent flex items-center justify-center
                  text-white text-[12px] font-bold">S</div>
      <span class="font-semibold text-[13px] text-neutral-900 dark:text-neutral-50">Sandbox</span>
      <span id="runcount" class="ml-auto text-[11px] text-neutral-400"></span>
    </button>
    <div class="px-3 pt-3 pb-1 text-[11px] font-medium uppercase tracking-wide
                text-neutral-400">Instances</div>
    <nav id="list" class="flex-1 overflow-auto px-2 pb-2 space-y-0.5"></nav>
    <div class="p-2 border-t border-brd dark:border-brd-dark space-y-0.5">
      <button id="newBtn" class="w-full text-[13px] px-3 py-2 rounded
        text-neutral-600 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800
        flex items-center gap-2">
        <span class="text-[15px] leading-none">+</span> New instance</button>
      <button id="termBtn" class="w-full text-[13px] px-3 py-2 rounded text-left
        text-neutral-500 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800
        flex items-center gap-2">
        <span class="text-[13px] leading-none font-mono">›_</span> Terminal</button>
      <button id="usageBtn" class="w-full text-[13px] px-3 py-2 rounded text-left
        text-neutral-500 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800
        flex items-center gap-2">
        <span class="text-[13px] leading-none">◴</span> Claude usage</button>
      <button id="helpBtn" class="w-full text-[13px] px-3 py-2 rounded text-left
        text-neutral-500 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800
        flex items-center gap-2">
        <span class="text-[14px] leading-none">?</span> Using Claude</button>
    </div>
  </aside>

  <!-- Detail panel -->
  <div class="flex-1 flex flex-col min-w-0">
    <main id="detail" class="flex-1 overflow-auto"></main>
    <!-- Footer bar -->
    <footer class="h-12 shrink-0 border-t border-brd dark:border-brd-dark
       bg-app dark:bg-card-dark px-5 flex items-center gap-3 text-[12.5px]">
      <span id="footstat" class="text-neutral-500 dark:text-neutral-400"></span>
      <div class="ml-auto flex items-center gap-2">
        <button id="startAll" class="px-3 py-1 rounded border border-brd dark:border-neutral-700
          text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">
          Start all</button>
        <button id="stopAll" class="px-3 py-1 rounded border border-red-200 dark:border-red-900/60
          text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40">
          Stop all</button>
      </div>
    </footer>
  </div>

  <!-- Console: right-side drawer, slides in. Width animates 0 → 26rem. -->
  <div id="console" class="shrink-0 w-0 overflow-hidden border-l border-neutral-800
       bg-neutral-950 flex flex-col transition-[width] duration-300 ease-out">
    <div class="w-[26rem] flex flex-col h-full">
      <div class="h-14 px-4 flex items-center gap-2 border-b border-neutral-800 shrink-0">
        <span id="conDot" class="w-2 h-2 rounded-full bg-neutral-500"></span>
        <span id="conTitle" class="text-neutral-200 text-[13px] font-medium truncate flex-1">Activity</span>
        <button id="conClose" class="text-neutral-500 hover:text-neutral-200 text-[18px] leading-none">×</button>
      </div>
      <pre id="conBody" class="flex-1 overflow-auto px-4 py-3 text-[11.5px] leading-relaxed
        font-mono text-neutral-300 whitespace-pre-wrap"></pre>
      <!-- Interactive terminal input (runs in the selected instance's container) -->
      <div id="conInputRow" class="hidden shrink-0 border-t border-neutral-800 flex items-center gap-1.5 px-3 py-2">
        <span class="text-emerald-400 font-mono text-[12px]">›</span>
        <input id="conInput" spellcheck="false" autocomplete="off"
          placeholder="wp plugin list   ·   or any shell command"
          class="flex-1 min-w-0 bg-transparent text-neutral-100 font-mono text-[12px] outline-none placeholder:text-neutral-600">
      </div>
    </div>
  </div>
</div>

<!-- Modal -->
<div id="modal" class="hidden fixed inset-0 z-50 flex items-center justify-center
     bg-black/40 backdrop-blur-sm p-4">
  <div class="bg-app dark:bg-card-dark border border-brd dark:border-brd-dark rounded-lg
       shadow-xl w-full max-w-md p-5 flex flex-col gap-3.5 max-h-[85vh]">
    <h2 id="mTitle" class="text-[15px] font-semibold text-neutral-900 dark:text-neutral-50"></h2>
    <p id="mDesc" class="text-[13px] text-neutral-500 dark:text-neutral-400 leading-snug"></p>
    <div id="mFields" class="flex flex-col gap-2.5 overflow-y-auto -mx-1 px-1"></div>
    <div class="flex justify-end gap-2 pt-1">
      <button id="mCancel" class="px-3 py-1.5 rounded border border-brd dark:border-neutral-700
         text-[13px] text-neutral-600 dark:text-neutral-300
         hover:bg-neutral-50 dark:hover:bg-neutral-800">Cancel</button>
      <button id="mOk" class="px-3 py-1.5 rounded text-[13px] text-white
         bg-accent border border-accent hover:bg-blue-700">Confirm</button>
    </div>
  </div>
</div>

<div id="toasts" class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 items-end"></div>

<script>__SANDBOX_WEB_JS__</script></body></html>"""
