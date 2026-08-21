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
    """Legacy repository-helper sudo authority is never considered usable."""
    return False


def _hosts_edit(action: str, domain: str) -> tuple[bool, str]:
    """Retired: never execute a checkout path through stale sudo policy."""
    return False, ("legacy /etc/hosts mutation is disabled; use the scoped "
                   "resolver service or the per-port fallback")


def revoke_legacy_sudoers(*, interactive: bool = False) -> tuple[bool, str]:
    """Remove only the two known unsafe repository-helper sudoers rules.

    This is an explicit upgrade action with a normal sudo prompt.  It never
    invokes either user-writable helper and never broadens the fixed target set.
    """
    targets = tuple(path for path in (SUDOERS_FILE, PROXY_SUDOERS) if path.exists())
    if not targets:
        return True, "legacy repository-helper sudo authority is absent"
    if not interactive:
        return False, "interactive sudo is required to revoke legacy helper authority"
    result = subprocess.run(["sudo", "rm", "-f", *map(str, targets)],
                            capture_output=True, text=True)
    if result.returncode != 0 or any(path.exists() for path in targets):
        return False, (result.stderr or result.stdout or
                       "legacy sudoers revocation did not complete").strip()
    return True, "legacy repository-helper sudo authority revoked"


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
    return False


def valet_proxy_remove(domain: str) -> None:
    """Remove a Valet proxy (`valet unproxy`), if Valet is available. Same
    interactive/non-interactive handling as valet_proxy_add (it also reloads
    nginx via sudo)."""
    return None


def _valet_proxy_active(domain: str) -> bool:
    """True when Valet currently serves a proxy for this domain. Checked from
    Valet's own site dir so site_url() reflects reality (clean vs per-port)."""
    if not domain:
        return False
    # Valet stores per-site nginx configs as ~/.config/valet/Nginx/<domain>.
    return (Path.home() / ".config" / "valet" / "Nginx" / domain).exists()


def proxy_availability(*, observer=None, docker_path=None,
                       running: bool | None = None, bind_probe=None) -> dict:
    """Return structured exact-endpoint availability for Sandbox Caddy."""
    from sandbox.ingress.listeners import ListenerObserver, SocketBindProbe
    from sandbox.ingress.models import ListenerEndpoint

    docker_path = shutil.which("docker") if docker_path is None else docker_path
    if not docker_path:
        return {"available": False, "reason_code": "docker_binary_unavailable",
                "message": "Docker is not installed.", "conflicts": []}
    running = _proxy_container_running() if running is None else running
    if running:
        return {"available": True, "reason_code": "sandbox_proxy_owned",
                "message": "Sandbox Caddy already owns the ingress endpoints.",
                "conflicts": []}
    observer = observer or ListenerObserver(
        platform="darwin" if sys.platform == "darwin" else "linux",
        process=__import__("sandbox.services", fromlist=["BoundedProcessRunner"]).BoundedProcessRunner(),
    )
    requested = (
        ListenerEndpoint(PROXY_BIND_IP, 80),
        ListenerEndpoint(PROXY_BIND_IP, 443),
    )
    probe = bind_probe or SocketBindProbe()
    probe_results = tuple(probe.check(endpoint) for endpoint in requested)
    if all(result == "free" for result in probe_results):
        return {"available": True, "reason_code": "endpoints_free",
                "message": "Sandbox ingress endpoints are free.", "conflicts": []}
    snapshot = observer.snapshot()
    conflicts = tuple(
        listener for listener in snapshot
        if any(listener.overlaps(endpoint) for endpoint in requested)
    )
    if conflicts or "conflict" in probe_results:
        rendered = ", ".join(
            f"{item.address}:{item.port}" for item in conflicts
        ) or f"{PROXY_BIND_IP}:80/443"
        return {"available": False, "reason_code": "listener_conflict",
                "message": f"Another listener overlaps Sandbox ingress: {rendered}.",
                "conflicts": [item.to_dict() for item in conflicts]}
    return {"available": True, "reason_code": "endpoints_free",
            "message": "Sandbox ingress endpoints are free.", "conflicts": []}


def proxy_available() -> bool:
    """Compatibility boolean backed by kernel listener truth."""
    return bool(proxy_availability()["available"])


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
    if running:
        checks.append(_published_listener_check())
    return checks


