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



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register



def cmd_up(cfg: dict, args) -> None:
    inst = args.resolved_instance
    inst_cfg = resolve_instances(cfg)[inst]
    if inst_cfg.get("server") == "herd":
        # Host-served by Herd — nothing to boot; Herd serves linked sites
        # whenever it's running.
        ok(f"WordPress: {site_url(inst_cfg)}  (host-served by Herd)")
        return
    # If this instance uses the HTTPS proxy, make sure the loopback alias is
    # present (it's dropped on reboot) and the proxy is running before we report
    # the https URL. Cheap + silent; passwordless via the sudoers rule.
    dom = inst_cfg.get("domain")
    if dom and dom.endswith(f".{_tld(inst_cfg)}") and proxy_available():
        _ensure_proxy_up(cfg)
    compose("up", "-d", *_web_services(inst_cfg.get("server", "apache")),
            instance=inst)
    # Re-assert the mail-capture mu-plugin on every up so it survives
    # down/up and any wp-content reset. Cheap + idempotent; only touches the
    # shared runtime bind-mount, which exists for any provisioned instance.
    if wp_dir(inst).exists():
        _write_mail_muplugin(inst)
        _write_dl_cache_muplugin(inst)
        # Re-assert the snapshot bridge mu-plugin + ensure the host bridge server
        # is running so Tools → Sandbox Snapshots works after a plain `up` (FR-014).
        # Mint the token if it's missing so `up` self-heals an instance whose
        # token was never created (or was dropped by an older apply, before the
        # _build_instance_block preservation fix). Not on herd (no bridge yet).
        if not _is_herd_instance(inst):
            _tok = _bridge_token_for(inst)
            if not _tok:
                import secrets as _secrets
                _tok = _secrets.token_hex(16)
                save_local_bridge_token(_tok, instance=inst)
            _write_snapshot_muplugin(inst, _tok)
            _ensure_bridge_server()
    ok(f"WordPress: {site_url(inst_cfg)}")
    ok(f"Mailpit:   http://localhost:{inst_cfg['mailpit_port']}")

def cmd_down(cfg, args) -> None:
    if _is_herd_instance(args.resolved_instance):
        info("host-served by Herd — nothing to stop (sites serve while Herd "
             "runs). Remove entirely with: ./sb instance delete "
             f"{args.resolved_instance}")
        return
    compose("down", instance=args.resolved_instance)

def cmd_status(cfg, args) -> None:
    inst = args.resolved_instance
    if _is_herd_instance(inst):
        entry = _core().registry_find_instance(inst) or {}
        up = _instance_reachable(entry)
        ok(f"Instance: {inst}  (host-served by Herd — "
           f"{'reachable' if up else 'NOT reachable'} at {entry.get('url')})")
    else:
        compose("ps", instance=inst)
    apf = active_project_file(inst)
    ff = focus_file(inst)
    srv = mcp_server_name(inst)
    ok(f"Instance: {inst}  (Claude tools: mcp__{srv}__*)")
    ok(f"Server: {resolve_instances(cfg)[inst].get('server', 'apache')}")
    owner = _core().registry_find_instance(inst)
    if owner and owner.get("root"):
        ok(f"Project: {owner['root']}")
    else:
        info("No project registered (cd into a plugin repo and run ./sb ensure)")
    if ff.exists():
        ok(f"Focused plugin: {ff.read_text().strip()}")
    else:
        info("No focused plugin (run: ./sb focus <slug>)")
    # Keep the wp-admin snapshot bridge reachable for running instances (it only
    # auto-starts on `sb up`, so an already-running instance would otherwise have
    # no bridge — Tools → Sandbox Snapshots would fail to connect).
    if not _is_herd_instance(inst) and _bridge_token_for(inst):
        _ensure_bridge_server()

def cmd_logs(cfg, args) -> None:
    if _is_herd_instance(args.resolved_instance):
        die("no containers on a herd instance — tail the WP debug log instead: "
            f"tail -f runtime/wp-{args.resolved_instance}/wp-content/debug.log")
    compose("logs", "-f", "wp", "db", instance=args.resolved_instance)

def cmd_shell(cfg, args) -> None:
    if _is_herd_instance(args.resolved_instance):
        die("no containers on a herd instance — the WP install is on the host "
            f"at runtime/wp-{args.resolved_instance}/")
    compose("exec", "wp", "bash", instance=args.resolved_instance)

