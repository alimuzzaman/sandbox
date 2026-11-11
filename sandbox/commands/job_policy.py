"""`sb job-policy`: show or set a host's durable-job slot policy.

The scheduler runs on the host that owns the job registry, so the policy is
that host's ``jobs.scheduler`` block in its own ``sandbox.local.yml``. Without
``--remote`` this command reads or writes the local host; with ``--remote R``
it runs the same command on R through the job control transport. The
scheduler re-reads the block at each admission, so a change applies to the
next queued or submitted job without restarting running jobs or the service.
"""

from __future__ import annotations

import json

from sandbox.config.job_scheduler import (
    JobSchedulerConfigError, JobSchedulerPolicy, normalize_job_scheduler,
)
from sandbox.registry import CommandSpec, register_specs

_FLAGS = (
    ("max_parallel", "--max-parallel"),
    ("min_free_memory_mb", "--min-free-memory-mb"),
    ("min_free_disk_mb", "--min-free-disk-mb"),
)


def configure_parser(parser) -> None:
    for _key, flag in _FLAGS:
        parser.add_argument(flag, type=int)
    parser.add_argument("--confirm", action="store_true",
                        help="write the requested values; without it the change is only planned")
    parser.add_argument("--show", action="store_true", help="show the effective policy (default)")
    parser.add_argument("--remote")
    parser.add_argument("--json", action="store_true")


def _requested(args) -> dict[str, int]:
    return {key: getattr(args, key) for key, _flag in _FLAGS if getattr(args, key, None) is not None}


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        if not payload.get("ok"):
            raise SystemExit(1)
        return
    if not payload.get("ok"):
        from sandbox.core import die
        die(f"{payload.get('code')}: {payload.get('error')}")
    from sandbox.jobs.scheduler import capacity_line
    where = payload.get("remote") or "local"
    policy = payload.get("policy") or {}
    print(f"job policy ({where}): {payload.get('status')}; max_parallel={policy.get('max_parallel')} "
          f"min_free_memory_mb={policy.get('min_free_memory_mb')} "
          f"min_free_disk_mb={policy.get('min_free_disk_mb')}")
    if isinstance(payload.get("slots"), dict):
        print(capacity_line(payload["slots"]))
    if payload.get("status") == "planned":
        print("Re-run with --confirm to write it.")


def _refusal(exc: JobSchedulerConfigError, remote: str | None = None) -> dict:
    return {"ok": False, "code": exc.code, "error": str(exc), "remote": remote}


def _local(requested: dict[str, int], confirm: bool) -> dict:
    from sandbox.core._config import _local_yaml, _write_local_yaml
    from sandbox.jobs.listing import job_slots
    from sandbox.jobs.scheduler import check_host_policy_write, host_scheduler_policy

    try:
        current = host_scheduler_policy()
    except JobSchedulerConfigError as exc:
        if not requested or exc.code == "job_policy_config_unreadable":
            raise
        # An invalid block may be repaired: the new block is built from the
        # defaults plus the requested values and replaces it whole.
        current = None
    if not requested:
        status, policy = "observed", current
    else:
        base = (current or JobSchedulerPolicy()).as_mapping()
        policy = normalize_job_scheduler({**base, **requested})
        check_host_policy_write(policy)
        if policy == current:
            status = "unchanged"
        elif not confirm:
            status = "planned"
        else:
            local = _local_yaml()
            jobs = local.get("jobs")
            jobs = dict(jobs) if isinstance(jobs, dict) else {}
            # Persist the full validated block, so a later default change
            # cannot silently alter a value an operator set on purpose.
            jobs["scheduler"] = policy.as_mapping()
            local["jobs"] = jobs
            _write_local_yaml(local)
            policy = host_scheduler_policy()
            status = "updated"
    from sandbox.application.context import durable_job_dependencies
    scheduler = getattr(durable_job_dependencies()["job_service"], "scheduler", None)
    return {
        "ok": True, "status": status, "remote": None, "config_key": "jobs.scheduler",
        "policy": policy.as_mapping(), "requested": requested or None,
        "slots": job_slots(scheduler.capacity()) if scheduler is not None else None,
        "error": None,
    }


