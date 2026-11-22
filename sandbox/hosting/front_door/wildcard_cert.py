"""Shared wildcard DNS and certificate for DNS-only ACME hosting (``cloudflare.dns: wildcard``).

A target that declares ``cloudflare: {proxied: false, tls: acme, dns: wildcard}``
writes no DNS record of its own and asks for no certificate of its own. Instead
the remote serves every first-level name under the zone from:

- one DNS-only ``*.<zone>`` A record (and AAAA when the remote has IPv6) that
  points at the remote's origin; exactly one identical record per type is
  adopted, anything else (another address, a second row, a proxied record, a
  CNAME, an AAAA when the remote has no IPv6) is refused and never changed. It
  is written only after the certificate is installed;
- one ``*.<zone>`` Let's Encrypt certificate obtained with a DNS-01 challenge
  by the pinned ``lego`` broker (the tailnet-name machinery: the Cloudflare
  token reaches only the broker's use profile). The key is generated on the
  remote, root 0600, and never leaves it; the controller sees only the CSR and
  the issued chain.

Every apply of a wildcard target calls :func:`ensure`, which reissues the
certificate when it has fewer than ``RENEW_DAYS`` days left, so the shared
certificate renews as long as any wildcard target is applied in that window.
``sb remote edge`` reports an overdue certificate like any other.

Nothing here deletes the shared record or certificate: a target's destroy and
the reaper remove only the target's own route, and ``sb remote edge
--remove-route`` refuses the shared directory. The shared record is zone-wide
production DNS; it is created once and never removed by Sandbox.
"""
from __future__ import annotations

import shlex
from typing import Any, Callable

from sandbox.hosting.front_door import FrontDoorError, exact_read, nginx

RENEW_DAYS = 30
CERT_PREFIX = "wildcard-"

WILDCARD_DNS_CONFLICT = "wildcard_dns_conflict"
WILDCARD_CERTIFICATE_FAILED = "wildcard_certificate_failed"
WILDCARD_NAME_INELIGIBLE = "wildcard_name_ineligible"
WILDCARD_ISSUER_UNAVAILABLE = "wildcard_issuer_unavailable"
# lego may hold the broker for up to its 900 s budget; wait that long, re-checking
# the remote each time so a certificate another apply installed meanwhile is used.
BUSY_ATTEMPTS = 32
BUSY_WAIT_SECONDS = 30.0
_KEY_MISMATCH_EXIT = 76


def normalize_zone(zone: str) -> str:
    clean = str(zone or "").strip().rstrip(".").lower()
    labels = clean.split(".")
    if len(labels) < 2 or not all(nginx._NAME_RE.fullmatch(label) and "." not in label
                                  for label in labels):
        raise FrontDoorError(WILDCARD_NAME_INELIGIBLE, f"invalid zone {zone!r}")
    return clean


def zone_of(hostname: str) -> str:
    """The zone a first-level name sits directly under (``a.example.com`` -> ``example.com``)."""
    label, _, rest = str(hostname).partition(".")
    if not label or label.startswith("*") or rest.count(".") < 1:
        raise FrontDoorError(
            WILDCARD_NAME_INELIGIBLE,
            f"{hostname} is not a first-level name under a zone; cloudflare.dns: wildcard "
            "covers <label>.<zone> only")
    return rest


def paths(zone: str) -> tuple[str, str]:
    base = f"{nginx.CERT_ROOT}/{CERT_PREFIX}{normalize_zone(zone)}"
    return f"{base}/fullchain.pem", f"{base}/privkey.pem"


def is_shared_certificate_path(path: str) -> bool:
    return str(path or "").startswith(f"{nginx.CERT_ROOT}/{CERT_PREFIX}")


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------

