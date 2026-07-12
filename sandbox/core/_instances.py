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
                                             runtime.get("server", "nginx"))),
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


def wp_dir(instance: str) -> Path:
    """Per-instance WordPress install dir."""
    return RUNTIME_DIR / f"wp-{instance}"


def plugins_dir(instance: str) -> Path:
    return wp_dir(instance) / "wp-content" / "plugins"


def focus_file(instance: str) -> Path:
    return ROOT / f".focus.{instance}"


def snapshots_dir(instance: str) -> Path:
    return RUNTIME_DIR / "snapshots" / instance


def project_name(instance: str) -> str:
    """docker-compose project name — must be unique per instance."""
    return f"sandbox-{instance}"


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


def _core():
    """Lazy import of the shared sandbox_core module (CLI + MCP share it).
    ROOT is the resolved sandbox dir, so this works even via the global symlink."""
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import sandbox_core
    return sandbox_core


def _cwd_instance(label: str | None = None) -> str | None:
    """Resolve the instance owning the current working directory's project via
    the on-disk registry. label=None resolves the sole/default instance for
    that root (back-compat); a specific label resolves that exact instance.
    Returns the instance name, or None when cwd isn't a registered project, OR
    when the root has multiple instances and no label/default disambiguates it
    — the caller then errors with the ambiguity (there is no fallback instance).

    This is what lets `sb <cmd>` (no --instance) target the project you're
    standing in — mirroring how the MCP tools route by project_dir."""
    sc = _core()
    try:
        root = sc.find_project_root(Path.cwd())
    except Exception:
        return None
    entry = sc.registry_get(str(root), label=label)
    return entry.get("instance") if entry else None


def _derive_instance_name(root: str, taken: set, label: str = "default") -> str:
    """A valid, unique instance name from a project dir basename. The default
    label reuses today's plain basename. A non-default label is APPENDED
    AFTER truncating the basename (reserving room for the `-<label>` suffix),
    not folded in before truncation — otherwise a basename that already fills
    the 24-char budget (common with long plugin-repo names) eats the whole
    suffix, two labels of the same root collide on the same truncated name,
    and the label silently disappears into an anonymous `-2` (multi-instance-
    per-root)."""
    base_norm = re.sub(r"[^a-z0-9]+", "-", Path(root).name.lower()).strip("-") or "proj"
    if label == "default":
        seed = base_norm[:24]
    else:
        suffix = f"-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}"
        seed = base_norm[:max(24 - len(suffix), 1)] + suffix
    # Truncate to 24 first, THEN strip dashes, so a cut that lands on a hyphen
    # (e.g. "templately-nav-menu-url-replace"[:24] → "templately-nav-menu-url-")
    # doesn't leave an invalid trailing hyphen in the instance name / domain.
    base = seed[:24].strip("-") or "proj"
    if not re.match(r"^[a-z0-9]", base):
        base = "p-" + base
    name, i = base, 2
    while name in taken:
        name, i = f"{base}-{i}", i + 1
    return name


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
    # Spec 010: canonical plugin map — every LOCAL-path source (active, inactive,
    # or on-demand) needs a bind-mount so the symlink resolves / the on-demand
    # mu-plugin can read+zip it inside the container.
    for _e in (pconf.get("plugins_resolved") or {}).values():
        _si = _e.get("source") or {}
        if _si.get("kind") != "path" or not _si.get("value"):
            continue
        _src = Path(str(_si["value"])).expanduser()
        if not _src.is_absolute():
            _src = (root_p / _src).resolve()
        _src = _src.resolve()
        if _src.exists() and not _src.is_relative_to(plugins_home_p):
            _extra.append(str(_src))
    extra_mounts = list(dict.fromkeys(_extra))  # deduplicate, preserve order
    if extra_mounts:
        block["extra_mounts"] = extra_mounts

    # Preserve secrets minted at install time. They live in the instance block
    # (written by save_local_bridge_token / save_local_app_password /
    # save_local_autologin_token), but this function rebuilds the block from
    # config alone — so without carrying them over, every ensure/apply/onboard
    # rewrite would drop them, breaking the wp-admin snapshot bridge
    # (bridge_token), MCP REST auth (app_password), and the autologin link.
    _prev = _local_yaml().get("instances", {}).get(name, {})
    for _secret in ("bridge_token", "app_password", "autologin_token"):
        if _prev.get(_secret) and not block.get(_secret):
            block[_secret] = _prev[_secret]
    return block


