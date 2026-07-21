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


def _tld(ic: dict | None = None) -> str:
    """Local domain TLD for an instance — from its `tld` (sandbox.config.json),
    defaulting to PROXY_TLD ('tst'). The proxy is global, but each instance's
    domain is built from — and matched against — its own configured TLD, so a
    custom `tld` in one project never breaks another's domain detection."""
    return (ic or {}).get("tld") or PROXY_TLD


def _distinct_tlds(cfg: dict) -> set:
    """Every TLD in use across instances (for DNS setup); at least {PROXY_TLD}."""
    return {_tld(ic) for ic in resolve_instances(cfg).values()} or {PROXY_TLD}


def _generic_proxy_entries() -> list[dict]:
    """Declared generic runtime routes owned by the aggregate proxy."""
    return [e for e in registry_all().values()
            if e.get("kind") == "compose" and e.get("instance")]


def _generic_tld(entry: dict) -> str:
    return entry.get("tld") or PROXY_TLD


def _lo0_alias_present() -> bool:
    """True if the proxy's loopback alias is already on lo0 — checkable WITHOUT
    sudo, so we can skip an unnecessary `sudo alias-up`.

    On Linux there is no alias to check for: the whole 127.0.0.0/8 range
    already routes to loopback with zero setup (verified live —
    `nc -l 127.0.0.77` accepted connections with no prior `ip addr add` at
    all), so `tools/proxy-helper.sh`'s `alias-up`/`alias-down` are no-ops
    there. Reporting "already present" here skips calling `alias-up` at all,
    which also avoids depending on `ifconfig` — not guaranteed to exist on
    minimal Linux installs (`ip addr` is the modern replacement)."""
    if sys.platform != "darwin":
        return True
    r = subprocess.run(["ifconfig", "lo0"], capture_output=True, text=True)
    return PROXY_BIND_IP in (r.stdout or "")


def _resolver_present(tld: str) -> bool:
    """True if this TLD's wildcard DNS is already configured — checkable
    WITHOUT sudo, so we can skip an unnecessary `sudo dns-up`.

    macOS: /etc/resolver/<tld> (written by dns-up). Linux: our own
    dnsmasq's conf.d/<tld>.conf (tools/proxy-helper.sh's linux_dns_up) — a
    DIFFERENT on-disk marker since Linux has no /etc/resolver/ mechanism at
    all. Existence needs no read permission on either path."""
    if sys.platform != "darwin":
        return Path(f"/etc/sandbox-dnsmasq/conf.d/{tld}.conf").exists()
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
    if not _caddyfile_has_route(domain):
        return False
    return _proxy_container_running()


def _caddyfile_has_route(domain: str, text: str | None = None) -> bool:
    """True if the Caddyfile carries a site block for <domain> (http or https)."""
    if not domain:
        return False
    if text is None:
        if not PROXY_CADDYFILE.exists():
            return False
        text = PROXY_CADDYFILE.read_text()
    return f"http://{domain} {{" in text or f"\n{domain} {{" in text


def _caddyfile_readable_in_container() -> bool | None:
    """True/False if the running proxy can read /etc/caddy/Caddyfile; None when
    the container isn't running (nothing to assert).

    This is the check that would have caught the dangling file bind mount: the
    host file existed and had every route, but inside the container the path was
    gone, so every `caddy reload` failed and new domains silently fell back to
    localhost."""
    if not _proxy_container_running():
        return None
    res = subprocess.run(
        ["docker", "exec", PROXY_PROJECT, "test", "-r", "/etc/caddy/Caddyfile"],
        capture_output=True, text=True)
    return res.returncode == 0