def proxy_endpoint_owned(address: str, port: int) -> bool:
    """True when the running Sandbox proxy publishes `address:port` itself.

    Listener evidence alone cannot tell: Docker publishes the port, so the
    process holding it is the container runtime's helper, not Caddy. The
    authoritative signal is the proxy project's own port mapping, so read that.
    """
    if str(address) != PROXY_BIND_IP or int(port) not in (80, 443):
        return False
    if not _proxy_container_running():
        return False
    res = subprocess.run(
        ["docker", "compose", "-p", PROXY_PROJECT, "-f", str(PROXY_COMPOSE),
         "--project-directory", str(ROOT), "port", "proxy", str(int(port))],
        capture_output=True, text=True)
    published = (res.stdout or "").strip()
    return res.returncode == 0 and published.endswith(f":{int(port)}")


def _published_listener_check(*, connector=None, listeners=None) -> dict:
    """Assert the proxy's published endpoints actually accept connections.

    Docker reporting `127.0.0.77:80->80/tcp` is not proof that anything listens:
    a container runtime can widen a published loopback-alias bind to a wildcard
    one, which then loses to whatever already owns that port (observed live:
    OrbStack with `docker.expose_ports_to_lan` versus Herd's nginx on
    `127.0.0.1:80`). The clean URL then fails with a bare connection refusal
    while every other view looks healthy. Name the owner instead (037 FR-034).
    """
    import socket

    def _connect(address: str, port: int) -> bool:
        try:
            with socket.create_connection((address, port), timeout=1.5):
                return True
        except OSError:
            return False

    probe = connector or _connect
    dead = [port for port in (80, 443) if not probe(PROXY_BIND_IP, port)]
    if not dead:
        return {"label": f"proxy endpoints accepting on {PROXY_BIND_IP}:80,443",
                "ok": True, "hint": ""}
    owners = _port_owners(dead) if listeners is None else listeners
    detail = "; ".join(f"{port} held by {owner}" for port, owner in sorted(owners.items())) \
        or "no other listener identified"
    if owners:
        hint = ("free the port (stop the owning service), or select an adopted "
                "ingress with `./sb domains use <provider>`; per-port URLs keep "
                "working meanwhile")
    elif not _lo0_alias_present():
        # No listener is attributable, and the required loopback address is
        # absent. The supported setup command restores that host prerequisite;
        # doctor itself only observes it and must not start proxy lifecycle work.
        hint = (f"the {PROXY_BIND_IP} loopback alias is missing; run "
                "`./sb domains setup` to restore the required host setup; "
                "per-port URLs keep working meanwhile")
    else:
        # There is no evidence that another process owns the endpoint, so do not
        # tell the operator to stop an arbitrary service.  The supported recovery
        # asks the proxy lifecycle to restore its alias and published endpoints.
        hint = ("no owning listener identified; run `./sb domains up` to restore "
                "the proxy's published endpoints; per-port URLs keep working "
                "meanwhile")
    return {
        "label": (f"proxy published on {PROXY_BIND_IP}:{','.join(map(str, dead))} "
                  f"but nothing accepts there ({detail})"),
        "ok": False,
        "hint": hint,
    }


def _port_owners(ports: list[int]) -> dict:
    """Best-effort {port: 'process (address)'} for host listeners on `ports`."""
    owners: dict[int, str] = {}
    try:
        res = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return owners
    for line in (res.stdout or "").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 9:
            continue
        endpoint = fields[8]
        _, _, port_text = endpoint.rpartition(":")
        if not port_text.isdigit():
            continue
        port = int(port_text)
        if port in ports and port not in owners:
            owners[port] = f"{fields[0]} ({endpoint})"
    return owners


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


def _valid_alias(host: str) -> str | None:
    """One normalized alias hostname, or None when it is not usable as one.

    Stricter than `_valid_domain`: an alias is a bare hostname that Caddy can
    match and mkcert can put in a SAN, so a scheme, a port, a path, or a
    wildcard is rejected rather than silently truncated. Returns None instead
    of dying — the caller decides whether a bad entry is fatal (config write)
    or skippable (Caddyfile render, which runs on every `sb` invocation)."""
    host = (host or "").strip().lower().rstrip(".")
    if not host or host.startswith("*.") or "://" in host or "/" in host or ":" in host:
        return None
    if not DOMAIN_RE.match(host) or len(host) > 253:
        return None
    return host