def _remote(name: str, requested: dict[str, int], confirm: bool) -> dict:
    from sandbox.core import _remote
    from sandbox.jobs.listing import job_slots
    from sandbox.transports.remote_jobs import RemoteJobTransport, RemoteJobTransportError

    argv = ["job-policy"]
    for key, flag in _FLAGS:
        if key in requested:
            argv += [flag, str(requested[key])]
    if confirm:
        argv.append("--confirm")
    transport = RemoteJobTransport(
        deploy=_remote.deploy_exact_working_tree, ssh_run=_remote.ssh_run,
        remote_lookup=_remote.get_remote, remote_sb_path=_remote.remote_sb_path)
    try:
        payload = transport.control(name, argv)
    except RemoteJobTransportError as exc:
        return {"ok": False, "code": "remote_job_policy_failed", "error": str(exc), "remote": name}
    try:
        if not isinstance(payload, dict) or not isinstance(payload.get("policy"), dict):
            raise JobSchedulerConfigError("not an object", "job_policy_invalid")
        policy = normalize_job_scheduler(payload["policy"]).as_mapping()
    except JobSchedulerConfigError:
        return {"ok": False, "code": "remote_job_policy_invalid_response",
                "error": "remote returned an invalid job policy", "remote": name}
    status = payload.get("status")
    slots = payload.get("slots")
    return {
        "ok": True, "remote": name, "config_key": "jobs.scheduler",
        "status": status if status in {"observed", "unchanged", "planned", "updated"} else "unknown",
        "policy": policy, "requested": requested or None,
        "slots": job_slots(slots) if isinstance(slots, dict) else None,
        "error": None,
    }


def cmd_job_policy(_cfg, args) -> None:
    requested = _requested(args)
    remote = getattr(args, "remote", None)
    as_json = bool(getattr(args, "json", False))
    if getattr(args, "show", False) and requested:
        _emit({"ok": False, "code": "job_policy_usage",
               "error": "--show takes no values", "remote": remote}, as_json)
        return
    try:
        if requested:
            # Refuse a bad value before any write or remote contact; the host
            # validates again against its own merged block.
            normalize_job_scheduler({**JobSchedulerPolicy().as_mapping(), **requested})
        payload = (_remote(remote, requested, bool(args.confirm)) if remote
                   else _local(requested, bool(args.confirm)))
    except JobSchedulerConfigError as exc:
        payload = _refusal(exc, remote)
    _emit(payload, as_json)


def _remote_control_predispatch(args) -> bool:
    return bool(getattr(args, "remote", None))


def _configure_capabilities(parser) -> None:
    parser.add_argument('--json', action='store_true')


def cmd_job_capabilities(_cfg, args) -> None:
    """Observe the assembled installed job contract without opening its ledger."""
    from sandbox.application.server_cli import capabilities, ServerCLIUnavailable
    try:
        payload = capabilities()
    except (ServerCLIUnavailable, OSError):
        payload = {'schema_version': 1, 'ok': False, 'code': 'server_cli_capability_unavailable',
                   'error': 'installed server CLI capability is unavailable'}
    print(json.dumps(payload, sort_keys=True))
    if not payload['ok']:
        raise SystemExit(1)


register_specs((
    CommandSpec('job-capabilities', cmd_job_capabilities, configure=_configure_capabilities,
                owner=__name__, scope='global', help='read installed durable CLI execution capabilities'),
    CommandSpec("job-policy", cmd_job_policy, configure=configure_parser, owner=__name__,
                scope="global", predispatch_policy=_remote_control_predispatch,
                help="show or set the durable-job slot limit and resource floors of a host"),
))
