from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr


def _valid_server(server: str) -> str:
    s = (server or "nginx").strip().lower()
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


def compose_file(instance: str) -> Path:
    """Per-instance generated compose file path."""
    return COMPOSE_DIR / f"{instance}.yml"


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
    lines += _alias_url_php(inst_cfg)
    return "\n".join(lines)


def _alias_url_php(inst_cfg: dict) -> list[str]:
    """PHP that makes WP_HOME/WP_SITEURL follow the request host — but ONLY
    when that host is a declared alias of this instance.

    Constants, not an `option_home` filter: they are read before plugins load,
    so login redirects, enqueued asset URLs, and the REST root all follow the
    alias, which an mu-plugin hooking the option would miss.

    $_SERVER['HTTP_HOST'] is attacker-controlled, so it is matched against the
    declared alias list and nothing else. Anything unrecognized — a spoofed
    Host, the instance's own primary domain, or wp-cli, which sets no HTTP_HOST
    at all — leaves both constants undefined, and WP falls back to the home and
    siteurl options exactly as it does today. That is what keeps this additive:
    the primary hostname's behavior is untouched, and a forged Host cannot
    rewrite the URL WordPress prints into a password-reset mail.

    The scheme comes from X-Forwarded-Proto when the direct HTTPS flag is
    absent, because Caddy (and, on a remote, Cloudflare in front of it)
    terminates TLS and forwards plain HTTP. That header is only trustworthy
    because every path into the instance goes through the sandbox proxy, which
    sets it — a container published straight to the internet would not qualify.
    """
    aliases = instance_aliases(inst_cfg)
    if not aliases:
        return []
    listed = ", ".join(_php_squote(a) for a in aliases)
    return [
        f"$sandbox_alias_hosts = array({listed});",
        "$sandbox_alias_host = isset($_SERVER['HTTP_HOST'])",
        "    ? rtrim(strtolower(trim((string) $_SERVER['HTTP_HOST'])), '.') : '';",
        "if (in_array($sandbox_alias_host, $sandbox_alias_hosts, true)) {",
        "    $sandbox_alias_scheme = 'http';",
        "    if (!empty($_SERVER['HTTPS'])",
        "        && strtolower((string) $_SERVER['HTTPS']) !== 'off') {",
        "        $sandbox_alias_scheme = 'https';",
        "    } elseif (isset($_SERVER['HTTP_X_FORWARDED_PROTO'])",
        "        && strtolower((string) $_SERVER['HTTP_X_FORWARDED_PROTO']) === 'https') {",
        "        $sandbox_alias_scheme = 'https';",
        "    }",
        "    $sandbox_alias_url = $sandbox_alias_scheme . '://' . $sandbox_alias_host;",
        "    defined('WP_HOME') || define('WP_HOME', $sandbox_alias_url);",
        "    defined('WP_SITEURL') || define('WP_SITEURL', $sandbox_alias_url);",
        "}",
    ]


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
    image = _instance_web_image("apache", inst_cfg)
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
      ; for i in 1 2 3 4 5 6 7 8 9 10 11 12; do mkdir -p /var/www/html/wp-content/plugins /var/www/html/wp-content/upgrade /var/www/html/wp-content/upgrade-temp-backup ; chown www-data:www-data /var/www/html/wp-content 2>/dev/null || true ; chmod 0777 /var/www/html/wp-content /var/www/html/wp-content/plugins /var/www/html/wp-content/upgrade /var/www/html/wp-content/upgrade-temp-backup /var/www/html/wp-content/uploads 2>/dev/null || true ; chown -R www-data:www-data /var/www/html/wp-content/plugins /var/www/html/wp-content/upgrade /var/www/html/wp-content/upgrade-temp-backup /var/www/html/wp-content/uploads 2>/dev/null || true ; sleep 4 ; done ) &
      docker-entrypoint.sh apache2-foreground"
    environment:
      WORDPRESS_DB_HOST: db:3306
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wp
      # wp-cli runs here via `exec -u www-data` (HOME=/var/www, unwritable) — point
      # its download cache at a writable path so it doesn't warn on every call.
      WP_CLI_CACHE_DIR: /tmp/.wp-cli/cache
{_env_config_lines(inst_cfg)}
    volumes:
      - {RUNTIME_DIR}/wp-{instance}:/var/www/html
      - {RUNTIME_DIR}/seeds:/seeds
      # Shared plugin/theme download cache: the dl-cache mu-plugin serves &
      # populates zips here so WP-runtime installs (Templately FSI especially)
      # reuse a cached zip instead of re-downloading. Shared across instances.
      - {RUNTIME_DIR}/dl-cache/wp-http:/sandbox-dl-cache
      # Bind-mount plugin sources at the same absolute host path so the
      # symlinks ensure_instance creates under wp-content/plugins/ resolve
      # inside the container.
      - {plugins_host}:{plugins_host}{_extra_vol_lines(inst_cfg)}
      - {ROOT}/config/php-sandbox.ini:/usr/local/etc/php/conf.d/zz-sandbox.ini:ro
      # Built-in wp-cli: shared host phar → exec `wp` in this container (no per-call container).
      - {RUNTIME_DIR}/bin/wp-cli.phar:/usr/local/bin/wp:ro
