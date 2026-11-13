"""Fixed create/list/revoke for an admitted Tailnet panel on the installed owner.

The controller path remains separate. No failure here falls back to a remote,
SSH, an invented original job or the paused URL/network sharing authority.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import io
import hashlib
import secrets
import time
import uuid
from pathlib import Path

from sandbox.hosting import login_service as login
from sandbox.hosting import panel_records
from sandbox.hosting import share
from sandbox.hosting import owner_panel_gate as gate
from sandbox.hosting.owner_share_record import digest
from sandbox.hosting.owner_share_store import Store, apply_lease
from sandbox.hosting.share import ShareRefusal, stack_page


def _context(grant):
    summary, facts = grant.summary, grant.summary["owner_share"]
    slug = summary["stack"]["slug"]
    links = panel_records.read_server_records(Path(grant.owner.sandbox_home) / "runtime")["links"]
    linked = tuple(row for row in links if row["slug"] == slug and row["remote"] == summary["remote"])
    record = {**summary["stack"], "uses": facts["uses"],
              "hostnames": [route["hostname"] for route in summary["routes"]]}
    return stack_page.TargetContext(grant.request.remote, slug, grant.validated,
        grant.validated["login_service"], record, linked, summary["commit"], "serving")


def _binding(grant):
    return {"stack": grant.summary["stack"]["slug"], "snapshot": grant.snapshot,
            "applied_digest": digest(grant.summary["owner_share"]),
            "gate_id": grant.gate_id, "wordpress_id": grant.wordpress_id}


def _same_original(grant, original):
    if _binding(grant) != original:
        raise gate._refuse("owner_share_original_target_changed")


def _audit(store, request, op, *, outcome, link_id=None, expires_at=None, code=None):
    # The approved policy admits a panel peer, not a named operator.
    share.append_audit(share.audit_record(op, remote=request.remote, target=f"stack:{request.stack}",
        entry="share", outcome=outcome, code=code, link_id=link_id, expires_at=expires_at,
        controller="owner-panel", owner="tailnet-panel"), store.root / "audit.jsonl")


@contextmanager
def _held(grants, row):
    from sandbox.hosting.recovery.repository import RecoveryRepository
    owner = grants[0].owner
    repository = RecoveryRepository(Path(owner.sandbox_home) / "runtime" / "hosts.json",
                                    Path(owner.sandbox_home) / "runtime" / "hosting" / "locks")
    leases = []
    with ExitStack() as held:
        for grant in sorted(grants, key=lambda g: (g.snapshot["project"], g.snapshot["environment"])):
            leases.append(held.enter_context(apply_lease(owner.sandbox_home,
                grant.snapshot["project"], grant.snapshot["environment"], row["id"],
                resume=(row.get("operation") or {}).get("state") == "settled")))
        for key in sorted({key for grant in grants for key in grant.snapshot["native_keys"]}):
            # Separate port instances permit multiple targets in stable order.
            held.enter_context(repository.target_mutation_port("login-url", timeout_seconds=5)
                               .target_mutation_transaction(key))
        for grant in grants:
            grant.require_current()
        try:
            yield
        finally:
            if (row.get("operation") or {}).get("state") == "unknown":
                for lease in leases:
                    lease["unknown"] = True


def _public(row):
    return {"attempt_id": row["id"], "phase": row["phase"], "link_id": row["grants"][0]["id"],
        "expires_at": row["expires_at"], "stack": row["stack"],
        "target": row["grants"][0]["target"],
        "still_live": [{"id": item["id"], "entry": "share", "expires_at": item["expires_at"],
            "target": item["target"], "status": item["status"]} for item in row["grants"]
            if item["status"] not in ("revoked", "planned")]}


def _pending(row):
    if not row.get("grants"):
        return {"ok": False, "code": "share_cleanup_pending", "phase": "cleanup_pending",
                "attempt_id": row["id"], "still_live": []}
    return {"ok": False, "code": "share_cleanup_pending", **_public(row)}


def _resolve_original(request, row):
    members = [gate.bind(request, original["stack"]) for original in row["bindings"]]
    for member, original in zip(members, row["bindings"]):
        _same_original(member, original)
    return members


def _cleanup(store, request, row, grants):
    if (row.get("operation") or {}).get("state") in ("accepted", "unknown"):
        # No pid-based reconstruction, kill or re-launch after loss/timeout.
        # The retained original may still be writing inside the Docker daemon.
        return _pending(row)
    row["phase"] = "revoking"
    store.save(row)
    counts = {"revoked_links": 0, "revoked_sessions": 0, "revoked_keys": 0}
    for member, original, item in zip(grants, row["bindings"], row["grants"]):
        _same_original(member, original)
        if item["status"] == "planned":
            item["status"] = "revoked"
            store.save(row)
            continue
        if item["status"] == "revoked":
            continue
        # Every child id was retained before the first effect, independent of
        # the parent gate's state. Cleanup never follows a same-name replacement.
        if not item.get("gate_revoked"):
            reply = gate._execute(member, "revoke", row, store, grant_id=item["id"])
            item["gate_revoked"] = True
            counts["revoked_links"] += reply.get("revoked_links", 0)
            counts["revoked_sessions"] += reply.get("revoked_sessions", 0)
            store.save(row)
        if item.get("wordpress") and not item.get("wordpress_revoked"):
            gate._execute(member, "wp_remove", row, store, grant_id=item["id"])
            item["wordpress_revoked"] = True
            counts["revoked_keys"] += 1
            store.save(row)
        item["status"] = "revoked"
        store.save(row)
        _audit(store, request, "revoke", outcome="ok", link_id=item["id"], expires_at=item["expires_at"])
    row["phase"] = "revoked"
    store.save(row)
    return {"ok": True, **_public(row), **counts}


def _saved_email(grant):
    """Read only the admitted target's existing owner-local login default."""
    from sandbox.hosting.recovery.repository import RecoveryRepository
    grant.require_current()
    state = RecoveryRepository(Path(grant.owner.sandbox_home) / "runtime" / "hosts.json").load()
    addresses = set()
    for key in grant.snapshot["native_keys"]:
        record = state["hosts"].get(key)
        if not isinstance(record, dict):
            raise gate._refuse("owner_share_target_changed")
        saved = record.get(login.STATE_KEY)
        if saved:
            addresses.add(login.validate_email(saved))
    if len(addresses) > 1:
        raise gate._refuse("owner_share_email_ambiguous")
    return next(iter(addresses), None)


