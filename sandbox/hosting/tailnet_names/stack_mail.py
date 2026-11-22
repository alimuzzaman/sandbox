"""A stack's tailnet-only inbox name (``stack.mail_hostname``), kept with the stack.

After each completed apply the name is published (or kept) at the stack's
``mailpit`` port through the ordinary ``add`` flow in stack mode, so a rerun is
a no-op and a changed port replaces the upstream. ``down`` and the reaper
remove the names the stack's applies created. Every ownership decision is made
by ``service.add``/``service.remove`` under the name store's lock: a name added
by hand is never re-pointed or removed, and a name tagged for another stack is
refused.

Names whose stack is gone (a reap by the host timer, a removal refused during
``down``) are swept by the next apply on the same machine: a tagged name is an
orphan only on positive evidence, its stack's lifetime record ``reaped`` with no
live record of that slug, or neither a lifetime record nor a local stack record
of that slug. Orphans are never renewed.

Nothing here raises: a stack apply or teardown never fails because of its
inbox name. Each call returns a small report for the command's output.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Callable

from sandbox.hosting.tailnet_names.models import BUSY, CONFIG_MISSING, TailnetNameError

# A host lock held by another apply clears in seconds to minutes; retry a few
# times, then leave the rest to the next apply's sweep.
BUSY_ATTEMPTS = 4
BUSY_WAIT_SECONDS = 20.0
# Renewals ride along with an apply; one per apply keeps the job's duration
# bounded (each can take an ACME issuance).
RENEWALS_PER_APPLY = 1


def _context(remote_name: str, *, public: bool):
    from sandbox.commands.host_tailnet_name import default_deps, resolve_context
    from sandbox.hosting.tailnet_names import models, state
    config = models.TailnetNameConfig.load()
    ctx = resolve_context(remote_name, config, public=public)
    # A Sandbox-owned directory with no project descriptor: only the machine's
    # own token reference is used, never the stack source's secret profiles.
    return config, ctx, default_deps(str(state.default_root()))


def live_slugs(remote_name: str, entry) -> dict[str, str] | None:
    """slug -> ``live`` | ``reaped`` from the lifetime store, plus local stack
    records as ``live``; None when either cannot be read (then nothing is an orphan)."""
    from sandbox.hosting import stack_registry
    from sandbox.hosting.lifetime import client as lifetime_client
    try:
        listing = lifetime_client.StoreClient(lifetime_client.ssh_runner(entry), None).list()
        records = stack_registry.records_for_remote(stack_registry.read(), remote_name)
    except Exception:
        return None
    slugs: dict[str, str] = {}
    for item in listing.get("records") or []:
        if isinstance(item, dict) and item.get("kind") == "stack" and item.get("slug"):
            state = "reaped" if item.get("state") == "reaped" else "live"
            if slugs.get(item["slug"]) != "live":
                slugs[item["slug"]] = state
    for record in records:
        if record.get("slug") and record["slug"] not in slugs:
            slugs[record["slug"]] = "live"
    return slugs


def _report(fqdn: str | None, status: str, code: str | None = None, error: str | None = None) -> dict:
    out = {"fqdn": fqdn, "status": status}
    if code:
        out.update(code=code, error=(error or code)[:300])
    return out


def _retrying(call: Callable, sleep: Callable[[float], None]):
    for attempt in range(BUSY_ATTEMPTS):
        result = call()
        if result.code != BUSY or attempt == BUSY_ATTEMPTS - 1:
            return result
        sleep(BUSY_WAIT_SECONDS)


def _remove(ctx, deps, record, sleep) -> dict:
    from sandbox.hosting.tailnet_names import models, service
    result = _retrying(lambda: service.remove(
        ctx, deps, models.TailnetName(record.fqdn, record.zone),
        expect_stack=record.stack, expect_upstream=record.upstream), sleep)
    return (_report(record.fqdn, "removed") if result.ok
            else _report(record.fqdn, "failed", result.code, result.error))


def ensure(remote_name: str, slug: str, fqdn: str, port: int, *, context: Callable | None = None,
           liveness: Callable | None = None, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Publish ``fqdn`` -> ``127.0.0.1:<port>`` for ``slug``; never raises."""
    from sandbox.hosting.front_door import FrontDoorError
    from sandbox.hosting.tailnet_names import models, service
    try:
        config, ctx, deps = (context or _context)(remote_name, public=True)
        name = models.TailnetName.parse(fqdn, config.zones)
        upstream = models.Upstream.parse(f"127.0.0.1:{int(port)}")
        store = deps.store(ctx.target.name)
        slugs = (liveness or live_slugs)(remote_name, ctx.target.entry)
        swept = []
        for record in store.load().values():
            if not record.stack or record.stack == slug or record.fqdn == name.fqdn:
                continue
            # Live stacks never share a port: another stack's name on this one
            # outlived its stack. Otherwise only positive evidence counts.
            if record.upstream == str(upstream) or (
                    slugs is not None and slugs.get(record.stack, "gone") != "live"):
                swept.append(_remove(ctx, deps, record, sleep))
        result = _retrying(lambda: service.add(ctx, deps, name, upstream, stack=slug), sleep)
    except TailnetNameError as exc:
        status = "unconfigured" if exc.code == CONFIG_MISSING else "refused"
        return _report(fqdn, status, exc.code, getattr(exc, "detail", None) or str(exc))
    except FrontDoorError as exc:
        return _report(fqdn, "refused", exc.code, str(exc))
    except Exception as exc:  # the apply already succeeded; report, never fail it
        return _report(fqdn, "failed", "tailnet_name_internal_error", type(exc).__name__)
    report = _report(fqdn, "ready") if result.ok else _report(fqdn, "failed", result.code, result.error)
    if swept:
        report["swept"] = swept
    if result.ok:
        # The template's mail_hostname changed (for example to a name under a
        # tailnet wildcard): the stack's earlier names, and their DNS records, go
        # once the new one is published. A failure is retried by the next apply.
        try:
            retired = [_remove(ctx, deps, record, sleep) for record in store.load().values()
                       if record.stack == slug and record.fqdn != name.fqdn]
        except Exception as exc:
            retired = [_report(None, "failed", "tailnet_name_internal_error", type(exc).__name__)]
        if retired:
            report["retired"] = retired
    try:
        renewed = _renew_due(ctx, deps, exclude=name.fqdn, slugs=slugs)
    except Exception:  # renewal is retried by the next apply
        renewed = []
    if renewed:
        report["renewed"] = renewed
    return report


