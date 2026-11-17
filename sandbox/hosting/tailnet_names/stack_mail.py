"""A stack's tailnet-only inbox name (``stack.mail_hostname``), kept with the stack.

After each completed apply the name is published (or kept) at the stack's
``mailpit`` port through the ordinary ``add`` flow, so a rerun is a no-op and a
changed port replaces the upstream. ``down`` and the reaper remove the names
the stack's applies created. A name added by hand (no ``stack`` tag) is never
re-pointed or removed here, and a name tagged for another stack is refused.

Neither call raises: a stack apply or teardown never fails because of its
inbox name. Each returns a small report for the command's output.
"""
from __future__ import annotations

import time
from typing import Callable

from sandbox.hosting.tailnet_names.models import (
    BUSY,
    CONFIG_MISSING,
    TailnetNameError,
)

STACK_CONFLICT = "tailnet_name_stack_conflict"
# A host lock held by another apply clears in seconds to minutes; retry a few
# times, then leave the rest to the next apply or `sb host tailnet-name add`.
BUSY_ATTEMPTS = 4
BUSY_WAIT_SECONDS = 20.0


def _context(remote_name: str, *, public: bool):
    from sandbox.commands.host_tailnet_name import default_deps, resolve_context
    from sandbox.hosting.tailnet_names import models, state
    config = models.TailnetNameConfig.load()
    ctx = resolve_context(remote_name, config, public=public)
    # A Sandbox-owned directory with no project descriptor: the stack's own
    # source never shapes the broker's secret profiles.
    return config, ctx, default_deps(str(state.default_root()))


def _report(fqdn: str | None, status: str, code: str | None = None, error: str | None = None) -> dict:
    out = {"fqdn": fqdn, "status": status}
    if code:
        out.update(code=code, error=(error or code)[:300])
    return out


def ensure(remote_name: str, slug: str, fqdn: str, port: int, *,
           context: Callable | None = None, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Publish ``fqdn`` -> ``127.0.0.1:<port>`` for ``slug``; never raises."""
    from sandbox.hosting.front_door import FrontDoorError
    from sandbox.hosting.tailnet_names import models, service
    try:
        config, ctx, deps = (context or _context)(remote_name, public=True)
        name = models.TailnetName.parse(fqdn, config.zones)
        upstream = models.Upstream.parse(f"127.0.0.1:{int(port)}")
        existing = deps.store(ctx.target.name).get(name.fqdn)
        if existing is not None and existing.stack not in (None, slug):
            return _report(fqdn, "refused", STACK_CONFLICT,
                           f"{fqdn} belongs to stack {existing.stack}")
        ours = existing is None or existing.stack == slug
        # Live stacks never share a port, so another stack's name on this port
        # outlived its stack (a reap with no tailnet context): remove it, or
        # it would serve this stack's inbox and the panel would see two names.
        for stale in deps.store(ctx.target.name).load().values():
            if stale.stack not in (None, slug) and stale.upstream == str(upstream) \
                    and stale.fqdn != name.fqdn:
                service.remove(ctx, deps, models.TailnetName(stale.fqdn, stale.zone))
        for attempt in range(BUSY_ATTEMPTS):
            result = service.add(ctx, deps, name, upstream, replace_upstream=ours,
                                 stack=slug if ours else None)
            if result.code != BUSY or attempt == BUSY_ATTEMPTS - 1:
                break
            sleep(BUSY_WAIT_SECONDS)
    except TailnetNameError as exc:
        status = "unconfigured" if exc.code == CONFIG_MISSING else "refused"
        return _report(fqdn, status, exc.code, getattr(exc, "detail", None) or str(exc))
    except FrontDoorError as exc:
        return _report(fqdn, "refused", exc.code, str(exc))
    except Exception as exc:  # the apply already succeeded; report, never fail it
        return _report(fqdn, "failed", "tailnet_name_internal_error", type(exc).__name__)
    report = _report(fqdn, "ready") if result.ok else _report(fqdn, "failed", result.code, result.error)
    try:
        renewed = _renew_due(ctx, deps, exclude=name.fqdn)
    except Exception:  # renewal is retried by the next apply
        renewed = []
    if renewed:
        report["renewed"] = renewed
    return report


def _renew_due(ctx, deps, *, exclude: str) -> list[str]:
    """Renew the other stacks' names inside the renewal window.

    Any stack's apply on the host keeps every stack inbox certificate current,
    so no separate schedule is needed while the host sees an apply at least
    once a month. A name that fails is left for the next apply.
    """
    import datetime as dt
    from sandbox.hosting.tailnet_names import models, service
    horizon = deps.now() + dt.timedelta(days=models.RENEWAL_WINDOW_DAYS)
    renewed = []
    for record in deps.store(ctx.target.name).load().values():
        if not record.stack or record.fqdn == exclude:
            continue
        expiry = record.cert_not_after and dt.datetime.strptime(
            record.cert_not_after, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        if expiry and expiry > horizon:
            continue
        outcome = service.renew(ctx, deps, models.TailnetName(record.fqdn, record.zone))
        if outcome.ok:
            renewed.append(record.fqdn)
    return renewed


def release(remote_name: str, slug: str, *, context: Callable | None = None) -> list[dict]:
    """Remove every name tagged for ``slug``; never raises."""
    from sandbox.hosting.front_door import FrontDoorError
    from sandbox.hosting.tailnet_names import models, service
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
            result = service.remove(ctx, deps, models.TailnetName(record.fqdn, record.zone))
        except Exception as exc:
            reports.append(_report(record.fqdn, "failed", "tailnet_name_internal_error",
                                   type(exc).__name__))
            continue
        reports.append(_report(record.fqdn, "removed") if result.ok
                       else _report(record.fqdn, "failed", result.code, result.error))
    return reports