def normalize_aliases(value, primary: str | None = None,
                      strict: bool = False) -> list[str]:
    """Extra hostnames one instance answers on, de-duplicated and ordered.

    The instance's own domain is filtered out — a site is not an alias of
    itself, and emitting it twice would give Caddy a duplicate site address.
    `strict` (config write path) dies on a bad entry so a typo surfaces at
    `sb apply`; lenient (render path) drops it, because a single malformed
    alias must never make every later `sb` command unusable."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        if strict:
            die("`aliases` must be a list of hostnames")
        return []
    primary_norm = (primary or "").strip().lower().rstrip(".")
    out: list[str] = []
    for raw in value:
        # A blank entry is not a typo — it is how a caller says "declared, and
        # empty" (`--alias ""`, an MCP `aliases=[]`) to override an inherited
        # project declaration. Skip it in both modes rather than dying.
        if isinstance(raw, str) and not raw.strip():
            continue
        host = _valid_alias(raw) if isinstance(raw, str) else None
        if host is None:
            if strict:
                die(f"invalid alias {raw!r}. Use a bare hostname like "
                    "cdn.example.com — no scheme, port, path, or wildcard.")
            continue
        if host == primary_norm or host in out:
            continue
        out.append(host)
    return out


def instance_aliases(inst_cfg: dict, strict: bool = False) -> list[str]:
    """The alias hostnames an instance serves, resolved from its config block.

    Empty for multisite: a network already maps hostnames to sites through
    wp_site.domain, so a second name for site 1 would fight that mapping
    rather than extend it. Empty for herd, whose sites are served by Herd at
    <name>.test and never routed through the sandbox proxy."""
    if _multisite_mode(inst_cfg) or inst_cfg.get("server") == "herd":
        return []
    return normalize_aliases(inst_cfg.get("aliases"),
                             primary=inst_cfg.get("domain"), strict=strict)


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


def _caddy_block(domain: str, port: int, wildcard: bool = False,
                 cert_domain: str | None = None) -> str:
    """One Caddy site block. Default is plain http://<domain> (no port, no cert
    — zero CA-trust fragility, browsers never warn on http). If this domain has
    been secured (a mkcert cert exists), serve https + bounce http→https.

    When `wildcard` is set (subdomain multisite), the site address list also
    includes `*.<domain>` so every sub-site host (sub1.<domain>) reverse-
    proxies to the same instance port. dnsmasq already wildcards `.tst`, and
    the secured cert carries a matching `*.<domain>` SAN (see _mint_cert).

    `cert_domain` serves `domain` under ANOTHER domain's certificate. An
    instance alias is minted as a SAN on the instance's own cert (one cert per
    instance, keyed by its primary domain), so the alias block has to read the
    primary's cert files or it would fall back to http while https is live."""
    cert, key = _cert_paths(cert_domain or domain)
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
        routed = bool(dom and dom.endswith(f".{_tld(ic)}"))
        port = ic.get("wordpress_port")
        if routed:
            # No cert minting here — default is plain http. _caddy_block emits an
            # https block only if a cert already exists (i.e. `./sb secure` ran).
            # Subdomain multisite also needs a wildcard `*.<name>.tst` block so each
            # sub-site host proxies to the same port.
            wildcard = _multisite_mode(ic) == "subdomain"
            blocks.append(_caddy_block(dom, ic["wordpress_port"], wildcard=wildcard))
        # Declared aliases get their own site block on the same port. They are
        # emitted even when the instance has no routed .tst domain: the proxy
        # matches on Host, so an alias resolved through /etc/hosts or real DNS
        # still reaches the instance. Resolution is the operator's to arrange —
        # only .tst names are wildcarded by the sandbox resolver.
        if port:
            for alias in instance_aliases(ic):
                blocks.append(_caddy_block(alias, port,
                                           cert_domain=dom if routed else None))
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
    """Legacy no-password DNS flushing is retired.

    Scoped resolver adapters perform their own bounded reload/verification.
    Keeping this compatibility hook as a no-op prevents an old registry path
    from invoking the user-writable proxy helper with root authority.
    """
    return None


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
    verified = inst_cfg.get("url")
    if isinstance(verified, str) and verified.startswith(("http://", "https://")):
        return verified
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