def _create(store, request, parent, rows):
    for previous in rows:
        if previous["phase"] in ("prepared", "minting", "cleanup_pending", "revoking"):
            if not previous.get("grants"):
                return {"ok": False, "code": "share_cleanup_pending", "phase": "cleanup_pending",
                        "attempt_id": previous["id"], "still_live": []}
            originals = _resolve_original(request, previous)
            gate.observe_original(originals[0], previous, store)
            if (previous.get("operation") or {}).get("state") in ("accepted", "unknown"):
                return _pending(previous)
            with _held(originals, previous):
                result = _cleanup(store, request, previous, originals)
            if not result["ok"]:
                return result
    from sandbox.hosting.share.fanout import CoverageRefusal
    members, gaps = [parent], []
    for slug in parent.summary["owner_share"]["uses"]:
        try:
            members.append(gate.bind(request, slug))
        except Exception:
            gaps.append({"stack": slug, "target": request.remote, "piece": "target",
                "code": "companion_unavailable", "fix": "restore the direct stack's applied owner-sharing evidence"})
    contexts = [_context(member) for member in members]
    for gap in stack_page.coverage_all(contexts[0], contexts[1:]):
        gaps.append({"stack": gap.slug, "target": gap.remote, "hostname": gap.hostname,
                     "piece": "access", "code": gap.code, "fix": gap.guidance})
    actions = {}
    for member, context in zip(members, contexts):
        rows = []
        names = context.declaration["share"].get("stack_sign_in", [])
        try:
            saved = (_saved_email(member) if any(context.declaration["entries"][name]["mode"] == "magic_link"
                                               for name in names) else None)
        except Exception:
            gaps.append({"stack": context.slug, "target": context.remote, "piece": "access",
                "code": "share_email_unavailable", "fix": "restore one allowed saved email for this exact target"})
            saved = None
        for name in names:
            entry = context.declaration["entries"][name]
            action = {"entry": name}
            if entry["mode"] == "magic_link":
                try:
                    action["email"] = login.resolve_email(entry, requested=None, replace_default=False,
                        saved=saved, stdin=stack_page._NoPrompt(), stderr=io.StringIO()).address
                except Exception:
                    gaps.append({"stack": context.slug, "target": context.remote, "piece": "access",
                        "code": "share_email_unavailable", "fix": "save an allowed login email for this exact target"})
            rows.append(action)
        actions[(context.remote, context.slug)] = rows
    if gaps:
        raise CoverageRefusal(gaps)
    base_pages = stack_page.prepare_pages(contexts[0], contexts[1:], sign_ins=actions)
    until = min(login.enabled_until_epoch(member.validated["login_service"]) for member in members)
    requests = [share.LinkRequest("share", "stack", share.STACK_PAGE_PATH,
                share.DEFAULT_TTL_SECONDS, None) for _ in members]
    issued = time.time()
    minted = [share.mint(item, now=issued, ends_at=until) for item in requests]
    row = {"id": "a-" + uuid.uuid4().hex, "remote": request.remote, "stack": request.stack,
        "phase": "prepared", "expires_at": min(item.expires_at for item in minted),
        "bindings": [_binding(member) for member in members], "grants": [
            {"id": item.link_id, "expires_at": item.expires_at, "status": "planned",
             "target": f"{request.remote}/{member.snapshot['project']}/{member.snapshot['environment']}",
             "wordpress": bool(member.validated.get("autologin"))} for member, item in zip(members, minted)]}
    # Final pages are bounded before the journal or any gate/key effect.
    pages = []
    for index, context in enumerate(contexts):
        companions = [{"slug": child.slug,
            "target": f"{child.remote}/{member.snapshot['project']}/{member.snapshot['environment']}",
            "hostname": child.declaration["share"]["hostname"], "link_id": child_link.link_id,
            "expires_at": child_link.expires_at, "token": child_link.token,
            "title": prepared["title"], "hosts": prepared["hosts"], "notes": prepared["notes"]}
            for child, child_link, member, prepared in zip(contexts[1:], minted[1:], members[1:], base_pages[1:])] if index == 0 else []
        page = stack_page.build_v2(context, sign_in=actions[(context.remote, context.slug)], companions=companions)
        pages.append(stack_page.with_key(page, minted[index].link_id))
    for page, item in zip(pages, minted):
        stack_page.gate_record(page, "share", item.link_id)
    with _held(members, row):
        store.save(row)
        for index in [*range(1, len(members)), 0]:
            member, item, page = members[index], minted[index], pages[index]
            _same_original(member, row["bindings"][index])
            row["phase"] = "minting"
            row["grants"][index]["status"] = "unknown"
            store.save(row)
            _audit(store, request, "create", outcome="started", link_id=item.link_id, expires_at=item.expires_at)
            if page.get("wordpress"):
                gate._execute(member, "wp_install", row, store, grant_id=item.link_id,
                    wordpress={**page["wordpress"], "expires_at": item.expires_at})
            gate._execute(member, "mint", row, store, grant_id=item.link_id, payload={
                "link": share.link_record(requests[index], item), "stack": stack_page.gate_record(page, "share", item.link_id)})
            row["grants"][index]["status"] = "live"
            store.save(row)
            _audit(store, request, "create", outcome="ok", link_id=item.link_id, expires_at=item.expires_at)
        for member in members:
            member.require_current()
        row["phase"] = "active"
        store.save(row)
        result = {"ok": True, "link": share.share_url(parent.validated["login_service"]["share"]["hostname"], minted[0].token),
            "link_id": minted[0].link_id, "target": f"{parent.snapshot['project']}/{parent.snapshot['environment']}",
            "entry": "share", "kind": "stack", "scope": share.STACK_PAGE_PATH,
            "hostname": parent.validated["login_service"]["share"]["hostname"],
            "expires_at": row["expires_at"], "expires_clamped": minted[0].clamped, "uses": None}
        parent.require_current()
        return result