"""


def _web_nginx(instance: str, inst_cfg: dict, plugins_host: Path) -> str:
    """nginx + php-fpm: two services sharing the WP bind-mount at the SAME
    path (/var/www/html). The `wp` service is php-fpm (internal :9000, no
    published port); `nginx` publishes the instance's wordpress_port and
    reverse-proxies .php to wp:9000. nginx-sandbox.conf carries the WP
    front-controller rewrite (permalinks + /wp-json/ both fall through to
    index.php). Default image is Apache-specific, so pin the fpm flavor here."""
    fpm_image = _instance_web_image("nginx", inst_cfg)
    return f"""  wp:
    image: {fpm_image}
    extra_hosts:                       # reach the host `sb web` snapshot bridge
      - "host.docker.internal:host-gateway"
    depends_on:
      db:
        condition: service_healthy
    # The WordPress bind mount can leave wp-content/plugins owned by the host
    # user, while PHP-FPM runs as www-data. Reconcile the install destination
    # during bootstrap so wp.org and wp-admin plugin installs can create a slug
    # directory (the Apache variant uses the same repair loop).
    command: >
      bash -c "( while [ ! -f /var/www/html/wp-load.php ]; do sleep 1; done
      ; for i in 1 2 3 4 5 6 7 8 9 10 11 12; do mkdir -p /var/www/html/wp-content/plugins /var/www/html/wp-content/upgrade /var/www/html/wp-content/upgrade-temp-backup ; chown www-data:www-data /var/www/html/wp-content 2>/dev/null || true ; chmod 0777 /var/www/html/wp-content /var/www/html/wp-content/plugins /var/www/html/wp-content/upgrade /var/www/html/wp-content/upgrade-temp-backup /var/www/html/wp-content/uploads 2>/dev/null || true ; chown -R www-data:www-data /var/www/html/wp-content/plugins /var/www/html/wp-content/upgrade /var/www/html/wp-content/upgrade-temp-backup /var/www/html/wp-content/uploads 2>/dev/null || true ; sleep 4 ; done ) &
      docker-entrypoint.sh php-fpm"
    # php-fpm listens on :9000 internally; nginx reaches it as wp:9000.
    # No published port — only nginx is web-facing.
    environment:
      WORDPRESS_DB_HOST: db:3306
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wp
      # wp-cli runs here via `exec -u www-data` (HOME=/var/www, unwritable) — point
      # its download cache at a writable path so it doesn't warn on every call.
      WP_CLI_CACHE_DIR: /tmp/.wp-cli/cache
{_env_config_lines(inst_cfg)}
    volumes:
      - {RUNTIME_DIR}/wp-{instance}:/var/www/html
      - {RUNTIME_DIR}/seeds:/seeds
      - {plugins_host}:{plugins_host}{_extra_vol_lines(inst_cfg)}
      - {RUNTIME_DIR}/dl-cache/wp-http:/sandbox-dl-cache
      - {ROOT}/config/php-sandbox.ini:/usr/local/etc/php/conf.d/zz-sandbox.ini:ro
      # Built-in wp-cli: shared host phar → exec `wp` in the fpm container.
      - {RUNTIME_DIR}/bin/wp-cli.phar:/usr/local/bin/wp:ro

  nginx:
    image: nginx:1.27-alpine
    depends_on:
      - wp
    ports:
      - "{inst_cfg["wordpress_port"]}:80"
    volumes:
      # Same WP files nginx serves statically + computes $document_root from.
      - {RUNTIME_DIR}/wp-{instance}:/var/www/html:ro
      # Plugins are symlinked into wp-content/plugins as ABSOLUTE host paths
      # under plugins_host. nginx serves their static assets (js/css/images)
      # itself, so it must resolve those symlinks too — mount plugins_host at
      # the same path it does in the fpm `wp` service, or every symlinked
      # plugin's assets 404 and its admin UI renders blank.
      - {plugins_host}:{plugins_host}:ro{_extra_vol_lines(inst_cfg, ro=True)}
      - {ROOT}/config/nginx-sandbox.conf:/etc/nginx/conf.d/default.conf:ro
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
      # wp-cli runs here via `exec -u www-data` (HOME=/var/www, unwritable) — point
      # its download cache at a writable path so it doesn't warn on every call.
      WP_CLI_CACHE_DIR: /tmp/.wp-cli/cache
    volumes:
      - {RUNTIME_DIR}/wp-{instance}:{docroot}
      - {RUNTIME_DIR}/seeds:/seeds
      - {plugins_host}:{plugins_host}{_extra_vol_lines(inst_cfg)}
      - {RUNTIME_DIR}/dl-cache/wp-http:/sandbox-dl-cache
"""


def _wpcli_service(instance: str, inst_cfg: dict, plugins_host: Path) -> str:
    """The wp-cli helper container. Must mount the SAME host WP dir at the
    SAME in-container docroot as the web tier, and run as the matching uid,
    so `./sb wp` operates on the files the web server actually serves."""
    rt = _server_runtime(inst_cfg["server"])
    docroot = rt["docroot"]
    return f"""  wpcli:
    image: {_instance_wpcli_image(inst_cfg)}
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
      - {RUNTIME_DIR}/wp-{instance}:{docroot}
      - {RUNTIME_DIR}/seeds:/seeds
      - {plugins_host}:{plugins_host}{_extra_vol_lines(inst_cfg)}
      # Persistent, shared wp-cli download cache (WP_CLI_CACHE_DIR points here):
      # `wp plugin/theme/core install` reuse downloads across instances + runs
      # instead of re-fetching into ephemeral /tmp every time.
      - {RUNTIME_DIR}/dl-cache/wp-cli:/tmp/.wp-cli/cache
      - {ROOT}/config/php-sandbox.ini:/usr/local/etc/php/conf.d/zz-sandbox.ini:ro
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
    server = inst_cfg.get("server", "nginx")
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


# The shared download cache's layers, in display order. wp-cli holds wp-cli's
# own versioned cache (plugin/theme/core installs); wp-http holds the runtime
# HTTP cache the dl-cache mu-plugin populates (Templately FSI etc.).
_DL_CACHE_LAYERS = {
    "wp-cli": "wp-cli install cache (plugins/themes/core)",
    "wp-http": "WP runtime cache (Templately FSI etc.)",
}


def _human_bytes(n: int) -> str:
    """Compact human-readable size, e.g. 1.4 MB."""
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{n} B"


def dl_cache_info() -> dict:
    """File count + byte size of the shared download cache, per layer. Pure
    read — used by `./sb cache` and the MCP cache_info tool."""
    layers = []
    for sub, label in _DL_CACHE_LAYERS.items():
        d = DL_CACHE_DIR / sub
        files = [f for f in d.rglob("*") if f.is_file()] if d.is_dir() else []
        layers.append({
            "name": sub, "label": label, "path": str(d),
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files),
        })
    return {
        "dir": str(DL_CACHE_DIR),
        "layers": layers,
        "total_files": sum(l["files"] for l in layers),
        "total_bytes": sum(l["bytes"] for l in layers),
    }


def dl_cache_clear(layer: str | None = None) -> dict:
    """Empty the shared download cache (optionally a single layer: wp-cli |
    wp-http). Re-creates the now-empty dirs 0777 so the next compose run finds
    them ready to bind-mount. Returns the freed byte count."""
    if layer and layer not in _DL_CACHE_LAYERS:
        raise ValueError(f"unknown cache layer '{layer}' "
                         f"(expected one of {', '.join(_DL_CACHE_LAYERS)})")
    subs = [layer] if layer else list(_DL_CACHE_LAYERS)
    freed = 0
    for sub in subs:
        d = DL_CACHE_DIR / sub
        if d.is_dir():
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o777)
        except OSError:
            pass
    return {"cleared": subs, "freed_bytes": freed}


def write_compose_files(cfg: dict) -> None:
    """Regenerate one compose file per instance under runtime/compose/.

    Idempotent: safe to call on every `sb` invocation. Old compose files
    for instances no longer in sandbox.yml are removed so stale stacks
    don't linger.
    """
    COMPOSE_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_wp_cli_phar()  # shared wp-cli phar mounted into each wp container (built-in wp-cli)
    _sync_shipped_seeds()  # seed fixtures shipped in the repo → base SEEDS_DIR (spec 009)
    # Shared, persistent download caches bind-mounted into every instance's web
    # + wpcli tiers. Pre-create them 0777 so the bind mount isn't created
    # root-owned by Docker (the container uids — www-data 33 / lsphp 1000 —
    # must be able to write cached zips).
    for sub in _DL_CACHE_LAYERS:
        d = DL_CACHE_DIR / sub
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o777)
        except OSError:
            pass
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
            check: bool = True, capture: bool = False,
            timeout: float | None = None, stdin=None, stdout=None):
    """Run `docker compose` against one instance's stack.

    Uses the per-instance project name (-p sandbox-<instance>) and
    generated compose file. Caller must have run write_compose_files()
    at least once (the CLI entrypoint does this on every invocation).
    """
    run_kwargs = {
        "check": check,
        "capture": capture,
        "timeout": timeout,
    }
    if stdin is not None:
        run_kwargs["stdin"] = stdin
    if stdout is not None:
        run_kwargs["stdout"] = stdout
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
        **run_kwargs,
    )