def _renew_due(ctx, deps, *, exclude: str, slugs: dict | None) -> list[str]:
    """Renew at most one other live stack name inside the renewal window.

    Each stack apply on the host renews the earliest-expiring due stack inbox
    certificate; ``renew --all`` (with ``--server-held`` for a joined remote's
    server) covers a host with too few applies.
    """
    from sandbox.hosting.tailnet_names import models, service
    if slugs is None:
        return []
    horizon = deps.now() + dt.timedelta(days=models.RENEWAL_WINDOW_DAYS)
    due = []
    for record in deps.store(ctx.target.name).load().values():
        if (not record.stack or record.fqdn == exclude or record.state != "published"
                or slugs.get(record.stack) != "live"):
            continue
        expiry = record.cert_not_after and dt.datetime.strptime(
            record.cert_not_after, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        if not expiry or expiry <= horizon:
            due.append((record.cert_not_after or "", record))
    renewed = []
    for _expiry, record in sorted(due, key=lambda item: item[0])[:RENEWALS_PER_APPLY]:
        if service.renew(ctx, deps, models.TailnetName(record.fqdn, record.zone)).ok:
            renewed.append(record.fqdn)
    return renewed


def release(remote_name: str, slug: str, *, context: Callable | None = None,
            sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    """Remove every name tagged for ``slug``; never raises. A name left behind
    (still busy, a failed step) is swept by the next apply on this machine."""
    from sandbox.hosting.front_door import FrontDoorError
    try:
        config, ctx, deps = (context or _context)(remote_name, public=False)
        records = [row for row in deps.store(ctx.target.name).load().values() if row.stack == slug]
    except TailnetNameError as exc:
        if exc.code == CONFIG_MISSING:
            return []
        return [_report(None, "refused", exc.code, getattr(exc, "detail", None) or str(exc))]
    except FrontDoorError as exc:
        return [_report(None, "refused", exc.code, str(exc))]
    except Exception as exc:
        return [_report(None, "failed", "tailnet_name_internal_error", type(exc).__name__)]
    reports = []
    for record in records:
        try:
            reports.append(_remove(ctx, deps, record, sleep))
        except Exception as exc:
            reports.append(_report(record.fqdn, "failed", "tailnet_name_internal_error",
                                   type(exc).__name__))
    return reports