def clean_url_compatibility_handoff(cfg: dict, instance: str, *,
                                    interactive: bool = False,
                                    protocols=("http",), service_factory=None) -> dict:
    """Offer one legacy instance to the composed A → B → A clean-URL path.

    This is a compatibility facade, not a second route implementation: ingress
    owns route activation, DomainService owns DNS, and a declined/unproven
    selection returns the existing per-port URL without invoking proxy helpers.
    Legacy callers can retain their established proxy rollback path when the
    composed service is unavailable or returns a fallback result.
    """
    owner = registry_find_instance(instance) or {}
    root = owner.get("root")
    if not root:
        return {"ok": False, "state": "fallback", "mutated": False,
                "reason": {"code": "project_owner_unavailable"}}
    instance_config = resolve_instances(cfg).get(instance) or {}
    port = (instance_config.get("wordpress_port") or owner.get("wordpress_port")
            or owner.get("http_port"))
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return {"ok": False, "state": "fallback", "mutated": False,
                "reason": {"code": "backend_unavailable"}}
    fallback = owner.get("url") or f"http://localhost:{port}"
    if service_factory is None:
        from sandbox.application.context import clean_url_service
        service_factory = clean_url_service
    try:
        result = service_factory(cfg).apply(
            root, label=owner.get("label", "default"),
            backend={"address": "127.0.0.1", "port": port},
            protocols=tuple(protocols),
            capabilities=("wildcard",) if _multisite_mode(instance_config) == "subdomain" else (),
            interactive=interactive, fallback_url=fallback,
        )
    except Exception as exc:
        if not isinstance(exc, (OSError, RuntimeError, TypeError, ValueError)) \
                and exc.__class__.__name__ != "ConfigError":
            raise
        return {"ok": False, "state": "fallback", "mutated": False,
                "fallback_url": fallback,
                "reason": {"code": "compatibility_handoff_unavailable",
                           "message": str(exc)}}
    if not isinstance(result, dict):
        return {"ok": False, "state": "fallback", "mutated": False,
                "fallback_url": fallback,
                "reason": {"code": "compatibility_handoff_invalid"}}
    return {"fallback_url": fallback, **result}


def _compatibility_targets(cfg: dict) -> tuple[str, ...]:
    """Return registered local instances once, without reading registry state files."""
    names = list(resolve_instances(cfg))
    names.extend(
        entry.get("instance") for entry in _generic_proxy_entries()
        if entry.get("instance")
    )
    return tuple(dict.fromkeys(
        name for name in names
        if (registry_find_instance(name) or {}).get("root")
    ))


def _result_dict(result) -> dict:
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return {"ok": False, "state": "invalid", "mutated": False,
            "reason": {"code": "compatibility_result_invalid"}}


def _cleanup_composed_owner(cfg: dict, owner: dict, *,
                            ingress_factory=None, domain_factory=None) -> dict:
    """Remove only attributable application-service state for one owner."""
    if ingress_factory is None or domain_factory is None:
        from sandbox.application.context import domain_service, ingress_service
        ingress_factory = ingress_factory or ingress_service
        domain_factory = domain_factory or domain_service
    root, label = owner.get("root"), owner.get("label", "default")
    if not root:
        return {"ok": False, "state": "cleanup_incomplete", "mutated": False,
                "owner": None, "ingress": {}, "domains": {},
                "reason": {"code": "project_owner_unavailable"}}
    owner_id = f"{Path(root).expanduser().resolve()}::{label}"
    try:
        ingress = _result_dict(ingress_factory(cfg).cleanup_owner(owner_id))
    except Exception as exc:
        if not isinstance(exc, (OSError, RuntimeError, TypeError, ValueError)) \
                and exc.__class__.__name__ != "ConfigError":
            raise
        ingress = {"ok": False, "state": "cleanup_incomplete", "mutated": False,
                   "reason": {"code": "ingress_cleanup_unavailable",
                              "message": str(exc)}}
    try:
        domains = _result_dict(domain_factory(cfg).cleanup(
            root, label=label, interactive=False,
        ))
    except Exception as exc:
        if not isinstance(exc, (OSError, RuntimeError, TypeError, ValueError)) \
                and exc.__class__.__name__ != "ConfigError":
            raise
        domains = {"ok": False, "state": "cleanup_incomplete", "mutated": False,
                   "reason": {"code": "domain_cleanup_unavailable",
                              "message": str(exc)}}
    complete = bool(ingress.get("ok") and domains.get("ok"))
    return {"ok": complete,
            "state": "ready" if complete else "cleanup_incomplete",
            "mutated": bool(ingress.get("mutated") or domains.get("mutated")),
            "owner": owner_id, "ingress": ingress, "domains": domains}


