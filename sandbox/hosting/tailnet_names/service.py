"""add / list / renew / remove for tailnet-only names (spec 078).

Every side effect goes through :class:`Deps`, so each step is check-then-act
and the whole flow runs against fakes in tests. State is saved after every
step, so a rerun after any interruption finishes the remaining work.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from typing import Callable, Mapping

from sandbox.hosting.front_door import FrontDoorError, nginx
from sandbox.hosting.tailnet_names import render
from sandbox.hosting.tailnet_names.models import (
    ACME_CLIENT_MISSING,
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
    RENEWAL_WINDOW_DAYS,
    TAILNET_ADDRESS_UNBOUND,
    UPSTREAM_DIFFERS,
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

_REFUSALS = frozenset({BUSY, GATE_UNAVAILABLE, "tailnet_name_secret_unavailable"})
_INSTALL_CODES = {"nginx_rejected": NGINX_REJECTED, "reload_failed": RELOAD_FAILED}
_ROUTE_CODES = {"front_door_validate_failed": NGINX_REJECTED,
                "front_door_reload_failed": RELOAD_FAILED}
_TIME = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class Deps:
    remote_call: Callable[[Mapping, str, dict], dict]
    preflight: Callable[[Mapping, list, str], dict]
    apply_route: Callable[[Mapping, str, str], dict]
    remove_route: Callable[[Mapping, str], dict]
    dns: Callable[[TailnetNameConfig, dict], DnsResult]
    issue: Callable[[TailnetNameConfig, str, str, str], str]
    find_lego: Callable[[TailnetNameConfig], str | None]
    active_jobs: Callable[[str], bool]
    probe: Callable[..., ProbeResult]
    store: Callable[[str], StateStore]
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)


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
    if row is None or not (row.cert_present and row.key_present and row.key_matches is True):
        return False
    if fqdn not in row.cert_names or not row.cert_sha256:
        return False
    days = _days_left(row, now)
    return days is not None and days > RENEWAL_WINDOW_DAYS


def _inspect(ctx: Context, deps: Deps, fqdn: str | None) -> _Inspection:
    payload = {"tailnet_ip": ctx.target.tailnet_ip}
    if fqdn is not None:
        payload["fqdn"] = fqdn
    data = deps.remote_call(ctx.target.entry, "inspect", payload)
    if not data.get("ok"):
        raise TailnetNameError(REMOTE_FAILED,
                               f"the remote could not be inspected ({data.get('code')})")
    rows = {}
    for raw in data.get("inventory") or []:
        row = RemoteInventoryEntry.parse(raw)
        rows[row.fqdn] = row
    bound = data.get("tailnet_ip_bound")
    locks = tuple(str(item) for item in data.get("locks_held") or [])
    return _Inspection(bound if isinstance(bound, bool) else None, locks, rows)


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


def _verify(ctx: Context, deps: Deps, run: _Run, name: TailnetName,
            expected_sha: str | None) -> bool:
    """Run both probes; on exposure remove the block. Returns True when exposed."""
    target = ctx.target
    run.add(tailnet_outcome(deps.probe(target.tailnet_ip, name.fqdn, send_request=True),
                            expected_sha))
    if target.public_ip is None:
        run.add(StepOutcome("verify_public", "not_run", PUBLIC_PROBE_UNAVAILABLE,
                            "no public IPv4 is known for this remote; pass --public-address"))
        return False
    outcome, exposed = public_outcome(
        deps.probe(target.public_ip, name.fqdn, send_request=False), expected_sha)
    if exposed:
        detail = outcome.detail + "; the block was removed"
        try:
            deps.remove_route(target.entry, name.route_name)
        except FrontDoorError as exc:
            detail = outcome.detail + f"; removing the block failed: {exc}"
        outcome = replace(outcome, detail=detail)
        run.add(outcome)
        run.result.code, run.result.error = PUBLIC_EXPOSED, detail
        return True
    run.add(outcome)
    return False


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


def _issue_and_install(ctx: Context, deps: Deps, run: _Run, rec: _Recorder, name: TailnetName,
                       lego: str, *, reload: bool) -> str:
    entry = ctx.target.entry
    data = run.call("certificate", deps.remote_call, entry, "csr", {"fqdn": name.fqdn},
                    on_fail=rec.failing("certificate"))
    if not data.get("ok") or not isinstance(data.get("csr"), str):
        rec.save("certificate", "failed")
        run.stop("certificate", CERTIFICATE_FAILED,
                 f"the remote could not make a signing request ({data.get('code')})")
    chain = run.call("certificate", deps.issue, ctx.config, name.fqdn, data["csr"], lego,
                     on_fail=rec.failing("certificate"))
    rec.save("certificate", "done")
    run.done("certificate")
    data = run.call("install", deps.remote_call, entry, "install",
                    {"fqdn": name.fqdn, "fullchain": chain, "reload": reload},
                    on_fail=rec.failing("install"))
    if not data.get("ok"):
        rec.save("install", "failed")
        run.stop("install", _INSTALL_CODES.get(data.get("code"), INSTALL_FAILED),
                 f"{data.get('code')}: {data.get('detail') or 'install failed'}; "
                 "the previous certificate is in place")
    rec.save("install", "done", cert_not_after=data.get("not_after"),
             cert_sha256=data.get("sha256"))
    run.done("install", "reloaded" if reload and data.get("changed") else None)
    return str(data.get("sha256") or "")


def add(ctx: Context, deps: Deps, name: TailnetName, upstream: Upstream, *,
        replace_upstream: bool = False) -> Result:
    run = _Run("add", name.fqdn)
    target = ctx.target
    entry = target.entry
    try:
        lego = deps.find_lego(ctx.config)
        if lego is None:
            run.stop("preflight", ACME_CLIENT_MISSING,
                     "lego is not installed; install it (for example `brew install lego`) "
                     "or set tailnet_names.lego", "refused")
        store = deps.store(target.name)
        with store.lock():
            try:
                facts = deps.preflight(entry, [name.fqdn], nginx.route_file(name.route_name))
            except FrontDoorError as exc:
                run.stop("preflight", exc.code, str(exc), "refused")
            info = run.call("preflight", _inspect, ctx, deps, name.fqdn)
            if info.bound is not True:
                run.stop("preflight", TAILNET_ADDRESS_UNBOUND,
                         f"the remote does not hold its recorded tailnet address {target.tailnet_ip}",
                         "refused")
            run.done("preflight")
            _gate(ctx, deps, run, info.locks)
            row = info.rows.get(name.fqdn)
            if row and row.block_present and row.upstream and row.upstream != str(upstream) \
                    and not replace_upstream:
                run.stop("server_block", UPSTREAM_DIFFERS,
                         f"already published with upstream {row.upstream}; "
                         "pass --replace-upstream to change it", "refused")
            existing = store.get(name.fqdn)
            rec = _Recorder(store, replace(
                existing or TailnetNameRecord(name.fqdn, name.zone, str(upstream), target.tailnet_ip),
                state="publishing", zone=name.zone, tailnet_ip=target.tailnet_ip), deps.now)
            rec.save()

            # DNS (FR-007, FR-008)
            res = run.call("dns", deps.dns, ctx.config,
                           {"op": "ensure", "zone": name.zone, "name": name.fqdn,
                            "ip": target.tailnet_ip}, on_fail=rec.failing("dns"))
            if res.result == "conflict":
                rec.save("dns", "refused")
                run.stop("dns", DNS_CONFLICT,
                         f"an A record for the name already exists (content {res.content}, "
                         f"proxied {res.proxied}{', several records' if res.code else ''}); "
                         "Sandbox never changes it", "refused")
            if res.result not in ("created", "exists"):
                rec.save("dns", "failed")
                run.stop("dns", DNS_FAILED, f"the DNS provider call failed ({res.code or res.result})")
            adopted = rec.record.dns_adopted if rec.record.dns_record_id == res.record_id \
                else res.result == "exists"
            rec.save("dns", "done", dns_record_id=res.record_id, dns_adopted=adopted)
            if res.result == "created":
                run.done("dns", "created a DNS-only record")
            else:
                run.skipped("dns", "an identical DNS-only record exists")

            # Certificate and install (FR-009 to FR-011)
            if _cert_current(row, name.fqdn, deps.now()):
                run.skipped("certificate", f"valid until {row.cert_not_after}")
                run.skipped("install", "the installed certificate is current")
                rec.save("install", "done", cert_not_after=row.cert_not_after,
                         cert_sha256=row.cert_sha256)
                expected = row.cert_sha256
            else:
                expected = _issue_and_install(ctx, deps, run, rec, name, lego,
                                              reload=bool(row and row.block_present))

            # Server block (FR-012 to FR-016)
            info = run.call("gate", _inspect, ctx, deps, name.fqdn)
            _gate(ctx, deps, run, info.locks)
            content = render.render_block(name, upstream, target.tailnet_ip,
                                          http2_directive=nginx.features(facts)["http2_directive"])
            try:
                applied = deps.apply_route(entry, name.route_name, content)
            except FrontDoorError as exc:
                rec.save("server_block", "failed")
                run.stop("server_block", _ROUTE_CODES.get(exc.code, exc.code),
                         f"{exc}; nginx was not reloaded with the new block")
            rec.save("server_block", "done", upstream=str(upstream))
            if applied.get("state") == "unchanged":
                run.skipped("server_block", "the block is unchanged")
            else:
                run.done("server_block", "tested and reloaded")

            # Verification (FR-017, FR-018)
            exposed = _verify(ctx, deps, run, name, expected)
            if exposed:
                rec.save("server_block", "removed")
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
        if row and row.block_present and not row.cert_present:
            drift.append("certificate_missing")
        if record and row and row.upstream and row.upstream != record.upstream:
            drift.append("upstream_differs")
        if row and row.header and row.header.get("tailnet_ip") != ctx.target.tailnet_ip:
            drift.append("tailnet_address_differs")
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
        if not force and _cert_current(row, name.fqdn, deps.now()):
            run.skipped("certificate", f"valid until {row.cert_not_after}", NOT_DUE)
            return run.result
        _gate(ctx, deps, run, locks)
        record = store.get(name.fqdn) or TailnetNameRecord(
            name.fqdn, name.zone, row.upstream or "", ctx.target.tailnet_ip, state="published")
        rec = _Recorder(store, record, deps.now)
        expected = _issue_and_install(ctx, deps, run, rec, name, lego, reload=True)
        _verify(ctx, deps, run, name, expected)
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
        if lego is None:
            raise TailnetNameError(ACME_CLIENT_MISSING,
                                   "lego is not installed; install it or set tailnet_names.lego")
        store = deps.store(ctx.target.name)
        with store.lock():
            info = _inspect(ctx, deps, fqdn)
            if fqdn is not None:
                targets = [(name, info.rows.get(fqdn))]
            else:
                targets = []
                for row in info.rows.values():
                    if not row.block_present:
                        continue
                    try:
                        targets.append((TailnetName.parse(row.fqdn, ctx.config.zones), row))
                    except TailnetNameError:
                        continue
            return RenewResult([_renew_one(ctx, deps, item, row, info.locks, store, lego, force)
                                for item, row in targets])
    except TailnetNameError as exc:
        failed = Result("renew", fqdn, [StepOutcome("certificate", "refused", exc.code, exc.detail)],
                        exc.code, exc.detail)
        return RenewResult([failed])


def remove(ctx: Context, deps: Deps, name: TailnetName) -> Result:
    run = _Run("remove", name.fqdn)
    target = ctx.target
    try:
        store = deps.store(target.name)
        with store.lock():
            info = run.call("preflight", _inspect, ctx, deps, name.fqdn)
            row = info.rows.get(name.fqdn)
            existing = store.get(name.fqdn)
            present = bool(row and (row.block_present or row.cert_present or row.key_present))
            if not present and existing is None:
                run.stop("preflight", NOT_FOUND, "the name is neither on the remote nor recorded",
                         "refused")
            run.done("preflight")
            _gate(ctx, deps, run, info.locks)
            rec = _Recorder(store, replace(existing, state="removing") if existing else None,
                            deps.now)
            rec.save()

            if row and row.block_present:
                try:
                    deps.remove_route(target.entry, name.route_name)
                except FrontDoorError as exc:
                    rec.save("remove_block", "failed")
                    run.stop("remove_block", _ROUTE_CODES.get(exc.code, exc.code), str(exc))
                rec.save("remove_block", "done")
                run.done("remove_block", "tested and reloaded")
            else:
                run.skipped("remove_block", "no block on the remote")

            if row and (row.cert_present or row.key_present):
                data = run.call("remove_certificate", deps.remote_call, target.entry,
                                "remove_cert", {"fqdn": name.fqdn},
                                on_fail=rec.failing("remove_certificate"))
                if not data.get("ok"):
                    rec.save("remove_certificate", "failed")
                    run.stop("remove_certificate", REMOTE_FAILED,
                             f"the certificate could not be removed ({data.get('code')})")
                rec.save("remove_certificate", "done")
                run.done("remove_certificate")
            else:
                run.skipped("remove_certificate", "no certificate on the remote")

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
                if res.result != "absent" and res.result != "error":
                    run.stop("remove_dns", DNS_NOT_OWNED,
                             "this controller has no record of creating the DNS record; "
                             "delete it in the DNS provider if it is yours", "refused")
            if res.result == "conflict":
                rec.save("remove_dns", "refused")
                run.stop("remove_dns", DNS_NOT_OWNED,
                         f"the record changed (content {res.content}, proxied {res.proxied}); "
                         "Sandbox left it in place", "refused")
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