def check_record(client: Any, zone_id: str, zone: str, entry: dict) -> list[str]:
    """Read-only: the addresses whose ``*.<zone>`` row is still missing.

    Each address family the remote has needs exactly zero or one row, DNS-only at
    that address. A CNAME, a second row, another address, a proxied row, or a row
    for a family the remote does not have refuses (``wildcard_dns_conflict``)."""
    name = f"*.{normalize_zone(zone)}"
    rows = [row for row in client.records(zone_id, name)
            if row.get("type") in {"A", "AAAA", "CNAME"}]
    if any(row.get("type") == "CNAME" for row in rows):
        raise FrontDoorError(WILDCARD_DNS_CONFLICT,
                             f"{name} is a CNAME; Sandbox will not replace it")
    wanted = {"A": entry.get("origin_ipv4"), "AAAA": entry.get("origin_ipv6")}
    if not any(wanted.values()):
        raise FrontDoorError(WILDCARD_DNS_CONFLICT,
                             "the remote has no recorded origin address for the wildcard record")
    missing = []
    for kind, address in wanted.items():
        same = [row for row in rows if row.get("type") == kind]
        if not address:
            if same:
                raise FrontDoorError(
                    WILDCARD_DNS_CONFLICT,
                    f"{name} has a {kind} record but the remote has no {kind} origin; "
                    "remove it in Cloudflare first")
            continue
        if not same:
            missing.append(address)
            continue
        # Type, address and DNS-only are what serving depends on; the TTL and
        # comment of a record someone else wrote are left as they are.
        if len(same) != 1 or same[0].get("content") != address or same[0].get("proxied") is not False:
            found = ", ".join(f"{row.get('content')} (proxied={bool(row.get('proxied'))})"
                              for row in same)
            raise FrontDoorError(
                WILDCARD_DNS_CONFLICT,
                f"{name} {kind} is {found}; DNS-only wildcard hosting needs exactly one "
                f"DNS-only {kind} at {address}. Change it in Cloudflare first; Sandbox "
                "never rewrites a wildcard record")
    return missing


def create_missing_records(client: Any, zone_id: str, zone: str, missing: list[str]) -> list[dict]:
    name = f"*.{normalize_zone(zone)}"
    return [_row(client.create_address(zone_id, name, address, proxied=False,
                                       comment="sandbox wildcard hosting"))
            for address in missing]


def _row(row: dict) -> dict:
    return {key: row.get(key) for key in ("id", "name", "type", "content", "proxied")}


# ---------------------------------------------------------------------------
# Certificate
# ---------------------------------------------------------------------------

def _base(zone: str) -> str:
    return paths(zone)[0].rsplit("/", 1)[0]


def _check_command(zone: str, min_days: int) -> str:
    cert_path, key_path = paths(zone)
    cert, key = shlex.quote(cert_path), shlex.quote(key_path)
    return nginx._SUDO + (
        f"$SUDO test -s {cert} -a -s {key} && "
        f"$SUDO openssl x509 -noout -checkend {int(min_days) * 86400} -in {cert} >/dev/null && "
        f"[ \"$($SUDO openssl x509 -noout -pubkey -in {cert})\" = "
        f"\"$($SUDO openssl pkey -pubout -in {key})\" ] && "
        f"$SUDO openssl x509 -noout -ext subjectAltName -in {cert}")


def certificate_current(entry: dict, zone: str, *, min_days: int = RENEW_DAYS,
                        ssh: Callable = None) -> bool:
    """The shared certificate exists, matches its key, covers ``*.<zone>`` and has
    more than ``min_days`` left (0 for "valid now")."""
    run = ssh or nginx._ssh
    result = run(entry, _check_command(zone, min_days), timeout=30)
    return result.returncode == 0 and f"DNS:*.{normalize_zone(zone)}" in (result.stdout or "")


def _csr_command(zone: str) -> str:
    cert_path, key_path = paths(zone)
    base = _base(zone)
    csr_path = f"{base}/request.csr"
    wildcard = f"*.{normalize_zone(zone)}"
    # The key is created once, atomically and without clobbering, so two applies
    # racing on a first issuance end up signing requests for the same key.
    keygen = (f"umask 077; tmp=$(mktemp {shlex.quote(base)}/.key.XXXXXX) && "
              f"openssl ecparam -name prime256v1 -genkey -noout -out \"$tmp\" && "
              f"ln \"$tmp\" {shlex.quote(key_path)} 2>/dev/null; rm -f \"$tmp\"")
    request = (f"umask 077; tmp=$(mktemp {shlex.quote(base)}/.csr.XXXXXX) || exit 1; "
               "trap 'rm -f \"$tmp\"' EXIT; "
               f"openssl req -new -key {shlex.quote(key_path)} "
               f"-subj {shlex.quote('/CN=' + wildcard)} "
               f"-addext {shlex.quote('subjectAltName=DNS:' + wildcard)} -out \"$tmp\" && "
               f"mv \"$tmp\" {shlex.quote(csr_path)}")
    return (nginx._ensure_edge_dirs_command() + "; "
            f"$SUDO install -d -o root -g root -m 0700 {shlex.quote(base)}; "
            f"if ! $SUDO test -s {shlex.quote(key_path)}; then "
            f"$SUDO sh -c {shlex.quote(keygen)}; fi; "
            f"$SUDO chmod 0600 {shlex.quote(key_path)}; "
            f"$SUDO sh -c {shlex.quote(request)} && "
            f"$SUDO sh -c {shlex.quote(exact_read.SCRIPT)} sandbox-read {shlex.quote(csr_path)}")