def clean_url_lifecycle_handoff(cfg: dict, action: str, *,
                                interactive: bool = False,
                                protocols=("http",), service_factory=None,
                                ingress_factory=None, domain_factory=None) -> dict:
    """Delegate legacy setup/up/down/teardown through application services.

    Setup and up are batch-atomic from the compatibility caller's perspective.
    If one owner cannot adopt, only state newly created by this attempt is
    rolled back before the caller is allowed to use the legacy proxy. Down and
    teardown clean application-owned state first and report incomplete cleanup
    rather than concealing it behind a successful legacy command.
    """
    if action not in {"setup", "up", "down", "teardown"}:
        raise ValueError("unsupported clean URL compatibility action")
    targets = _compatibility_targets(cfg)
    if not targets:
        return {"ok": False, "state": "fallback", "mutated": False,
                "action": action, "results": [], "rollback": {"complete": True},
                "safe_to_fallback": True,
                "reason": {"code": "no_registered_clean_url_targets"}}

    if action in {"down", "teardown"}:
        results = []
        for name in targets:
            owner = registry_find_instance(name) or {}
            results.append({"instance": name, **_cleanup_composed_owner(
                cfg, owner, ingress_factory=ingress_factory,
                domain_factory=domain_factory,
            )})
        complete = all(item["ok"] for item in results)
        return {"ok": complete,
                "state": "ready" if complete else "cleanup_incomplete",
                "mutated": any(item["mutated"] for item in results),
                "action": action, "results": results,
                "rollback": {"complete": complete},
                "safe_to_fallback": complete,
                "reason": {"code": "cleanup_complete" if complete
                           else "cleanup_incomplete"}}

    attempted = []
    for name in targets:
        result = clean_url_compatibility_handoff(
            cfg, name, interactive=interactive, protocols=protocols,
            service_factory=service_factory,
        )
        attempted.append({"instance": name, "protocols": tuple(protocols), **result})
        if result.get("ok"):
            continue
        rollback = []
        for prior in attempted:
            if not prior.get("mutated"):
                continue
            owner = registry_find_instance(prior["instance"]) or {}
            rollback.append({"instance": prior["instance"],
                             **_cleanup_composed_owner(
                                 cfg, owner, ingress_factory=ingress_factory,
                                 domain_factory=domain_factory,
                             )})
        complete = all(item["ok"] for item in rollback)
        return {"ok": False,
                "state": "fallback" if complete else "rollback_incomplete",
                "mutated": any(item.get("mutated") for item in attempted),
                "action": action, "results": attempted,
                "rollback": {"complete": complete, "results": rollback},
                "safe_to_fallback": complete,
                "reason": {"code": result.get("reason", {}).get(
                    "code", "composed_clean_url_unavailable")}}
    return {"ok": True, "state": "ready",
            "mutated": any(item.get("mutated") for item in attempted),
            "action": action, "results": attempted,
            "rollback": {"complete": True}, "safe_to_fallback": False,
            "reason": {"code": "composed_clean_urls_ready"}}


def _persist_composed_clean_urls(cfg: dict, lifecycle: dict) -> dict:
    """Persist verified names only after the entire composed batch succeeds."""
    local = _local_yaml()
    instances = local.setdefault("instances", {})
    changed = False
    for item in lifecycle.get("results", ()):
        hostname = item.get("hostname")
        owner = registry_find_instance(item.get("instance")) or {}
        if not (isinstance(hostname, str) and hostname and owner.get("root")):
            continue
        protocols = tuple(item.get("protocols") or ())
        scheme = "https" if "https" in protocols else "http"
        verified_url = item.get("url") or f"{scheme}://{hostname}"
        block = instances.setdefault(item["instance"], {})
        desired = {"domain": hostname, "tld": hostname.rsplit(".", 1)[-1],
                   "url": verified_url}
        if any(block.get(key) != value for key, value in desired.items()):
            block.update(desired); changed = True
        registry_put(owner["root"], label=owner.get("label", "default"),
                     domain=hostname, url=verified_url)
        item["url"] = verified_url
    refreshed = cfg
    if changed:
        _write_local_yaml(local)
        try:
            refreshed = load_config()
        except Exception as exc:
            if not isinstance(exc, (OSError, RuntimeError, TypeError, ValueError)) \
                    and exc.__class__.__name__ != "ConfigError":
                raise
    resolved = resolve_instances(refreshed)
    for item in lifecycle.get("results", ()):
        name, verified_url = item.get("instance"), item.get("url")
        if name not in resolved or not verified_url or not _instance_running(name):
            continue
        if verified_url.startswith("https://"):
            _write_ssl_muplugin(name)
        wpcli(["option", "update", "siteurl", verified_url],
              instance=name, check=False)
        wpcli(["option", "update", "home", verified_url],
              instance=name, check=False)
    return refreshed


def _site_host(inst_cfg: dict) -> str:
    """Host[:port] of the instance's URL — DOMAIN_CURRENT_SITE must match
    wp_site.domain byte-for-byte, and `wp core multisite-convert` stores the
    siteurl's full netloc INCLUDING the port (e.g. 'localhost:8191')."""
    from urllib.parse import urlparse
    return urlparse(site_url(inst_cfg)).netloc or "localhost"


def _ensure_proxy_up(cfg: dict) -> None:
    """Restart only an already-provisioned legacy proxy without root actions."""
    if (_lo0_alias_present() and all(_resolver_present(tld)
                                    for tld in _distinct_tlds(cfg))
            and not _proxy_container_running()):
        regen_caddyfile(cfg)
        reload_proxy()