def proxy_health_checks(cfg: dict) -> list[dict]:
    """Doctor checks for domain/proxy drift. Asserts the three views agree:
    domain present in config == route present in the host Caddyfile ==
    Caddyfile readable inside the proxy container. They drifted apart silently
    once (file bind mount pinned to a replaced inode); assert them explicitly."""
    checks: list[dict] = []
    in_sync = total = 0
    running = _proxy_container_running()
    text = PROXY_CADDYFILE.read_text() if PROXY_CADDYFILE.exists() else ""
    readable = _caddyfile_readable_in_container()
    if running:
        checks.append({
            "label": "proxy Caddyfile readable inside the container",
            "ok": bool(readable),
            "hint": ("the bind mount is stale — `./sb domains setup` now "
                     "force-recreates the proxy to repair it"),
        })
    for name, ic in resolve_instances(cfg).items():
        if ic.get("server") == "herd":
            continue
        dom = ic.get("domain")
        has_route = _caddyfile_has_route(dom, text) if dom else False
        if dom and not dom.endswith(f".{_tld(ic)}"):
            continue  # not a proxy-managed domain (legacy Valet etc.)
        if dom:
            in_sync += 1 if has_route else 0
            total += 1
            if not has_route:
                checks.append({
                    "label": f"{name}: domain {dom} configured but no route in the "
                             f"Caddyfile",
                    "ok": False,
                    "hint": "./sb domains setup   (regenerates + reloads the routes)",
                })
        elif ic.get("tld") and _caddyfile_has_route(f"{name}.{_tld(ic)}", text):
            # Half-rolled-back state: the route survived but the domain didn't.
            checks.append({
                "label": f"{name}: orphaned route {name}.{_tld(ic)} with no domain "
                         f"in config",
                "ok": False,
                "hint": "./sb domains setup   (reassigns the domain and reloads)",
            })
    if total:
        # One aggregate line for the healthy majority; drift is listed above.
        checks.append({
            "label": f"instance domains routed ({in_sync}/{total})",
            "ok": in_sync == total,
            "hint": "./sb domains setup   (regenerates + reloads the routes)",
        })
    return checks


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
      # Mount the DIRECTORY, not the Caddyfile itself. A file bind mount pins
      # the host inode; regen_caddyfile() replaces the file atomically (new
      # inode), which leaves the container's mount dangling — /etc/caddy/Caddyfile
      # disappears inside the container and every `caddy reload` fails until the
      # container is recreated. A directory mount resolves the name on each open,
      # so it survives file replacement.
      - {PROXY_DIR}:/etc/caddy:ro
      - {PROXY_CERTS_DIR}:/certs:ro
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
    hosts = [domain, _wildcard_san(domain)] if wildcard else [domain]
    if cert.exists() and key.exists():
        return "\n".join(
            f"""http://{host} {{
    redir https://{{host}}{{uri}} 308
}}

{host} {{
    tls /certs/{cert.name} /certs/{key.name}
    reverse_proxy host.docker.internal:{port} {{
        header_up X-Forwarded-Proto https
        header_up Host {{host}}
    }}
}}
"""
            for host in hosts
        )
    return "\n".join(
        f"""http://{host} {{
    reverse_proxy host.docker.internal:{port} {{
        header_up Host {{host}}
    }}
}}
"""
        for host in hosts
    )


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
    for entry in _generic_proxy_entries():
        dom = entry.get("domain")
        port = entry.get("http_port")
        if dom and port and dom.endswith(f".{_generic_tld(entry)}"):
            blocks.append(_caddy_block(dom, int(port)))
    # Replace atomically. Docker Desktop can retain a stale view of a bind
    # mounted file after in-place truncation; an inode replacement makes the
    # generated config change visible before Caddy reloads/restarts.
    rendered = "\n".join(blocks)
    temporary = PROXY_CADDYFILE.with_name(f".{PROXY_CADDYFILE.name}.tmp")
    temporary.write_text(rendered)
    os.replace(temporary, PROXY_CADDYFILE)


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


def _proxy_compose_up(force_recreate: bool = False):
    """`docker compose up -d` the proxy project. Returns the CompletedProcess."""
    cmd = ["docker", "compose", "-p", PROXY_PROJECT, "-f", str(PROXY_COMPOSE),
           "--project-directory", str(ROOT), "up", "-d"]
    if force_recreate:
        cmd.append("--force-recreate")
    return subprocess.run(cmd, capture_output=True, text=True)


def _run_detail(res) -> str:
    """Shortest useful diagnostic from a CompletedProcess."""
    txt = ((res.stderr or "") + "\n" + (res.stdout or "")).strip()
    return " ".join(txt.split())[:300]


def proxy_apply() -> tuple[bool, str]:
    """Apply the current proxy config; returns (ok, detail) where detail is the
    underlying stderr/stdout when it failed (empty on success).

    Always rewrite the compose from the template; if it changed (e.g. ports, or
    the Caddyfile mount shape) recreate the container. Otherwise hot-reload Caddy
    with the regenerated Caddyfile — UNLESS a cert was re-minted since the proxy
    started, in which case restart the container so Caddy re-reads the changed
    cert files (a plain `caddy reload` won't).

    Self-heal: if the hot reload fails while the container IS running, the mount
    or the running config is stale (the historical case: a file bind mount of the
    Caddyfile dangling after regen replaced the inode). Recreate the container
    once before reporting failure, rather than leaving the whole domain step dead.

    Then self-heal DNS (clear stale *.tst cache) so a new/changed domain resolves
    immediately — no manual flush. Non-interactive, never hangs."""
    desired = render_proxy_compose()
    changed = (not PROXY_COMPOSE.exists()) or PROXY_COMPOSE.read_text() != desired
    if changed:
        PROXY_COMPOSE.write_text(desired)
    hot = _proxy_container_running() and not changed
    if hot:
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
        res = _proxy_compose_up()
    if res.returncode != 0 and hot:
        reload_detail = _run_detail(res)
        res = _proxy_compose_up(force_recreate=True)
        if res.returncode != 0:
            _dns_flush()
            return False, (f"caddy reload failed ({reload_detail}); "
                           f"recreate also failed: {_run_detail(res)}")
    _dns_flush()
    return (res.returncode == 0, "" if res.returncode == 0 else _run_detail(res))


