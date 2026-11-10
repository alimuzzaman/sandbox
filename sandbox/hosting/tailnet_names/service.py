"""add / list / renew / remove for tailnet-only names (spec 078).

Every side effect goes through :class:`Deps`, so each step is check-then-act
and the whole flow runs against fakes in tests. State is saved after every
step. Every mutating run holds the controller's per-remote lock and a remote
per-name lease (so two controllers cannot interleave on one name); the remote
program re-checks the host gate inside the nginx edge lock right before it
tests and reloads, and settles any interrupted activation first.
"""
from __future__ import annotations

import datetime as dt
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Callable, Iterator, Mapping

from sandbox.hosting.front_door import FrontDoorError, nginx
from sandbox.hosting.tailnet_names import render, x509
from sandbox.hosting.tailnet_names.models import (
    BUSY,
    CERTIFICATE_FAILED,
    DNS_CONFLICT,
    DNS_FAILED,
    DNS_NOT_OWNED,
    GATE_UNAVAILABLE,
    INSTALL_FAILED,
    NGINX_REJECTED,
    NOT_DUE,
    NOT_FOUND,
    PUBLIC_EXPOSED,
    PUBLIC_PROBE_UNAVAILABLE,
    RELOAD_FAILED,
    REMOTE_FAILED,
    REMOTE_PATH_UNSAFE,
    RENEWAL_WINDOW_DAYS,
    SECRET_UNAVAILABLE,
    TAILNET_ADDRESS_UNBOUND,
    UNMANAGED,
    UPSTREAM_DIFFERS,
    ZONE_NOT_ALLOWED,
    DnsResult,
    RemoteInventoryEntry,
    RemoteTarget,
    StepOutcome,
    TailnetName,
    TailnetNameConfig,
    TailnetNameError,
    TailnetNameRecord,
    Upstream,
)
from sandbox.hosting.tailnet_names.state import StateStore
from sandbox.hosting.tailnet_names.verify import ProbeResult, public_outcome, tailnet_outcome

LEASE_SECONDS = 1800
TAILNET_ATTEMPTS = 5
_REFUSALS = frozenset({BUSY, GATE_UNAVAILABLE, SECRET_UNAVAILABLE, UNMANAGED, REMOTE_PATH_UNSAFE,
                       ZONE_NOT_ALLOWED, "tailnet_name_acme_client_untrusted",
                       "tailnet_name_acme_client_missing", "tailnet_name_state_corrupt"})
_REMOTE_CODES = {
    "busy": BUSY, "lease_lost": BUSY, "lock_busy": BUSY, "gate_unavailable": GATE_UNAVAILABLE,
    "unmanaged": UNMANAGED, "path_unsafe": REMOTE_PATH_UNSAFE,
    "nginx_rejected": NGINX_REJECTED, "reload_failed": RELOAD_FAILED,
    "recovery_failed": RELOAD_FAILED, "restore_failed": RELOAD_FAILED,
    "certificate_mismatch": INSTALL_FAILED, "key_missing": INSTALL_FAILED,
    "key_failed": CERTIFICATE_FAILED, "csr_failed": CERTIFICATE_FAILED,
}
_TIME = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class Deps:
    remote_call: Callable[[Mapping, str, dict], dict]
    preflight: Callable[[Mapping, list, str], dict]
    dns: Callable[[TailnetNameConfig, dict], DnsResult]
    issue: Callable[[TailnetNameConfig, str, str, str], str]
    cached_chain: Callable[[str], str | None]
    find_lego: Callable[[TailnetNameConfig], str]
    active_jobs: Callable[[str], bool]
    jobs_probe: Callable[[Mapping], dict]
    probe: Callable[..., ProbeResult]
    store: Callable[[str], StateStore]
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)
    new_token: Callable[[], str] = lambda: secrets.token_hex(16)
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class Context:
    config: TailnetNameConfig | None
    target: RemoteTarget


@dataclass
class Result:
    operation: str
    fqdn: str | None
    steps: list[StepOutcome] = field(default_factory=list)
    code: str | None = None
    error: str | None = None
    record: dict | None = None

    @property
    def ok(self) -> bool:
        return self.code is None

    def envelope(self) -> dict:
        out = {"ok": self.ok, "operation": self.operation, "fqdn": self.fqdn,
               "steps": [step.as_dict() for step in self.steps]}
        if self.code:
            out["code"] = self.code
            out["error"] = self.error or self.code
        if self.record is not None:
            out["record"] = self.record
        return out


