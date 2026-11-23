"""`sb host follow add|status|pause|resume|remove|run-now|key` (spec 089).

For a joined remote every owner command is dispatched as a server CLI durable
job (`sandbox/hosting/server_cli_dispatch.py`) and this handler runs on the
remote inside it, against that host's follow store. Without that job context
the command is refused before any write. `tick --here` (the timer's poll) and
`apply` (one attempt, inside its own server CLI job) are internal.
"""
from __future__ import annotations

import json
from pathlib import Path

from sandbox.core import die
from sandbox.hosting.follow import git, units
from sandbox.hosting.follow import spec as follow_spec
from sandbox.hosting.follow.spec import FollowError
from sandbox.hosting.follow.store import FollowStore, empty_state, utc_now

FOLLOW_ACTIONS = ("add", "status", "pause", "resume", "remove", "run-now", "key", "tick", "apply")
OWNER_ACTIONS = frozenset({"add", "status", "pause", "resume", "remove", "run-now", "key"})


def _store() -> FollowStore:
    return FollowStore()


def owner_context(args):
    """The retained server CLI job this command runs in, or a refusal."""
    from sandbox.hosting.follow.apply import owner_target
    return owner_target(getattr(args, "remote", None), code="follow_requires_joined_remote",
                        message="host follow runs on a joined (admitted Tailnet) remote: pass --remote "
                                "with a joined registration")


def _context(args):
    """``owner_context``, remembered on ``args`` so a later refusal can be audited."""
    context = owner_context(args)
    args._follow_context = context
    return context


def _actor(context) -> str:
    return f"job:{context.job_id}"


def _name(args) -> str:
    return follow_spec.validate_name(getattr(args, "name", None))


def _read(store: FollowStore, name: str) -> dict:
    record = store.read(name)
    if record is None:
        raise FollowError("follow_not_found", f"no follow named {name} on this remote")
    return record


def _public_attempt(attempt: dict | None) -> dict | None:
    if not attempt:
        return None
    return {key: value for key, value in attempt.items() if key != "argv"}


def _view(store: FollowStore, record: dict) -> dict:
    state = record["state"]
    return {
        "name": record["name"], "remote": record["remote"], "template": record["template"],
        "ref": record["ref"], "owner": record["owner"], "environment": record.get("environment"),
        "lifetime": record.get("lifetime"), "generate": record.get("generate"),
        "prepare": record.get("prepare"), "prepare_timeout": record.get("prepare_timeout"),
        "repos": [{**repo, **git.key_status(store.keys, repo["github"])} for repo in record["repos"]],
        "paused": state.get("paused"), "pause_reason": state.get("pause_reason"),
        "retry": state.get("retry"), "applied": state.get("applied"),
        "failed_heads": state.get("failed_heads"), "attempt": _public_attempt(state.get("attempt")),
        "last_check": state.get("last_check"), "stack_seen": state.get("stack_seen"),
        "declared_at": record.get("declared_at"), "updated_at": record.get("updated_at"),
        "audit": store.audit_tail(record["name"], 5),
    }


def _key_rows(store: FollowStore, repos: list[dict]) -> tuple[list[dict], list]:
    rows, created = [], []
    seen: dict[str, dict] = {}
    for repo in repos:
        github = repo["github"]
        if github not in seen:
            seen[github] = git.ensure_key(store.keys, github)
            if seen[github]["deploy_key"] == "created":
                created.append(git.key_path(store.keys, github))
        rows.append({**repo, **seen[github],
                     "add_at": f"https://github.com/{github}/settings/keys/new"})
    return rows, created


GITHUB_STEP = ("Add each public key in GitHub as a deploy key of its repository "
               "(Settings > Deploy keys > Add deploy key) and leave 'Allow write access' unchecked.")