def reload_proxy() -> bool:
    """proxy_apply() reduced to a bool — kept for callers that only branch on
    success. Prefer proxy_apply() when the failure reason should be surfaced."""
    return proxy_apply()[0]


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
    port = inst_cfg.get("http_port", inst_cfg["wordpress_port"])
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


def _site_host(inst_cfg: dict) -> str:
    """Host[:port] of the instance's URL — DOMAIN_CURRENT_SITE must match
    wp_site.domain byte-for-byte, and `wp core multisite-convert` stores the
    siteurl's full netloc INCLUDING the port (e.g. 'localhost:8191')."""
    from urllib.parse import urlparse
    return urlparse(site_url(inst_cfg)).netloc or "localhost"


def _ensure_proxy_up(cfg: dict) -> None:
    """Restore the lo0 alias (dropped on reboot) and start the proxy if it's
    not running. Best-effort, passwordless, silent on success."""
    if _proxy_sudoers_installed():
        subprocess.run(["sudo", "-n", str(PROXY_HELPER), "alias-up"],
                       capture_output=True, text=True)
    if not _proxy_container_running():
        regen_caddyfile(cfg)
        reload_proxy()


def _proxy_sudoers_installed() -> bool:
    """True if the passwordless rule for proxy-helper.sh is installed."""
    try:
        if not PROXY_SUDOERS.exists():
            return False
    except PermissionError:
        # Some VPS images make /etc/sudoers.d searchable only by root. If we
        # cannot even stat the file, treat the proxy helper as unavailable
        # instead of crashing unrelated remote instance creation.
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

    # Linux has its own working implementation now (tools/proxy-helper.sh:
    # a self-managed dnsmasq + /etc/resolv.conf override, live-verified —
    # see docs/cross-platform-support.md §4) — no platform gate needed here
    # anymore. proxy-helper.sh itself declines (exit 3, caught below as a
    # nonzero returncode) on the ONE case that couldn't be verified safely:
    # a symlinked /etc/resolv.conf (systemd-resolved/NetworkManager-managed),
    # or port 53 already held by something that isn't our own dnsmasq — both
    # fall through to the existing "could not set up ... using localhost"
    # message below, same as any other proxy-setup failure.

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
        tmp = RUNTIME_DIR / "sandbox-proxy.sudoers"
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
    up, detail = proxy_apply()
    if not up:
        if _proxy_container_running():
            # The container IS up — this is a config apply failure, not Docker
            # being down. Say so, and show what Caddy actually reported.
            info(f"proxy is running but the config reload failed: "
                 f"{detail or 'no output'}")
        else:
            info(f"proxy container did not start (is Docker running?)"
                 f"{': ' + detail if detail else '.'}")
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

    # 2. mkcert + trust the CA (interactive), and VERIFY the OS really trusts it.
    if shutil.which("mkcert") is None:
        pm, sudo = _pkg_manager()
        # Package + command per manager — verified live against real images,
        # not guessed: `apt-get install mkcert` (Ubuntu 22.04+/Debian 12+,
        # universe repo) needs `libnss3-tools` alongside it for `certutil`
        # (mkcert's own Linux requirement for the OS/browser trust store) —
        # apt does NOT pull it in as a dependency, unlike dnf. `dnf install
        # mkcert` (Fedora) pulls in nss-tools transitively on its own. Arch's
        # `pacman -S mkcert nss` mirrors the original brew command ("mkcert
        # nss") since pacman doesn't auto-pull nss either.
        install_cmd = {
            "brew": ["brew", "install", "mkcert", "nss"],
            "apt": ["sudo", "apt-get", "install", "-y", "mkcert", "libnss3-tools"],
            "dnf": ["sudo", "dnf", "install", "-y", "mkcert"],
            "pacman": ["sudo", "pacman", "-S", "--noconfirm", "mkcert", "nss"],
            "zypper": ["sudo", "zypper", "install", "-y", "mkcert", "mozilla-nss-tools"],
        }.get(pm)
        if install_cmd is None:
            info("no supported package manager found (brew/apt/dnf/pacman/zypper) "
                 "— install mkcert yourself: "
                 "https://github.com/FiloSottile/mkcert#installation")
            return False
        info(f"installing mkcert (+ NSS tools) via {pm}\u2026")
        if subprocess.run(install_cmd).returncode != 0:
            info(f"{pm} install of mkcert failed.")
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
        # Pop BOTH keys written at step 1 — a half rollback (domain gone, tld
        # kept) leaves a block that looks configured, while _build_instance_block
        # only re-adopts a domain that is still present, so no later `sb ensure`
        # ever repairs it.
        local = _local_yaml()
        blk = local.get("instances", {}).get(name, {})
        blk.pop("domain", None)
        blk.pop("tld", None)
        _write_local_yaml(local)
        # Drop the now-orphaned route: _ensure_url_proxy regenerated the Caddyfile
        # WITH this domain before failing, so leaving it there means the file
        # claims a route the config no longer has.
        regen_caddyfile(load_config())
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
    if changed:
        # Wire the new routes BEFORE deciding each site's URL below: site_url()
        # only returns http(s)://<domain> when the proxy already serves a route
        # for it, so without this the freshly-assigned instances would resolve to
        # http://localhost:<port>, that would match their current siteurl, and
        # the loop would skip them — leaving WP pinned to localhost even though
        # the domain is now configured and routed.
        regen_caddyfile(cfg)
        reload_proxy()

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
    _assign_generic_domains(tld)
    return cfg