@dataclass
class RenewResult:
    results: list[Result]

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    def envelope(self) -> dict:
        return {"ok": self.ok, "operation": "renew",
                "results": [result.envelope() for result in self.results]}


@dataclass
class ListResult:
    names: list[dict]

    ok = True

    def envelope(self) -> dict:
        return {"ok": True, "operation": "list", "names": self.names}


class _Stop(Exception):
    pass


class _Run:
    def __init__(self, operation: str, fqdn: str | None) -> None:
        self.result = Result(operation, fqdn)

    def add(self, outcome: StepOutcome) -> None:
        self.result.steps.append(outcome)
        if outcome.status in ("refused", "failed", "not_run") and self.result.code is None:
            self.result.code = outcome.code or REMOTE_FAILED
            self.result.error = outcome.detail

    def done(self, step: str, detail: str | None = None) -> None:
        self.add(StepOutcome(step, "done", detail=detail))

    def skipped(self, step: str, detail: str | None = None, code: str | None = None) -> None:
        self.add(StepOutcome(step, "skipped", code, detail))

    def stop(self, step: str, code: str, detail: str | None, status: str = "failed"):
        self.add(StepOutcome(step, status, code, detail))
        raise _Stop

    def call(self, step: str, fn: Callable, *args, on_fail: Callable | None = None, **kwargs):
        try:
            return fn(*args, **kwargs)
        except TailnetNameError as exc:
            if on_fail:
                on_fail()
            self.stop(step, exc.code, exc.detail, "refused" if exc.code in _REFUSALS else "failed")


@dataclass(frozen=True)
class _Inspection:
    bound: bool | None
    locks: tuple[str, ...]
    rows: dict[str, RemoteInventoryEntry]


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime(_TIME)


