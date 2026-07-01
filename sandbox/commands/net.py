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



def cmd_secure(cfg, args) -> None:
    """Upgrade an instance from http://<name>.tst to trusted https://<name>.tst.
    OPT-IN: installs + trusts the mkcert CA (one password), mints a cert for the
    domain, switches WP's siteurl to https, and reloads the proxy. The default
    install never does this — plain http needs no cert + never warns."""
    # Accept the instance as a positional (`./sb secure he`) or via --instance.
    name = getattr(args, "name", None) or args.resolved_instance
    inst = resolve_instances(cfg).get(name)
    if not inst:
        die(f"unknown instance '{name}'. Run: ./sb instances")
    dom = inst.get("domain")
    if not (dom and dom.endswith(f".{_tld(inst)}")):
        die(f"'{name}' has no .{_tld(inst)} domain to secure. Create it with a "
            f"domain first (e.g. ./sb instance create {name}).")
    if not sys.stdin.isatty():
        die("`./sb secure` needs an interactive terminal (it trusts a local CA "
            "via sudo). Run it directly in your shell.")
    print(f"\n▸ Securing https://{dom} — installs + trusts a local certificate.")
    if not proxy_setup(cfg):   # ensures proxy + mkcert CA trusted + mints certs
        die("could not enable HTTPS (see messages above).")
    cfg = load_config()
    # Subdomain multisite: add a `*.<name>.tst` SAN so every sub-site host is
    # covered by this one cert.
    sans = [_wildcard_san(dom)] if _multisite_mode(inst) == "subdomain" else None
    _mint_cert(dom, extra_sans=sans)
    _write_ssl_muplugin(name)
    regen_caddyfile(cfg)
    reload_proxy()
    # Regenerate the instance's compose + recreate its web tier so the LIVE
    # container always matches the current generated compose. Without this, a
    # container created before a compose-template change (e.g. the nginx
    # plugins_host mount that lets symlinked-plugin assets resolve) keeps its
    # stale mounts — so a secured nginx instance 404s plugin assets and renders
    # the admin UI blank. Cheap + idempotent; recreates only the web service(s).
    write_compose_files(cfg)
    inst = resolve_instances(cfg)[name]
    compose("up", "-d", "--force-recreate",
            *_web_services(inst.get("server", "nginx")),
            instance=name, check=False)
    url = f"https://{dom}"
    wpcli(["option", "update", "siteurl", url], instance=name, check=False)
    wpcli(["option", "update", "home", url], instance=name, check=False)
    ok(f"{name} now serves {url}  (hard-refresh your browser)")

