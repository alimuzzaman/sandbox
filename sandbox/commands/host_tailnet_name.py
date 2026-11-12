"""`sb host tailnet-name add|list|renew|remove` (spec 078).

Parses every argument at the boundary in the order FR-003 requires (usage,
name, upstream, configuration, then the remote and its front door), builds
the real dependencies and prints one envelope. See
specs/078-host-tailnet-name/contracts/host-tailnet-name-cli.md.
"""
from __future__ import annotations

import json
import sys

from sandbox.hosting.tailnet_names.models import REMOTE_UNKNOWN, TailnetNameError

USAGE = "tailnet_name_usage"
ACTIONS = ("add", "list", "renew", "remove")


def _emit(payload: dict, as_json: bool) -> None:
    from sandbox.services.redaction import redact_structure
    payload = redact_structure(payload)
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    operation = payload.get("operation", "tailnet-name")
    if operation == "list":
        rows = payload.get("names") or []
        if not rows:
            print(f"no tailnet names on {payload.get('remote')}")
        for row in rows:
            expiry = row.get("cert_not_after") or "no certificate"
            days = row.get("days_remaining")
            flags = list(row.get("drift") or []) + (["renewal due"] if row.get("renewal_due") else [])
            print(f"{row['fqdn']}  -> {row.get('upstream') or '?'}  expires {expiry}"
                  + (f" ({days} days)" if days is not None else "")
                  + (f"  [{', '.join(flags)}]" if flags else ""))
        return
    results = payload.get("results") or [payload]
    for result in results:
        name = result.get("fqdn") or ""
        print(f"tailnet-name {operation} {name}: {'ok' if result.get('ok') else 'failed'}")
        for step in result.get("steps") or []:
            detail = step.get("detail") or step.get("code") or ""
            print(f"  {step['step']}: {step['status']}" + (f" ({detail})" if detail else ""))
        if not result.get("ok") and result.get("code"):
            print(f"  {result['code']}: {result.get('error') or ''}", file=sys.stderr)
    if payload.get("code") and "results" not in payload and not payload.get("steps"):
        print(f"{payload['code']}: {payload.get('error') or ''}", file=sys.stderr)


def _refuse(code: str, message: str, as_json: bool, operation: str | None) -> None:
    _emit({"ok": False, "operation": operation or "tailnet-name", "code": code, "error": message},
          as_json)
    sys.exit(1)


def _usage(args) -> str:
    action = getattr(args, "image_action", None)
    if action not in ACTIONS:
        return "use `sb host tailnet-name add|list|renew|remove`"
    if not getattr(args, "remote", None):
        return "--remote is required"
    fqdn = getattr(args, "tailnet_hostname", None)
    if action == "add" and (not fqdn or not getattr(args, "upstream", None)):
        return "add needs FQDN and --upstream HOST:PORT"
    if action == "remove" and not fqdn:
        return "remove needs FQDN"
    if action == "renew" and bool(fqdn) == bool(getattr(args, "all", False)):
        return "renew needs FQDN or --all"
    if action == "list" and fqdn:
        return "list takes no FQDN"
    if getattr(args, "upstream", None) and action != "add":
        return "--upstream is only for add"
    if getattr(args, "replace_upstream", False) and action != "add":
        return "--replace-upstream is only for add"
    if getattr(args, "force", False) and action != "renew":
        return "--force is only for renew"
    return ""


def secret_service_factory(project_dir: str | None):
    """Build the broker's SecretService with Sandbox's two fixed use profiles.

    The profiles are added to the project's descriptor BEFORE the service is
    built, so a provider source binds them into its authority like any other
    profile. A project profile that already uses one of these names is refused
    rather than overridden.
    """
    import copy
    from pathlib import Path

    from sandbox.core._paths import BASE
    from sandbox.core._secrets import secret_file
    from sandbox.hosting.tailnet_names.models import SECRET_UNAVAILABLE
    from sandbox.secrets.context import build_secret_service
    from sandbox.secrets.service import load_secret_project_descriptor

    root = Path(project_dir or ".").expanduser().resolve()

    def factory(extra_profiles: dict):
        descriptor = copy.deepcopy(dict(load_secret_project_descriptor(root) or {}))
        secrets_block = dict(descriptor.get("secrets") or {})
        profiles = dict(secrets_block.get("useProfiles") or {})
        clash = sorted(set(profiles) & set(extra_profiles))
        if clash:
            raise TailnetNameError(SECRET_UNAVAILABLE,
                                   "the project defines reserved use profile(s) "
                                   + ", ".join(clash) + "; rename them")
        profiles.update(copy.deepcopy(extra_profiles))
        secrets_block["useProfiles"] = profiles
        descriptor["secrets"] = secrets_block
        return build_secret_service(project_root=root, config=descriptor,
                                    personal_path=secret_file(), runtime_root=BASE / "runtime")

    return factory