def _proxy_sudoers_installed() -> bool:
    """True when the root-owned helper + its scoped NOPASSWD rule are ready.

    Only the installed copy at PROXY_HELPER_INSTALLED is ever trusted; the old
    rule pointing at the writable checkout is revoked, never probed.
    """
    if not PROXY_HELPER_INSTALLED.exists():
        return False
    res = subprocess.run(["sudo", "-n", str(PROXY_HELPER_INSTALLED),
                          "installed-status"], capture_output=True, text=True)
    return res.returncode == 0 and (res.stdout or "").strip() == "ready"


def clean_url_selection(cfg: dict | None = None):
    """Effective clean-URL provider, delegated to the composed application seam.

    This facade only gathers the configuration layers; the decision itself lives
    in `sandbox.application.clean_url_provider` (037 T043, 038 T033). Keeping the
    facade means the default provider stays a working rollback control.
    """
    from sandbox.application.clean_url_provider import resolve_provider

    return resolve_provider(env=os.environ, machine=_machine_domains_block(),
                            project=cfg or {})


def clean_url_mode(cfg: dict | None = None) -> str:
    """The effective provider id — `sandbox-caddy` unless adoption was selected."""
    return clean_url_selection(cfg).provider


def _machine_domains_block() -> dict:
    """`domains:` from sandbox.local.yml, without forcing the PyYAML bootstrap."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        return {}
    block = (_local_yaml() or {}).get("domains")
    return block if isinstance(block, dict) else {}


def _adoption_selected(cfg: dict | None = None) -> bool:
    """True only when the user explicitly opted out of the default provider."""
    return clean_url_selection(cfg).adoption


def clean_url_setup(cfg: dict, *, tld=None, interactive: bool = False) -> dict:
    """Set up clean URLs with the selected provider.

    Default is the Docker/Caddy stack (`_ensure_url_proxy`). Host-incumbent
    adoption runs only when the user selected it (specs 037 FR-031, 038 FR-030).
    """
    if _adoption_selected(cfg):
        lifecycle = clean_url_lifecycle_handoff(
            cfg, "setup", interactive=interactive, protocols=("http",),
        )
        if lifecycle["ok"]:
            refreshed = _persist_composed_clean_urls(cfg, lifecycle)
            return {**lifecycle, "mode": "application", "cfg": refreshed}
        return {**lifecycle, "mode": "application", "cfg": cfg,
                "safe_to_fallback": True,
                "reason": lifecycle.get("reason") or {
                    "code": "composed_clean_url_unavailable"}}
    up, refreshed = _ensure_url_proxy(cfg, tld=tld, interactive=interactive)
    if up:
        return {"ok": True, "state": "ready", "mutated": True,
                "mode": "sandbox-caddy", "cfg": refreshed,
                "safe_to_fallback": True,
                "reason": {"code": "sandbox_caddy_ready"}}
    return {"ok": False, "state": "fallback", "mutated": False,
            "mode": "sandbox-caddy", "cfg": refreshed,
            "safe_to_fallback": True,
            "reason": {"code": "sandbox_caddy_unavailable",
                       "message": "the default Docker/Caddy clean-URL stack "
                                  "could not be provisioned; the per-port URL "
                                  "remains available"}}


def _ensure_url_proxy(cfg, *, quiet: bool = False, tld=None,
                      interactive: bool | None = None):
    """Ensure the DEFAULT clean-URL stack is up: the scoped NOPASSWD rule for
    the root-owned helper, the lo0 alias, dnsmasq/resolver for each configured
    TLD, the boot LaunchDaemon, and the running Caddy container. Plain
    `http://<name>.<tld>`, no certificates. One interactive sudo the first time
    (helper install); password-free after that. Returns (ok, cfg).

    This is the product default (specs 037 FR-007/FR-033, 038 FR-029/FR-032).
    Do not stub it out: incumbent adoption is the opt-in alternative, and
    disabling this path counts as removal under constitution principle VI.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()
    if shutil.which("docker") is None:
        if not quiet:
            info("Docker not found — clean URLs need it. Using localhost:<port>.")
        return False, cfg

    # 1. Root-owned helper + scoped NOPASSWD rule. One interactive sudo, once.
    #    The rule names /usr/local/libexec/sandbox-proxy-helper only, so the
    #    writable checkout is never a privileged target.
    if not _proxy_sudoers_installed():
        if not interactive:
            if not quiet:
                info("clean URLs need a one-time setup (a password) — run "
                     "`./sb domains setup` in your terminal. Using localhost.")
            return False, cfg
        revoke_legacy_sudoers(interactive=True)
        info("One-time setup for clean http://<name>.<tld> URLs — your password "
             "ONCE (no certificate, no browser warning).")
        installed = subprocess.run(["sudo", str(PROXY_HELPER), "install"],
                                   capture_output=True, text=True)
        if installed.returncode != 0 or not _proxy_sudoers_installed():
            info("clean-URL helper installation failed: "
                 f"{(installed.stderr or installed.stdout or '').strip()[:200]}")
            return False, cfg
        ok("clean-URL host actions are now password-free.")

    # 2. lo0 alias + dnsmasq/resolver per TLD. Only sudo for what is MISSING:
    #    alias and resolver persist, so ordinary ensure needs no prompt — that
    #    is what lets secure-at-create work from the MCP server's subprocess.
    ok_all = True
    if not _lo0_alias_present():
        ok_all = subprocess.run(
            ["sudo", "-n", str(PROXY_HELPER_INSTALLED), "alias-up"],
            capture_output=True, text=True).returncode == 0
    tlds = _distinct_tlds(cfg) | ({tld} if tld else set())
    for t in tlds:
        if not _resolver_present(t):
            r = subprocess.run(
                ["sudo", "-n", str(PROXY_HELPER_INSTALLED), "dns-up", t],
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
            info(f"proxy is running but the config reload failed: "
                 f"{detail or 'no output'}")
        else:
            availability = proxy_availability(running=False)
            if availability["reason_code"] == "listener_conflict":
                info(f"proxy container did not start: {availability['message']} "
                     "The localhost:<port> URL remains available. Select an "
                     "adopted ingress with `./sb domains use <adapter>` if the "
                     "owner is another dev tool.")
            else:
                info(f"proxy startup failed"
                     f"{': ' + detail if detail else '.'} "
                     "The localhost:<port> URL remains available.")
        return False, cfg
    return True, cfg


def proxy_setup(cfg, tld=None) -> bool:
    """OPT-IN: upgrade clean URLs to trusted HTTPS (https://<name>.<tld>). This is
    `./sb secure` / `./sb domains setup`. It first ensures the HTTP URL proxy is
    up (_ensure_url_proxy), then installs + trusts the mkcert CA and mints a cert
    per proxy instance, switching them to https. Interactive (password once for the
    CA). `tld` overrides the per-project default for newly-assigned domains.
    The DEFAULT install path does NOT call this — plain http needs no cert."""
    if _adoption_selected(cfg):
        lifecycle = clean_url_lifecycle_handoff(
            cfg, "setup", interactive=sys.stdin.isatty(), protocols=("https",),
        )
        if lifecycle["ok"]:
            _persist_composed_clean_urls(cfg, lifecycle)
            return True
        info("the selected adopted ingress could not provide HTTPS; preserved "
             "the existing route and per-port fallback")
        return False

    # 1. Ensure the base HTTP proxy infra (helper, alias, dnsmasq, container).
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
            sans = [_wildcard_san(dom)] if _multisite_mode(ic) == "subdomain" else []
            # Aliases ride on the instance's own cert as extra SANs — one cert
            # per instance, so `sb secure` covers every name it answers on.
            sans += instance_aliases(ic)
            _mint_cert(dom, extra_sans=sans or None)
    regen_caddyfile(cfg)
    if not reload_proxy():
        info("proxy reload failed (is Docker running?).")
        return False
    # Re-run the URL pass now that the certs exist and the https routes are
    # live: the first pass above ran BEFORE minting, so site_url() still
    # resolved to http and WP was left one scheme behind the route it is
    # actually served on (browser gets a 308 to https while WP believes http).
    _assign_domains_to_all(cfg, tld)
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
    if _adoption_selected(cfg):
        handoff = clean_url_compatibility_handoff(cfg, name, protocols=("https",))
        hostname = handoff.get("hostname") if handoff.get("ok") else None
        if isinstance(hostname, str) and hostname:
            verified_url = handoff.get("url") or f"https://{hostname}"
            local = _local_yaml()
            block = local.setdefault("instances", {}).setdefault(name, {})
            block.update({"domain": hostname, "tld": hostname.rsplit(".", 1)[-1],
                          "url": verified_url})
            _write_local_yaml(local)
            owner = registry_find_instance(name) or {}
            if owner.get("root"):
                registry_put(owner["root"], label=owner.get("label", "default"),
                             domain=hostname, url=verified_url)
            return True
        # The selected adopted ingress created no owned route: keep localhost
        # rather than silently falling back to the default provider the user
        # opted out of.
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
    sans = [_wildcard_san(domain)] if _multisite_mode(ic) == "subdomain" else []
    sans += instance_aliases(ic)
    _mint_cert(domain, extra_sans=sans or None)
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
    return False, ("generic HTTPS requires a proof-scoped ingress/resolver route; "
                   "the unreceipted aggregate proxy is retired")
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


def proxy_up(cfg: dict) -> bool:
    """Start the selected clean-URL ingress.

    Default provider: re-render and (re)start the Caddy proxy, recreating the
    container so a host publish that was lost — e.g. the loopback alias appeared
    after the container did — is re-established. Adoption mode restores composed
    routes instead.
    """
    if _adoption_selected(cfg):
        instances = tuple(resolve_instances(cfg).values())
        secure = any(
            str(item.get("url") or "").startswith("https://")
            or (item.get("domain") and _cert_paths(item["domain"])[0].exists())
            for item in instances
        )
        lifecycle = clean_url_lifecycle_handoff(
            cfg, "up", protocols=("https",) if secure else ("http",),
        )
        if lifecycle["ok"]:
            _persist_composed_clean_urls(cfg, lifecycle)
            return True
        info("the selected adopted ingress is unavailable; per-port URLs remain")
        return False
    PROXY_COMPOSE.write_text(render_proxy_compose())
    regen_caddyfile(cfg)
    res = _proxy_compose_up(force_recreate=True)
    up, detail = (res.returncode == 0), _run_detail(res)
    if up:
        up, detail = proxy_apply()
    if not up:
        info(f"clean URL ingress did not start{': ' + detail if detail else '.'}")
    return up


def proxy_down(cfg: dict) -> bool:
    """Stop the selected clean-URL ingress."""
    if _adoption_selected(cfg):
        lifecycle = clean_url_lifecycle_handoff(cfg, "down")
        return lifecycle["ok"] or lifecycle.get("reason", {}).get("code") == \
            "no_registered_clean_url_targets"
    res = subprocess.run(["docker", "compose", "-p", PROXY_PROJECT, "-f",
                          str(PROXY_COMPOSE), "--project-directory", str(ROOT),
                          "stop"], capture_output=True, text=True)
    return res.returncode == 0


def proxy_teardown(cfg) -> bool:
    """Reverse the selected provider's setup.

    Adoption mode removes receipt-owned composed state only. Default mode stops
    the proxy, removes the resolver/dnsmasq entries and the loopback alias
    through the installed helper, untrusts the CA, and drops the boot item and
    the scoped sudoers rule. Every step is best-effort so a partial state still
    cleans up, and certificates are left on disk for manual recovery.
    """
    if _adoption_selected(cfg):
        lifecycle = clean_url_lifecycle_handoff(cfg, "teardown")
        if not lifecycle["ok"] and not lifecycle.get("safe_to_fallback"):
            info("adopted clean-URL cleanup is incomplete; nothing else was removed")
            return False
        revoke_legacy_sudoers(interactive=sys.stdin.isatty())
        ok("receipt-owned clean URLs removed; unreceipted routes and certs "
           "were preserved for manual recovery")
        return True

    subprocess.run(["docker", "compose", "-p", PROXY_PROJECT, "-f",
                    str(PROXY_COMPOSE), "--project-directory", str(ROOT),
                    "down"], capture_output=True, text=True)
    if _proxy_sudoers_installed():
        for tld in _distinct_tlds(cfg):
            subprocess.run(["sudo", "-n", str(PROXY_HELPER_INSTALLED),
                            "dns-down", tld], capture_output=True, text=True)
        subprocess.run(["sudo", "-n", str(PROXY_HELPER_INSTALLED), "alias-down"],
                       capture_output=True, text=True)
    if shutil.which("mkcert"):
        subprocess.run(["mkcert", "-uninstall"], capture_output=True, text=True)
    _UNINSTALL_REASON = (
        "Sandbox is cleaning up its clean-URL setup — removing the startup item "
        "and the local DNS rule it added. Your Mac password confirms this final "
        "step.")
    if sys.platform == "darwin":
        # launchctl does not exist on Linux, and the boot item is only ever
        # installed on macOS (see _install_alias_launchd).
        _sudo(["launchctl", "unload", "-w", str(LAUNCHD_PLIST)],
              reason=_UNINSTALL_REASON, capture_output=True, text=True)
    _sudo(["rm", "-f", str(LAUNCHD_PLIST), str(PROXY_SUDOERS),
           str(proxy_sudoers_scoped()), str(PROXY_HELPER_INSTALLED)],
          reason=_UNINSTALL_REASON, capture_output=True, text=True)
    ok("clean-URL provider torn down (certs left in runtime/proxy/certs — "
       "delete manually if desired).")
    return True