def execute(request: gate.PanelRequest):
    if type(request) is not gate.PanelRequest or request._seal is not gate._SEAL:
        raise gate._refuse("panel_request_refused")
    parent = gate.bind(request)
    with Store(parent.owner.sandbox_home) as store:
        rows = store.rows(request.remote, request.stack)
        _audit(store, request, request.action, outcome="admitted")
        try:
            if request.action == "create":
                return _create(store, request, parent, rows)
            if request.action == "list":
                pending = [row for row in rows if row["phase"] in ("prepared", "minting", "revoking", "cleanup_pending")]
                for original in pending:
                    originals = _resolve_original(request, original)
                    gate.observe_original(originals[0], original, store)
                    if (not original.get("grants") and original.get("kind") in ("wordpress-admin", "observation")
                            and original.get("operation", {}).get("state") == "settled"):
                        with _held(originals, original):
                            original["phase"] = ("observed" if original["kind"] == "observation"
                                                 else "admin-unreported")
                            store.save(original)
                        if original["kind"] == "observation":
                            store.finish_observation(original)
                rows = store.rows(request.remote, request.stack)
                if any(row["phase"] in ("prepared", "minting", "revoking", "cleanup_pending") for row in rows):
                    return {"ok": False, "links": [], "sessions": [],
                        "attempts": [_public(row) for row in rows if row.get("grants")],
                        "partial_reasons": ["cleanup_pending"]}
                observation = {"id": "a-" + uuid.uuid4().hex, "remote": request.remote,
                    "stack": request.stack, "phase": "observed", "grants": [],
                    "kind": "observation", "bindings": [_binding(parent)]}
                with _held([parent], observation):
                    result = gate._execute(parent, "list", observation, store)
                store.finish_observation(observation)
                return {"ok": not any(row["phase"] in ("prepared", "minting", "revoking", "cleanup_pending") for row in rows),
                    "links": result.get("links", []), "sessions": result.get("sessions", []),
                    "attempts": [_public(row) for row in rows if row.get("grants")]}
            if request.link_id.startswith("s-"):
                return _revoke_session(store, request, parent, rows)
            originals = [row for row in rows if row.get("grants") and row["grants"][0]["id"] == request.link_id]
            if len(originals) != 1:
                raise gate._refuse("owner_share_original_unavailable")
            row = originals[0]
            members = _resolve_original(request, row)
            gate.observe_original(members[0], row, store)
            if (row.get("operation") or {}).get("state") in ("accepted", "unknown"):
                return _pending(row)
            with _held(members, row):
                return _cleanup(store, request, row, members)
        except ShareRefusal:
            _audit(store, request, request.action, outcome="refused")
            pending = [row for row in store.rows(request.remote, request.stack)
                       if row["phase"] in ("prepared", "minting", "revoking", "cleanup_pending")]
            if pending:
                return _pending(pending[-1])
            raise