# Host-cached wp-cli phar, bind-mounted into every instance's `wp` container at
# /usr/local/bin/wp (one shared file — see _web_apache/_nginx volumes). The path
# uses ROOT, which is back-filled at call time, so it's computed in the function.
_WP_CLI_PHAR_URL = "https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar"
_WP_CLI_BUILTIN: dict[str, bool] = {}  # per-process cache: instance → built-in wp present


def _sync_shipped_seeds() -> None:
    """Copy seed fixtures shipped in the repo (`sandbox/assets/seeds/`) into the
    base SEEDS_DIR if missing. Spec 009: seeds are a tracked repo ASSET (shared via
    git), but the running stacks read/mount SEEDS_DIR under the per-user base — so a
    fresh clone (empty base) still gets the demo content. Idempotent; never
    overwrites a user's own/edited seed of the same name."""
    shipped = ROOT / "sandbox" / "assets" / "seeds"
    if not shipped.is_dir():
        return
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    for src in shipped.iterdir():
        if not src.is_file():
            continue
        dst = SEEDS_DIR / src.name
        if not dst.exists():
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass


def _ensure_wp_cli_phar() -> None:
    """Download the wp-cli phar once into runtime/ (shared by all instances). It is
    bind-mounted read-only into each `wp` container so wp-cli runs via `exec` with no
    per-call `compose run` container (spec: built-in wp-cli per instance)."""
    phar = RUNTIME_DIR / "bin" / "wp-cli.phar"
    if phar.exists() and phar.stat().st_size > 0:
        return
    phar.parent.mkdir(parents=True, exist_ok=True)
    try:
        _download(_WP_CLI_PHAR_URL, phar)
        phar.chmod(0o755)
    except Exception:
        pass  # fall back to the wpcli `run` container if the download fails


def _wp_has_builtin_cli(instance: str) -> bool:
    """True if the instance's running `wp` container has the mounted wp binary.
    Doubles as a running-check (the exec fails if the container is down). Positive
    results are cached for the process; negatives are retried (transient downtime)."""
    if _WP_CLI_BUILTIN.get(instance):
        return True
    r = compose("exec", "-T", "wp", "test", "-f", "/usr/local/bin/wp",
                instance=instance, check=False, capture=True)
    ok = getattr(r, "returncode", 1) == 0
    if ok:
        _WP_CLI_BUILTIN[instance] = True
    return ok


def wpcli(args: list[str], instance: str,
          check: bool = True, capture: bool = False,
          timeout: float | None = None):
    """Run wp-cli against an instance. Herd runs the HOST wp with --path; Docker
    runs the **built-in** wp (a host wp-cli.phar bind-mounted into the always-running
    `wp` container) via `exec -u www-data` — no per-call container. Falls back to the
    one-shot `wpcli` service container if the built-in isn't present yet (e.g. an
    instance not recreated since this landed, or the web container is down)."""
    gateway = _managed_execution_gate(instance, "wordpress.cli", "wordpress_cli", ("wp", *args))
    if gateway is not None:
        return gateway
    if _is_herd_instance(instance):
        # Run wp-cli under the instance's PINNED PHP (php_version), not the
        # phar's default `php` — so plugin code, migrations, and `wp eval`
        # execute on the same PHP the web tier serves.
        return run([*_herd_wp_cmd(instance), f"--path={wp_dir(instance)}", *args],
                   check=check, capture=capture, timeout=timeout)
    # `wp db ...` shells out to the mysql/mysqldump client binary, which the fpm
    # (nginx) web image does NOT ship — only the dedicated `wpcli` service image
    # does. Route DB subcommands to that service so db query/reset/import/etc.
    # work on every server tier (the exec-into-web path 500s with
    # "env: 'mysql': No such file or directory" on nginx).
    needs_db_client = bool(args) and args[0] == "db"
    if _wp_has_builtin_cli(instance) and not needs_db_client:
        # exec into the running web container as www-data (uid 33) so files stay
        # www-data-owned and no --allow-root is needed; same PHP the site serves.
        return compose("exec", "-u", "www-data", "-T", "wp", "wp", *args,
                       instance=instance, check=check, capture=capture,
                       timeout=timeout)
    return compose("run", "--rm", "wpcli", *args,
                   instance=instance, check=check, capture=capture,
                   timeout=timeout)


def _managed_execution_gate(instance: str, capability: str, entry_path: str, argv: tuple[str, ...],
                            *, timeout: int = 300):
    """Refuse managed-native legacy execution until its adapter endpoint is wired.

    Compose instances return ``None`` and retain the exact historical path.
    A managed selection never reaches Docker, Herd, or the host while the
    adapter-native execution transport is unavailable.
    """
    from sandbox.application.context import execute_project, managed_native_instance_selected
    from sandbox.runtimes.base import ExecutionRequest

    owner = managed_native_instance_selected(instance)
    if owner is None:
        return None
    root, label = owner
    request = ExecutionRequest(str(root), label, entry_path, tuple(argv), timeout)
    execution = execute_project(load_config(), request)
    stdout = str(execution.data.get("stdout", ""))
    stderr = str(execution.data.get("stderr", ""))
    return _types.SimpleNamespace(returncode=execution.exit_code, stdout=stdout, stderr=stderr,
                                  managed_native=True, execution=execution)


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
                "dnf": "sudo dnf install -y python3.12",
                "pacman": "sudo pacman -S --noconfirm python",
                "zypper": "sudo zypper install -y python3"}.get(pm))
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
            print("      Install Docker Desktop or OrbStack, then start it so the")
            print("      Docker CLI and Compose plugin can reach its engine.")
            if _offer_install("Docker Desktop", "brew install --cask docker"):
                print("      → now OPEN Docker Desktop once, then re-run `./sb setup`.")
        elif pm == "pacman":
            # docker + docker-compose are official Arch `extra` packages —
            # verified live (pacman -Si docker / docker-compose, both present).
            if _offer_install("docker + docker-compose",
                              "sudo pacman -S --noconfirm docker docker-compose "
                              "&& sudo systemctl enable --now docker.service"):
                user = os.environ.get("USER", "$USER")
                print(f"      → add yourself to the docker group, then re-login:")
                print(f"          sudo usermod -aG docker {user}")
                print(f"          newgrp docker   # or log out/in")
                print(f"        then re-run:  ./sb setup")
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
        engine = "Docker"
        if sys.platform == "darwin":
            context = subprocess.run(["docker", "context", "show"],
                                     capture_output=True, text=True)
            if context.returncode == 0 and context.stdout.strip() == "orbstack":
                engine = "OrbStack"
        print(f"  ✗ docker is installed but {engine} is not running")
        start_cmd = (f"open -a {engine}" if sys.platform == "darwin"
                     else "sudo systemctl start docker")
        if _offer_install(engine, start_cmd, verb="Start"):
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
        print("      (it ships with Docker Desktop and OrbStack; otherwise this")
        print("       usually means an older Docker engine without the v2 plugin)")
        print("      → install Compose v2: https://docs.docker.com/compose/install/")
    else:
        print(f"  ✓ {r.stdout.strip()}")
    return problems


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