def cmd_install(cfg, args) -> None:
    inst = args.resolved_instance
    inst_cfg = resolve_instances(cfg)[inst]
    adm = inst_cfg["admin"]
    port = inst_cfg["wordpress_port"]
    server = inst_cfg.get("server", "apache")

    # WordPress bootstrap, made robust for EVERY server:
    #  - The image is PHP-only (wordpress:php8.1) — WP core is downloaded here,
    #    following @wordpress/env's approach. This avoids 'manifest unknown'
    #    errors from non-existent patch-level image tags (e.g. 6.9.4-php8.1).
    #  - When a wp_version is pinned, always force-download it so the container
    #    runs the exact requested version, not whatever ships in the base image.
    #  - Without a pin, skip the download if WP is already present (the base
    #    image ships WP latest, which is fine for unpinned instances).
    wp_v = inst_cfg.get("wp_version")
    if wp_v:
        info(f"downloading WordPress {wp_v}…")
        wpcli(["core", "download", "--force", f"--version={wp_v}"],
              instance=inst, check=False)
    else:
        ver = wpcli(["core", "version"], instance=inst, check=False, capture=True)
        if ver.returncode != 0:
            info("downloading WordPress core (latest)…")
            wpcli(["core", "download", "--force"], instance=inst, check=False)
    chk = wpcli(["config", "path"], instance=inst, check=False, capture=True)
    if chk.returncode != 0:
        info("generating wp-config.php…")
        if server == "herd":
            # Host MySQL (DBngin) with a per-instance database — every herd
            # instance shares the one host server, unlike docker's per-stack DB.
            wpcli([
                "config", "create",
                f"--dbhost={HERD_DB_HOST}:{HERD_DB_PORT}",
                f"--dbname={_herd_db_name(inst)}",
                f"--dbuser={HERD_DB_USER}", f"--dbpass={HERD_DB_PASSWORD}",
                "--skip-check", "--force",
            ], instance=inst, check=False)
        else:
            wpcli([
                "config", "create",
                "--dbhost=db:3306", "--dbname=wp",
                "--dbuser=wp", "--dbpass=wp",
                "--skip-check", "--force",
            ], instance=inst, check=False)
    if server == "herd":
        # Idempotent: errors when the DB already exists, which is fine.
        wpcli(["db", "create"], instance=inst, check=False, capture=True)

    wpcli([
        "core", "install",
        f"--url={site_url(inst_cfg)}",
        f"--title={adm.get('site_title', 'Sandbox')}",
        f"--admin_user={adm.get('user', 'admin')}",
        f"--admin_password={adm.get('password', 'admin')}",
        f"--admin_email={adm.get('email', 'admin@example.com')}",
        "--skip-email",
    ], instance=inst)
    wpcli(["rewrite", "structure", "/%postname%/"], instance=inst)

    # The OpenLiteSpeed image's vhost template does `autoLoadHtaccess`, but
    # WordPress only writes a physical .htaccess under Apache — under OLS it
    # writes none, so pretty permalinks + /wp-json/ 404. Drop the canonical
    # WP rewrite .htaccess into the docroot so OLS's autoload picks it up.
    if server == "litespeed":
        _ensure_litespeed_htaccess(inst)
    if server in ("litespeed", "herd"):
        # No WORDPRESS_CONFIG_EXTRA env on these runtimes — lsphp can't read
        # the container env, and herd has no container at all. Pin the
        # project's wp-config constants as literals (the host/OLS wp-config
        # is stable: nothing regenerates it, so literals persist).
        _pin_wp_constants_in_config(inst, inst_cfg)

    # Multisite (sandbox.config.json `multisite`): convert the fresh install,
    # write the marker that activates the network constants, and lay down the
    # network rewrite rules. Must run before plugin/theme wiring so those
    # activate against the converted site.
    _convert_multisite(inst, inst_cfg)

    # Auto-provision an Application Password for the MCP server so wp_rest
    # works out of the box. Idempotent: deletes any prior 'wp-mcp' password
    # and re-creates so the value lands in sandbox.local.yml.
    info("Provisioning Application Password for wp-mcp…")
    wpcli([
        "user", "application-password", "delete",
        adm.get("user", "admin"), "wp-mcp", "--all",
    ], instance=inst, check=False, capture=True)
    res = wpcli([
        "user", "application-password", "create",
        adm.get("user", "admin"), "wp-mcp", "--porcelain",
    ], instance=inst, check=False, capture=True)
    app_pw = (res.stdout or "").strip().splitlines()[-1] if res.returncode == 0 else ""
    if app_pw:
        save_local_app_password(app_pw, instance=inst)
        ok("Application Password saved to sandbox.local.yml")
    else:
        info("(Could not auto-create Application Password — set it manually "
             f"in sandbox.local.yml under instances.{inst}.app_password)")

    # The OpenLiteSpeed image pre-installs the LiteSpeed Cache plugin (LSCWP)
    # + server-level lscache. That competes with xSpeed, so leaving it active
    # would pollute any xSpeed cache/benchmark test on a litespeed instance.
    # Deactivate it (idempotent — no-op if not present).
    if server == "litespeed":
        info("litespeed: deactivating LiteSpeed Cache (LSCWP) so it doesn't "
             "shadow xSpeed's page cache…")
        wpcli(["plugin", "deactivate", "litespeed-cache"],
              instance=inst, check=False, capture=True)

    # One-click autologin URL — token embedded in the mu-plugin file so it
    # survives container restarts (the WP Docker entrypoint regenerates
    # wp-config.php from env-vars on every start, wiping `wp config set` values).
    import secrets as _secrets
    autologin_token = _secrets.token_hex(16)
    mu_dir = wp_dir(inst) / "wp-content" / "mu-plugins"
    mu_dir.mkdir(parents=True, exist_ok=True)
    (mu_dir / "00-sandbox-autologin.php").write_text(_autologin_mu_plugin(autologin_token))
    save_local_autologin_token(autologin_token, instance=inst)

    # Dashboard snapshots mu-plugin (Tools → Sandbox Snapshots) — calls the host
    # `sb web` bridge. Docker only; snapshots aren't supported on herd yet.
    if server != "herd":
        bridge_token = _secrets.token_hex(16)
        _write_snapshot_muplugin(inst, bridge_token)
        save_local_bridge_token(bridge_token, instance=inst)

    # Capture all PHP mail in the Mailpit container (the image has no working
    # sendmail; without this wp_mail() silently fails). Visible to web + wpcli
    # tiers via the shared bind-mount. NOT on herd — there is no `mailpit`
    # host there, and the mu-plugin would break host mail instead.
    if server != "herd":
        _write_mail_muplugin(inst)
        # Cache plugin/theme zip downloads (Templately FSI etc.) — the cache dir
        # is only mounted on docker tiers, so the mu-plugin no-ops on herd anyway.
        _write_dl_cache_muplugin(inst)

    base = site_url(inst_cfg)  # https://<name>.<tld> when secured, else localhost:<port>
    ok(f"Admin: {base}/wp-admin"
       f"  •  Login: {base}/?sandbox_autologin={autologin_token}")

