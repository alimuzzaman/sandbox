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
      # Shared plugin/theme download cache: the dl-cache mu-plugin serves &
      # populates zips here so WP-runtime installs (Templately FSI especially)
      # reuse a cached zip instead of re-downloading. Shared across instances.
      - ./runtime/dl-cache/wp-http:/sandbox-dl-cache
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
      - ./runtime/dl-cache/wp-http:/sandbox-dl-cache
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
      - ./runtime/dl-cache/wp-http:/sandbox-dl-cache
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
      # Persistent, shared wp-cli download cache (WP_CLI_CACHE_DIR points here):
      # `wp plugin/theme/core install` reuse downloads across instances + runs
      # instead of re-fetching into ephemeral /tmp every time.
      - ./runtime/dl-cache/wp-cli:/tmp/.wp-cli/cache
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