def _extension_plan_requirements(requirements):
    """Return a detached plan input with the profile's safe image fallback.

    ``wordpress@1`` requires an image capability, but intentionally does not
    force a project to name GD or Imagick.  Until a fresh observation exists,
    the official child-image path uses the catalogued GD recipe.  Explicitly
    disabling GD leaves the profile unsatisfied (and therefore fails closed)
    instead of silently overriding user intent.  No package, URL, or command
    from the project config can enter this mapping.
    """
    if hasattr(requirements, "to_dict") and callable(requirements.to_dict):
        requirements = requirements.to_dict()
    if not isinstance(requirements, dict):
        requirements = dict(requirements)
    result = {
        str(key): value for key, value in requirements.items()
        if key in {"profile", "extensions"}
    }
    extensions = result.get("extensions", {})
    if not isinstance(extensions, dict):
        extensions = dict(extensions)
    else:
        extensions = dict(extensions)
    profile = result.get("profile")
    if profile == "wordpress@1":
        # A raw scaffold intentionally contains only the immutable profile.
        # Expand its required assertions here as well as in the config model,
        # so direct callers and older persisted blocks do not fail with a
        # misleading ``profile_required_missing`` error before Docker starts.
        for required in (
                "curl", "dom", "exif", "fileinfo", "hash", "json", "mbstring",
                "mysqli", "openssl", "pcre", "xml"):
            extensions.setdefault(required, True)
    gd = extensions.get("gd")
    imagick = extensions.get("imagick")
    if profile == "wordpress@1" and gd is None and imagick is None:
        extensions["gd"] = True
    elif profile == "wordpress@1" and gd is None and imagick is False:
        # The profile cannot be satisfied by the unsupported Imagick path;
        # retain the explicit disable so the resolver returns missing_capability.
        pass
    result["extensions"] = extensions
    return result


def _instance_extension_plan(inst_cfg: dict, server: str, *, requirements=None,
                             parent_digests=None):
    """Return a pure PHP-extension child-image plan when one is requested.

    Image digest resolution is deliberately an input to the planner.  The
    normal Compose path has no extension requirements and remains byte-for-
    byte compatible; opting in without a resolved digest fails before a
    generated Compose file can point at an unverifiable child image.
    """
    requirements = (requirements if requirements is not None else
                    inst_cfg.get("php_extensions", inst_cfg.get("phpExtensions")))
    if requirements in (None, {}, {"extensions": {}}):
        return None
    requirements = _extension_plan_requirements(requirements)
    from sandbox.php_extensions.compose_builder import plan_compose_extension_images

    parent_image = _web_image(
        server, inst_cfg.get("php_version"), inst_cfg.get("wp_version"),
        inst_cfg.get("wordpress_image"),
    )
    wpcli_image = inst_cfg.get("wpcli_image") or _cli_image(inst_cfg.get("php_version"))
    if parent_digests is None:
        parent_digests = inst_cfg.get("php_extension_parent_digests")
        if not isinstance(parent_digests, dict):
            parent_digests = inst_cfg.get("phpExtensionParentDigests")
    parent_digest = (
        inst_cfg.get("php_extension_parent_digest")
        or inst_cfg.get("phpExtensionParentDigest")
        or inst_cfg.get("wordpress_image_digest")
        or inst_cfg.get("wordpressImageDigest")
        or ((parent_digests or {}).get("web") if isinstance(parent_digests, dict) else None)
    )
    wpcli_parent_digest = (
        inst_cfg.get("wpcli_image_digest")
        or inst_cfg.get("wpcliImageDigest")
        or ((parent_digests or {}).get("wpcli") if isinstance(parent_digests, dict) else None)
    )
    # A digest embedded in an image reference remains valid; otherwise the
    # planner emits a clear preflight error instead of inventing a fingerprint.
    return plan_compose_extension_images(
        requirements,
        parent_image=parent_image,
        wpcli_image=wpcli_image,
        parent_digest=parent_digest,
        wpcli_parent_digest=wpcli_parent_digest,
        server=server,
        php_version=inst_cfg.get("php_version"),
        platform=inst_cfg.get("platform"),
        architecture=inst_cfg.get("architecture") or inst_cfg.get("arch"),
        expected_digest=inst_cfg.get("php_extension_digest") or inst_cfg.get("phpExtensionDigest"),
    )


_PHP_EXTENSION_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
_PHP_EXTENSION_PARENT_RE = re.compile(
    r"^(?P<repo>wordpress)(?::(?P<tag>[a-z0-9][a-z0-9._-]*))?"
    r"(?:@(?P<digest>sha256:[0-9a-f]{64}))?$", re.IGNORECASE,
)


def _parent_image_reference(image: str, *, role: str, server: str) -> tuple[str, str | None]:
    """Validate a parent reference before any pull/inspect side effect."""
    if role == "web" and server not in {"apache", "nginx"}:
        raise ValueError(
            f"phpExtensions child-image provisioning supports official Apache/nginx only; "
            f"{server!r} is validate-only"
        )
    if not isinstance(image, str) or not image.strip():
        raise ValueError(f"phpExtensions {role} parent image is missing")
    match = _PHP_EXTENSION_PARENT_RE.fullmatch(image.strip())
    if not match:
        raise ValueError(
            f"phpExtensions {role} parent image must be an official wordpress image"
        )
    tag = match.group("tag") or "latest"
    if role == "wpcli":
        if not tag.startswith("cli"):
            raise ValueError("phpExtensions wpcli parent image must be wordpress:cli")
    elif server == "nginx" and not tag.endswith("-fpm"):
        raise ValueError("phpExtensions nginx parent image must be official wordpress FPM")
    elif server == "apache" and tag.startswith("cli"):
        raise ValueError("phpExtensions Apache parent image cannot be wordpress:cli")
    return image.strip(), match.group("digest")


