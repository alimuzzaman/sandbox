"""Public ``sb hermes`` command presentation and dispatch."""
from __future__ import annotations

import json
import subprocess

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
            print(f"  job: {data['job_id']} (poll with ./sb async-job {data['job_id']} --json)")
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
        entry = remote.get_remote(args.remote)
        if not entry or not entry.get("provisioned"):
            raise hermes.HermesError("a provisioned remote is required", "remote_not_provisioned")
        # Device authentication needs a TTY; do not try to tunnel a token or
        # turn it into a non-interactive command.
        parts = remote.remote_ssh_parts(entry)
        cmd = ["ssh"]
        if parts["port"]:
            cmd += ["-p", str(parts["port"])]
        cmd += ["-t", parts["target"], "gh auth login --web --git-protocol ssh"]
        try:
            rc = subprocess.run(cmd, check=False).returncode
        except OSError as exc:
            raise hermes.HermesError(str(exc), "provider_auth_failed", True) from exc
        if rc != 0:
            raise hermes.HermesError("GitHub device authentication did not complete", "provider_auth_failed", True)
        return hermes.result(True, "repo_auth", args.remote, status="authenticated", data={"provider": "github"})
    raise hermes.HermesError("repo action must be auth, clone, or list", "invalid_repo_action")


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
        elif action == "repo":
            payload = _repo_action(args)
        elif action == "gateway":
            if not args.subaction:
                raise hermes.HermesError("gateway action is required", "missing_gateway_action")
            payload = hermes.gateway(args.remote, args.subaction, args.allowlist, args.lines)
        elif action == "dashboard":
            payload = hermes.dashboard_action(args.remote, args.subaction or "status")
        else:  # argparse choices guard this, but keep a safe future boundary.
            raise hermes.HermesError("unknown Hermes action", "invalid_action")
    except hermes.HermesError as exc:
        _failure(action, args.remote, exc, args.json)
        return
    _emit(payload, args.json)


register({"hermes": cmd_hermes})