def cmd_doctor(cfg, args) -> None:
    """Audit the whole stack and report what's broken."""
    inst = args.resolved_instance
    inst_cfg = resolve_instances(cfg)[inst]
    adm = inst_cfg["admin"]
    port = inst_cfg["wordpress_port"]
    problems = 0

    print(f"\nInstance: {inst}  (http://localhost:{port})")

    def check(label: str, ok_: bool, hint: str = "") -> None:
        nonlocal problems
        mark = "✓" if ok_ else "✗"
        line = f"  {mark} {label}"
        if not ok_:
            problems += 1
            if hint:
                line += f"\n      → {hint}"
        print(line)

    print("\nContainers:")
    ps = compose("ps", "--format", "json", instance=inst,
                 check=False, capture=True)
    containers = []
    for ln in (ps.stdout or "").splitlines():
        try:
            containers.append(json.loads(ln))
        except ValueError:
            pass
    services = {c.get("Service"): c.get("State") for c in containers}
    for svc in ("wp", "db", "mailpit"):
        check(f"{svc} running", services.get(svc) == "running",
              hint=f"./sb up --instance {inst}   (currently: {services.get(svc, 'missing')})")

    print("\nWordPress:")
    r = wpcli(["core", "is-installed"], instance=inst,
              check=False, capture=True)
    check("core installed", r.returncode == 0,
          hint=f"./sb install --instance {inst}")

    print("\nMCP wiring:")
    app_pw = ((cfg.get("instances", {}) or {}).get(inst, {}) or {}
              ).get("app_password", "")
    check("application_password set", bool(app_pw),
          hint=f"./sb install --instance {inst}   (auto-provisions)")
    if app_pw:
        import base64, urllib.request, urllib.error, time as _t
        token = base64.b64encode(
            f"{adm.get('user', 'admin')}:{app_pw}".encode()).decode()

        import ssl as _ssl
        _noverify = _ssl.create_default_context()
        _noverify.check_hostname = False
        _noverify.verify_mode = _ssl.CERT_NONE

        def _rest_probe(url):
            req = urllib.request.Request(url + "/wp-json/wp/v2/users/me")
            req.add_header("Authorization", f"Basic {token}")
            # Local probe — don't fail on the mkcert cert not being in Python's
            # trust store (it's a connectivity/auth check, not a security gate).
            ctx = _noverify if url.startswith("https") else None
            try:
                with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
            except Exception:
                return 0

        # Probe BOTH the canonical site URL and localhost:port. A custom domain
        # makes WP redirect localhost→https://<domain>, which can drop the auth
        # header (→ 403). Auth is fine if EITHER endpoint accepts it. Retry once
        # to ride out the brief window right after the password is provisioned.
        urls = []
        canon = site_url(resolve_instances(cfg).get(inst, {}))
        if canon:
            urls.append(canon)
        urls.append(f"http://localhost:{port}")
        status = 0
        for attempt in range(2):
            for u in urls:
                status = _rest_probe(u)
                if 200 <= status < 300:
                    break
            if 200 <= status < 300:
                break
            _t.sleep(1.5)
        check(f"REST auth works (status {status})", 200 <= status < 300,
              hint=f"rerun ./sb install --instance {inst} to refresh the password")
    check("wp-mcp venv built", (MCP_VENV / "bin" / "python").exists(),
          hint="./sb mcp-install")

    print("\nState:")
    # Per-project model: the instance maps to a project root in the registry.
    owner = _core().registry_find_instance(inst)
    proj_root = owner.get("root") if owner else None
    check(f"project: {proj_root or '—'}", True,
          hint="cd into a plugin repo and run `./sb ensure` to register one")
    ff = focus_file(inst)
    focus = ff.read_text().strip() if ff.exists() else None
    check(f"focused plugin: {focus or '—'}", True,
          hint="(optional — set with ./sb focus <slug>)")

    print("\nLinked plugins:")
    plug_dir = plugins_dir(inst)
    if plug_dir.exists():
        linked = []
        for entry in sorted(plug_dir.iterdir()):
            if entry.is_symlink():
                linked.append(entry.name)
                tgt = entry.resolve()
                check(f"{entry.name} → {tgt}", tgt.exists(),
                      hint="source missing — re-run `./sb ensure` for the project")
        if not linked:
            info("  (no source-symlinked plugins)")

    print("\nCredentials:")
    local = _local_yaml()
    # FluentBoards — optional but warn if URL saved but unreachable.
    fb = local.get("fluentboards", {}) or {}
    fb_url = fb.get("url", "").rstrip("/")
    if fb_url:
        import urllib.request as _ur, urllib.error as _ue, ssl as _ssl
        _ctx = _ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_NONE
        try:
            req = _ur.Request(fb_url, method="HEAD")
            with _ur.urlopen(req, timeout=5, context=_ctx):
                fb_ok = True
        except Exception:
            fb_ok = False
        check(f"FluentBoards reachable ({fb_url})", fb_ok,
              hint="check the URL in sandbox.local.yml or re-run `./sb connect fb`")
    else:
        info("  FluentBoards not configured (optional — ./sb connect fb)")

    # GitHub org
    gh_org = (local.get("defaults", {}) or {}).get("github_org", "").strip()
    check("github_org set", bool(gh_org),
          hint="run `./sb connect gh` to pick your org (WPDevelopers for team members)")

    # .env.local exists and is chmod 600
    if SECRETS_ENV.exists():
        mode = oct(SECRETS_ENV.stat().st_mode)[-3:]
        check(f".env.local permissions ({mode})", mode == "600",
              hint=f"run: chmod 600 {SECRETS_ENV}")
    else:
        info("  .env.local not yet created (run `./sb connect` to populate)")

    print()
    if problems:
        info(f"{problems} issue(s) — see → hints above")
        sys.exit(1)
    ok("All checks passed.")