def _repo_digest_for_image(image: str, output: str) -> str | None:
    """Extract a registry digest from bounded ``docker image inspect`` output."""
    candidates: list[str] = []
    try:
        parsed = json.loads(output or "")
        if isinstance(parsed, list):
            candidates.extend(str(item) for item in parsed if isinstance(item, str))
        elif isinstance(parsed, str):
            candidates.append(parsed)
    except (TypeError, ValueError):
        candidates.extend((output or "").splitlines())
    image_repo = image.split("@", 1)[0].lower()
    # Official references in this feature have no registry port; the tag is
    # not part of RepoDigests (``wordpress:cli-php8.3`` -> ``wordpress@...``).
    if ":" in image_repo.rsplit("/", 1)[-1]:
        image_repo = image_repo.rsplit(":", 1)[0]
    for candidate in candidates:
        candidate = candidate.strip().strip('"')
        if "@" not in candidate:
            continue
        repo, digest = candidate.rsplit("@", 1)
        repo = repo.lower()
        if repo.startswith("docker.io/"):
            repo = repo.removeprefix("docker.io/")
        if repo.startswith("index.docker.io/"):
            repo = repo.removeprefix("index.docker.io/")
        if repo.startswith("registry-1.docker.io/"):
            repo = repo.removeprefix("registry-1.docker.io/")
        repo = repo.removeprefix("library/")
        if repo in {image_repo, "docker.io/library/" + image_repo,
                    "index.docker.io/library/" + image_repo,
                    "registry-1.docker.io/library/" + image_repo} \
                and _PHP_EXTENSION_DIGEST_RE.fullmatch(digest):
            return digest.lower()
    return None


def _resolve_php_extension_parent_digest(image: str, *, role: str, server: str,
                                         provided: str | None = None,
                                         timeout: float = 60) -> str:
    """Resolve a trusted official image digest using the bounded Docker adapter.

    Existing adapter-produced digests are reusable only when structurally
    valid.  A new scaffold has no hidden digest requirement: it resolves a
    local image or performs one bounded pull, then inspects the registry digest.
    """
    image, embedded = _parent_image_reference(image, role=role, server=server)
    if provided is not None:
        if not isinstance(provided, str) or not _PHP_EXTENSION_DIGEST_RE.fullmatch(provided):
            raise ValueError(f"phpExtensions {role} parent digest is invalid")
        return provided.lower()
    if embedded:
        return embedded.lower()

    def inspect():
        return run(
            ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image],
            check=False, capture=True, timeout=timeout,
        )

    result = inspect()
    digest = _repo_digest_for_image(image, getattr(result, "stdout", "")) \
        if getattr(result, "returncode", 1) == 0 else None
    if digest:
        return digest
    pull = run(["docker", "pull", image], check=False, capture=True, timeout=timeout)
    if getattr(pull, "returncode", 1) != 0:
        detail = (getattr(pull, "stderr", "") or getattr(pull, "stdout", "") or "").strip()
        raise ValueError(
            f"phpExtensions {role} parent image could not be pulled: {detail[:400]}"
        )
    result = inspect()
    digest = _repo_digest_for_image(image, getattr(result, "stdout", "")) \
        if getattr(result, "returncode", 1) == 0 else None
    if not digest:
        raise ValueError(
            f"phpExtensions {role} parent image has no trusted repository digest"
        )
    return digest


_PHP_EXTENSION_LABEL_DIGEST = "org.sandbox.php-extensions.digest"
_PHP_EXTENSION_LABEL_ROLE = "org.sandbox.php-extensions.role"
_PHP_EXTENSION_LABEL_PROVENANCE = "org.sandbox.php-extensions.provenance"


def _extension_image_receipt(
    image: str,
    *,
    expected_digest: str,
    expected_role: str,
    expected_provenance: str,
    timeout: float,
) -> dict[str, str] | None:
    """Return a verified receipt for a Sandbox-owned child image tag.

    A successful ``docker image inspect`` is not enough: a mutable tag may
    point at an unrelated image after a cleanup or manual retag.  The image
    must carry the digest, role, and immutable catalog/provenance labels
    emitted by :func:`materialize_compose_extension_context`'s Dockerfile.
    ``None`` means missing, malformed, or mismatched and is intentionally
    treated as a cache miss by the bounded build lifecycle.
    """
    result = run(
        ["docker", "image", "inspect", "--format", "{{json .}}", image],
        check=False, capture=True, timeout=timeout,
    )
    if getattr(result, "returncode", 1) != 0:
        return None
    raw = (getattr(result, "stdout", "") or "").strip()
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(document, list):
        document = document[0] if document else None
    if not isinstance(document, Mapping):
        return None
    labels = document.get("Config")
    labels = labels.get("Labels") if isinstance(labels, Mapping) else None
    if not isinstance(labels, Mapping):
        # A few Docker-compatible adapters expose the flattened key in their
        # inspect document; accepting it keeps this verification adapter
        # portable without weakening the exact label comparisons.
        labels = document.get("Config.Labels")
    if not isinstance(labels, Mapping):
        return None
    observed = {
        "digest": str(labels.get(_PHP_EXTENSION_LABEL_DIGEST, "")).lower(),
        "role": str(labels.get(_PHP_EXTENSION_LABEL_ROLE, "")),
        "provenance": str(labels.get(_PHP_EXTENSION_LABEL_PROVENANCE, "")).lower(),
    }
    if (observed["digest"] != str(expected_digest).lower()
            or observed["role"] != expected_role
            or observed["provenance"] != str(expected_provenance).lower()):
        return None
    image_id = document.get("Id") or document.get("ID") or document.get("id")
    return {
        "image": image,
        "image_id": str(image_id) if image_id else "",
        **observed,
    }


def _extension_image_exists(image: str, *, timeout: float) -> bool:
    """Compatibility existence check for callers outside the build seam."""
    result = run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        check=False, capture=True, timeout=timeout,
    )
    return getattr(result, "returncode", 1) == 0 and bool(
        (getattr(result, "stdout", "") or "").strip()
    )


def _build_php_extension_images(plan, *, timeout: float = 900) -> dict[str, str]:
    """Build both child images through Sandbox's bounded Docker command adapter."""
    from sandbox.php_extensions.compose_builder import materialize_compose_extension_context

    context = materialize_compose_extension_context(plan)
    built: dict[str, str] = {}
    expected_provenance = str(plan.web.provenance.get("recipe_catalog_digest", ""))
    if not expected_provenance:
        raise ValueError("phpExtensions plan is missing catalog provenance")
    for image_plan, dockerfile in (
            (plan.web, context / "Dockerfile.web"),
            (plan.wpcli, context / "Dockerfile.wpcli")):
        receipt = _extension_image_receipt(
            image_plan.image,
            expected_digest=plan.digest,
            expected_role=image_plan.role,
            expected_provenance=expected_provenance,
            timeout=min(timeout, 60),
        )
        if receipt is None:
            result = run(
                ["docker", "build", "--quiet", "--file", str(dockerfile),
                 "--tag", image_plan.image, str(context)],
                check=False, capture=True, timeout=timeout,
            )
            if getattr(result, "returncode", 1) != 0:
                detail = (getattr(result, "stderr", "") or
                          getattr(result, "stdout", "") or "").strip()
                raise ValueError(
                    f"phpExtensions {image_plan.role} child image build failed: {detail[:500]}"
                )
            receipt = _extension_image_receipt(
                image_plan.image,
                expected_digest=plan.digest,
                expected_role=image_plan.role,
                expected_provenance=expected_provenance,
                timeout=min(timeout, 60),
            )
        if receipt is None:
            raise ValueError(
                f"phpExtensions {image_plan.role} child image lacks the expected "
                "Sandbox digest/role/provenance labels after build"
            )
        built[image_plan.role] = image_plan.image
    return built