def cmd_server(cfg, args) -> None:
    """Switch an existing instance's web server in place: apache | nginx |
    litespeed. Mutates the instance's `server` field, regenerates its compose,
    and recreates the web tier — swapping the wp image (litespeed uses the OLS
    image), adding/removing the nginx sidecar, and re-asserting permalinks — so
    the SAME site is served by the new stack on the SAME URL. No new ports, no
    new DB, no content move (the WP files are the same bind-mounted host dir)."""
    name = getattr(args, "name", None) or args.resolved_instance
    inst = resolve_instances(cfg).get(name)
    if not inst:
        die(f"unknown instance '{name}'. Run: ./sb instances")
    target = _valid_server(args.server_type)
    current = inst.get("server", "nginx")
    if target == current:
        ok(f"{name} already uses {current} — nothing to change.")
        return
    if "herd" in (current, target):
        die("docker↔herd isn't a hot web-tier swap — it's a re-provision. "
            "Set `server` in the project's sandbox.config.json (or override) "
            f"and recreate: ./sb instance delete {name} && ./sb ensure "
            "--project-dir <project>")

    info(f"switching {name}: {current} → {target}")

    # 1. Persist the new server to the instance's block, then reload + regen
    #    compose so the generated stack reflects the new web tier.
    local = _local_yaml()
    blk = local.setdefault("instances", {}).setdefault(name, {})
    blk["server"] = target
    _write_local_yaml(local)
    cfg = load_config()
    write_compose_files(cfg)

    # 2. Cross-uid file perms when litespeed is on either side of the switch
    #    (33 ↔ 1000). Same files, different serving uid — relax so both read.
    if "litespeed" in (current, target):
        _relax_perms_for_uid_switch(name)

    # 3. Recreate the web tier from the NEW server. --remove-orphans drops the
    #    nginx sidecar when leaving nginx; --force-recreate swaps the wp image
    #    (e.g. wordpress:latest ↔ OpenLiteSpeed) even if the service name is
    #    unchanged.
    inst = resolve_instances(cfg)[name]
    compose("up", "-d", "--force-recreate", "--remove-orphans",
            *_web_services(target), instance=name, check=False)

    # 4. litespeed needs: literal DB creds in wp-config (lsphp doesn't inherit
    #    the container env that getenv_docker relies on, so env-based config
    #    500s), its WP .htaccess in the docroot (WP won't write one under OLS),
    #    and an OLS reload. Apache/nginx derive both natively from the image.
    if target == "litespeed":
        _pin_db_creds_in_config(name)
        _pin_wp_constants_in_config(name, inst)
        _ensure_litespeed_htaccess(name)

    # 5. Re-assert permalinks so /wp-json/ + pretty URLs work on the new stack.
    wpcli(["rewrite", "structure", "/%postname%/"], instance=name, check=False)
    wpcli(["rewrite", "flush"], instance=name, check=False)

    ok(f"{name} now served by {target}: {site_url(inst)}  (hard-refresh your browser)")