def _add(args) -> dict:
    context = _context(args)
    declaration = follow_spec.build(args, origin=context.origin_remote_name,
                                    routing=dict(context.target_metadata))
    store = _store()
    units.check_host()
    store.ensure()
    name = declaration["name"]
    created: list = []
    try:
        rows, created = _key_rows(store, declaration["repos"])
        with store.lock(name):
            previous = store.read(name)
            attempt = (previous or {}).get("state", {}).get("attempt") or {}
            if attempt.get("status") in ("submitting", "running"):
                raise FollowError("follow_attempt_running",
                                  f"follow {name} has attempt {attempt.get('id')} running; replace it afterwards")
            record = {**declaration, "declared_at": (previous or {}).get("declared_at") or utc_now(),
                      "state": (previous or {}).get("state") or empty_state()}
            # Under the units lock, a concurrent `remove` of the last other
            # follow sees this record before it decides to drop the timer.
            with store.units_lock():
                store.write(record)
                try:
                    timer = units.install(sb_path=context.owner.executable,
                                          sandbox_home=context.owner.sandbox_home)
                except Exception:
                    if previous is None:
                        store.delete(name)
                    else:
                        store.write(previous)
                    raise
            store.audit("replace" if previous else "add", name, actor=_actor(context),
                        owner=declaration["owner"],
                        repos=[f"{r['name']}={r['github']}@{r['branch']}" for r in declaration["repos"]])
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
            path.with_name(path.name + ".pub").unlink(missing_ok=True)
        raise
    return {"ok": True, "action": "follow-add", "name": name, "remote": declaration["remote"],
            "replaced": previous is not None, "repos": rows, "timer": timer, "next": GITHUB_STEP}


def _key(args) -> dict:
    context = _context(args)
    store = _store()
    name = _name(args)
    record = _read(store, name)
    rows, _created = _key_rows(store, record["repos"])
    store.audit("key", name, actor=_actor(context))
    return {"ok": True, "action": "follow-key", "name": name, "repos": rows, "next": GITHUB_STEP}


def _status(args) -> dict:
    context = _context(args)
    store = _store()
    names = [_name(args)] if getattr(args, "name", None) else store.names()
    follows = [_view(store, _read(store, name)) for name in names]
    store.audit("status", getattr(args, "name", None) or "*", actor=_actor(context))
    return {"ok": True, "action": "follow-status", "follows": follows,
            "timer": {"name": units.TIMER, "installed": units.installed(), "interval": units.INTERVAL}}


def _set_state(args, event: str, change) -> dict:
    context = _context(args)
    store = _store()
    name = _name(args)
    with store.lock(name):
        record = _read(store, name)
        change(record["state"], name)
        store.write(record)
        store.audit(event, name, actor=_actor(context))
    return {"ok": True, "action": f"follow-{event.replace('_', '-')}", **_view(store, record)}


def _pause(args) -> dict:
    return _set_state(args, "pause", lambda state, _n: state.update({"paused": True, "pause_reason": "owner"}))


def _resume(args) -> dict:
    return _set_state(args, "resume", lambda state, _n: state.update({"paused": False, "pause_reason": None}))


def _run_now(args) -> dict:
    def change(state, name):
        if state.get("paused"):
            raise FollowError("follow_paused", f"follow {name} is paused; resume it first")
        state["retry"] = True
    payload = _set_state(args, "run_now", change)
    owner = args._follow_context.owner
    store = _store()
    # A timer installed by an earlier release would run this poll on that release.
    with store.units_lock():
        payload["timer"] = units.refresh(sb_path=owner.executable, sandbox_home=owner.sandbox_home)
    payload["poll_started"] = units.start_now()
    return payload


def _remove(args) -> dict:
    context = _context(args)
    store = _store()
    name = _name(args)
    with store.lock(name):
        record = _read(store, name)
        attempt = record["state"].get("attempt") or {}
        if attempt.get("status") in ("submitting", "running") or store.apply_running(name):
            raise FollowError("follow_attempt_running",
                              f"follow {name} has attempt {attempt.get('id')} running; remove it afterwards")
        store.delete(name)
        store.audit("remove", name, actor=_actor(context))
    with store.units_lock():
        timer = units.remove() if not store.names() else {"timer": units.TIMER, "removed": False}
    return {"ok": True, "action": "follow-remove", "name": name, "timer": timer,
            "kept": ["deploy keys", "the follow's clones (the stack's worktrees point into them)",
                     "the stack itself"]}