def prepare_php_extension_runtime(inst_cfg: dict, server: str | None = None,
                                  *, timeout: float = 900) -> dict | None:
    """Resolve, materialize, and build an opted-in official child-image pair.

    The returned data is safe to persist in the instance block.  Omitted
    ``phpExtensions`` returns ``None`` without inspecting Docker.  Custom
    images/LiteSpeed are rejected before a pull/build; only the reviewed
    official Apache/nginx path reaches Docker.
    """
    requirements = inst_cfg.get("php_extensions", inst_cfg.get("phpExtensions"))
    if requirements is None:
        return None
    server = (server or inst_cfg.get("server") or "nginx").strip().lower()
    effective = _extension_plan_requirements(requirements)
    # Validate the immutable catalog and provisioning boundary before deriving
    # parent images or invoking even a read-only Docker inspect.  In
    # particular, v1's observation-only imagick/xdebug requests must fail
    # without a pull, materialized context, or build side effect.
    from sandbox.php_extensions.compose_builder import normalize_requirements
    normalize_requirements(effective)
    parent_image = _web_image(
        server, inst_cfg.get("php_version"), inst_cfg.get("wp_version"),
        inst_cfg.get("wordpress_image"),
    )
    wpcli_image = inst_cfg.get("wpcli_image") or _cli_image(inst_cfg.get("php_version"))
    existing = inst_cfg.get("php_extension_parent_digests")
    if not isinstance(existing, dict):
        existing = inst_cfg.get("phpExtensionParentDigests")
    existing_images = inst_cfg.get("php_extension_parent_images")
    if not isinstance(existing_images, dict):
        existing_images = {}
    web_provided = ((existing or {}).get("web")
                    if existing_images.get("web") == parent_image
                    else None)
    cli_provided = ((existing or {}).get("wpcli")
                    if existing_images.get("wpcli") == wpcli_image
                    else None)
    parent_digests = {
        "web": _resolve_php_extension_parent_digest(
            parent_image, role="web", server=server,
            provided=web_provided,
            timeout=min(timeout, 120),
        ),
        "wpcli": _resolve_php_extension_parent_digest(
            wpcli_image, role="wpcli", server=server,
            provided=cli_provided,
            timeout=min(timeout, 120),
        ),
    }
    preflight = php_extension_preflight(
        {**inst_cfg, "php_extensions": effective,
         "php_extension_parent_digests": parent_digests},
        server,
    )
    plan = _instance_extension_plan(
        {**inst_cfg, "php_extensions": effective,
         "php_extension_parent_digests": parent_digests},
        server, requirements=effective, parent_digests=parent_digests,
    )
    if plan is None:
        raise ValueError("phpExtensions child-image plan was unexpectedly empty")
    built = _build_php_extension_images(plan, timeout=timeout)
    return {
        "plan": plan,
        "preflight": preflight,
        "parent_digests": parent_digests,
        "built_images": built,
    }


def _persist_php_extension_runtime(block: dict, prepared: dict) -> None:
    """Attach only adapter-produced identity to an instance block."""
    plan = prepared["plan"]
    block["php_extension_parent_digests"] = dict(prepared["parent_digests"])
    block["php_extension_parent_images"] = {
        "web": plan.web.parent_image,
        "wpcli": plan.wpcli.parent_image,
    }
    block["php_extension_digest"] = plan.digest
    block["platform"] = plan.platform
    block["architecture"] = plan.architecture


def php_extension_preflight(inst_cfg: dict, server: str | None = None,
                            *, parent_digests: Mapping[str, str] | None = None) -> dict | None:
    """Validate extension intent and return a deterministic build/readiness plan.

    This is intentionally side-effect free.  It resolves the immutable catalog
    and, when the caller has supplied trusted parent digests, computes the
    child-image plan.  A missing digest is an actionable preflight block rather
    than an invented fingerprint or an implicit Docker build.
    """
    requirements = inst_cfg.get("php_extensions", inst_cfg.get("phpExtensions"))
    if requirements is None:
        return None
    from sandbox.php_extensions.service import PhpExtensionService

    effective = _extension_plan_requirements(requirements)
    service = PhpExtensionService()
    resolution = service.resolve(effective)
    # A profile-only declaration is satisfiable by the reviewed GD child-image
    # fallback above.  Any other catalog failure blocks before state/runtime
    # mutation and preserves its stable issue code in the message.
    if not resolution.ok:
        issues = [issue for issue in resolution.issues
                  if issue.code != "missing_capability"]
        if issues:
            issue = issues[0]
            raise ValueError(f"phpExtensions preflight blocked ({issue.code}): {issue.message}")
        if not (effective.get("profile") == "wordpress@1" and
                effective.get("extensions", {}).get("gd") is True):
            issue = resolution.issues[0] if resolution.issues else None
            raise ValueError(
                "phpExtensions preflight blocked (missing_capability): "
                f"{issue.message if issue else 'WordPress requires GD or Imagick'}"
            )
        # Re-resolve after the deterministic GD fallback so status/readiness
        # carries the same effective requirement set as the image planner.
        resolution = service.resolve(effective)
    plan_cfg = inst_cfg
    if parent_digests is not None:
        plan_cfg = {**inst_cfg, "php_extension_parent_digests": dict(parent_digests)}
    plan = _instance_extension_plan(plan_cfg, server or inst_cfg.get("server", "nginx"),
                                    requirements=effective,
                                    parent_digests=parent_digests)
    return {
        "resolution": resolution.to_dict(),
        "requirements": effective,
        "plan": plan.as_dict() if plan is not None else None,
        "readiness": "ready" if plan is not None else "validate_only",
    }


