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
        # The wordpress:cli image ships no `git`, so composer can't fetch
        # git-sourced deps (github vcs repos) → vendor/ never builds → phpunit
        # fatals on the missing autoload. Install git first (alpine apk or
        # debian apt, whichever the image is) via a shell entrypoint.
        flags = "--no-interaction --no-progress --no-plugins"
        # Install git (as root — the wordpress:cli image runs as non-root, so apk
        # would be permission-denied) and rewrite git@github SSH URLs to HTTPS so
        # PUBLIC git-sourced deps clone without SSH keys. (Private deps still need
        # the plugin's own composer auth — out of the sandbox's scope.)
        ensure_git = (
            "command -v git >/dev/null 2>&1 || "
            "apk add --no-cache git >/dev/null 2>&1 || "
            "{ apt-get update && apt-get install -y git; } >/dev/null 2>&1 || true; "
            'git config --global url."https://github.com/".insteadOf "git@github.com:" '
            ">/dev/null 2>&1 || true")
        base = ["run", "--rm", "-u", "0:0",
                "-e", "COMPOSER_HOME=/tmp/composer",
                "-e", "COMPOSER_ALLOW_SUPERUSER=1",
                "-v", f"{tools['composer']}:/composer.phar:ro",
                # Mount the project root at its own path so composer (and phpunit
                # below) see it even when the project lives OUTSIDE plugins_home
                # (the base compose only bind-mounts plugins_home).
                "-v", f"{plug}:{plug}",
                "-w", plug, "--entrypoint", "sh", "wpcli", "-c"]
        r = compose(*base, f"{ensure_git}; php /composer.phar install {flags}",
                    instance=inst, check=False, capture=True)
        if getattr(r, "returncode", 1) != 0:
            # Stale/incompatible composer.lock (common: a lock pinned for PHP 7
            # against a PHP 8.x container) — regenerate it for the live PHP.
            info("locked install failed (stale lock) — running composer update…")
            compose(*base, f"{ensure_git}; php /composer.phar update {flags}",
                    instance=inst, check=False)
    info("running phpunit…")
    r = compose("run", "--rm",
                "-v", f"{suite}:/wordpress-phpunit",
                "-v", f"{tools['polyfills']}:/wp-phpunit-polyfills:ro",
                "-v", f"{tools['phpunit']}:/phpunit.phar:ro",
                # The project root (phpunit.xml + tests/) — mounted explicitly so
                # `sb test` works for projects outside plugins_home too.
                "-v", f"{plug}:{plug}",
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
