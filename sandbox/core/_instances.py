from __future__ import annotations
import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr


def _json_safe_php_extensions(value):
    """Detach a normalized extension model at the state-file boundary.

    ``PhpExtensionsConfig`` intentionally exposes immutable mapping proxies and
    typed requirement objects.  The instance state is YAML/JSON persisted and
    must contain only ordinary scalar/list/dict values.  Keep this helper
    local to the instance boundary so the config model remains immutable and
    the omitted-field path does not gain a synthetic key.
    """
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    elif hasattr(value, "as_dict") and callable(value.as_dict):
        value = value.as_dict()
    if isinstance(value, Mapping):
        return {str(key): _json_safe_php_extensions(item)
                for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe_php_extensions(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError("phpExtensions contains a non-serializable value")


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
        resolved = {
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
        # ``phpExtensions`` is an additive WordPress-only capability.  Keep
        # the legacy resolved shape byte-compatible when it is omitted; when
        # present, carry only the detached JSON/YAML-safe mapping persisted by
        # ``_build_instance_block`` (never the immutable config model itself).
        extension_requirements = inst.get(
            "php_extensions", runtime.get("php_extensions"))
        if extension_requirements is None:
            extension_requirements = inst.get(
                "phpExtensions", runtime.get("phpExtensions"))
        if extension_requirements is not None:
            resolved["php_extensions"] = _json_safe_php_extensions(
                extension_requirements)
        # Trusted, adapter-produced extension plan inputs are optional state;
        # preserve them only when present so the omission path remains exactly
        # the historical resolved mapping.
        for extension_key in (
                "php_extension_parent_digest", "php_extension_parent_digests",
                "php_extension_parent_images", "php_extension_digest", "wpcli_image_digest", "platform",
                "architecture"):
            value = inst.get(extension_key, runtime.get(extension_key))
            if value is not None:
                resolved[extension_key] = _json_safe_php_extensions(value)
        return resolved

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
               if e.get("instance") and e.get("kind") != "compose"}
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
    entry = resolve_registered_instance(str(root), label=label)
    return entry.get("instance") if entry else None


def resolve_registered_instance(project_dir: str | Path, label: str | None = None) -> dict | None:
    """Resolve one registry record from a project directory, with guidance.

    This is the kind-neutral lookup seam used by CLI/MCP composition roots.  It
    canonicalizes a nested caller path through the same project-root finder as
    configuration, then selects an exact label.  A root with more than one
    record is never guessed when no default identity is available; the error
    names every label and gives the concrete ``--label``/``label=`` remedy.
    """
    sc = _core()
    try:
        root = sc.find_project_root(project_dir)
    except Exception:
        root = Path(project_dir).expanduser().resolve()
    records = list(sc.registry_list_for_root(str(root)))
    if label is not None:
        return next((record for record in records if record.get("label") == label), None)
    if not records:
        return None
    if len(records) == 1:
        return records[0]
    default = next((record for record in records if record.get("is_default")), None)
    if default is not None:
        return default
    labels = ", ".join(str(record.get("label")) for record in records)
    raise sc.ConfigError(
        f"project {root} has multiple registered instances ({labels}); "
        "pass an exact --label (or label=) to select one"
    )


def _git_branch(root: str) -> str | None:
    """Current branch of `root`, or None when it isn't a git checkout, HEAD is
    detached, or git isn't installed. Used only to flavour derived names, so
    every failure mode degrades to the plain basename."""
    # symbolic-ref, not `rev-parse --abbrev-ref`: it resolves the branch on a
    # fresh checkout with no commits yet (rev-parse fails there, and a just-
    # cloned/inited project is exactly when the first instance is minted), and
    # it exits non-zero on detached HEAD instead of printing literal "HEAD".
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if res.returncode != 0:
        return None
    return (res.stdout or "").strip() or None


def _git_repo_basename(root: str) -> str | None:
    """Canonical repository basename, including from a linked worktree.

    Generated worktree directories (for example ``t3code-360e3021``) are
    transport details, not project identity. The common Git directory remains
    under the primary checkout, so its parent supplies the stable repo name.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if res.returncode != 0:
        return None
    common = Path((res.stdout or "").strip())
    if not common.name:
        return None
    return common.parent.name if common.name == ".git" else common.name


def _meaningful_branch(root: str, branch: str | None) -> str | None:
    """Drop a generated-worktree namespace from its matching branch name."""
    if not branch:
        return None
    worktree = Path(root).name.lower()
    match = re.fullmatch(r"([a-z0-9]+)-[0-9a-f]{6,}", worktree)
    if match and branch.lower().startswith(f"{match.group(1)}/"):
        return branch.split("/", 1)[1]
    return branch


def _fit_stem(base: str, branch: str, budget: int) -> str:
    """`<basename>-<branch>` squeezed into `budget` chars without letting either
    half disappear. The branch is capped at half the budget so a long branch
    can't swallow the repo identity, and the basename side is re-truncated to
    whatever remains (dash-stripped, so a cut landing on a hyphen doesn't leave
    a double dash)."""
    if not branch or branch == base:
        return base[:budget]
    branch = branch[:max(budget // 2, 1)].strip("-")
    keep = max(budget - len(branch) - 1, 3)
    return f"{base[:keep].strip('-')}-{branch}"[:budget]


def _derive_instance_name(root: str, taken: set, label: str = "default") -> str:
    """A valid, unique instance name from a project dir basename plus its git
    branch (`<basename>-<branch>`), so two worktrees/branches of one repo read
    apart at a glance. Non-git roots, detached HEAD, or a branch equal to the
    basename fall back to the plain basename. The default label reuses that
    stem. A non-default label is APPENDED AFTER truncating the stem (reserving
    room for the `-<label>` suffix), not folded in before truncation —
    otherwise a basename that already fills the 24-char budget (common with
    long plugin-repo names) eats the whole suffix, two labels of the same root
    collide on the same truncated name, and the label silently disappears into
    an anonymous `-2` (multi-instance-per-root).

    Existing instances keep their recorded name: ensure_instance reuses the
    registry record for a (root, label) and only calls this for a brand-new
    one, so switching branches in place never renames a live stack."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    base_norm = norm(_git_repo_basename(root) or Path(root).name) or "proj"
    branch_norm = norm(_meaningful_branch(root, _git_branch(root)))
    if label == "default":
        seed = _fit_stem(base_norm, branch_norm, 24)
    else:
        suffix = f"-{norm(label)}"
        seed = _fit_stem(base_norm, branch_norm, max(24 - len(suffix), 1)) + suffix
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
    root = pconf.get("root") or "."
    info(f"⚠ '{instance_name}' is running php={cur[0] or 'latest'}/wp={cur[1] or 'latest'} "
         f"but config now pins php={want[0] or 'latest'}/wp={want[1] or 'latest'}. "
         f"Reconcile in place: ./sb apply --project-dir {root} "
         f"(php = web tier recreate, wp = core update, no data loss).")


def _live_wp_core_version(instance: str) -> str | None:
    """The WordPress version the instance is ACTUALLY running, or None when no
    core is installed yet / wp-cli can't answer."""
    res = wpcli(["core", "version"], instance=instance,
                check=False, capture=True)
    if getattr(res, "returncode", 1) not in (0, None):
        return None
    lines = [ln.strip() for ln in (getattr(res, "stdout", "") or "").splitlines()
             if ln.strip()]
    if not lines:
        return None
    v = lines[-1]
    return v if re.match(r"^\d+(\.\d+)*(-\S+)?$", v) else None


def _reconcile_wp_core(instance: str, inst_cfg: dict, pconf: dict) -> dict:
    """Bring the RUNNING WordPress core in line with the project config.

    The image is PHP-only and core is downloaded into the bind mount at install
    time (see cmd_install), so nothing about a container recreate re-versions
    WordPress — an instance keeps whatever core it was installed with forever.
    That is how an instance sits on an old patch release long after its config
    changed: a pin edit only affected NEW instances, and dropping a pin affected
    nothing at all. Apply owns that reconcile:

      * pinned `wpVersion` and live core differs → `wp core update --version=<pin>
        --force` (works in both directions, so a downgrade for a version-specific
        repro works too);
      * no pin → "track the current release": `wp core update`, a no-op when the
        site is already current.

    Then `wp core update-db` (network-wide on multisite), because a core swap
    under a live DB leaves the schema at the old version otherwise.

    Non-fatal by construction: a wp.org hiccup or an update failure warns and
    leaves the site on its current core rather than failing the whole apply.
    """
    want = pconf.get("wpVersion")
    want = str(want) if want not in (None, "") else None
    live = _live_wp_core_version(instance)
    if live is None:
        info("apply: no WordPress core to reconcile yet (skipping core update)")
        return {"changed": False, "reason": "not-installed"}
    if want and live == want:
        return {"changed": False, "from": live, "to": live}
    if want:
        info(f"apply: WordPress core {live} → {want} (pinned)…")
        args = ["core", "update", f"--version={want}", "--force"]
    else:
        args = ["core", "update"]
    res = wpcli(args, instance=instance, check=False, capture=True)
    if getattr(res, "returncode", 1) not in (0, None):
        detail = ((getattr(res, "stderr", "") or getattr(res, "stdout", "")
                   or "").strip().splitlines() or [""])[-1]
        info(f"⚠ WordPress core reconcile failed ({detail[:200]}); "
             f"site stays on {live}")
        return {"changed": False, "from": live, "to": live,
                "error": detail[:200]}
    now = _live_wp_core_version(instance) or want or live
    if now == live:
        return {"changed": False, "from": live, "to": now}
    network = ["--network"] if _multisite_mode(inst_cfg) else []
    wpcli(["core", "update-db", *network], instance=instance,
          check=False, capture=True)
    ok(f"WordPress core {live} → {now}")
    return {"changed": True, "from": live, "to": now}


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
    """Wait for the instance's canonical URL without following redirects.

    ``site_url`` selects the real browser URL (including a secured proxy host)
    rather than merely checking the published port.  A response in the
    2xx--4xx range proves that the web tier answered; 5xx responses and
    transport failures keep retrying until the bounded timeout expires.  The
    redirect handler is deliberately disabled so a redirect to a stale or
    unrelated host cannot make the health gate report the wrong service as
    reachable.  Local mkcert certificates are not necessarily in Python's
    trust store, so HTTPS verification is disabled for this loopback probe.
    """
    import ssl
    import time
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    url = site_url(inst_cfg)
    ctx = ssl._create_unverified_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect,
        urllib.request.HTTPSHandler(context=ctx),
    )
    for attempt in range(timeout):
        try:
            response = opener.open(url, timeout=2)
            try:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if 200 <= status < 500:
                    return True
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
        except urllib.error.HTTPError as e:
            status = e.code
            close = getattr(e, "close", None)
            if close is not None:
                close()
            if 200 <= status < 500:
                return True
        except Exception:
            pass
        if attempt + 1 < timeout:
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
    # Persist the normalized, immutable project declaration as a detached
    # ordinary mapping.  The explicit presence check is important: omission
    # preserves the historical instance block and therefore its compose
    # output, cache identity, and readiness behavior exactly.
    if "phpExtensions" in pconf and pconf.get("phpExtensions") is not None:
        block["php_extensions"] = _json_safe_php_extensions(
            pconf.get("phpExtensions"))
    # Re-use only the previous adapter-produced identities.  These are not
    # project inputs and are never invented here; retaining them lets an
    # explicitly materialized child-image plan survive a later apply while a
    # changed requirement naturally invalidates it via the planner digest.
    _previous_block = _local_yaml().get("instances", {}).get(name, {})
    for _extension_key in (
            "php_extension_parent_digest", "php_extension_parent_digests",
            "php_extension_parent_images", "php_extension_digest", "wpcli_image_digest", "platform",
            "architecture"):
        if (_previous_block.get(_extension_key) is not None and
                _extension_key not in block):
            block[_extension_key] = _json_safe_php_extensions(
                _previous_block[_extension_key])
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

    # Extra hostnames this instance answers on. Declared in the project's
    # sandbox.config.json, so they follow the project to a remote and are
    # re-rendered into the Caddyfile, the cert SANs, and WORDPRESS_CONFIG_EXTRA
    # on every apply. Validated strictly here — a typo should fail the apply
    # that introduced it, not degrade quietly into an unroutable hostname.
    # An explicit empty list is a removal; omission preserves what is there.
    if "aliases" in pconf and pconf.get("aliases") is not None:
        _aliases = normalize_aliases(pconf.get("aliases"),
                                     primary=block.get("domain"), strict=True)
        if _aliases:
            block["aliases"] = _aliases
    else:
        _prev_aliases = _local_yaml().get("instances", {}).get(name, {}).get("aliases")
        if _prev_aliases:
            block["aliases"] = normalize_aliases(_prev_aliases,
                                                 primary=block.get("domain"))

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


def _auto_heal_wp_url(name: str) -> bool:
    """Restore a registered HTTPS instance URL if WP drifted to localhost."""
    cfg = load_config()
    ic = resolve_instances(cfg).get(name) or {}
    expected = site_url(ic)
    if not expected.startswith("https://"):
        return False

    current = wpcli(["option", "get", "siteurl"], instance=name,
                    check=False, capture=True)
    if (getattr(current, "stdout", "") or "").strip() == expected:
        return False

    _write_ssl_muplugin(name)
    wpcli(["option", "update", "siteurl", expected], instance=name,
          check=False)
    wpcli(["option", "update", "home", expected], instance=name,
          check=False)
    info(f"{name}: auto-healed WP url → {expected}")
    return True


def _refresh_registered_url(sc, root: str, label: str, existing: dict,
                            cfg: dict) -> dict:
    """Re-record the instance URL from live state on the ready fast path.

    A clean URL can be assigned AFTER an instance was registered — `./sb domains
    setup` on an existing stack is the normal case. Without this, ensure keeps
    returning the recorded `http://localhost:<port>` forever, and every caller
    that trusts it (`.wp-env-port`, MCP, E2E config) stays on the per-port URL
    while the browser is served the clean one. site_url() returns the per-port
    URL itself when no routed domain is serving, so this never invents one.
    """
    name = existing.get("instance")
    resolved = resolve_instances(cfg).get(name) if name else None
    if not resolved:
        return existing
    fresh = site_url({k: v for k, v in resolved.items() if k != "url"})
    if not fresh or fresh == existing.get("url"):
        return existing
    block = _local_yaml().get("instances", {}).get(name, {})
    token = block.get("autologin_token", "")
    extra = {}
    # Record the clean hostname alongside the URL: consumers (domain status,
    # MCP, doctor) read the registry, and a URL alone leaves them re-deriving
    # or inventing an identity the instance is not actually serving.
    if block.get("domain"):
        extra["domain"] = block["domain"]
        if block.get("tld"):
            extra["tld"] = block["tld"]
    return sc.registry_put(
        root, label=label, instance=name, url=fresh,
        login_url=f"{fresh}/?sandbox_autologin={token}" if token else "",
        admin_url=f"{fresh}/wp-admin/", **extra,
    )


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

    # Instance names are project-scoped, but published ports and the rendered
    # runtime config are host-scoped. Matrix cells use distinct workspace roots
    # and would otherwise pass their per-project locks concurrently, selecting
    # the same free Mailpit/WordPress port before either registry write lands.
    with sc.project_lock(root), sc.project_lock(RUNTIME_DIR / ".instance-ports"):
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
            _auto_heal_wp_url(existing["instance"])
            return _refresh_registered_url(sc, root, label, existing, cfg)

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

        # Resolve, materialize, and build extension images before writing
        # sandbox.local.yml, generating Compose, or booting a container.  A
        # fresh scaffold resolves trusted official parent digests through the
        # bounded Docker adapter; no hidden digest input is required.
        try:
            _prepared_extensions = prepare_php_extension_runtime(block, server)
            if _prepared_extensions is not None:
                _persist_php_extension_runtime(block, _prepared_extensions)
        except (TypeError, ValueError) as exc:
            raise sc.ConfigError(str(exc)) from exc

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
        secured = False
        if server == "herd":
            # Host driver: link + isolate + secure replace the docker boot.
            _provision_herd(name, pconf)
        else:
            cmd_up(cfg, ns)
            # Secure-at-create: when the clean-URL proxy is already set up, give
            # the instance its https://<name>.<tld> BEFORE install so WP never
            # stores an http localhost URL (whose port leaks into redirects).
            # Single-site only; falls back to localhost otherwise.
            if _proxy_sudoers_installed() and _secure_at_create(cfg, name):
                secured = True
                cfg = load_config()
        cmd_install(cfg, ns)
        # A version-pinned bootstrap may need to repair an empty or partial
        # document root before WordPress can answer HTTP. Probe only after core
        # installation, otherwise the first ensure fails before repair starts.
        if server != "herd":
            _wait_http(ports["wordpress_port"])
            if block.get("php_extensions") is not None:
                extension_status = php_extension_status(
                    resolve_instances(cfg)[name], instance=name,
                )
                drift = (extension_status or {}).get("drift", {})
                if drift.get("state") != "ready":
                    issues = drift.get("issues") or []
                    detail = (issues[0].get("message")
                              if issues and isinstance(issues[0], dict)
                              else "PHP extension planes are not verified")
                    raise sc.ConfigError(
                        f"PHP extension verification blocked after ensure: {detail}")
        if secured:
            _auto_heal_wp_url(name)
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

        # Spec 008: a newly provisioned instance gets both restore points only
        # after its project plugins/themes are in their final installed state.
        # `capture_install_snapshots` is idempotent, so resuming a pending
        # instance never replaces a clean baseline with later DB changes.
        from sandbox.commands.data import capture_install_snapshots
        capture_install_snapshots(name)

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
        prior_local = _local_yaml()
        prev_block = (prior_local.get("instances", {}).get(name, {}))
        prev_ms = _multisite_mode(prev_block)
        rollback_snapshot = _capture_apply_rollback_state(
            name, cfg, existing, prior_local, prev_block,
        )

        # 1. Rewrite the instance block from the current config, then resolve
        # and build opted-in extension images before persisting it. A failed
        # parent pull/build therefore cannot touch the running stack.
        block = _build_instance_block(cfg, name, root, pconf, ports, server)
        try:
            _prepared_extensions = prepare_php_extension_runtime(block, server)
            if _prepared_extensions is not None:
                _persist_php_extension_runtime(block, _prepared_extensions)
        except (TypeError, ValueError) as exc:
            # Do not rewrite the instance block or touch the running stack when
            # extension intent cannot be resolved/materialized safely.
            raise sc.ConfigError(str(exc)) from exc
        # Persisted state and Compose generation are part of the same
        # transaction boundary as the later web reconcile.  A failure in
        # either loader/generator after local YAML was written must restore the
        # exact prior state before surfacing the error.
        try:
            local = _local_yaml()
            local.setdefault("instances", {})[name] = block
            _write_local_yaml(local)

            # 2. Regenerate compose + recreate the web tier in place (no DB
            # drop). Herd has no web tier to recreate; its branch only re-pins
            # host configuration below.
            cfg = load_config()
            write_compose_files(cfg)
            inst_cfg = resolve_instances(cfg)[name]
            info(f"apply_config: reconciling '{name}' in place (no data loss)…")
        except Exception as exc:
            rollback = _restore_apply_rollback_state(
                rollback_snapshot, name, runtime_touched=False,
            )
            detail = str(exc)[:500]
            if rollback["ok"]:
                raise sc.ConfigError(
                    f"apply failed before web reconcile: {detail}; "
                    "rollback=succeeded (prior state and Compose artifact restored)"
                ) from exc
            rollback_detail = "; ".join(rollback.get("errors") or ())
            raise sc.ConfigError(
                f"apply failed before web reconcile: {detail}; "
                f"rollback=failed ({rollback_detail}); manual recovery required"
            ) from exc
        if server == "herd":
            _pin_wp_constants_in_config(name, inst_cfg)
            if wp_dir(name).exists():
                _write_host_runtime_muplugins(name)
                _remove_obsolete_builder_authoring_assets(name)
        else:
            # Apply owns only the PHP/web tier. ``_web_services`` also names
            # DB and Mailpit for a full boot; using it here (and allowing
            # dependency recreation) needlessly restarts stateful services.
            # Both selected web services are explicit, so ``--no-deps`` keeps
            # the database and mail capture containers untouched.
            _apply_services = ["wp"]
            if inst_cfg.get("server", "nginx") == "nginx":
                _apply_services.append("nginx")
            try:
                result = compose("up", "-d", "--no-deps", "--force-recreate",
                                 *_apply_services,
                                 instance=name, check=False)
                returncode = getattr(result, "returncode", 0)
                if returncode not in (None, 0):
                    detail = (getattr(result, "stderr", "") or
                              getattr(result, "stdout", "") or
                              f"exit {returncode}").strip()
                    raise RuntimeError(f"web reconcile failed: {detail[:240]}")
                if not _wait_reachable(inst_cfg):
                    raise RuntimeError("web reconcile did not become reachable")
                if inst_cfg.get("php_extensions", inst_cfg.get("phpExtensions")) is not None:
                    extension_status = php_extension_status(inst_cfg, instance=name)
                    drift = (extension_status or {}).get("drift", {})
                    if drift.get("state") != "ready":
                        issues = drift.get("issues") or []
                        detail = (issues[0].get("message")
                                  if issues and isinstance(issues[0], dict)
                                  else "PHP extension planes are not verified")
                        raise RuntimeError(
                            f"PHP extension verification blocked: {detail}")
            except Exception as exc:
                rollback = _restore_apply_rollback_state(
                    rollback_snapshot, name,
                )
                detail = str(exc)[:500]
                if rollback["ok"]:
                    raise sc.ConfigError(
                        f"apply failed after web reconcile: {detail}; "
                        "rollback=succeeded (prior state and web runtime restored)"
                    ) from exc
                rollback_detail = "; ".join(rollback.get("errors") or ())
                raise sc.ConfigError(
                    f"apply failed after web reconcile: {detail}; "
                    f"rollback=failed ({rollback_detail}); manual recovery required"
                ) from exc
            # Re-assert the SSL + mail mu-plugins (recreate may have reset
            # nothing, but keep them guaranteed-present like cmd_up does).
            if wp_dir(name).exists():
                _write_mail_muplugin(name)
                _write_dl_cache_muplugin(name)
                _write_ondemand_muplugin(name)   # spec 010 — on-demand local plugin sourcing
                _write_host_runtime_muplugins(name)  # specs 003/007 — host-file runtime tools
                _write_licensing_muplugin(name)  # spec 013 — cross-instance Pro license activation
                _remove_obsolete_builder_authoring_assets(name)

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

        # 5. Reconcile WordPress core itself. The instance block was rewritten
        #    from pconf above, so a pin-vs-block comparison can never disagree
        #    here — the only honest source of drift is the LIVE `wp core
        #    version`, which nothing else in apply touches.
        core_state = _reconcile_wp_core(name, inst_cfg, pconf)

        # Re-derive from live state instead of reusing the recorded URL: a
        # clean URL assigned AFTER this instance was registered (e.g. by a later
        # `./sb domains setup`) must be picked up here, or ensure keeps handing
        # callers the stale localhost:<port> for the life of the instance.
        # site_url() falls back to that per-port URL on its own when no routed
        # domain is serving.
        base_url = (site_url(inst_cfg) or existing.get("url")
                    or f"http://localhost:{ports['wordpress_port']}")
        record = sc.registry_put(
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
        # Report the core reconcile alongside the record (the registry stores
        # the PIN; this is what the site actually runs after this apply).
        if isinstance(record, dict) and core_state:
            record = {**record, "wp_core": core_state}
        return record


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


def _capture_apply_rollback_state(name: str, cfg: dict, existing: dict,
                                  local: dict, prev_block: dict) -> dict:
    """Capture every controller-owned input needed for an in-place rollback.

    The snapshot is deliberately taken before ``sandbox.local.yml`` is
    rewritten or a web container is recreated.  It contains the previous
    resolved runtime inputs (including extension-image identities), the exact
    persisted local document, and the generated Compose artifact.  Database,
    uploads, and Mailpit are not touched or copied: apply owns only the web
    service set and rollback uses ``--no-deps`` as well.
    """
    prior_cfg = cfg
    try:
        prior_cfg = load_config()
    except Exception:
        # The caller's already-resolved cfg is the safest fallback if the
        # machine-level config is temporarily unreadable.
        prior_cfg = cfg
    try:
        prior_runtime = resolve_instances(prior_cfg).get(name)
    except Exception:
        prior_runtime = None
    compose_path = compose_file(name)
    compose_exists = compose_path.is_file()
    compose_bytes = compose_path.read_bytes() if compose_exists else None
    return {
        "local": copy.deepcopy(local),
        "compose_path": compose_path,
        "compose_exists": compose_exists,
        "compose_bytes": compose_bytes,
        "runtime": copy.deepcopy(prior_runtime or prev_block or {}),
        "registry": copy.deepcopy(existing),
    }


def _restore_apply_rollback_state(snapshot: dict, name: str,
                                  *, runtime_touched: bool = True) -> dict:
    """Restore persisted/runtime artifacts and reconcile only old web services.

    Each restoration step is attempted independently so a failed state-file
    write does not suppress a best-effort runtime recovery.  The returned
    envelope is safe to include in a stable operator error and explicitly
    distinguishes a complete rollback from a partial/failed one.
    """
    errors: list[str] = []
    try:
        _write_local_yaml(copy.deepcopy(snapshot["local"]))
    except Exception as exc:
        errors.append(f"persisted state restore failed: {str(exc)[:240]}")

    compose_path = snapshot["compose_path"]
    try:
        if snapshot["compose_exists"]:
            compose_path.parent.mkdir(parents=True, exist_ok=True)
            compose_path.write_bytes(snapshot["compose_bytes"] or b"")
        elif compose_path.exists():
            compose_path.unlink()
    except Exception as exc:
        errors.append(f"Compose artifact restore failed: {str(exc)[:240]}")

    runtime = snapshot.get("runtime") or {}
    old_server = runtime.get("server", "nginx")
    if runtime_touched and old_server != "herd":
        services = ["wp"]
        if old_server == "nginx":
            services.append("nginx")
        try:
            result = compose("up", "-d", "--no-deps", "--force-recreate",
                             *services, instance=name, check=False,
                             capture=True)
            returncode = getattr(result, "returncode", 0)
            if returncode not in (None, 0):
                detail = (getattr(result, "stderr", "") or
                          getattr(result, "stdout", "") or
                          f"exit {returncode}").strip()
                errors.append(f"runtime rollback failed: {detail[:240]}")
            elif runtime.get("wordpress_port") and not _wait_reachable(runtime):
                errors.append("runtime rollback failed: restored web tier did not become reachable")
        except Exception as exc:
            errors.append(f"runtime rollback failed: {str(exc)[:240]}")

    if errors:
        return {"ok": False, "state": "rollback_incomplete", "errors": errors}
    return {
        "ok": True,
        "state": "rolled_back" if runtime_touched else "state_and_artifact_restored",
        "runtime_restored": bool(runtime_touched and old_server != "herd"),
        "errors": [],
    }
def registry_all() -> dict:
    """Compatibility facade for the typed project registry."""
    import sandbox_core as sc
    return sc.registry_all()


def registry_find_instance(instance_name: str) -> dict | None:
    """Compatibility facade for reverse instance ownership lookup."""
    import sandbox_core as sc
    return sc.registry_find_instance(instance_name)


def registry_put(root, label="default", **fields) -> dict:
    """Compatibility facade for updating one typed registry record."""
    import sandbox_core as sc
    return sc.registry_put(root, label=label, **fields)