def cmd_smoke(cfg, args) -> None:
    """Self-test: boot a temporary instance, verify WP + REST, tear down.

    Validates the full create-install-probe cycle without touching any
    project instance. Takes ~60s on a cold Docker image, ~20s warm.
    """
    import tempfile, time as _t, urllib.request as _ur, urllib.error as _ue

    print("\nSandbox smoke test")
    print("══════════════════")
    problems = 0

    def _check(label: str, ok_: bool, hint: str = "") -> None:
        nonlocal problems
        mark = "✓" if ok_ else "✗"
        line = f"  {mark} {label}"
        if not ok_:
            problems += 1
            if hint:
                line += f"\n      → {hint}"
        print(line)

    # 1. Boot a fresh instance from a throw-away project dir under $HOME
    #    (the project-root allowlist rejects system temp dirs like /var/folders).
    smoke_base = Path.home() / ".sandbox" / "smoke"
    smoke_base.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="sb-smoke-", dir=smoke_base))
    import json as _json
    (tmpdir / "sandbox.config.json").write_text(
        _json.dumps({"slug": "smoke-test", "plugins": []}))

    print(f"\nTemp project dir: {tmpdir}")
    print("\nBoot:")
    t0 = _t.time()
    try:
        entry = ensure_instance(cfg, str(tmpdir))
        inst = entry["instance"]
        port = entry["wordpress_port"]
        elapsed = _t.time() - t0
        _check(f"instance '{inst}' booted ({elapsed:.0f}s)", True)
    except Exception as exc:
        _check("boot", False, str(exc))
        shutil.rmtree(tmpdir, ignore_errors=True)
        sys.exit(1)

    print("\nWordPress:")
    r = wpcli(["core", "is-installed"], instance=inst, check=False, capture=True)
    _check("WP installed", r.returncode == 0,
           hint="check `./sb logs` for install errors")

    print("\nREST:")
    rest_url = f"http://localhost:{port}/wp-json/"
    try:
        with _ur.urlopen(rest_url, timeout=8) as resp:
            rest_ok = resp.status == 200
    except Exception:
        rest_ok = False
    _check(f"GET {rest_url}", rest_ok,
           hint="check pretty-permalinks / AllowOverride in the container")

    print("\nTeardown:")
    try:
        compose("down", "-v", instance=inst, check=False)
        for path in (wp_dir(inst), snapshots_dir(inst)):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        for path in (focus_file(inst), active_project_file(inst), compose_file(inst)):
            if path.exists():
                path.unlink()
        local = _local_yaml()
        if inst in (local.get("instances") or {}):
            del local["instances"][inst]
            if not local["instances"]:
                del local["instances"]
            _write_local_yaml(local)
        _core().registry_remove(str(tmpdir))
        shutil.rmtree(tmpdir, ignore_errors=True)
        _check(f"instance '{inst}' deleted", True)
    except Exception as exc:
        _check("cleanup", False, str(exc))

    print()
    if problems:
        die(f"{problems} check(s) failed — smoke test red")
    ok("Smoke test green.")