def cmd_domains(cfg, args) -> None:
    """Manage custom local domains + the HTTPS proxy.

    Primary: a sandbox-managed Caddy proxy serves every instance at a trusted
    no-port URL — https://<name>.tst — bound to a dedicated loopback IP so it
    coexists with Valet/anything on 127.0.0.1:443. Setup is one-time (one sudo).
    Fallbacks (no proxy): Valet (http://<name>.<tld>) or <name>.tst:<port>.

    `./sb domains setup [tld]` — one-time: mkcert CA + *.<tld> cert + dnsmasq +
                              proxy. Uses <tld> if given, else prompts (default
                              tst); a project's own `tld` config still wins.
    `./sb domains up`       — (re)start the proxy + restore the loopback alias.
    `./sb domains down`     — stop the proxy container.
    `./sb domains teardown` — undo setup (untrust CA, remove dnsmasq/alias).
    `./sb domains list`     — show domains + proxy status.
    """
    action = getattr(args, "action", None) or "list"

    if action == "setup":
        tld = _resolve_setup_tld(args)   # CLI arg, else prompt (default tst)
        # `domains setup` always delivers trusted HTTPS out of the box: it mints
        # a per-instance cert and points WP at https://<name>.<tld>. The only
        # interactive bits are first-time (install the sudoers rule + trust the
        # local CA); once those exist, re-running is non-interactive — so it also
        # secures instances created after the initial setup.
        ca_ok = _ca_trusted_macos() if sys.platform == "darwin" else True
        first_time = not (_proxy_sudoers_installed() and ca_ok and shutil.which("mkcert"))
        if first_time and not sys.stdin.isatty():
            die("`./sb domains setup` needs an interactive terminal the first time "
                "(installs a sudoers rule + trusts a local HTTPS CA). Run it "
                "directly in your shell.")
        if proxy_setup(cfg, tld):
            _HTTPS_OFFER_MARKER.unlink(missing_ok=True)  # they opted in after all
            ok(f"Done. Every instance serves https://<name>.{tld or PROXY_TLD} — "
               "no port, trusted HTTPS, no further sudo.")
        else:
            info("HTTPS proxy setup did not complete — instances will fall back "
                 "to http://localhost:<port> until it does.")
        return

    if action == "up":
        if not proxy_available():
            die("HTTPS proxy not set up yet — run `./sb domains setup` first.")
        # Restore the loopback alias (dropped on reboot), then start the proxy.
        if _proxy_sudoers_installed():
            subprocess.run(["sudo", "-n", str(PROXY_HELPER), "alias-up"],
                           capture_output=True, text=True)
        regen_caddyfile(cfg)
        ok("HTTPS proxy up." if reload_proxy() else "proxy failed to start.")
        return

    if action == "down":
        subprocess.run(["docker", "compose", "-p", PROXY_PROJECT, "-f",
                        str(PROXY_COMPOSE), "--project-directory", str(ROOT),
                        "down"], capture_output=True, text=True)
        ok("HTTPS proxy stopped.")
        return

    if action == "teardown":
        proxy_teardown(cfg)
        return

    if action == "repair-ca":
        if not sys.stdin.isatty():
            die("repair-ca needs an interactive terminal (re-trusts the CA via "
                "sudo). Run it directly in your shell.")
        info("Repairing the local CA — removes stale/duplicate mkcert CAs and "
             "re-trusts a single fresh one (asks your password).")
        # mkcert -uninstall removes the CA from every trust store; -install then
        # creates + trusts a clean one. This clears the 'multiple stale CAs,
        # none trusted' state that causes ERR_CERT_AUTHORITY_INVALID.
        subprocess.run(["mkcert", "-uninstall"])
        if subprocess.run(["mkcert", "-install"]).returncode != 0:
            die("mkcert -install failed — run it yourself, then re-run repair-ca.")
        if sys.platform == "darwin" and not _ca_trusted_macos():
            die("CA still not trusted after reinstall. A keychain GUI prompt may "
                "need approving — open Keychain Access, or run `mkcert -install` "
                "interactively, then re-run.")
        ok("CA is trusted (verified).")
        # Re-mint every instance's cert against the new CA + reload the proxy.
        n = 0
        for name, ic in resolve_instances(cfg).items():
            dom = ic.get("domain")
            if dom and dom.endswith(f".{_tld(ic)}"):
                for p in _cert_paths(dom):
                    p.unlink(missing_ok=True)
                sans = [_wildcard_san(dom)] if _multisite_mode(ic) == "subdomain" else None
                if _mint_cert(dom, extra_sans=sans):
                    n += 1
        regen_caddyfile(cfg)
        reload_proxy()
        ok(f"re-minted {n} cert(s) against the trusted CA. Hard-refresh your "
           f"browser (or restart it) to drop the cached cert verdict.")
        return

    if action == "list":
        print()
        any_dom = False
        for name, ic in resolve_instances(cfg).items():
            if ic.get("domain"):
                any_dom = True
                print(f"  {name:<14} {site_url(ic)}")
        if not any_dom:
            info("No custom domains. Create one: ./sb instance create <name>")
        print()
        # Per-instance: which are plain http vs secured https. `.get("domain")`
        # can be None (key present but empty in sandbox.local.yml), so coerce to
        # "" before .endswith — the bare default doesn't cover an explicit None.
        http_doms = [ic["domain"] for ic in resolve_instances(cfg).values()
                     if (ic.get("domain") or "").endswith(f".{_tld(ic)}")
                     and not _cert_paths(ic["domain"])[0].exists()]
        running = _proxy_container_running()
        print(f"  Proxy: {'✓ running' if running else '○ not running'} on "
              f"{PROXY_BIND_IP}  ·  default URLs are plain http (no port, no cert)")
        if http_doms:
            print(f"  🔒 Want HTTPS for a site? Run:  ./sb secure <name>")
            print(f"     (one-time — trusts a local cert → https://<name>.{PROXY_TLD})")
        print()
        return

    die("usage: ./sb domains setup|up|down|teardown|repair-ca|list")