def _install_command(zone: str) -> str:
    """Install the chain (stdin) under the edge lock: it must match the key; then
    ``nginx -t`` and a reload, each bounded. On failure the previous chain comes
    back, or with none, the new one is removed."""
    cert_path, key_path = paths(zone)
    base = _base(zone)
    cert, key, old = shlex.quote(cert_path), shlex.quote(key_path), shlex.quote(cert_path + ".old")
    reload = ("timeout -k 5 60 nginx -t >/dev/null 2>&1 && "
              "timeout -k 5 60 systemctl reload nginx")
    inner = (
        f"umask 022; new=$(mktemp {shlex.quote(base)}/.chain.XXXXXX) || exit 1; "
        "trap 'rm -f \"$new\"' EXIT; cat > \"$new\" || exit 1; "
        f"[ \"$(openssl x509 -noout -pubkey -in \"$new\")\" = "
        f"\"$(openssl pkey -pubout -in {key})\" ] || exit {_KEY_MISMATCH_EXIT}; "
        "chmod 0644 \"$new\"; "
        f"exec 9>{shlex.quote(nginx.LOCK_PATH)}; flock -w {nginx.LOCK_WAIT_SECONDS} 9 || exit 75; "
        f"had=0; if [ -s {cert} ]; then cp -p {cert} {old} && had=1; fi; "
        f"mv \"$new\" {cert} || exit 1; "
        f"if {reload}; then rm -f {old}; exit 0; fi; "
        f"if [ \"$had\" = 1 ]; then mv {old} {cert}; else rm -f {cert}; fi; "
        f"{reload}; exit 1")
    return nginx._SUDO + f"$SUDO sh -c {shlex.quote(inner)}"


def lego_name(zone: str) -> str:
    """The file stem lego uses for ``*.<zone>`` (it writes ``_.<zone>.crt``)."""
    return "_." + normalize_zone(zone)


def issuer_preflight(zone: str) -> None:
    """Read-only: the lego broker is configured for ``zone`` and its binary is pinned."""
    from sandbox.commands.host_tailnet_name import default_deps
    from sandbox.hosting.tailnet_names import models, state
    try:
        config = models.TailnetNameConfig.load()
        if normalize_zone(zone) not in config.zones:
            raise FrontDoorError(
                WILDCARD_ISSUER_UNAVAILABLE,
                f"{zone} is not in tailnet_names.zones; the DNS-01 token is used only for "
                "listed zones")
        default_deps(str(state.default_root())).find_lego(config)
    except FrontDoorError:
        raise
    except Exception as exc:
        raise FrontDoorError(
            WILDCARD_ISSUER_UNAVAILABLE,
            f"the wildcard certificate issuer is not ready ({getattr(exc, 'code', type(exc).__name__)}): "
            f"{exc}") from None