def _revoke_session(store, request, parent, rows):
    pending = [row for row in rows if row["phase"] in ("prepared", "minting", "revoking", "cleanup_pending")]
    same = [row for row in pending if row.get("kind") == "session-revoke"
            and row.get("session_id") == request.link_id]
    if pending and (len(same) != 1 or len(pending) != 1):
        return _pending(pending[0])
    if same:
        row = same[0]
        _same_original(parent, row["bindings"][0])
        gate.observe_original(parent, row, store)
        if row["operation"]["state"] in ("accepted", "unknown"):
            return _pending(row)
    else:
        row = {"id": "a-" + uuid.uuid4().hex, "remote": request.remote, "stack": request.stack,
            "phase": "observed", "grants": [], "kind": "session-revoke",
            "session_id": request.link_id, "bindings": [_binding(parent)]}
    with _held([parent], row):
        if not row.get("original_parent_link"):
            # A settled preliminary list proves observation completed, not
            # that this session belongs to an owner-panel parent grant.
            listing = gate._execute(parent, "list", row, store)
            row["phase"] = "observed"
            store.save(row)
            matches = [item for item in listing.get("sessions", []) if item.get("id") == request.link_id]
            if len(matches) != 1:
                raise gate._refuse("owner_share_original_unavailable")
            link = matches[0]["link"]
            owners = [original for original in rows if original.get("grants")
                      and original["grants"][0]["id"] == link]
            if len(owners) != 1:
                raise gate._refuse("owner_share_original_unavailable")
            _same_original(parent, owners[0]["bindings"][0])
            row["original_parent_link"] = link
            store.save(row)
        reply = gate._execute(parent, "revoke", row, store, grant_id=request.link_id)
        row["phase"] = "observed"
        store.save(row)
        _audit(store, request, "revoke", outcome="ok", link_id=request.link_id)
    store.finish_observation(row)
    return {"ok": True, "revoked_links": reply.get("revoked_links", 0),
            "revoked_sessions": reply.get("revoked_sessions", 0), "revoked_keys": 0}