def cmd_pxdiff(cfg, args):
    """Pixel-diff two PNG screenshots and locate the drift (shells to tools/pxdiff/pxdiff.mjs)."""
    root = Path(__file__).resolve().parents[2]
    script = root / "tools" / "pxdiff" / "pxdiff.mjs"
    if not script.is_file():
        die(f"missing {script}")
    ref, build = Path(args.reference).expanduser(), Path(args.build).expanduser()
    for label, pth in (("reference", ref), ("build", build)):
        if not pth.is_file():
            die(f"{label} not found: {pth}")
    cmd = ["node", str(script), str(ref.resolve()), str(build.resolve()),
           "--threshold", str(args.threshold), "--bands", str(args.bands)]
    if args.diff_out:
        cmd += ["--out", str(Path(args.diff_out).expanduser())]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(root))
    except FileNotFoundError:
        die("node not found on PATH (needed for pixelmatch)")
    except subprocess.TimeoutExpired:
        die("pxdiff timed out after 60s")
    try:
        data = json.loads(res.stdout)
    except Exception:
        die(f"pxdiff produced no JSON:\n{res.stderr or res.stdout}")
    if not data.get("ok"):
        die(data.get("error", "pxdiff failed"))
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return
    r, b, c = data["reference"], data["build"], data["compared"]
    print(f"reference {r['w']}x{r['h']}  ·  build {b['w']}x{b['h']}  ·  compared {c['w']}x{c['h']}"
          + ("" if data["dimensionsMatch"] else "  (cropped to smaller)"))
    print(f"mismatch: {data['mismatch']:,} px  =  {data['pct']}%   [{data['verdict']}]")
    if data.get("worstBands"):
        print("worst bands  (y-top / height / diff%):")
        for wb in data["worstBands"]:
            print(f"  {wb['top']:>6} / {wb['h']:<5} {wb['pct']}%")
    if data.get("diff"):
        print(f"diff overlay: {data['diff']}")


def cmd_vrdiff(cfg, args):
    """BackstopJS visual-regression diff (reference URL vs build URL) with a browsable HTML web
    report. Shells to tools/backstop/vrdiff.mjs — the design-fidelity VISUAL/web-preview pass;
    `sb pxdiff` remains the numeric per-band locator."""
    root = Path(__file__).resolve().parents[2]
    script = root / "tools" / "backstop" / "vrdiff.mjs"
    if not script.is_file():
        die(f"missing {script}")
    cmd = ["node", str(script), args.reference_url, args.build_url,
           "--label", args.label, "--selector", args.selector,
           "--threshold", str(args.threshold), "--delay", str(args.delay),
           "--workdir", str(Path(args.workdir).expanduser())]
    for vp in (args.viewport or []):
        cmd += ["--viewport", vp]
    if getattr(args, "no_open", False):
        cmd.append("--no-open")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(root))
    except FileNotFoundError:
        die("node not found on PATH (needed for BackstopJS)")
    except subprocess.TimeoutExpired:
        die("vrdiff timed out after 600s")
    try:
        data = json.loads(res.stdout)
    except Exception:
        die(f"vrdiff produced no JSON:\n{res.stderr or res.stdout}")
    if not data.get("ok"):
        die(data.get("error", "vrdiff failed"))
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return
    print(f"reference  {data['reference']}")
    print(f"build      {data['build']}")
    for s in data.get("scenarios", []):
        dim = "" if s.get("sameDimensions") else "  (dims differ)"
        print(f"  [{s.get('viewport')}] {s.get('label')}: {s.get('misMatchPct')}% mismatch  [{s.get('status')}]{dim}")
    print(f"web report: {data['htmlReport']}")


register({
    'domains': cmd_domains,
    'secure': cmd_secure,
    'server': cmd_server,
    'pxdiff': cmd_pxdiff,
    'vrdiff': cmd_vrdiff,
})
