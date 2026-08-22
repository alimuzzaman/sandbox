"""Public ``sb hermes`` command presentation and dispatch."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from sandbox.hermes import facade as hermes
import sandbox.core._remote as remote
from sandbox.registry import register


def _emit(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, sort_keys=True))
        return
    if data["ok"]:
        print(f"{data['action']}: {data['status'] or 'ok'}")
        if data.get("repo"):
            print(f"  repo: {data['repo']}")
        if data.get("path"):
            print(f"  path: {data['path']}")
        if data.get("job_id"):
            print(
                f"  job: {data['job_id']} "
                f"(poll with ./sb hermes job status --remote {data['remote']} "
                f"--job-id {data['job_id']} --json)"
            )
        for key, value in data.get("data", {}).items():
            if value not in (None, "", [], {}):
                print(f"  {key}: {value}")
        return
    print(f"{data['error']['code']}: {data['error']['message']}")


def _failure(action: str, remote_name: str, exc: hermes.HermesError, as_json: bool) -> None:
    payload = hermes.result(False, action, remote_name, error=exc)
    _emit(payload, as_json)
    raise SystemExit(1)


def _repo_action(args) -> dict:
    if args.subaction == "list":
        return hermes.list_repos(args.remote)
    if args.subaction == "clone":
        url = args.url or args.target
        if not url:
            raise hermes.HermesError("hermes repo clone requires --url", "missing_repo_url")
        return hermes.clone_repo(args.remote, url, args.name, args.ref)
    if args.subaction == "sync":
        repo_name = args.repo or args.target
        if not repo_name:
            raise hermes.HermesError("hermes repo sync requires --repo", "missing_repo")
        return hermes.repo_sync(args.remote, repo_name, args.confirm)
    if args.subaction == "auth":
        provider = (args.target or "").lower()
        if provider != "github":
            raise hermes.HermesError("only `hermes repo auth github` is supported", "unsupported_provider")
        if not getattr(args, "token_stdin", False):
            raise hermes.HermesError(
                "GitHub browser OAuth has account-wide minimum scopes; use a repository-scoped fine-grained token with `--token-stdin`",
                "fine_grained_token_required",
            )
        if sys.stdin.isatty():
            raise hermes.HermesError(
                "--token-stdin requires a fine-grained GitHub token piped on standard input",
                "fine_grained_token_required",
            )
        entry = remote.get_remote(args.remote)
        if not entry or not entry.get("provisioned"):
            raise hermes.HermesError("a provisioned remote is required", "remote_not_provisioned")
        token = sys.stdin.buffer.read()
        if not token.startswith(b"github_pat_"):
            raise hermes.HermesError(
                "--token-stdin accepts only a GitHub fine-grained token",
                "fine_grained_token_required",
            )
        parts = remote.remote_ssh_parts(entry)
        cmd = ["ssh"]
        if parts["port"]:
            cmd += ["-p", str(parts["port"])]
        availability_cmd = [*cmd, "-o", "BatchMode=yes", parts["target"], "command -v gh"]
        login_cmd = [*cmd, "-o", "BatchMode=yes", parts["target"],
                     "gh auth login --hostname github.com --git-protocol https --with-token"]
        status_cmd = [*cmd, "-o", "BatchMode=yes", parts["target"], "gh auth status --hostname github.com"]
        try:
            if subprocess.run(availability_cmd, check=False, capture_output=True).returncode != 0:
                raise hermes.HermesError(
                    "GitHub CLI is not installed on the remote; install `gh` before `hermes repo auth github`",
                    "github_cli_missing",
                )
            rc = subprocess.run(login_cmd, input=token, check=False, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL).returncode
            if rc == 0:
                rc = subprocess.run(status_cmd, check=False, capture_output=True).returncode
        except OSError as exc:
            raise hermes.HermesError(str(exc), "provider_auth_failed", True) from exc
        if rc != 0:
            raise hermes.HermesError("GitHub fine-grained token authentication did not complete", "provider_auth_failed", True)
        return hermes.result(True, "repo_auth", args.remote, status="authenticated",
                             data={"provider": "github", "existing": False, "credential": "fine_grained"})
    raise hermes.HermesError("repo action must be auth, clone, list, or sync", "invalid_repo_action")


def _job_payload(remote_name: str, action: str, data: dict) -> dict:
    """Adapt the bounded job response to the public Hermes result envelope."""
    if data.get("status") == "not_found":
        return hermes.result(
            False,
            f"job_{action}",
            remote_name,
            status="not_found",
            job_id=data.get("job_id"),
            data=data,
            error=hermes.HermesError("Hermes job was not found", "job_not_found"),
        )
    return hermes.result(
        True,
        f"job_{action}",
        remote_name,
        status=data.get("status"),
        job_id=data.get("job_id"),
        data=data,
    )


def cmd_hermes(cfg, args) -> None:
    """Dispatch ``./sb hermes ...`` without ever printing secret material."""
    action = args.action
    try:
        if action == "install":
            payload = hermes.install(args.remote, args.version or hermes.SUPPORTED_TAG, args.commit)
        elif action == "setup":
            payload = hermes.setup(args.remote)
        elif action == "doctor":
            payload = hermes.doctor(args.remote)
        elif action == "status":
            payload = hermes.status(args.remote)
        elif action == "provider":
            if args.subaction != "openrouter":
                raise hermes.HermesError("provider action must be openrouter", "invalid_provider_action")
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                raise hermes.HermesError(
                    "OpenRouter setup requires an OPENROUTER_API_KEY supplied by the secret broker",
                    "openrouter_key_required",
                )
            payload = hermes.configure_openrouter(args.remote, api_key)
        elif action == "chat":
            if args.subaction == "status":
                payload = hermes.chat_status(args.remote)
                _emit(payload, args.json)
                return
            if not args.repo:
                raise hermes.HermesError("hermes chat requires --repo", "missing_repo")
            payload = hermes.chat(args.remote, args.repo, worktree=not args.no_worktree)
        elif action == "skills":
            payload = hermes.skills_action(args.remote, args.subaction, confirm=args.confirm)
        elif action == "run":
            if not args.repo or not args.prompt:
                raise hermes.HermesError("hermes run requires --repo and --prompt", "missing_run_input")
            payload = hermes.run(args.remote, args.repo, args.prompt,
                          worktree=not args.no_worktree, async_=bool(args.run_async), timeout=args.timeout,
                          workdir=args.workdir, yolo=bool(args.yolo))
        elif action == "job":
            if not args.job_id:
                raise hermes.HermesError("Hermes job action requires --job-id", "missing_job_id")
            if args.subaction == "status":
                payload = _job_payload(args.remote, "status", hermes.job_status(args.remote, args.job_id, args.offset))
            elif args.subaction == "kill":
                payload = _job_payload(args.remote, "kill", hermes.job_kill(args.remote, args.job_id))
            else:
                raise hermes.HermesError("job action must be status or kill", "invalid_job_action")
        elif action == "cron":
            if args.subaction == "list":
                payload = hermes.cron_list(args.remote)
            elif args.subaction == "output":
                job_id = args.target or args.job_id
                if not job_id:
                    raise hermes.HermesError("cron output requires a job id", "missing_cron_job_id")
                payload = hermes.cron_output(args.remote, job_id, args.lines)
            elif args.subaction == "validate":
                payload = hermes.cron_validate(args.remote)
            elif args.subaction == "create":
                if not args.schedule or not args.prompt:
                    raise hermes.HermesError(
                        "cron create requires --schedule and --prompt", "missing_cron_input"
                    )
                payload = hermes.cron_create(
                    args.remote, args.schedule, args.prompt,
                    name=args.name, workdir=args.workdir,
                    profile=args.profile, confirm=args.confirm,
                )
            elif args.subaction == "route":
                job_id = args.target or args.job_id
                if not job_id:
                    raise hermes.HermesError("cron route requires a job id", "missing_cron_job_id")
                payload = hermes.cron_route(args.remote, job_id, args.profile, args.confirm)
            elif args.subaction == "run":
                job_id = args.target or args.job_id
                if not job_id:
                    raise hermes.HermesError("cron run requires a job id", "missing_cron_job_id")
                payload = hermes.cron_run(args.remote, job_id, args.confirm)
            elif args.subaction == "catalog":
                payload = hermes.cron_catalog(args.remote)
            elif args.subaction == "reconcile":
                payload = hermes.cron_reconcile(args.remote, args.confirm, args.force_replace)
            elif args.subaction == "verify":
                job_id = args.target or args.job_id
                if not job_id:
                    raise hermes.HermesError("cron verify requires a job id", "missing_cron_job_id")
                payload = hermes.cron_verify(args.remote, job_id, args.timeout, args.confirm)
            else:
                raise hermes.HermesError(
                    "cron action must be list, output, validate, create, route, run, catalog, reconcile, or verify",
                    "invalid_cron_action",
                )
        elif action == "authorization":
            if args.subaction == "sync":
                payload = hermes.authorization_sync(args.remote)
            elif args.subaction == "list":
                payload = hermes.authorization_list(args.remote)
            elif args.subaction == "show":
                request_id = args.target or args.job_id
                if not request_id:
                    raise hermes.HermesError("authorization show requires a request id", "missing_authorization_id")
                payload = hermes.authorization_show(args.remote, request_id)
            elif args.subaction == "request":
                if not args.job or not args.scope or not args.replay_origin or not args.reason:
                    raise hermes.HermesError("authorization request requires --job, --scope, --replay-origin, and --reason", "missing_authorization_input")
                payload = hermes.authorization_request(args.remote, args.job, args.scope, args.replay_origin,
                                                       args.reason, args.expires_in_minutes)
            elif args.subaction == "approve":
                request_id = args.target or args.job_id
                if not request_id:
                    raise hermes.HermesError("authorization approve requires a request id", "missing_authorization_id")
                payload = hermes.authorization_approve(args.remote, request_id, args.confirm)
            else:
                raise hermes.HermesError("authorization action must be sync, list, show, request, or approve", "invalid_authorization_action")
        elif action == "repo":
            payload = _repo_action(args)
        elif action == "gateway":
            if not args.subaction:
                raise hermes.HermesError("gateway action is required", "missing_gateway_action")
            if args.subaction == "converge":
                payload = hermes.gateway_converge(args.remote, args.confirm)
            else:
                payload = hermes.gateway(args.remote, args.subaction, args.allowlist, args.lines)
        elif action == "worktree":
            if args.subaction == "list":
                payload = hermes.worktree_list(args.remote)
            elif args.subaction == "inspect":
                if not args.name:
                    raise hermes.HermesError("worktree inspect requires --name", "missing_worktree_name")
                payload = hermes.worktree_inspect(args.remote, args.name)
            elif args.subaction == "preserve":
                if not args.name:
                    raise hermes.HermesError("worktree preserve requires --name", "missing_worktree_name")
                payload = hermes.worktree_preserve(args.remote, args.name, args.confirm)
            else:
                raise hermes.HermesError("worktree action must be list, inspect, or preserve", "invalid_worktree_action")
        elif action == "update":
            if args.subaction == "plan":
                payload = hermes.update_plan(args.remote, args.version or hermes.SUPPORTED_TAG, args.commit)
            elif args.subaction == "provenance":
                payload = hermes.release_provenance_plan(args.remote, args.version or hermes.SUPPORTED_TAG, args.commit)
            elif args.subaction == "apply":
                payload = hermes.update_apply(args.remote, args.version or hermes.SUPPORTED_TAG, args.commit, args.confirm)
            else:
                raise hermes.HermesError("update action must be plan, provenance, or apply", "invalid_update_action")
        elif action == "backup":
            if args.subaction == "create":
                payload = hermes.backup_create(args.remote)
            elif args.subaction == "list":
                payload = hermes.backup_list(args.remote)
            elif args.subaction == "restore":
                if not args.backup_id:
                    raise hermes.HermesError("backup restore requires --backup-id", "missing_backup_id")
                payload = hermes.backup_restore(args.remote, args.backup_id, args.confirm)
            else:
                raise hermes.HermesError("backup action must be create or list", "invalid_backup_action")
        elif action == "cleanup":
            payload = hermes.cleanup(args.remote, args.confirm, args.dry_run, args.resolve_stale)
        elif action == "policy":
            if args.subaction == "show":
                payload = hermes.policy_show(args.remote)
            elif args.subaction == "set":
                payload = hermes.policy_set(args.remote, args.max_jobs, args.max_worktrees,
                                            args.min_free_disk_mb, args.min_free_memory_mb)
            else:
                raise hermes.HermesError("policy action must be show or set", "invalid_policy_action")
        elif action == "health":
            payload = hermes.health(args.remote)
        elif action == "acceptance":
            if args.subaction != "v2":
                raise hermes.HermesError("acceptance action must be v2", "invalid_acceptance_action")
            payload = hermes.acceptance_v2(args.remote)
        elif action == "dashboard":
            payload = hermes.dashboard_action(
                args.remote,
                args.subaction or "status",
                port=args.port,
                fqdn=args.fqdn,
                confirm=args.confirm,
                plan=args.plan,
                lines=args.lines,
                target=args.target,
                basic_auth_user=args.basic_auth_user,
                basic_auth_secret=args.basic_auth_secret,
            )
        elif action == "dashboard-ui":
            payload = hermes.dashboard_ui_action(
                args.remote, args.subaction or "status", catalog_path=args.authorization_catalog,
                confirm=args.confirm, port=args.port,
            )
        elif action == "state":
            if args.subaction == "setup":
                # Older scripts used the shared --repo spelling before state
                # gained its explicit option.  Keep it as a scoped alias.
                repository = args.state_repo or args.repo
                if not repository:
                    raise hermes.HermesError("hermes state setup requires --state-repo", "missing_state_repo")
                payload = hermes.state_setup(args.remote, repository)
            elif args.subaction == "sync":
                payload = hermes.state_sync(args.remote, args.confirm)
            elif args.subaction == "restore":
                payload = hermes.state_restore(args.remote, args.confirm)
            else:
                raise hermes.HermesError("state action must be setup, sync, or restore", "invalid_state_action")
        elif action == "drive":
            if args.subaction == "setup":
                if not args.drive_destination:
                    raise hermes.HermesError("hermes drive setup requires --drive-destination", "missing_drive_destination")
                payload = hermes.drive_setup(args.remote, args.drive_destination)
            elif args.subaction == "backup":
                if not args.passphrase_stdin or sys.stdin.isatty():
                    raise hermes.HermesError("Drive backup requires --passphrase-stdin", "recovery_passphrase_required")
                payload = hermes.drive_backup(args.remote, sys.stdin.buffer.read(), args.confirm)
            elif args.subaction == "list":
                payload = hermes.drive_list(args.remote)
            elif args.subaction == "restore":
                if not args.backup_id:
                    raise hermes.HermesError("Drive restore requires --backup-id", "missing_backup_id")
                if not args.passphrase_stdin or sys.stdin.isatty():
                    raise hermes.HermesError("Drive restore requires --passphrase-stdin", "recovery_passphrase_required")
                payload = hermes.drive_restore(args.remote, args.backup_id, sys.stdin.buffer.read(), args.confirm)
            else:
                raise hermes.HermesError("drive action must be setup, backup, list, or restore", "invalid_drive_action")
        else:  # argparse choices guard this, but keep a safe future boundary.
            raise hermes.HermesError("unknown Hermes action", "invalid_action")
    except hermes.HermesError as exc:
        _failure(action, args.remote, exc, args.json)
        return
    _emit(payload, args.json)


register({"hermes": cmd_hermes})