def wordpress_admin(request: gate.PanelRequest):
    """The same admitted create authority, narrowed to the applied WP declaration."""
    if type(request) is not gate.PanelRequest or request._seal is not gate._SEAL or request.action != "create":
        raise gate._refuse("panel_request_refused")
    parent = gate.bind(request)
    wp = parent.validated.get("autologin")
    if not wp or login.is_private(parent.validated["login_service"]):
        raise gate._refuse("sign_in_unavailable")
    with Store(parent.owner.sandbox_home) as store:
        for previous in store.rows(request.remote, request.stack):
            if previous["phase"] in ("prepared", "minting", "revoking", "cleanup_pending"):
                raise gate._refuse("owner_share_effect_unknown")
        issued = int(time.time())
        expires = min(issued + wp["ttl_seconds"], login.enabled_until_epoch(parent.validated["login_service"]))
        if expires - issued < 60:
            raise gate._refuse("sign_in_unavailable")
        token = secrets.token_urlsafe(32)
        operation_id, grant_id = "a-" + uuid.uuid4().hex, "l-" + uuid.uuid4().hex
        row = {"id": operation_id, "remote": request.remote, "stack": request.stack,
               "phase": "prepared", "grants": [], "kind": "wordpress-admin",
               "bindings": [_binding(parent)], "expires_at": expires}
        with _held([parent], row):
            store.save(row)
            _audit(store, request, "sign_in", outcome="started", expires_at=expires)
            gate._execute(parent, "wp_admin", row, store, grant_id=grant_id,
                wordpress={"token_hash": hashlib.sha256(token.encode()).hexdigest(),
                           "issued_at": issued, "expires_at": expires})
            parent.require_current()
            row["phase"] = "admin-completed"
            store.save(row)
            _audit(store, request, "sign_in", outcome="ok", expires_at=expires)
            from sandbox.core._hosting import autologin_url
            return {"ok": True, "url": autologin_url(parent.validated, token, expires),
                    "expires_at": expires, "one_time": True, "user": wp["user"]}
