"""Public ``sb hermes`` command presentation and dispatch."""
from __future__ import annotations

import json
import subprocess
import sys

import sandbox.core._hermes as hermes
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
    raise hermes.HermesError("repo action must be auth, clone, or list", "invalid_repo_action")


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
        elif action == "chat":
            if not args.repo:
                raise hermes.HermesError("hermes chat requires --repo", "missing_repo")
            payload = hermes.chat(args.remote, args.repo, worktree=not args.no_worktree)
        elif action == "run":
            if not args.repo or not args.prompt:
                raise hermes.HermesError("hermes run requires --repo and --prompt", "missing_run_input")
            payload = hermes.run(args.remote, args.repo, args.prompt,
                          worktree=not args.no_worktree, async_=bool(args.run_async), timeout=args.timeout)
        elif action == "job":
            if not args.job_id:
                raise hermes.HermesError("Hermes job action requires --job-id", "missing_job_id")
            if args.subaction == "status":
                payload = _job_payload(args.remote, "status", hermes.job_status(args.remote, args.job_id, args.offset))
            elif args.subaction == "kill":
                payload = _job_payload(args.remote, "kill", hermes.job_kill(args.remote, args.job_id))
            else:
                raise hermes.HermesError("job action must be status or kill", "invalid_job_action")
        elif action == "repo":
            payload = _repo_action(args)
        elif action == "gateway":
            if not args.subaction:
                raise hermes.HermesError("gateway action is required", "missing_gateway_action")
            payload = hermes.gateway(args.remote, args.subaction, args.allowlist, args.lines)
        elif action == "update":
            if args.subaction == "plan":
                payload = hermes.update_plan(args.remote, args.version or hermes.SUPPORTED_TAG, args.commit)
            elif args.subaction == "apply":
                payload = hermes.update_apply(args.remote, args.version or hermes.SUPPORTED_TAG, args.commit, args.confirm)
            else:
                raise hermes.HermesError("update action must be plan or apply", "invalid_update_action")
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
            payload = hermes.cleanup(args.remote, args.confirm, args.dry_run)
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
            )
        elif action == "state":
            if args.subaction == "setup":
                if not args.state_repo:
                    raise hermes.HermesError("hermes state setup requires --state-repo", "missing_state_repo")
                payload = hermes.state_setup(args.remote, args.state_repo)
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