def default_issuer(*, sleep: Callable[[float], None] = None) -> Callable[..., str | None]:
    """``issue(zone, csr_pem, still_needed) -> chain`` through the tailnet-name lego
    broker; None when ``still_needed()`` turns false while waiting for the broker."""
    import time
    from sandbox.commands.host_tailnet_name import default_deps
    from sandbox.hosting.tailnet_names import models, state
    pause = sleep or time.sleep

    def issue(zone: str, csr_pem: str, still_needed: Callable[[], bool] = lambda: True):
        config = models.TailnetNameConfig.load()
        if normalize_zone(zone) not in config.zones:
            raise FrontDoorError(WILDCARD_ISSUER_UNAVAILABLE,
                                 f"{zone} is not in tailnet_names.zones")
        deps = default_deps(str(state.default_root()))
        for attempt in range(BUSY_ATTEMPTS):
            if attempt and not still_needed():
                return None
            try:
                lego = deps.find_lego(config)
                return deps.issue(config, lego_name(zone), csr_pem, lego)
            except models.TailnetNameError as exc:
                if exc.code != models.BUSY or attempt == BUSY_ATTEMPTS - 1:
                    raise
                pause(BUSY_WAIT_SECONDS)
        raise AssertionError("unreachable")
    return issue


def ensure_certificate(entry: dict, zone: str, *, issue: Callable[[str, str], str] | None = None,
                       ssh: Callable = None) -> dict:
    """Reuse the shared certificate while it is current; otherwise issue and install one."""
    run = ssh or nginx._ssh
    zone = normalize_zone(zone)
    cert_path, key_path = paths(zone)
    if certificate_current(entry, zone, ssh=run):
        return {"state": "current", "cert_path": cert_path, "key_path": key_path}
    result = run(entry, _csr_command(zone), timeout=60)
    try:
        csr = exact_read.decode(result.stdout) if result.returncode == 0 else ""
    except exact_read.ExactReadError:
        csr = ""
    if "BEGIN CERTIFICATE REQUEST" not in csr:
        raise FrontDoorError(WILDCARD_CERTIFICATE_FAILED,
                             f"could not create the *.{zone} key/CSR on the remote: "
                             + nginx._diagnostic(entry, result))
    def still_needed() -> bool:
        return not certificate_current(entry, zone, ssh=run)
    try:
        chain = (issue or default_issuer())(zone, csr, still_needed)
    except FrontDoorError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise FrontDoorError(WILDCARD_CERTIFICATE_FAILED,
                             f"*.{zone} certificate was not issued ({code}): {exc}") from None
    if chain is None:
        # Another apply installed a current certificate while this one waited.
        return {"state": "current", "cert_path": cert_path, "key_path": key_path}
    if "BEGIN CERTIFICATE" not in chain:
        raise FrontDoorError(WILDCARD_CERTIFICATE_FAILED, f"*.{zone}: no certificate chain returned")
    # Lock wait, then test+reload and a restore pass, each bounded at 65 s.
    installed = run(entry, _install_command(zone), timeout=nginx.LOCK_WAIT_SECONDS + 300,
                    input_data=chain)
    if installed.returncode == _KEY_MISMATCH_EXIT and certificate_current(entry, zone, ssh=run):
        # Another apply installed a chain for the final key while this one issued.
        return {"state": "current", "cert_path": cert_path, "key_path": key_path}
    if installed.returncode != 0:
        raise FrontDoorError(WILDCARD_CERTIFICATE_FAILED,
                             f"*.{zone} certificate could not be installed: "
                             + nginx._diagnostic(entry, installed))
    if not certificate_current(entry, zone, ssh=run):
        raise FrontDoorError(WILDCARD_CERTIFICATE_FAILED,
                             f"*.{zone} certificate installed but does not check as current")
    return {"state": "issued", "cert_path": cert_path, "key_path": key_path}


def ensure(entry: dict, zone: str, zone_id: str, client: Any, *,
           issue: Callable[[str, str], str] | None = None, ssh: Callable = None) -> dict:
    """Check the wildcard record (read-only), install the certificate, then write
    the missing wildcard rows: a failed issuance leaves no zone-wide record behind."""
    from sandbox.core import _cloudflare as cloudflare
    zone = normalize_zone(zone)
    check_record(client, zone_id, zone, entry)
    certificate = ensure_certificate(entry, zone, issue=issue, ssh=ssh)
    # Read again: issuance can take minutes, and another apply may have written it.
    try:
        created = create_missing_records(client, zone_id, zone,
                                         check_record(client, zone_id, zone, entry))
    except cloudflare.CloudflareError as exc:
        raise FrontDoorError(WILDCARD_DNS_CONFLICT,
                             f"*.{zone} could not be created ({exc}); re-run the apply") from None
    return {"zone": zone, "created_records": created, "certificate": certificate}