def _tick(args) -> dict:
    if not getattr(args, "here", False):
        raise FollowError("follow_tick_requires_here", "host follow tick runs on the host itself (--here)")
    from sandbox.hosting.follow.tick import run_here
    only = getattr(args, "name", None)
    return run_here(follow_spec.validate_name(only) if only else None)


def _apply(args) -> dict:
    from sandbox.hosting.follow.apply import Attempt
    return Attempt().run(getattr(args, "remote", None) or "", _name(args), getattr(args, "attempt", None))


HANDLERS = {"add": _add, "status": _status, "pause": _pause, "resume": _resume, "remove": _remove,
            "run-now": _run_now, "key": _key, "tick": _tick, "apply": _apply}


def _render(payload: dict) -> str:
    action = payload.get("action")
    if action in ("follow-add", "follow-key"):
        lines = [f"follow {payload['name']}:"]
        for repo in payload["repos"]:
            lines.append(f"  {repo['name']}  {repo['github']}@{repo.get('branch')}  key {repo['deploy_key']}")
            lines.append(f"    {repo['public_key']}")
            lines.append(f"    add it at {repo['add_at']}")
        lines.append(payload["next"])
        return "\n".join(lines)
    if action == "follow-status":
        if not payload["follows"]:
            return "no follows on this remote"
        lines = []
        for follow in payload["follows"]:
            check = follow.get("last_check") or {}
            attempt = follow.get("attempt") or {}
            state = "paused" if follow["paused"] else (check.get("reason") or "not polled yet")
            lines.append(f"{follow['name']}: {state}")
            for repo in follow["repos"]:
                applied = (follow.get("applied") or {}).get(repo["name"])
                lines.append(f"  {repo['name']}  {repo['github']}@{repo['branch']}  applied "
                             f"{(applied or '-')[:12]}  key {repo['deploy_key']}")
            if attempt:
                lines.append(f"  last attempt {attempt.get('id')}: {attempt.get('status')}"
                             + (f" ({attempt['reason']})" if attempt.get("reason") else "")
                             + (f" job {attempt['job_id']}" if attempt.get("job_id") else ""))
        return "\n".join(lines)
    return json.dumps(payload, sort_keys=True)


def _audit_refusal(args, action: str, code: str) -> None:
    """FR-012: a refused owner command leaves one audit line once its context is known."""
    context = getattr(args, "_follow_context", None)
    if context is None or action not in OWNER_ACTIONS:
        return
    name = getattr(args, "name", None)
    try:
        _store().audit("refused", name if isinstance(name, str) and name else "*", actor=_actor(context),
                       command=action, code=code)
    except Exception:  # noqa: BLE001 -- never mask the refusal itself
        pass


def _redact(text: object) -> str:
    from sandbox.core import _remote
    return _remote.redact_text(str(text))[:300]


def cmd_host_follow(args) -> None:
    action = getattr(args, "image_action", None)
    if action not in HANDLERS:
        die("use `sb host follow add|status|pause|resume|remove|run-now|key`")
    try:
        payload = HANDLERS[action](args)
    except Exception as exc:  # noqa: BLE001 -- every refusal is one JSON line, never a traceback
        if isinstance(exc, FollowError):
            code, message = exc.code, exc.message
        else:
            code = "follow_internal_error"
            message = f"{type(exc).__name__}: {_redact(exc)}"
        _audit_refusal(args, action, code)
        payload = {"ok": False, "code": code, "error": message}
        if getattr(args, "json", False) or action in ("tick", "apply"):
            print(json.dumps(payload, sort_keys=True))
            raise SystemExit(1)
        die(f"{code}: {message}")
        return
    if getattr(args, "json", False) or action in ("tick", "apply"):
        print(json.dumps(payload, sort_keys=True))
    else:
        print(_render(payload))
    if payload.get("ok") is False:
        raise SystemExit(1)
