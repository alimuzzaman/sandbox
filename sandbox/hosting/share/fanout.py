"""Spec 088 controller fanout. Private pages stay in memory; recovery stores ids only.

Lock order: original parent ledger, sorted unique target effect ownership, short
state snapshots. No network operation holds the shared controller state lock.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from sandbox.hosting import login_service as login
from sandbox.hosting.share import ShareRefusal
from sandbox.hosting.share import attempts as ledger, stack_page, controller

CAPABILITIES = frozenset({"stack-page-v2", "stack-signin-pool-v1", "wordpress-claim-v1", "companion-status-v1"})


class CoverageRefusal(ShareRefusal):
    def __init__(self, gaps):
        super().__init__("stack_share_coverage_incomplete", "stack access coverage is incomplete")
        self.gaps = sorted((dict(row) for row in gaps), key=lambda row: tuple(str(row.get(k, "")) for k in
                          ("stack", "target", "hostname", "piece", "code", "fix")))


@dataclass(frozen=True)
class PlannedGrant:
    found: object
    context: stack_page.TargetContext
    minted: object
    page: dict
    cleanup: ledger.CleanupDescriptor
    request: object
    runtime: controller.BoundRuntime


def root() -> Path:
    from sandbox.core import _paths
    return _paths.RUNTIME_DIR / "share-links" / "attempts"


def target(found) -> ledger.Target:
    return ledger.Target(found.remote, found.validated["project"], found.validated["environment"])


def repository(parent) -> ledger.AttemptRepository:
    return ledger.AttemptRepository(root(), parent)


def recorded(remote_name, *, stack=None, link_id=None):
    """Read original ledgers BEFORE any current manifest/host resolution."""
    rows = ledger.AttemptRepository.list_recorded(root())
    matches = tuple(row for row in rows if row.parent.target.remote == remote_name
                    and (stack is None or row.parent.stack == stack)
                    and (link_id in (None, "all") or row.grants[0].id == link_id))
    if link_id not in (None, "all") and len(matches) > 1:
        raise ShareRefusal("share_failed", "ambiguous recorded parent link")
    return matches


def _label(identity):
    return f"{identity.remote}/{identity.project}/{identity.environment}"


def summary(attempt):
    return {"attempt_id": attempt.id, "phase": attempt.phase,
            "link_id": attempt.grants[0].id, "expires_at": attempt.expires_at,
            "target": _label(attempt.parent.target), "stack": attempt.parent.stack,
            "grants": [{"target": _label(g.target), "entry": g.entry, "id": g.id,
                        "expires_at": g.expires_at, "status": g.disposition,
                        "gate_revoked": g.gate_revoked, "wordpress_revoked": g.wordpress_revoked}
                       for g in attempt.grants]}


def pending(attempt, reasons=None):
    return {"ok": False, "attempt_id": attempt.id, "phase": "cleanup_pending",
            "code": "share_cleanup_pending", "message": "Original grants require confirmed cleanup before another share.", "still_live": [
        {"target": _label(grant.target), "entry": grant.entry, "id": grant.id,
         "expires_at": grant.expires_at, "status": grant.disposition,
         "reason_code": (reasons or {}).get(grant.id, "cleanup_unconfirmed")}
        for grant in attempt.still_live(int(time.time()))]}


def compose_prefix(compose):
    # All fields have passed the repository's strict descriptor parser before effects.
    parts = ["docker", "compose", "--env-file", compose.env_path, "-p", compose.project_name]
    for path in (*compose.files, compose.override_path):
        parts.extend(("-f", path))
    return "SANDBOX_HOST_ENV_FILE=" + shlex.quote(compose.env_path) + " " + shlex.join(parts)


def _source_directory(compose_file):
    # Descriptor paths are rooted at deploy-src/hosts/<project>, even when the
    # manifest's first compose file is nested below that checkout.
    parts = Path(compose_file).parts
    for index in range(len(parts) - 2):
        if parts[index:index + 2] == ("deploy-src", "hosts"):
            return str(Path(*parts[:index + 3]))
    raise ShareRefusal("share_runtime_unknown", "original source checkout cannot be identified")


def audit_grant(grant, op, *, outcome, code=None, found=None):
    from sandbox.commands.host_share import _audit
    _audit(found, op, remote_name=grant.target.remote, target_label=_label(grant.target),
           outcome=outcome, code=code, entry=grant.entry, kind="stack", link_id=grant.id,
           expires_at=grant.expires_at)


def inspect_services(entry, compose, wordpress):
    """Authoritative bounded read of BOTH services through hosting's observer.

    Full immutable container ids are required. A restart retaining the same
    containers is recoverable; replacement containers refuse destructive cleanup.
    No environment values/config file contents are requested by this observer.
    """
    from sandbox.commands.hosting import _host_observation_command, _remote_checked
    services = sorted({"sandbox-login", *([wordpress.service] if wordpress else [])})
    raw = _remote_checked(entry, _host_observation_command(
        compose_prefix(compose), services, [], 30,
        source_dir=_source_directory(compose.files[0])), timeout=35)
    if not isinstance(raw, str) or len(raw.encode()) > 262144:
        raise ShareRefusal("share_runtime_unknown", "original runtime could not be identified")
    receipt = json.loads(raw)
    if (not isinstance(receipt, dict) or receipt.get("complete") is not True
            or not receipt.get("epoch_start") or receipt.get("epoch_start") != receipt.get("epoch_end")):
        raise ShareRefusal("share_runtime_unknown", "original runtime could not be identified")
    # Compose commonly abbreviates ids. Inspect only the fixed services' ids,
    # requesting immutable id and service label, never Config.Env or credentials.
    short_ids = []
    for service in services:
        rows = [row for row in receipt.get("rows", []) if isinstance(row, dict) and row.get("Service") == service]
        if len(rows) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", str((rows[0].get("ID") or rows[0].get("Id", "")))):
            raise ShareRefusal("share_runtime_unknown", "original runtime could not be identified")
        short_ids.append(rows[0].get("ID") or rows[0]["Id"])
    command = shlex.join(["docker", "inspect", "--format",
        '{{json .Id}} {{json (index .Config.Labels "com.docker.compose.service")}}', *short_ids])
    raw_ids = _remote_checked(entry, command, timeout=15)
    if not isinstance(raw_ids, str) or len(raw_ids) > 4096:
        raise ShareRefusal("share_runtime_unknown", "immutable runtime identities unavailable")
    ids = []
    for line in raw_ids.splitlines():
        match = re.fullmatch(r'"([0-9a-f]{64})" "([A-Za-z0-9_.-]+)"', line)
        if match is None:
            raise ShareRefusal("share_runtime_unknown", "immutable runtime identities unavailable")
        ids.append((match[2], match[1]))
    ids.sort()
    if ([service for service, _ in ids] != services
            or any(not full.startswith(short) for (_, full), short in zip(ids, short_ids))):
        raise ShareRefusal("share_runtime_unknown", "immutable runtime identities changed")
    digest = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
    by_service = dict(ids)
    return controller.Services(ledger.RuntimeIdentity("runtime_fingerprint", digest),
                               by_service["sandbox-login"], by_service.get(wordpress.service) if wordpress else None)


def observe(entry, compose, wordpress):
    return inspect_services(entry, compose, wordpress).identity


def descriptor(found, minted, page):
    import sandbox.core._remote as remote
    import sandbox.core._hosting as hosting
    home = remote.resolve_sandbox_home(found.entry)
    validated = found.validated
    runtime = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    source = f"{home}/deploy-src/hosts/{validated['project']}"
    compose = ledger.ComposeCleanup(hosting.compose_project_name(validated), tuple(
        str(Path(source) / path) for path in validated["compose"]["files"]),
        runtime + "/compose.override.yml", runtime + "/environment.env")
    wp = page.get("wordpress")
    wordpress = ledger.WordPressCleanup(wp["service"], wp["container_path"], minted.link_id[2:], minted.expires_at) if wp else None
    return ledger.CleanupDescriptor(validated["login_service"]["share"]["hostname"],
                                    observe(found.entry, compose, wordpress), compose, wordpress)


def cleanup(repo, attempt, *, settled_originals=frozenset(), ownership=None, require_opt_in=False):
    """Caller holds parent and every target lease. Parent first; never abort siblings."""
    import sandbox.core._remote as remote
    repo.expire(now=int(time.time()))
    attempt = repo.read(attempt.id)
    reasons, counts = {}, {"revoked_links": 0, "revoked_sessions": 0, "revoked_keys": 0}
    if attempt.phase in ("revoked", "expired"):
        return {"ok": True, **summary(attempt), **counts}
    if ownership is None:
        if any(grant.disposition in ("live", "unknown") for grant in attempt.grants):
            with controller.hold(attempt.target_order) as physical:
                return cleanup(repo, attempt, settled_originals=settled_originals, ownership=physical,
                               require_opt_in=require_opt_in)
        ownership = controller.Ownership()  # Planned-only attempts have no remote effects.
    # Prepared and active phases require unchanged planned/live grants. Enter
    # cleanup before persisting any per-grant acknowledgement.
    repo.transition(attempt.id, "revoking" if attempt.phase in ("active", "cleanup_pending") else "cleanup_pending")
    # A durably planned grant has never crossed begin_mint, hence zero effects.
    # Close those original ids locally without pretending a remote revoke ran.
    for grant in attempt.grants:
        if grant.disposition == "planned":
            repo.update_grant(attempt.id, grant.id, "revoked")
            if grant.cleanup and grant.cleanup.wordpress:
                repo.confirm_wordpress_removed(attempt.id, grant.id)
    attempt = repo.read(attempt.id)
    for grant in attempt.grants:
        if grant.disposition not in ("live", "unknown"):
            continue
        if grant.disposition == "unknown" and grant.id in settled_originals and not grant.original_settled:
            repo.confirm_original_settled(attempt.id, grant.id)
            grant = replace(grant, original_settled=True)
        if (grant.disposition == "unknown" and not grant.original_settled
                and grant.id not in settled_originals):
            # An absence probe cannot stop an earlier timed-out exec from
            # writing later. Only this invocation's completion proof permits
            # compensation; a reloaded unknown remains pending until expiry.
            # Legacy schema-2 revoke bits do not establish original completion.
            reasons[grant.id] = "mint_completion_unknown"
            ownership.uncertain.add(grant.target)
            continue
        try:
            entry = remote.get_remote(grant.target.remote)
            if not entry or grant.cleanup is None:
                raise ShareRefusal("share_runtime_unknown", "original runtime unavailable")
            runtime = controller.bind(grant.target, entry, grant.cleanup, ownership)
            if require_opt_in:
                runtime.require_enabled(grant.entry)
            safe = repo.cleanup_for(attempt.id, grant.id, runtime.services.identity)
        except Exception:
            reasons[grant.id] = "original_runtime_unproven"
            continue
        if not grant.gate_revoked:
            try:
                reply = runtime.manage("revoke", grant.entry, grant.id)
                for key in ("revoked_links", "revoked_sessions"):
                    if type(reply.get(key)) is not int or reply[key] < 0:
                        raise ValueError("invalid revoke confirmation")
                    counts[key] += reply[key]
                repo.update_grant(attempt.id, grant.id, "revoked")
            except login.LoginServiceError as exc:
                if exc.code == "site_link_unknown":
                    try:
                        repo.update_grant(attempt.id, grant.id, "revoked")
                    except Exception:
                        reasons[grant.id] = "gate_cleanup_unknown"
                else:
                    reasons[grant.id] = "gate_cleanup_unknown"
            except Exception:
                reasons[grant.id] = "gate_cleanup_unknown"
        # WP cleanup is required even when the gate reports authoritative absence.
        if safe.wordpress and not grant.wordpress_revoked:
            try:
                output = runtime.remove_wordpress()
                if str(output).strip() != "sandbox-key-absent":
                    raise ValueError("unconfirmed key removal")
                repo.confirm_wordpress_removed(attempt.id, grant.id)
                counts["revoked_keys"] += 1
            except Exception:
                reasons[grant.id] = "wordpress_cleanup_unknown"
    current = repo.read(attempt.id)
    for grant in current.grants:
        audit_grant(grant, "revoke", outcome="refused" if grant in current.still_live(int(time.time())) else "ok",
                    code=reasons.get(grant.id))
    if current.still_live(int(time.time())):
        repo.transition(attempt.id, "cleanup_pending")
        return {**pending(repo.read(attempt.id), reasons), **counts}
    # Unattempted planned grants had zero effects; repository requires terminal dispositions.
    if any(grant.disposition == "planned" for grant in current.grants):
        repo.transition(attempt.id, "cleanup_pending")
        return {**pending(repo.read(attempt.id)), **counts}
    repo.transition(attempt.id, "revoked")
    return {"ok": True, **summary(repo.read(attempt.id)), **counts}


def revoke_attempt(attempt, *, require_opt_in=False):
    from sandbox.commands.hosting import _host_share_effects
    repo = repository(attempt.parent)
    with repo.locked():
        current = repo.read(attempt.id)
        try:
            with _host_share_effects(current.target_order):
                return cleanup(repo, current, require_opt_in=require_opt_in)
        except Exception:
            # Persistence/lease/transport failures cannot turn cleanup into success.
            if current.phase in ("revoked", "expired"):
                return {"ok": True, **summary(current)}
            try:
                latest = repo.read(current.id)
                if latest.phase == "active":
                    repo.transition(current.id, "revoking")
                repo.transition(current.id, "cleanup_pending")
                current = repo.read(current.id)
            except Exception:
                pass
            for grant in current.still_live(int(time.time())):
                audit_grant(grant, "revoke", outcome="refused", code="cleanup_unconfirmed")
            return pending(current)


def create(found, request, *, email=None, requested_entry=None, require_opt_in=False):
    from sandbox.commands import host_share as command
    from sandbox.commands.hosting import _host_share_effects
    from sandbox.hosting import stack_registry
    import sandbox.core._hosting as hosting
    from sandbox.hosting import share
    parent = ledger.Parent(target(found), found.stack["slug"])
    repo = repository(parent)
    with repo.locked():
        for previous in repo.list():
            if previous.phase not in ("active", "revoked", "expired"):
                with _host_share_effects(previous.target_order):
                    result = cleanup(repo, previous, require_opt_in=require_opt_in)
                if not result["ok"]:
                    return result
        members, gaps = [found], []
        def gap(member, code, fix, piece="target", hostname=None):
            row = {"stack": (member.stack or {}).get("slug", parent.stack), "target": member.label,
                   "piece": piece, "code": code, "fix": fix}
            if hostname:
                row["hostname"] = hostname
            gaps.append(row)
        uses = found.stack.get("uses") or []
        if len(uses) > 4:
            gap(found, "companion_overflow", "declare at most four direct uses")
        for slug in uses:
            try:
                child = command.resolve_target(found.remote, stack=slug)
                members.append(child)
            except Exception:
                gaps.append({"stack": str(slug), "target": found.remote, "piece": "target",
                             "code": "companion_unavailable", "fix": "restore the recorded same-remote stack and manifest"})
        contexts, sign_ins, snapshots, requests, windows = [], {}, {}, {}, []
        state = hosting.load_host_state()
        for member in members:
            declaration = member.validated.get("login_service") or {}
            # Coverage names the infrastructure entry 'share'. Normalize only a
            # proven equivalent gate for its pure page view; effects retain the
            # real declared name and linked access metadata is unchanged.
            view_declaration = declaration
            entries = declaration.get("entries") or {}
            gate_host = (declaration.get("share") or {}).get("hostname")
            equivalents = [name for name, config in entries.items()
                           if config.get("mode") == "site_gate" and config.get("hostname") == gate_host]
            if "share" not in entries and len(equivalents) == 1:
                original = equivalents[0]
                view_declaration = {**declaration, "entries": {
                    **{name: config for name, config in entries.items() if name != original},
                    "share": entries[original]}}
            context = stack_page.TargetContext(member.remote, member.stack["slug"], member.validated,
                view_declaration, member.stack, commit=member.stack.get("commit"))
            contexts.append(context)
            for item in stack_page.coverage(context).gaps:
                gap(member, item.code, item.guidance, "access", item.hostname)
            try:
                selected = command._entry_name(declaration, requested_entry if member is found else None)
                requests[target(member)] = replace(request, entry=selected)
            except (ShareRefusal, KeyError, TypeError):
                gap(member, "share_entry_unavailable", "declare or select exactly one site_gate entry", "entry")
            if not (declaration.get("share") or {}).get("agent_mint"):
                gap(member, "agent_mint_not_enabled", "enable share.agent_mint and apply", "opt_in")
            try:
                login.assert_applicable(declaration, now=time.time())
                end = login.enabled_until_epoch(declaration)
                if end - int(time.time()) < 60:
                    raise ValueError("short window")
                windows.append(end)
            except Exception:
                gap(member, "share_window_expired", "enable the gate with at least 60 seconds remaining", "window")
            selected_request = requests.get(target(member))
            if selected_request is not None:
                try:
                    reply = command._remote_site(member, "capabilities", selected_request.entry)
                    if (reply.get("ok") is not True or not isinstance(reply.get("capabilities"), list)
                            or not CAPABILITIES.issubset(set(reply["capabilities"]))):
                        raise ValueError("unsupported capabilities")
                except Exception:
                    gap(member, "share_protocol_unsupported", "restore the gate and install all four stack-page capabilities", "protocol")
            actions = []
            try:
                saved = (state.get("hosts", {}).get(hosting.state_key(member.remote, member.validated)) or {}).get(login.STATE_KEY)
                for name in (declaration.get("share") or {}).get("stack_sign_in", []):
                    config = declaration["entries"][name]
                    row = {"entry": name}
                    if config["mode"] == "magic_link":
                        try:
                            row["email"] = login.resolve_email(config, requested=email, replace_default=False,
                                saved=saved, stdin=stack_page._NoPrompt(), stderr=__import__("sys").stderr).address
                        except Exception:
                            gap(member, "share_email_unavailable", "resolve an allowed sign-in email", "email")
                    actions.append(row)
                sign_ins[(context.remote, context.slug)] = actions
                stack_page.build_v2(context, sign_in=actions)
            except Exception:
                gap(member, "share_page_unavailable", "resolve declared sign-in email and exact page bounds", "page")
            snapshots[target(member)] = (state.get("hosts", {}).get(hosting.state_key(member.remote, member.validated)), member.stack)
        for item in stack_page.coverage_all(contexts[0], contexts[1:]):
            # Includes duplicate and remote bounds in addition to per-target coverage.
            if item.code in ("companion_duplicate", "remote_mismatch"):
                gaps.append({"stack": item.slug, "target": item.remote, "piece": "target", "code": item.code, "fix": item.guidance})
        if email is not None and not any("email" in action for actions in sign_ins.values() for action in actions):
            gap(found, "share_email_unavailable", "--email requires a declared magic_link sign-in", "email")
        if gaps:
            raise CoverageRefusal(gaps)
        request = requests[target(found)]
        targets = tuple(sorted({target(member) for member in members}))
        with _host_share_effects(targets) as frozen, controller.hold(targets) as physical:
            registry = stack_registry.read().get("stacks", {})
            for member in members:
                original_host, original_stack = snapshots[target(member)]
                if (frozen.get("hosts", {}).get(hosting.state_key(member.remote, member.validated)) != original_host
                        or registry.get(stack_registry.record_key(member.remote, member.stack["slug"])) != original_stack
                        or hosting.validate_manifest(member.project_dir, member.validated["environment"]) != member.validated):
                    raise ShareRefusal("share_target_changed", "target changed during share preparation; retry")
            pages = stack_page.prepare_pages(contexts[0], contexts[1:], sign_ins=sign_ins)
            now = int(time.time())
            end = min(now + request.ttl_seconds, *windows)
            if end - now < 60:
                raise ShareRefusal("share_window_expired", "every stack needs at least 60 seconds remaining")
            minted = tuple(share.mint(requests[target(member)], now=now, ends_at=end) for member in members)
            children = [{"slug": context.slug, "target": _label(target(member)),
                "hostname": member.validated["login_service"]["share"]["hostname"],
                "link_id": link.link_id, "expires_at": link.expires_at, "token": link.token,
                "title": page["title"], "hosts": page["hosts"], "notes": page["notes"]}
                for member, context, link, page in zip(members[1:], contexts[1:], minted[1:], pages[1:])]
            pages = (stack_page.build_v2(contexts[0], sign_in=sign_ins[(found.remote, contexts[0].slug)], companions=children), *pages[1:])
            plan = []
            for member, context, link, page in zip(members, contexts, minted, pages):
                safe = descriptor(member, link, page)
                runtime = controller.bind(target(member), member.entry, safe, physical)
                runtime.require_applied(member, requests[target(member)].entry)
                plan.append(PlannedGrant(member, context, link, stack_page.with_key(page, link.link_id),
                                        safe, requests[target(member)], runtime))
            plan = tuple(plan)
            for row in plan:
                stack_page.gate_record(row.page, row.request.entry, row.minted.link_id)
            attempt = ledger.Attempt("a-" + uuid.uuid4().hex, parent, now, int(end), "prepared", tuple(
                ledger.Grant(target(row.found), row.request.entry, row.minted.link_id, row.minted.expires_at,
                             cleanup=row.cleanup) for row in plan))
            repo.prepare(attempt)
            settled_originals = set()
            try:
                for row in (*plan[1:], plan[0]):
                    repo.begin_mint(attempt.id, row.minted.link_id)
                    settled_originals.add(row.minted.link_id)  # No effect started yet.
                    # Re-prove container identity immediately before the first external effect.
                    repo.cleanup_for(attempt.id, row.minted.link_id,
                                     observe(row.found.entry, row.cleanup.compose, row.cleanup.wordpress))
                    settled_originals.discard(row.minted.link_id)
                    stack_page.install_wordpress_key(row.found, row.page, row.minted.expires_at, runtime=row.runtime)
                    settled_originals.add(row.minted.link_id)
                    settled_originals.discard(row.minted.link_id)
                    def completed(grant_id=row.minted.link_id):
                        settled_originals.add(grant_id)
                        repo.confirm_original_settled(attempt.id, grant_id)
                    _, reply = command.mint_link(row.found.validated, row.found.entry, row.request,
                                                 minted=row.minted, stack_record=row.page,
                                                 on_completed=completed, runtime=row.runtime)
                    if (reply.get("ok") is not True or reply.get("id") != row.minted.link_id
                            or type(reply.get("expires_at")) is not int or reply["expires_at"] != row.minted.expires_at
                            or type(reply.get("uses")) is not type(request.uses)
                            or reply.get("uses") != request.uses):
                        raise ShareRefusal("share_mint_unknown", "gate confirmation did not match the prepared grant")
                    repo.update_grant(attempt.id, row.minted.link_id, "live")
                    audit_grant(next(g for g in attempt.grants if g.id == row.minted.link_id),
                                "create", outcome="ok", found=row.found)
                for row in plan:
                    row.runtime.require_applied(row.found, row.request.entry)
                repo.transition(attempt.id, "active")
            except Exception:
                for grant in attempt.grants:
                    audit_grant(grant, "create", outcome="refused", code="share_mint_unknown")
                try:
                    result = cleanup(repo, repo.read(attempt.id), settled_originals=settled_originals,
                                     ownership=physical)
                except Exception:
                    # No URL is returned if acceptance or durable cleanup cannot be saved.
                    # Conservatively expose the original ids, including planned rows.
                    return pending(replace(attempt, grants=tuple(
                        replace(g, disposition="unknown") for g in attempt.grants)))
                if not result["ok"]:
                    return result
                raise ShareRefusal("share_failed", "share creation failed; original grants were revoked") from None
            return {"_minted": minted[0], "target": found.label, "entry": request.entry, "kind": request.kind,
                "scope": request.scope, "expires_at": minted[0].expires_at, "expires_clamped": minted[0].clamped,
                "uses": request.uses, "hostname": found.validated["login_service"]["share"]["hostname"],
                "attempt_id": attempt.id, "phase": "active"}
