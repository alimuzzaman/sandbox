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


def _herd_wp_cmd(instance: str) -> list[str]:
    """Argv prefix to run host wp-cli under a herd instance's pinned PHP.

    wp-cli ships as a phar with a `#!/usr/bin/env php` shebang, so invoking it
    as `<pinned-php> <wp.phar>` (instead of executing the phar directly, which
    would pick up the default `php`) is what makes `sb wp …` honor phpVersion."""
    # Herd's default CLI limit can be 128M, which is insufficient for
    # `wp core download` extracting a current WordPress archive. Keep this
    # scoped to Sandbox's host-side wp-cli calls; it does not alter the web
    # site's PHP configuration.
    return [_herd_php(instance), "-d", "memory_limit=512M", _host_wp()]


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


class _LegacyHerdIngressProcess:
    """Adapt the frozen core CLI runner to A's explicit ingress contract."""

    def __init__(self, *, cwd): self.cwd = cwd

    def run(self, argv, *, timeout):
        # A's facade owns the command selection.  This compatibility wrapper
        # only preserves the old cwd and bounded process invocation.
        return _herd(*argv[1:], cwd=self.cwd)


def _herd_ingress_facade(*, cwd):
    """Return A's legacy Herd route facade; C runtime code never receives it."""
    from sandbox.ingress.adapters.herd_valet import HerdSiteCompatibilityFacade
    return HerdSiteCompatibilityFacade(
        executable=_herd_cli(), process=_LegacyHerdIngressProcess(cwd=cwd),
    )


def _provision_herd_route(name: str, directory: Path) -> dict:
    """A-owned compatibility handoff for legacy link/secure route setup."""
    return _herd_ingress_facade(cwd=directory).provision(name, secure=True)


def _cleanup_herd_route(name: str, directory: Path | None = None) -> dict:
    """A-owned compatibility handoff for legacy route/TLS teardown."""
    return _herd_ingress_facade(cwd=directory).cleanup(name)


def _provision_herd(name: str, pconf: dict) -> None:
    """Host-side counterpart of `compose up`: make runtime/wp-<name>/ a Herd
    site at https://<name>.test. A's compatibility ingress facade owns link +
    secure; C retains only the PHP runtime pin. Isolate runs LAST (after A has
    registered the site) and
    is verified+retried — on a fresh link it otherwise fails ("site could not
    be found"). CLI/phpunit run the same pinned PHP via _herd_php().
    Everything is idempotent; isolate/secure failures degrade, not abort."""
    _host_wp()   # fail fast with a clear message if host wp-cli is missing
    wpd = wp_dir(name)
    wpd.mkdir(parents=True, exist_ok=True)
    info(f"herd: linking {wpd.name}/ → {_herd_domain(name)} …")
    route = _provision_herd_route(name, wpd)
    if not route["ok"]:
        die(f"herd link failed: {route.get('error', 'unknown error')[:400]}")
    info(f"herd: securing https://{_herd_domain(name)} …")
    if not route["secure"]:
        info(f"⚠ herd secure failed (site stays on http): "
             f"{route.get('error', 'unknown error')[:200]}")
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


def _herd_tests_db(instance: str) -> str:
    return _herd_db_name(instance) + "_tests"


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


def _ensure_project_dependencies_herd(inst: str, root: str, composer: Path) -> None:
    """Install only project Composer dependencies with Herd's pinned PHP."""
    php = _herd_php(inst)
    root_p = Path(root)
    if not (root_p / "composer.json").is_file() or (root_p / "vendor" / "autoload.php").exists():
        return
    info(f"composer install (plugin dev deps, host PHP {php})…")
    r = subprocess.run([php, str(composer), "install",
                        "--no-interaction", "--no-progress", "--no-plugins"],
                       cwd=root)
    if r.returncode != 0:
        info("locked install failed (stale lock) — running composer update…")
        subprocess.run([php, str(composer), "update",
                        "--no-interaction", "--no-progress", "--no-plugins"],
                       cwd=root)


def _run_tests_herd(inst: str, root: str, suite: Path, tools: dict,
                    extra: list) -> int:
    """Run PHPUnit with Herd's pinned PHP and the WordPress test environment."""
    php = _herd_php(inst)
    _ensure_project_dependencies_herd(inst, root, tools["composer"])
    info(f"running phpunit (host PHP {php})…")
    env = {**os.environ,
           "WP_TESTS_DIR": str(suite),
           "WP_TESTS_PHPUNIT_POLYFILLS_PATH": str(tools["polyfills"])}
    r = subprocess.run([php, str(tools["phpunit"]), *extra],
                       cwd=root, env=env)
    return r.returncode


def _run_tests_unit_herd(inst: str, root: str, tools: dict, extra: list) -> int:
    """Run a pure PHPUnit suite with Herd's pinned PHP and no WP environment."""
    php = _herd_php(inst)
    _ensure_project_dependencies_herd(inst, root, tools["composer"])
    info(f"running pure PHPUnit unit suite (host PHP {php})…")
    return subprocess.run([php, str(tools["phpunit"]), *extra], cwd=root).returncode