def _assign_generic_domains(tld=None) -> list[str]:
    """Assign proxy hostnames to generic runtimes without editing app config."""
    try:
        changed = []
        for entry in _generic_proxy_entries():
            chosen_tld = tld or _generic_tld(entry)
            domain = entry.get("domain") or f"{entry['instance']}.{chosen_tld}"
            if not entry.get("domain"):
                changed.append(domain)
            url = f"https://{domain}" if _cert_paths(domain)[0].exists() else f"http://{domain}"
            registry_put(entry["root"], label=entry.get("label", "default"),
                         domain=domain, tld=chosen_tld, url=url)
        return changed
    except Exception:
        return []


def secure_generic_instance(instance: str, *, tld=None) -> tuple[bool, str | None]:
    """Secure a generic route while leaving framework/application config alone."""
    entry = registry_find_instance(instance)
    if not entry or entry.get("kind") != "compose":
        return False, f"generic instance {instance!r} was not found"
    chosen_tld = tld or _generic_tld(entry)
    domain = entry.get("domain") or f"{instance}.{chosen_tld}"
    # A configured proxy is already sufficient for an existing generic route;
    # avoid restarting it before minting the new certificate (which can race
    # Docker Desktop's bind-mounted Caddyfile/cert view). Only run the full
    # setup path when the local DNS/proxy prerequisites are actually absent.
    ready = (_proxy_container_running() and _lo0_alias_present()
             and _resolver_present(chosen_tld))
    up = True if ready else _ensure_url_proxy(load_config(), tld=chosen_tld)[0]
    if not up:
        return False, "clean URL proxy is not available"
    ca_ok = _ca_trusted_macos() if sys.platform == "darwin" else True
    if not (shutil.which("mkcert") and ca_ok):
        return False, "mkcert CA is not installed and trusted"
    if not _mint_cert(domain):
        return False, f"could not mint certificate for {domain}"
    registry_put(entry["root"], label=entry.get("label", "default"),
                 domain=domain, tld=chosen_tld, url=f"https://{domain}")
    regen_caddyfile(load_config())
    if not reload_proxy():
        return False, "proxy reload failed"
    return True, f"https://{domain}"


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
    if sys.platform == "darwin":
        # `launchctl` doesn't exist on Linux at all — calling it there raises
        # FileNotFoundError unconditionally (unlike a nonzero exit code,
        # capture_output doesn't shield a missing executable). The LaunchDaemon
        # is only ever installed on macOS (_install_alias_launchd, gated in
        # _ensure_url_proxy), so this step is meaningless on Linux anyway.
        _sudo(["launchctl", "unload", "-w", str(LAUNCHD_PLIST)],
              reason=_UNINSTALL_REASON, capture_output=True, text=True)
    _sudo(["rm", "-f", str(LAUNCHD_PLIST), str(PROXY_SUDOERS)],
          reason=_UNINSTALL_REASON, capture_output=True, text=True)
    ok("HTTPS proxy torn down (certs left in runtime/proxy/certs — delete "
       "manually if desired).")