def cmd_update(cfg, args) -> None:
    """`git pull --ff-only` the project repo this instance tracks (per-project
    model: the registry maps the instance to a project root, which IS the
    plugin's git checkout)."""
    inst = args.resolved_instance
    owner = _core().registry_find_instance(inst)
    root = owner.get("root") if owner else None
    if not root:
        # Graceful (like cmd_status/cmd_doctor) — `main` is never registered,
        # and bare `./sb update` resolves to main.
        info(f"no project registered for instance '{inst}' — cd into a plugin "
             f"repo and run `./sb ensure` first, or pass --instance <project>.")
        return
    src = Path(root)
    if not (src / ".git").exists():
        info(f"{src} is not a git repo — nothing to pull")
        return
    print(f"▸ {src.name}  ({src})")
    run(["git", "-C", str(src), "pull", "--ff-only"], check=False)
    ok("Done.")

def cmd_open(cfg, args) -> None:
    inst_cfg = resolve_instances(cfg)[args.resolved_instance]
    port = inst_cfg["wordpress_port"]
    mailpit = inst_cfg["mailpit_port"]
    targets = {
        "admin": f"http://localhost:{port}/wp-admin",
        "site": f"http://localhost:{port}",
        "mail": f"http://localhost:{mailpit}",
    }
    url = targets.get(args.what or "admin")
    if not url:
        die(f"unknown target '{args.what}'. Try: admin | site | mail")
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    run([opener, url], check=False)

register({
    'up': cmd_up,
    'down': cmd_down,
    'status': cmd_status,
    'logs': cmd_logs,
    'shell': cmd_shell,
    'install': cmd_install,
    'smoke': cmd_smoke,
    'doctor': cmd_doctor,
    'update': cmd_update,
    'open': cmd_open,
})