def ensure_instance(cfg: dict, project_dir: str, label: str = "default",
                    create: bool = False, php_version: str | None = None,
                    wp_version: str | None = None,
                    config_label: str | None = None) -> dict:
    from sandbox.commands.lifecycle import cmd_up, cmd_install
    """Create-if-missing: boot a per-directory instance for the project at
    `project_dir`, keyed by its canonical root + `label` in the registry.
    Idempotent — a second call for a ready (root, label) returns the existing
    record.

    `label` distinguishes multiple simultaneous instances of the SAME project
    root (multi-instance-per-root) — e.g. a `qa` label alongside `default`.
    Minting a NEW non-default label requires `create=True` (a mistyped label
    would otherwise silently build a whole extra stack); the `default` label
    always creates on first call, matching pre-multi-instance behavior.

    `php_version`/`wp_version`: when given, OVERRIDE the project's own
    sandbox.config.json `phpVersion`/`wpVersion` for this specific labelled
    instance — this is how a CI matrix cell's requested version (e.g.
    `strategy.matrix.php` or a `shivammathur/setup-php` step's
    `with.php-version`) takes priority over the project's default config
    (docs/ci-e2e-runner-spec.md §3.5/§3.6 "CI takes priority over
    sandbox.config"). Omit for the normal case — the project's own config
    applies unchanged.

    `config_label`: the key used to look up an optional
    `sandbox.config.<config_label>.json` override layer (docs/multi-instance-
    spec.md — per-label config). Defaults to `label` itself when omitted,
    which is right for durable labels (`qa`, `php81` ARE their own stable
    config key). The CI runner passes a SEPARATE, run-independent value here
    (a matrix-cell slug like `wp68-php84`) because its actual `label` is
    randomized per run (`ci-<runid>-...`, for concurrency-safe isolation) and
    a user could never pre-author a config file matching a random label."""
    import types
    sc = _core()
    pconf = sc.load_project_config(project_dir,
                                   label=config_label if config_label is not None else label)
    if php_version:
        pconf = {**pconf, "phpVersion": php_version}
    if wp_version:
        pconf = {**pconf, "wpVersion": wp_version}
    root = pconf["root"]

    with sc.project_lock(root):
        existing = sc.registry_get(root, label=label)
        # A remote or local host may retain a listener that is not represented
        # in the registry (for example a stale Mailpit container). Reconcile
        # ports before the ready fast path; otherwise ensure can report a
        # healthy HTTP container while the next compose up partially fails and
        # leaves WP's database network unusable.
        cfg = _resolve_port_conflicts(cfg)
        resolved_existing = (
            resolve_instances(cfg).get(existing.get("instance"))
            if existing and existing.get("instance") else None
        )
        ports_changed = bool(
            existing and resolved_existing and any(
                resolved_existing.get(key) != existing.get(key)
                for key in ("wordpress_port", "db_port", "mailpit_port")
            )
        )
        if existing and existing.get("status") == "ready" \
                and not ports_changed and _instance_reachable(existing):
            # Already up. If the config's version pins no longer match the
            # running instance's image, say so loudly — silently returning the
            # stale record would let tests run against a different WP/PHP than
            # the live site. Re-versioning in place is a tracked follow-up; for
            # now the instance must be recreated to apply a changed pin.
            _warn_version_drift(cfg, existing.get("instance"), pconf)
            return existing

        if not existing and label != "default" and not create:
            known = [e["label"] for e in sc.registry_list_for_root(root)]
            raise sc.ConfigError(
                f"no instance labelled '{label}' for {root} "
                f"(existing labels: {known or 'none'}). Pass create=True / "
                f"`--label {label}` deliberately to mint a new one.")

        # Resume a prior record for this (root, label) (a partial/failed boot,
        # or a stopped instance) by REUSING its name + ports rather than
        # deriving a fresh `<name>-2` and orphaning the half-built stack. Only
        # when there's no record at all do we allocate a new name + ports.
        if existing and existing.get("instance"):
            name = existing["instance"]
            resolved = resolve_instances(cfg).get(name) or existing
            ports = {
                "wordpress_port": resolved["wordpress_port"],
                "db_port": resolved["db_port"],
                "mailpit_port": resolved["mailpit_port"],
            }
        else:
            taken = set(resolve_instances(cfg).keys())
            taken |= {e.get("instance") for e in sc.registry_all().values()
                      if e.get("instance")}
            name = _derive_instance_name(root, taken, label=label)
            ports = _pick_instance_ports(cfg)

        server = _valid_server(pconf.get("server") or "nginx")
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
        sc.registry_put(root, label=label, instance=name, status="pending",
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
            label=label,
            instance=name,
            url=_base_url,
            login_url=_login_url,
            admin_url=f"{_base_url}/wp-admin/",
            wordpress_port=ports["wordpress_port"],
            db_port=ports["db_port"],
            mailpit_port=ports["mailpit_port"],
            server=server,
            php_version=pconf.get("phpVersion"),
            wp_version=pconf.get("wpVersion"),
            source=pconf.get("source"),
            status="ready",
        )