def _parse_time(value: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(value or "", _TIME).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _days_left(row: RemoteInventoryEntry | None, now: dt.datetime) -> int | None:
    expiry = _parse_time(row.cert_not_after if row else None)
    return None if expiry is None else (expiry - now).days


def _cert_current(row: RemoteInventoryEntry | None, fqdn: str, now: dt.datetime) -> bool:
    if row is None or not (row.dir_managed and row.cert_present and row.key_present
                           and row.key_matches is True):
        return False
    if fqdn not in row.cert_names or not row.cert_sha256:
        return False
    days = _days_left(row, now)
    return days is not None and days > RENEWAL_WINDOW_DAYS


def _chain_usable(chain: str | None, fqdn: str, now: dt.datetime) -> bool:
    if not chain:
        return False
    try:
        leaf = x509.parse_leaf(x509.first_der(chain))
    except ValueError:
        return False
    return x509.covers(leaf.names, fqdn) and (leaf.not_after - now).days > RENEWAL_WINDOW_DAYS


def _zone_check(ctx: Context, name: TailnetName, record: TailnetNameRecord | None) -> None:
    """F6: the zone must be allowed now, and a stored record must agree with it."""
    zones = ctx.config.zones if ctx.config else ()
    if name.zone not in zones:
        raise TailnetNameError(ZONE_NOT_ALLOWED,
                               f"zone {name.zone} is not in tailnet_names.zones")
    if record is not None and record.zone != name.zone:
        raise TailnetNameError(
            ZONE_NOT_ALLOWED,
            f"the name was recorded under zone {record.zone}, which is no longer this name's "
            f"allowed zone ({name.zone}); restore the old zone to remove it, or clean it up by hand")


def _inspect(ctx: Context, deps: Deps, fqdn: str | None) -> _Inspection:
    payload = {"tailnet_ip": ctx.target.tailnet_ip}
    if fqdn is not None:
        payload["fqdn"] = fqdn
    data = deps.remote_call(ctx.target.entry, "inspect", payload)
    if not data.get("ok"):
        raise TailnetNameError(_REMOTE_CODES.get(data.get("code"), REMOTE_FAILED),
                               f"the remote could not be inspected ({data.get('code')})")
    rows = {}
    for raw in data.get("inventory") or []:
        row = RemoteInventoryEntry.parse(raw)
        rows[row.fqdn] = row
    bound = data.get("tailnet_ip_bound")
    locks = tuple(str(item) for item in data.get("locks_held") or [])
    return _Inspection(bound if isinstance(bound, bool) else None, locks, rows)


def _ownership(run: _Run, row: RemoteInventoryEntry | None) -> None:
    """F4: refuse anything at the reserved paths that Sandbox did not write."""
    if row is None:
        return
    if row.dir_unsafe:
        run.stop("preflight", REMOTE_PATH_UNSAFE,
                 "the name's certificate directory is a symlink or has an untrusted owner or mode",
                 "refused")
    if row.block_present and not row.block_managed:
        run.stop("preflight", UNMANAGED,
                 "the reserved block path holds a file Sandbox did not write; it was left alone",
                 "refused")
    if row.dir_present and not row.dir_managed:
        run.stop("preflight", UNMANAGED,
                 "the certificate directory exists without Sandbox's marker; it was left alone",
                 "refused")


def _gate(ctx: Context, deps: Deps, run: _Run, locks: tuple[str, ...]) -> None:
    if locks:
        run.stop("gate", BUSY, "a Sandbox host transaction holds "
                 + ", ".join(path.rsplit("/", 1)[-1] for path in locks), "refused")
    try:
        active = deps.active_jobs(ctx.target.name)
    except Exception:
        run.stop("gate", GATE_UNAVAILABLE,
                 "could not list the remote's durable jobs; nothing was changed", "refused")
    if active:
        run.stop("gate", BUSY, "a durable job is active on the remote; retry when it ends",
                 "refused")
    run.done("gate")


def _remote_refusal(run: _Run, step: str, data: dict, on_fail: Callable | None = None):
    if on_fail:
        on_fail()
    code = _REMOTE_CODES.get(data.get("code"), REMOTE_FAILED)
    run.stop(step, code, f"{data.get('code')}: {data.get('detail') or 'remote refusal'}",
             "refused" if code in _REFUSALS else "failed")


def _remote(run: _Run, step: str, deps: Deps, ctx: Context, action: str, payload: dict, *,
            on_fail: Callable | None = None) -> dict:
    data = run.call(step, deps.remote_call, ctx.target.entry, action, payload, on_fail=on_fail)
    if not data.get("ok"):
        _remote_refusal(run, step, data, on_fail)
    return data


@contextmanager
def _lease(run: _Run, deps: Deps, ctx: Context, fqdn: str, token: str) -> Iterator[None]:
    _remote(run, "gate", deps, ctx, "reserve", {"fqdn": fqdn, "token": token,
                                                 "seconds": LEASE_SECONDS})
    try:
        yield
    finally:
        try:
            deps.remote_call(ctx.target.entry, "release", {"fqdn": fqdn, "token": token})
        except Exception:
            pass  # the lease expires on its own


def _jobs(run: _Run, deps: Deps, ctx: Context) -> dict:
    try:
        return deps.jobs_probe(ctx.target.entry)
    except Exception:
        run.stop("gate", GATE_UNAVAILABLE,
                 "could not resolve the remote's Sandbox home for the job check", "refused")


class _Recorder:
    """Saves the record after each step (FR-020)."""

    def __init__(self, store: StateStore, record: TailnetNameRecord | None, now) -> None:
        self.store, self.record, self.now = store, record, now

    def save(self, step: str | None = None, status: str | None = None, **changes) -> None:
        if self.record is None:
            return
        steps = dict(self.record.steps)
        if step:
            steps[step] = status
        self.record = replace(self.record, steps=steps, updated_at=_iso(self.now()), **changes)
        self.store.put(self.record)

    def failing(self, step: str) -> Callable[[], None]:
        return lambda: self.save(step, "failed")


def _record_view(record: TailnetNameRecord | None) -> dict | None:
    if record is None:
        return None
    return {"upstream": record.upstream, "tailnet_ip": record.tailnet_ip,
            "cert_not_after": record.cert_not_after, "dns_record_id": record.dns_record_id,
            "state": record.state}


def _obtain_chain(ctx: Context, deps: Deps, run: _Run, rec: _Recorder, name: TailnetName,
                  lego: str, token: str, *, allow_cached: bool) -> tuple[str, bool]:
    """(chain, from_cache). Makes the CSR on the remote, then reuses or issues."""
    data = _remote(run, "certificate", deps, ctx, "csr", {"fqdn": name.fqdn, "token": token},
                   on_fail=rec.failing("certificate"))
    if not isinstance(data.get("csr"), str):
        rec.save("certificate", "failed")
        run.stop("certificate", CERTIFICATE_FAILED, "the remote returned no signing request")
    cached = deps.cached_chain(name.fqdn) if allow_cached else None
    if _chain_usable(cached, name.fqdn, deps.now()):
        rec.save("certificate", "done")
        run.done("certificate", "reused the certificate issued earlier for this name")
        return cached, True
    chain = run.call("certificate", deps.issue, ctx.config, name.fqdn, data["csr"], lego,
                     on_fail=rec.failing("certificate"))
    rec.save("certificate", "done")
    run.done("certificate", "issued")
    return chain, False


def _committed(run: _Run, rec: _Recorder, data: dict) -> dict:
    rec.save("activate", "done", cert_not_after=data.get("not_after"),
             cert_sha256=data.get("sha256"))
    detail = "settled an interrupted change, " if data.get("settled") else ""
    if data.get("changed"):
        run.done("activate", detail + "tested and reloaded")
    else:
        run.skipped("activate", detail + "already active")
    return data


def _activate(ctx: Context, deps: Deps, run: _Run, rec: _Recorder, name: TailnetName,
              lego: str, token: str, payload: dict, cached: bool) -> dict:
    """Commit on the remote; a reused chain the remote rejects is replaced once."""
    fail = rec.failing("activate")
    data = _remote(run, "activate", deps, ctx, "commit", payload, on_fail=fail) if not cached \
        else run.call("activate", deps.remote_call, ctx.target.entry, "commit", payload,
                      on_fail=fail)
    if data.get("ok"):
        return _committed(run, rec, data)
    if data.get("code") != "certificate_mismatch":
        _remote_refusal(run, "activate", data, fail)
    chain, _ = _obtain_chain(ctx, deps, run, rec, name, lego, token, allow_cached=False)
    data = _remote(run, "activate", deps, ctx, "commit", dict(payload, chain=chain), on_fail=fail)
    return _committed(run, rec, data)


def _verify(ctx: Context, deps: Deps, run: _Run, rec: _Recorder, name: TailnetName,
            token: str, jobs: dict, expected_sha: str | None) -> None:
    target = ctx.target
    outcome = None
    for attempt in range(TAILNET_ATTEMPTS):
        outcome = tailnet_outcome(deps.probe(target.tailnet_ip, name.fqdn, send_request=True),
                                  expected_sha)
        if outcome.status == "done":
            break
        if attempt + 1 < TAILNET_ATTEMPTS:
            deps.sleep(1.0)
    run.add(outcome)
    if target.public_ip is None:
        run.add(StepOutcome("verify_public", "not_run", PUBLIC_PROBE_UNAVAILABLE,
                            "no public IPv4 is known for this remote; pass --public-address"))
        return
    verdict = public_outcome(deps.probe(target.public_ip, name.fqdn, send_request=False),
                             expected_sha, name.fqdn)
    if not verdict.exposed:
        run.add(verdict.outcome)
        return
    # A certificate valid for the name is served publicly: take our block down
    # (fail safe), then probe again to report what is still exposed.
    removal = "remove_failed"
    try:
        data = deps.remote_call(target.entry, "remove", {
            "fqdn": name.fqdn, "token": token, "keep_directory": True, "jobs_probe": jobs})
        if data.get("ok"):
            removal = "removed"
    except Exception:
        pass
    again = public_outcome(deps.probe(target.public_ip, name.fqdn, send_request=False),
                           expected_sha, name.fqdn)
    if removal == "removed" and not again.exposed:
        detail = verdict.outcome.detail + "; the block was removed and the name is no longer served"
    elif removal == "removed":
        detail = (verdict.outcome.detail + "; the block was removed but the public address still "
                  "presents a certificate for this name, so something else serves it")
    else:
        detail = (verdict.outcome.detail + "; removing the block FAILED, so the name may still be "
                  "exposed: run `sb host tailnet-name remove`")
    rec.save("activate", removal)
    run.add(replace(verdict.outcome, detail=detail))
    run.result.code, run.result.error = PUBLIC_EXPOSED, detail


def add(ctx: Context, deps: Deps, name: TailnetName, upstream: Upstream, *,
        replace_upstream: bool = False) -> Result:
    run = _Run("add", name.fqdn)
    target = ctx.target
    entry = target.entry
    try:
        lego = run.call("preflight", deps.find_lego, ctx.config)
        store = deps.store(target.name)
        with store.lock():
            existing = run.call("preflight", store.get, name.fqdn)
            run.call("preflight", _zone_check, ctx, name, existing)
            try:
                facts = deps.preflight(entry, [name.fqdn], nginx.route_file(name.route_name))
            except FrontDoorError as exc:
                run.stop("preflight", exc.code, str(exc), "refused")
            info = run.call("preflight", _inspect, ctx, deps, name.fqdn)
            if info.bound is not True:
                run.stop("preflight", TAILNET_ADDRESS_UNBOUND,
                         f"the remote does not hold its recorded tailnet address {target.tailnet_ip}",
                         "refused")
            row = info.rows.get(name.fqdn)
            _ownership(run, row)
            run.done("preflight")
            _gate(ctx, deps, run, info.locks)
            if row and row.block_present and row.upstream and row.upstream != str(upstream) \
                    and not replace_upstream:
                run.stop("activate", UPSTREAM_DIFFERS,
                         f"already published with upstream {row.upstream}; "
                         "pass --replace-upstream to change it", "refused")
            token = (existing.lease_token if existing and existing.lease_token
                     else deps.new_token())
            rec = _Recorder(store, replace(
                existing or TailnetNameRecord(name.fqdn, name.zone, str(upstream), target.tailnet_ip),
                state="publishing", tailnet_ip=target.tailnet_ip, lease_token=token), deps.now)
            rec.save()
            jobs = _jobs(run, deps, ctx)
            with _lease(run, deps, ctx, name.fqdn, token):
                # DNS (FR-007, FR-008)
                res = run.call("dns", deps.dns, ctx.config,
                               {"op": "ensure", "zone": name.zone, "name": name.fqdn,
                                "ip": target.tailnet_ip}, on_fail=rec.failing("dns"))
                if res.result == "conflict":
                    rec.save("dns", "refused")
                    run.stop("dns", DNS_CONFLICT,
                             f"the name already has DNS records Sandbox will not change "
                             f"({res.code or 'other record'}: content {res.content}, "
                             f"proxied {res.proxied})", "refused")
                if res.result not in ("created", "exists"):
                    rec.save("dns", "failed")
                    run.stop("dns", DNS_FAILED,
                             f"the DNS provider call failed ({res.code or res.result})")
                adopted = rec.record.dns_adopted if rec.record.dns_record_id == res.record_id \
                    else res.result == "exists"
                rec.save("dns", "done", dns_record_id=res.record_id, dns_adopted=adopted)
                if res.result == "created":
                    run.done("dns", "created a DNS-only record")
                else:
                    run.skipped("dns", "an identical DNS-only record exists")

                # Certificate (FR-009, FR-010)
                chain, cached = None, False
                if _cert_current(row, name.fqdn, deps.now()):
                    run.skipped("certificate", f"valid until {row.cert_not_after}")
                else:
                    chain, cached = _obtain_chain(ctx, deps, run, rec, name, lego, token,
                                                  allow_cached=True)

                # Activation: block and chain together (FR-011 to FR-016, FR-019)
                block = render.render_block(
                    name, upstream, target.tailnet_ip,
                    http2_directive=nginx.features(facts)["http2_directive"])
                payload = {"fqdn": name.fqdn, "token": token, "block": block, "chain": chain,
                           "jobs_probe": jobs}
                data = _activate(ctx, deps, run, rec, name, lego, token, payload, cached)
                rec.save(upstream=str(upstream))

                # Verification (FR-017, FR-018)
                _verify(ctx, deps, run, rec, name, token, jobs, data.get("sha256"))
                rec.save("verify", "done" if run.result.ok else "failed",
                         state="published" if run.result.ok else "publishing")
                run.result.record = _record_view(rec.record)
    except _Stop:
        pass
    except TailnetNameError as exc:
        run.add(StepOutcome("gate", "refused" if exc.code in _REFUSALS else "failed",
                            exc.code, exc.detail))
    return run.result


def list_names(ctx: Context, deps: Deps) -> ListResult:
    info = _inspect(ctx, deps, None)
    records = deps.store(ctx.target.name).load()
    zones = ctx.config.zones if ctx.config else None
    now = deps.now()
    names = []
    for fqdn in sorted(set(info.rows) | set(records)):
        row, record = info.rows.get(fqdn), records.get(fqdn)
        days = _days_left(row, now)
        drift = []
        if record and not (row and row.block_present):
            drift.append("block_missing")
        if row and row.block_present and record is None:
            drift.append("not_recorded")
        if row and row.block_present and not row.block_managed:
            drift.append("unmanaged_block")
        if row and row.dir_present and not row.dir_managed:
            drift.append("unmanaged_directory")
        if row and row.block_present and not row.cert_present:
            drift.append("certificate_missing")
        if row and row.block_present and row.activation != "loaded":
            drift.append("activation_" + (row.activation or "unproven"))
        if record and row and row.upstream and row.upstream != record.upstream:
            drift.append("upstream_differs")
        if row and row.header and row.header.get("tailnet_ip") != ctx.target.tailnet_ip:
            drift.append("tailnet_address_differs")
        if record and zones is not None and record.zone not in zones:
            drift.append("zone_not_allowed")
        if record and record.state != "published":
            drift.append("state_" + record.state)
        names.append({
            "fqdn": fqdn,
            "upstream": (row.upstream if row and row.upstream else
                         record.upstream if record else None),
            "cert_not_after": row.cert_not_after if row else None,
            "days_remaining": days,
            "renewal_due": days is not None and days <= RENEWAL_WINDOW_DAYS,
            "block_present": bool(row and row.block_present),
            "cert_present": bool(row and row.cert_present),
            "recorded": record is not None,
            "drift": drift,
        })
    return ListResult(names)


def _renew_one(ctx: Context, deps: Deps, name: TailnetName, row: RemoteInventoryEntry | None,
               locks: tuple[str, ...], store: StateStore, lego: str, force: bool) -> Result:
    run = _Run("renew", name.fqdn)
    try:
        if row is None or not row.block_present:
            run.stop("certificate", NOT_FOUND,
                     "no published block for this name on the remote; use add", "refused")
        _ownership(run, row)
        record = run.call("certificate", store.get, name.fqdn)
        run.call("certificate", _zone_check, ctx, name, record)
        # F8: only a journal that proves the chain is loaded lets renew skip.
        current = not force and _cert_current(row, name.fqdn, deps.now())
        if current and row.activation == "loaded":
            run.skipped("certificate", f"valid until {row.cert_not_after}", NOT_DUE)
            return run.result
        _gate(ctx, deps, run, locks)
        token = record.lease_token if record and record.lease_token else deps.new_token()
        rec = _Recorder(store, replace(record or TailnetNameRecord(
            name.fqdn, name.zone, row.upstream or "127.0.0.1:1", ctx.target.tailnet_ip,
            state="published"), lease_token=token), deps.now)
        rec.save()
        jobs = _jobs(run, deps, ctx)
        with _lease(run, deps, ctx, name.fqdn, token):
            if current:
                # The certificate is fine but its activation is not proven:
                # reconcile (test and reload) without issuing.
                chain, cached = None, False
                run.skipped("certificate", f"valid until {row.cert_not_after}; reconciling "
                            "an unproven activation")
            else:
                chain, cached = _obtain_chain(ctx, deps, run, rec, name, lego, token,
                                              allow_cached=not force)
            payload = {"fqdn": name.fqdn, "token": token, "block": None, "chain": chain,
                       "jobs_probe": jobs}
            data = _activate(ctx, deps, run, rec, name, lego, token, payload, cached)
            _verify(ctx, deps, run, rec, name, token, jobs, data.get("sha256"))
            rec.save("verify", "done" if run.result.ok else "failed")
            run.result.record = _record_view(rec.record)
    except _Stop:
        pass
    return run.result


def renew(ctx: Context, deps: Deps, name: TailnetName | None, *, all_names: bool = False,
          force: bool = False) -> RenewResult:
    fqdn = None if all_names or name is None else name.fqdn
    try:
        lego = deps.find_lego(ctx.config)
        store = deps.store(ctx.target.name)
        with store.lock():
            info = _inspect(ctx, deps, fqdn)
            if fqdn is not None:
                targets = [(name, info.rows.get(fqdn))]
            else:
                targets = []
                skipped = []
                for row in info.rows.values():
                    if not row.block_present:
                        continue
                    try:
                        targets.append((TailnetName.parse(row.fqdn, ctx.config.zones), row))
                    except TailnetNameError as exc:
                        # F6: a name outside today's zones is reported, never renewed.
                        skipped.append(Result("renew", row.fqdn, [StepOutcome(
                            "certificate", "refused", exc.code, exc.detail)],
                            exc.code, exc.detail))
                return RenewResult(skipped + [
                    _renew_one(ctx, deps, item, row, info.locks, store, lego, force)
                    for item, row in targets])
            return RenewResult([_renew_one(ctx, deps, item, row, info.locks, store, lego, force)
                                for item, row in targets])
    except TailnetNameError as exc:
        status = "refused" if exc.code in _REFUSALS else "failed"
        failed = Result("renew", fqdn, [StepOutcome("certificate", status, exc.code, exc.detail)],
                        exc.code, exc.detail)
        return RenewResult([failed])


def remove(ctx: Context, deps: Deps, name: TailnetName) -> Result:
    run = _Run("remove", name.fqdn)
    target = ctx.target
    try:
        store = deps.store(target.name)
        with store.lock():
            existing = run.call("preflight", store.get, name.fqdn)
            run.call("preflight", _zone_check, ctx, name, existing)
            info = run.call("preflight", _inspect, ctx, deps, name.fqdn)
            row = info.rows.get(name.fqdn)
            present = bool(row and (row.block_present or row.dir_present))
            if not present and existing is None:
                run.stop("preflight", NOT_FOUND, "the name is neither on the remote nor recorded",
                         "refused")
            _ownership(run, row)
            run.done("preflight")
            _gate(ctx, deps, run, info.locks)
            token = (existing.lease_token if existing and existing.lease_token
                     else deps.new_token())
            rec = _Recorder(store, replace(existing, state="removing", lease_token=token)
                            if existing else None, deps.now)
            rec.save()
            jobs = _jobs(run, deps, ctx)
            with _lease(run, deps, ctx, name.fqdn, token):
                if present:
                    data = _remote(run, "remove_block", deps, ctx, "remove",
                                   {"fqdn": name.fqdn, "token": token, "jobs_probe": jobs},
                                   on_fail=rec.failing("remove_block"))
                    rec.save("remove_block", "done")
                    if data.get("block_removed"):
                        run.done("remove_block", "tested and reloaded")
                    else:
                        run.skipped("remove_block", "no block on the remote")
                    rec.save("remove_certificate", "done")
                    run.done("remove_certificate") if data.get("directory_removed") else \
                        run.skipped("remove_certificate", "no certificate directory")
                else:
                    run.skipped("remove_block", "no block on the remote")
                    run.skipped("remove_certificate", "no certificate directory")

                record = rec.record
                if record and record.dns_record_id:
                    res = run.call("remove_dns", deps.dns, ctx.config,
                                   {"op": "delete", "zone": record.zone, "name": name.fqdn,
                                    "ip": record.tailnet_ip, "record_id": record.dns_record_id},
                                   on_fail=rec.failing("remove_dns"))
                else:
                    res = run.call("remove_dns", deps.dns, ctx.config,
                                   {"op": "inspect", "zone": name.zone, "name": name.fqdn,
                                    "ip": target.tailnet_ip})
                    if res.result not in ("absent", "error"):
                        run.stop("remove_dns", DNS_NOT_OWNED,
                                 "this controller has no record of creating the DNS record; "
                                 "delete it in the DNS provider if it is yours", "refused")
                if res.result == "conflict":
                    rec.save("remove_dns", "refused")
                    run.stop("remove_dns", DNS_NOT_OWNED,
                             f"the name's DNS records changed ({res.code or 'record changed'}: "
                             f"content {res.content}, proxied {res.proxied}); Sandbox left them "
                             "in place", "refused")
                if res.result == "error":
                    rec.save("remove_dns", "failed")
                    run.stop("remove_dns", DNS_FAILED, f"the DNS provider call failed ({res.code})")
                if res.result == "deleted":
                    run.done("remove_dns", "deleted the DNS record")
                else:
                    run.skipped("remove_dns", "no DNS record")
                store.drop(name.fqdn)
    except _Stop:
        pass
    except TailnetNameError as exc:
        run.add(StepOutcome("gate", "refused" if exc.code in _REFUSALS else "failed",
                            exc.code, exc.detail))
    return run.result