def default_deps(project_dir: str | None):
    from sandbox.core import _remote
    from sandbox.hosting.front_door import nginx
    from sandbox.hosting.tailnet_names import remote_program, service, state, verify
    from sandbox.hosting.tailnet_names.broker import Broker
    from sandbox.hosting.server_cli_target import selected_owner_target

    broker = Broker(secret_service_factory(project_dir), acme_root=state.default_root() / "acme")

    def active_jobs(remote: str) -> bool:
        owner = selected_owner_target(remote)
        if owner is not None:
            from sandbox.core import _paths
            from sandbox.jobs.registry import JobRepository
            context = owner.require_current()
            repository = JobRepository(_paths.RUNTIME_DIR / "jobs" / "registry.sqlite3")
            try:
                jobs = repository.list(active_only=True, limit=2)
            finally:
                repository.close()
            owner.require_current()
            if not isinstance(jobs, list) or any(not isinstance(row, dict) for row in jobs):
                raise RuntimeError("job list unavailable")
            if not any(row.get("job_id") == context.job_id for row in jobs):
                return True  # Missing own row or other jobs filling the page keeps the gate shut.
            return any(row.get("job_id") != context.job_id for row in jobs)
        from sandbox.transports.remote_jobs import RemoteJobTransport
        transport = RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
                                       ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
                                       remote_sb_path=_remote.remote_sb_path)
        page = transport.list(remote, active_only=True, limit=1)
        jobs = page.get("jobs") if isinstance(page, dict) else None
        if not isinstance(jobs, list):
            raise RuntimeError("job list unavailable")
        return bool(jobs)

    def jobs_probe(entry) -> dict:
        # The remote program re-checks jobs as the SSH user against this home.
        from sandbox.hosting.server_cli_target import is_owner_target
        if is_owner_target(entry):
            context = entry.require_current()
            return {"home": entry.sandbox_home(), "exclude_job_id": context.job_id}
        return {"home": _remote.resolve_sandbox_home(entry)}

    return service.Deps(
        remote_call=lambda entry, action, payload: remote_program.call(entry, action, payload),
        preflight=lambda entry, declared, own_file: nginx.preflight(
            entry, declared=declared, own_file=own_file),
        dns=broker.dns,
        issue=broker.issue,
        cached_chain=broker.cached_chain,
        find_lego=broker.find_lego,
        active_jobs=active_jobs,
        jobs_probe=jobs_probe,
        probe=verify.probe,
        store=lambda remote: state.StateStore(state.default_root(), remote),
    )


def cmd_host_tailnet_name(args) -> None:
    from sandbox.hosting import front_door
    from sandbox.hosting.front_door import FrontDoorError
    from sandbox.hosting.tailnet_names import models, service

    as_json = bool(getattr(args, "json", False))
    action = getattr(args, "image_action", None)
    problem = _usage(args)
    if problem:
        _refuse(USAGE, problem, as_json, action)
    try:
        config = None
        name = upstream = None
        if action == "list":
            try:
                config = models.TailnetNameConfig.load()
            except TailnetNameError:
                config = None
        else:
            config = models.TailnetNameConfig.load().with_acme_server(
                getattr(args, "acme_server", None))
            if args.tailnet_hostname:
                name = models.TailnetName.parse(args.tailnet_hostname, config.zones)
            if action == "add":
                upstream = models.Upstream.parse(args.upstream)
        from sandbox.core import _remote
        from sandbox.hosting.server_cli_target import host_target, is_owner_target
        entry = host_target(args.remote)
        owner = is_owner_target(entry)
        if not isinstance(entry, dict) and not owner:
            raise TailnetNameError(REMOTE_UNKNOWN, f"remote {args.remote!r} is not registered")
        front_door.require_capability(entry, front_door.TAILNET_NAMES)
        tailnet_ip = models.tailnet_address(entry.get("tailscale_host"))
        public_ip = None
        if action in ("add", "renew"):
            if owner:
                # Actual receiver interface metadata is bound into the original job.
                # An override cannot replace its public exposure-check target.
                supplied = getattr(args, "public_address", None)
                observed = entry.get("origin_ipv4")
                if not observed or (supplied is not None and supplied != observed):
                    raise TailnetNameError(models.PUBLIC_ADDRESS_INVALID,
                                           "the receiver's unique public IPv4 address is required")
                public_ip = models.public_address(override=observed, ssh_host=None)
            else:
                public_ip = models.public_address(override=getattr(args, "public_address", None),
                    ssh_host=_remote.remote_ssh_parts(entry).get("host"))
        ctx = service.Context(config, models.RemoteTarget(args.remote, entry, tailnet_ip, public_ip))
        deps = default_deps(getattr(args, "project_dir", None))
        if action == "add":
            result = service.add(ctx, deps, name, upstream,
                                 replace_upstream=bool(getattr(args, "replace_upstream", False)))
        elif action == "list":
            result = service.list_names(ctx, deps)
        elif action == "renew":
            result = service.renew(ctx, deps, name, all_names=bool(args.all),
                                   force=bool(getattr(args, "force", False)))
        else:
            result = service.remove(ctx, deps, name)
    except (TailnetNameError, FrontDoorError) as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        _refuse(exc.code, detail, as_json, action)
    payload = result.envelope()
    payload["remote"] = args.remote
    _emit(payload, as_json)
    sys.exit(0 if result.ok else 1)