def apply_config(cfg: dict, project_dir: str, label: str | None = None) -> dict:
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
        existing = sc.registry_get(root, label=label)
        if not (existing and existing.get("instance")):
            if label is None and len(sc.registry_list_for_root(root)) > 1:
                known = [e["label"] for e in sc.registry_list_for_root(root)]
                raise sc.ConfigError(
                    f"'{root}' has multiple instances ({', '.join(known)}); "
                    f"pass label= to disambiguate.")
            raise sc.ConfigError(
                f"no instance for {root} yet — run ensure_instance first.")
        name = existing["instance"]
        label = existing["label"]
        # Re-load with the RESOLVED label now known, so a per-label
        # sandbox.config.<label>.json layer (if present) applies correctly —
        # the first load above (label-less) only existed to find `root`.
        pconf = sc.load_project_config(project_dir, label=label)
        ports = {
            "wordpress_port": existing["wordpress_port"],
            "db_port": existing["db_port"],
            "mailpit_port": existing["mailpit_port"],
        }
        server = _valid_server(pconf.get("server") or existing.get("server")
                               or "nginx")

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
            if wp_dir(name).exists():
                _remove_obsolete_builder_authoring_assets(name)
        else:
            compose("up", "-d", "--force-recreate",
                    *_web_services(inst_cfg.get("server", "nginx")),
                    instance=name, check=False)
            _wait_http(ports["wordpress_port"])
            # Re-assert the SSL + mail mu-plugins (recreate may have reset
            # nothing, but keep them guaranteed-present like cmd_up does).
            if wp_dir(name).exists():
                _write_mail_muplugin(name)
                _write_dl_cache_muplugin(name)
                _write_ondemand_muplugin(name)   # spec 010 — on-demand local plugin sourcing
                _write_abilities_muplugin(name)  # spec 003 — in-instance WP Abilities
                _write_licensing_muplugin(name)  # spec 013 — cross-instance Pro license activation
                _remove_obsolete_builder_authoring_assets(name)

        # 3. Re-sync plugins + themes (idempotent symlinks + installs).
        _wire_project_plugins(name, root, pconf)
        _wire_project_themes(name, root, pconf)

        # spec 008: capture the post-provision @install state ONCE (no-op if it
        # already exists; a destroy wipes snapshots, so recreate naturally refreshes
        # both). The db-only `__install__` baseline powers `./sb reset`; the full
        # `install-baseline` named snapshot (DB + uploads) allows a complete rollback.
        try:
            from sandbox.commands.data import (capture_install_baseline,
                                               capture_install_full_snapshot)
            capture_install_baseline(name)
            capture_install_full_snapshot(name)
        except Exception:
            pass

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
            label=label,
            instance=name,
            url=base_url,
            status="ready",
            server=server,
            php_version=pconf.get("phpVersion"),
            wp_version=pconf.get("wpVersion"),
            source=pconf.get("source"),
        )


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