_PHP_EXTENSION_PLANES = ("web", "cli", "exec", "phpunit")
_PHP_EXTENSION_FAILURE_CODES = frozenset({
    "missing", "version_mismatch", "version_unobservable",
    "unsupported_provisioning", "unsupported_disable", "plane_drift",
})
_PHP_EXTENSION_ISSUE_MESSAGES = {
    "missing": "required PHP extension is missing",
    "version_mismatch": "PHP extension version does not match the requirement",
    "version_unobservable": "PHP extension version cannot be observed",
    "unsupported_provisioning": "PHP extension provisioning is unsupported",
    "unsupported_disable": "disabling this PHP extension is unsupported",
    "plane_drift": "PHP extension observations differ between execution planes",
}
_PHP_EXTENSION_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@|*-]{0,127}$")
_PHP_EXTENSION_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _php_probe_runner(instance: str, plane: str):
    """Return one adapter-owned runner for the standalone PHP payload.

    Every branch remains argv-only and uses the already generated Compose/test
    execution service.  In particular, none of these commands invokes ``wp``
    or mounts a project, so a status/doctor check cannot bootstrap WordPress or
    mutate its database/uploads.
    """
    from sandbox.php_extensions.probe import ProbeResult, ProbeError

    if plane not in _PHP_EXTENSION_PLANES:
        raise ValueError(f"unknown PHP extension probe plane: {plane}")

    class _Runner:
        def run(self, argv, *, timeout=None):
            try:
                if plane == "phpunit":
                    # The test helper owns the resolved PHPUnit image/native
                    # adapter and applies the same finite timeout contract.
                    from sandbox.core._tests import _run_php_extension_probe
                    return _run_php_extension_probe(
                        instance, tuple(argv), timeout=timeout or 5,
                    )
                if _is_herd_instance(instance):
                    # Herd has no web/wpcli containers.  Do not silently run a
                    # host PHP binary and label it as a container plane.
                    return _types.SimpleNamespace(
                        returncode=1, stdout="", stderr="PHP probe plane unavailable: no container",
                    )
                # ``argv[0]`` is the trusted PHP binary.  The cli/exec one-shot
                # services set ``--entrypoint php``, so only the remaining
                # arguments are passed to Compose.
                if plane == "web":
                    command = ("exec", "-T", "wp", *tuple(argv))
                elif plane == "cli":
                    command = ("run", "--rm", "--no-deps", "--entrypoint", "php",
                               "wpcli", *tuple(argv)[1:])
                else:  # bounded exec plane: one-shot web service, no WP code.
                    command = ("run", "--rm", "--no-deps", "--entrypoint", "php",
                               "wp", *tuple(argv)[1:])
                return compose(*command, instance=instance, check=False,
                               capture=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return _types.SimpleNamespace(
                    returncode=124, stdout="", stderr="process timed out",
                )
            except (OSError, RuntimeError, ValueError) as exc:
                # ``run_probe`` turns a non-zero bounded result into a
                # structured failure; preserve only a short safe reason.
                return _types.SimpleNamespace(
                    returncode=1, stdout="", stderr=str(exc)[:4096],
                )

    return _Runner()


def php_extension_probe(instance: str, requirements: object, *,
                        timeout: float = 5):
    """Probe all available PHP execution planes for one running instance.

    The return value is ``{plane: ProbeResult}``, ready for
    :meth:`PhpExtensionService.verify`.  A missing container is represented as
    a failed/unavailable probe, never as a synthetic successful observation.
    """
    from sandbox.php_extensions.probe import (
        ProbeError, ProbeResult, probe_all_planes,
    )

    if not isinstance(instance, str) or not instance:
        raise ValueError("PHP extension probe instance is invalid")
    if _is_herd_instance(instance):
        return {
            plane: ProbeResult(
                False, plane,
                errors=(ProbeError(
                    "probe_unavailable",
                    "PHP extension probe plane unavailable for host-served instance",
                    plane=plane,
                ),),
            )
            for plane in _PHP_EXTENSION_PLANES
        }
    return probe_all_planes(
        {plane: _php_probe_runner(instance, plane) for plane in _PHP_EXTENSION_PLANES},
        requirements, timeout=timeout,
    )


def _canonical_php_extension_issue(issue: object) -> dict:
    """Reduce a resolver/probe issue to the public stable failure vocabulary."""
    raw = issue.to_dict() if hasattr(issue, "to_dict") else issue
    raw = raw if isinstance(raw, Mapping) else {}
    code = str(raw.get("code", "plane_drift"))
    aliases = {
        "missing_capability": "missing",
        "profile_required_missing": "missing",
        "profile_required_disabled": "missing",
    }
    code = aliases.get(code, code)
    if code not in _PHP_EXTENSION_FAILURE_CODES:
        code = "plane_drift"
    result = {"code": code, "message": _PHP_EXTENSION_ISSUE_MESSAGES[code]}
    for key in ("plane", "extension", "expected", "observed"):
        value = raw.get(key)
        if value is not None and _PHP_EXTENSION_SAFE_VALUE.fullmatch(str(value)):
            result[key] = str(value)
    return result


def _canonical_php_extension_issues(issues: object) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for issue in issues or ():
        row = _canonical_php_extension_issue(issue)
        identity = tuple(sorted((key, str(value)) for key, value in row.items()
                                if key != "message"))
        if identity not in seen:
            seen.add(identity)
            rows.append(row)
    return rows


def _php_extension_build_receipt(inst_cfg: Mapping[str, object]) -> tuple[str | None, dict]:
    """Return only complete, allow-listed read-only build provenance."""
    digest = inst_cfg.get("php_extension_digest", inst_cfg.get("phpExtensionDigest"))
    if not isinstance(digest, str) or not _PHP_EXTENSION_DIGEST.fullmatch(digest.lower()):
        return None, {"state": "unavailable"}
    from sandbox.php_extensions.compose_builder import RECIPES, extension_cache_status

    receipt = extension_cache_status(digest.lower())
    receipt_state = receipt.get("state") if isinstance(receipt, Mapping) else None
    provenance = receipt.get("provenance") if isinstance(receipt, Mapping) else None
    if receipt_state != "ready" or not isinstance(provenance, Mapping):
        # Preserve the cache classifier for status consumers.  A missing or
        # explicitly discarded entry is materially different from a tampered
        # receipt, while malformed/legacy data remains the conservative stale
        # state used by older callers.  The values come from the pure cache
        # status helper and contain no paths or receipt contents.
        state = receipt_state
        if state not in {"missing", "discarded"}:
            state = "stale"
        return None, {"state": state}
    parent_digests = provenance.get("parent_digests")
    recipe_digest = provenance.get("recipe_catalog_digest")
    recipe_ids = provenance.get("recipe_ids")
    trusted_recipe_ids = {recipe.recipe_id for recipe in RECIPES.values()}
    complete = (
        provenance.get("digest") == digest.lower()
        and isinstance(recipe_digest, str)
        and _PHP_EXTENSION_DIGEST.fullmatch(recipe_digest.lower())
        and isinstance(parent_digests, Mapping)
        and set(parent_digests) == {"web", "wpcli"}
        and all(isinstance(value, str) and _PHP_EXTENSION_DIGEST.fullmatch(value.lower())
                for value in parent_digests.values())
        and isinstance(recipe_ids, list)
        and all(isinstance(value, str) and value in trusted_recipe_ids
                for value in recipe_ids)
    )
    if not complete:
        return None, {"state": "stale"}
    return digest.lower(), {
        "state": "ready",
        "recipe_catalog_digest": recipe_digest.lower(),
        "parent_digests": {role: str(parent_digests[role]).lower()
                           for role in ("web", "wpcli")},
        "recipe_ids": list(recipe_ids),
    }


def _php_observed_row(result) -> dict:
    """Reduce one ProbeResult to a secret-free status row."""
    observation = result.observation
    if observation is None:
        stderr = (getattr(result, "stderr", "") or "").lower()
        unavailable_markers = (
            "not running", "no such service", "no such container",
            "cannot connect", "connection refused", "container unavailable",
        )
        code = result.errors[0].code if result.errors else "probe_failed"
        state = ("unavailable" if code == "probe_unavailable"
                 or any(marker in stderr for marker in unavailable_markers)
                 else "error")
        return {"state": state, "issues": _canonical_php_extension_issues(result.errors)}
    rows = {}
    safe_dimensions = True
    for item in observation.extensions:
        if not _PHP_EXTENSION_SAFE_VALUE.fullmatch(item.name):
            safe_dimensions = False
            continue
        version = (item.version if item.version is None or
                   _PHP_EXTENSION_SAFE_VALUE.fullmatch(item.version) else None)
        if version is None and item.version is not None:
            safe_dimensions = False
        rows[item.name] = {"enabled": item.enabled, "version": version}
    php_version = (observation.php_version
                   if observation.php_version and
                   _PHP_EXTENSION_SAFE_VALUE.fullmatch(observation.php_version)
                   else None)
    sapi = (observation.sapi if observation.sapi and
            _PHP_EXTENSION_SAFE_VALUE.fullmatch(observation.sapi) else None)
    safe_dimensions = bool(safe_dimensions and php_version and sapi)
    result_row = {
        "state": "ready" if result.ok and safe_dimensions else "drift",
        "php_version": php_version,
        "sapi": sapi,
        "extensions": rows,
    }
    row_issues = list(result.errors)
    if not safe_dimensions:
        row_issues.append({"code": "plane_drift", "plane": result.plane})
    if row_issues:
        result_row["issues"] = _canonical_php_extension_issues(row_issues)
    return result_row


def php_extension_status(inst_cfg: dict, *, instance: str | None = None,
                         timeout: float = 5) -> dict | None:
    """Construct the single canonical, secret-free PHP-extension report."""
    requirements = inst_cfg.get("php_extensions", inst_cfg.get("phpExtensions"))
    if requirements is None:
        return None
    from sandbox.php_extensions.catalog import DEFAULT_CATALOG
    from sandbox.php_extensions.service import PhpExtensionService

    effective = _extension_plan_requirements(requirements)
    service = PhpExtensionService()
    resolution = service.resolve(effective)
    build_digest, provenance = _php_extension_build_receipt(inst_cfg)
    desired = {
        "profile": (resolution.profile if isinstance(resolution.profile, str) and
                    _PHP_EXTENSION_SAFE_VALUE.fullmatch(resolution.profile) else None),
        "catalog": {"revision": DEFAULT_CATALOG.schema_version,
                    "digest": resolution.catalog},
        "requirements": [dict(item) for item in resolution.requirements],
        "resolution_digest": resolution.digest,
    }
    if build_digest is not None:
        desired["build_digest"] = build_digest

    probes = None
    verification = None
    if instance is not None and resolution.ok:
        probes = php_extension_probe(instance, effective, timeout=timeout)
        verification = service.verify(effective, probes)
    observed = {
        plane: (_php_observed_row(probes[plane]) if probes is not None
                else {"state": "unavailable", "issues": [{
                    "code": "plane_drift",
                    "message": _PHP_EXTENSION_ISSUE_MESSAGES["plane_drift"],
                    "plane": plane,
                }]})
        for plane in _PHP_EXTENSION_PLANES
    }
    fresh = bool(probes) and all(result.observation is not None
                                 for result in probes.values())
    raw_issues = list(resolution.issues)
    if verification is not None:
        raw_issues.extend(verification.errors)
        # Observation-only recipes are valid assertions. They become an
        # unsupported-provisioning failure only when the fresh probe proves
        # the requested extension is absent.
        missing = {error.extension for error in verification.errors
                   if error.code == "missing" and error.extension}
        for name in sorted(missing):
            try:
                recipe = DEFAULT_CATALOG.recipe(name)
            except Exception:
                continue
            if not recipe.provisionable:
                raw_issues.append({"code": "unsupported_provisioning",
                                   "extension": name})
    elif not resolution.issues:
        raw_issues.extend({"code": "plane_drift", "plane": plane}
                          for plane in _PHP_EXTENSION_PLANES)
    for row in observed.values():
        raw_issues.extend(row.get("issues", ()))
    issues = _canonical_php_extension_issues(raw_issues)
    unavailable = any(row["state"] in {"unavailable", "error"}
                      for row in observed.values())
    report_ok = (resolution.ok and verification is not None and verification.ok
                 and all(row["state"] == "ready" for row in observed.values()))
    readiness = ("ready" if report_ok else
                 "blocked" if not resolution.ok else
                 "unavailable" if unavailable else "blocked")
    drift_state = "ready" if report_ok else ("unknown" if unavailable else "drift")
    report = {
        "ok": report_ok,
        "exit_code": 0 if report_ok else 1,
        "desired": desired,
        "provenance": provenance,
        "observed": observed,
        "readiness": {"state": readiness},
        "staleness": {
            "state": "fresh" if fresh else "stale",
            "reason": ("all_four_planes_observed" if fresh
                       else "one_or_more_planes_unavailable"),
        },
        "drift": {"state": drift_state},
        "issues": issues,
    }
    from sandbox.services.redaction import redact_structure

    public = redact_structure(report)
    if isinstance(public, Mapping):
        return dict(public)
    return {
        "ok": False,
        "exit_code": 1,
        "desired": {},
        "provenance": {"state": "unavailable"},
        "observed": {plane: {"state": "unavailable"}
                     for plane in _PHP_EXTENSION_PLANES},
        "readiness": {"state": "unavailable"},
        "staleness": {"state": "stale", "reason": "redaction_failed"},
        "drift": {"state": "unknown"},
        "issues": [{"code": "plane_drift",
                    "message": _PHP_EXTENSION_ISSUE_MESSAGES["plane_drift"]}],
    }


def _instance_web_image(server: str, inst_cfg: dict) -> str:
    parent = _web_image(
        server, inst_cfg.get("php_version"), inst_cfg.get("wp_version"),
        inst_cfg.get("wordpress_image"),
    )
    plan = _instance_extension_plan(inst_cfg, server)
    return plan.web.image if plan else parent


def _instance_wpcli_image(inst_cfg: dict) -> str:
    parent = inst_cfg["wpcli_image"]
    plan = _instance_extension_plan(inst_cfg, inst_cfg.get("server", "nginx"))
    return plan.wpcli.image if plan else parent


def _cli_image(php=None) -> str:
    """The wp-cli helper image. Matches the pinned PHP so composer + phpunit
    (which run in the wpcli container) execute under the project's PHP."""
    return f"wordpress:cli-php{php}" if php else "wordpress:cli"


def _compose_no_follow_logs(instance: str, tail: int = 200) -> None:
    """Print the last `tail` lines of wp+db logs WITHOUT -f (cmd_logs follows
    forever, which would hang a web job). Prints to current stdout (captured
    by the job stream)."""
    compose("logs", "--no-color", f"--tail={tail}", "wp", "db",
            instance=instance, check=False)
