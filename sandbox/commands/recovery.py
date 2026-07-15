"""Profile-driven recovery CLI surface."""
from __future__ import annotations

import json
import os
from pathlib import Path

from sandbox.recovery.context import recovery_service
from sandbox.registry import CommandSpec, register_specs


ROOT = Path(__file__).resolve().parents[2]


def configure_recovery(parser) -> None:
    parser.description = "Plan and operate scoped encrypted recovery profiles"
    parser.add_argument("action", choices=("profiles", "plan", "create", "list", "verify", "restore", "retention", "schedule"))
    parser.add_argument("--remote", default=None)
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--backup-id", default=None)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--json", action="store_true")


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    print(f"recovery {payload['action']}: {payload.get('status') or 'failed'}")
    if payload.get("error"):
        print(f"  {payload['error']['code']}: {payload['error']['message']}")
    elif payload["action"] == "profiles":
        for profile in payload["data"]["profiles"]:
            print(f"  {profile}")
    elif payload["action"] == "plan":
        for artifact in payload["data"]["artifacts"]:
            print(f"  {artifact['profile_id']}: {artifact['capture_mode']} ({artifact['rationale']})")


def cmd_recovery(_cfg, args) -> None:
    service = recovery_service(ROOT)
    if args.action == "profiles":
        payload = service.profiles(args.remote)
    elif args.action == "plan":
        payload = service.plan(tuple(args.profile), args.remote)
    elif args.action == "list":
        payload = service.list(args.remote)
    elif args.action == "verify":
        from sandbox.recovery.errors import RecoveryError, result
        payload = service.verify(args.backup_id, args.remote) if args.backup_id else result(
            False, "verify", remote=args.remote,
            error=RecoveryError("--backup-id is required", "missing_backup_id"))
    elif args.action == "create":
        from sandbox.recovery.errors import RecoveryError, result
        if not args.confirm:
            payload = result(False, "create", remote=args.remote,
                             error=RecoveryError("recovery create requires --confirm", "confirmation_required"))
        elif not os.environ.get("RECOVERY_PASSPHRASE"):
            payload = result(False, "create", remote=args.remote,
                             error=RecoveryError("RECOVERY_PASSPHRASE is not available", "missing_passphrase"))
        else:
            payload = result(False, "create", remote=args.remote, error=RecoveryError(
                "profile capture requires a configured remote adapter", "recovery_not_configured"))
    elif args.action == "restore":
        from sandbox.recovery.errors import RecoveryError, result
        if not args.backup_id:
            payload = result(False, "restore", remote=args.remote,
                             error=RecoveryError("--backup-id is required", "missing_backup_id"))
        elif args.confirm:
            payload = result(False, "restore", remote=args.remote, error=RecoveryError(
                "restore apply requires disposable target adapters", "recovery_not_configured"))
        else:
            payload = service.restore_plan(args.backup_id, tuple(args.profile), remote=args.remote)
    elif args.action == "schedule":
        from sandbox.recovery.errors import RecoveryError, result
        if args.confirm:
            payload = result(False, "schedule", remote=args.remote, error=RecoveryError(
                "schedule activation requires a verified real recovery set", "protected_operation"))
        else:
            from sandbox.recovery.scheduler import build_schedule_policy, render_systemd_units
            profiles = tuple(args.profile) or tuple(profile.profile_id for profile in service.catalog.profiles)
            policy = build_schedule_policy("recovery-daily", profiles, "daily", remote=args.remote)
            payload = result(True, "schedule", remote=args.remote, status="planned", data={"units": render_systemd_units(policy)})
    elif args.action == "retention":
        from sandbox.recovery.errors import RecoveryError, result
        if args.confirm:
            payload = result(False, "retention", remote=args.remote, error=RecoveryError(
                "retention deletion requires a verified real recovery set", "protected_operation"))
        else:
            from sandbox.recovery.retention import build_retention_plan
            plan = build_retention_plan("sets/", ())
            payload = result(True, "retention", remote=args.remote, status="planned", data={
                "destination_prefix": plan.destination_prefix, "protected_sets": plan.protected_sets,
                "candidates": plan.candidates, "requires_confirmation": True})
    else:
        from sandbox.recovery.errors import RecoveryError, result
        payload = result(False, args.action, remote=args.remote, error=RecoveryError(
            f"recovery {args.action} is not available until its verified implementation phase",
            "not_implemented",
        ))
    _emit(payload, args.json)
    if not payload["ok"]:
        raise SystemExit(1)


register_specs((CommandSpec(
    name="recovery", handler=cmd_recovery, owner=__name__, order=200,
    configure=configure_recovery, scope="global", destructive=False,
),))
