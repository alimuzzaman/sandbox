"""CLI adapter for owned storage authority operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from sandbox.application.owned_storage_service import (
    OwnedStorageApplicationError,
    build_owned_storage_application_service,
)
from sandbox.registry import CommandSpec, register_specs


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = "Inspect, preview, and reclaim owned storage authority objects"
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    auth = subparsers.add_parser("authority", help="authority operations")
    auth_sub = auth.add_subparsers(dest="authority_action", required=True)

    # capability
    cp = auth_sub.add_parser("capability", help="inspect platform capability status")
    cp.add_argument("--remote", required=True, help="remote identity")
    cp.add_argument("--project-identity", help="optional project identity")
    cp.add_argument("--json", action="store_true", help="output JSON format")

    # status
    st = auth_sub.add_parser("status", help="list bounded storage authority objects")
    st.add_argument("--remote", required=True, help="remote identity")
    st.add_argument("--project-identity", required=True, help="project identity")
    st.add_argument("--kind", choices=("sync_generation", "ci_materialization", "retained_artifact",
                                       "generation", "materialization", "artifact"))
    st.add_argument("--limit", type=int, default=100, help="max records (capped at 500)")
    st.add_argument("--cursor", help="opaque pagination cursor")
    st.add_argument("--json", action="store_true", help="output JSON format")

    # preview
    pv = auth_sub.add_parser("preview", help="generate bounded 15-minute reclamation preview")
    pv.add_argument("--remote", required=True, help="remote identity")
    pv.add_argument("--project-identity", required=True, help="project identity")
    pv.add_argument("--kind", choices=("sync_generation", "ci_materialization", "retained_artifact",
                                       "generation", "materialization", "artifact"))
    pv.add_argument("--limit", type=int, default=100, help="max records (capped at 500)")
    pv.add_argument("--cursor", help="opaque pagination cursor")
    pv.add_argument("--json", action="store_true", help="output JSON format")

    # reclaim
    rc = auth_sub.add_parser("reclaim", help="safely reclaim eligible preview candidate")
    rc.add_argument("--remote", required=True, help="remote identity")
    rc.add_argument("--project-identity", required=True, help="project identity")
    rc.add_argument("--preview-id", required=True, help="preview ID")
    rc.add_argument("--object-id", required=True, help="target object ID")
    rc.add_argument("--request-id", required=True, help="replay-safe request identity")
    rc.add_argument("--confirm", action="store_true", required=True, help="confirm explicit removal")
    rc.add_argument("--json", action="store_true", help="output JSON format")


def _normalize_kind(kind: str | None) -> str | None:
    if not kind:
        return None
    if kind == "generation":
        return "sync_generation"
    if kind == "materialization":
        return "ci_materialization"
    if kind == "artifact":
        return "retained_artifact"
    return kind


def cmd_storage(args: argparse.Namespace) -> None:
    action = getattr(args, "authority_action", None)
    service = build_owned_storage_application_service()

    try:
        if action == "capability":
            from sandbox.owned_storage_lifecycle.service import build_authority_lifecycle_service
            lifecycle_service = build_authority_lifecycle_service()
            res = lifecycle_service.evaluate_capability(
                remote_identity=args.remote,
            )
        elif action == "status":
            res = service.get_status(
                remote_identity=args.remote,
                project_identity=args.project_identity,
                kind=_normalize_kind(args.kind),
                limit=args.limit,
                cursor=args.cursor,
            )
        elif action == "preview":
            res = service.generate_preview(
                remote_identity=args.remote,
                project_identity=args.project_identity,
                kind=_normalize_kind(args.kind),
                limit=args.limit,
                cursor=args.cursor,
            )
        elif action == "reclaim":
            res = service.reclaim(
                remote_identity=args.remote,
                project_identity=args.project_identity,
                preview_id=args.preview_id,
                object_id=args.object_id,
                request_id=args.request_id,
                confirm=args.confirm,
            )
        else:
            res = {"ok": False, "code": "request_invalid", "message": f"Unsupported action {action}"}
    except OwnedStorageApplicationError as exc:
        res = {"ok": False, "code": exc.code, "message": str(exc)}
    except Exception as exc:
        res = {"ok": False, "code": "internal_indeterminate", "message": str(exc)}

    from sandbox.owned_storage.redaction import redact_storage_projection
    res = redact_storage_projection(res)

    if getattr(args, "json", False):
        print(json.dumps(res, sort_keys=True, indent=2))
    else:
        if res.get("ok"):
            op = res.get("operation")
            if op == "preview":
                print(f"Preview {res['preview_id']} generated ({len(res.get('candidates', []))} candidates, "
                      f"{res.get('estimated_reclaimable_bytes', 0)} eligible bytes). Expires: {res.get('expires_at')}")
            elif op == "cleanup":
                print(f"Reclaimed {res.get('object_id')}: status={res.get('status')} "
                      f"observed_reclaimed_bytes={res.get('observed_reclaimed_bytes')}")
            elif op == "status":
                print(f"Storage status for {res.get('project_identity')}: {len(res.get('objects', []))} objects")
        else:
            print(f"Error [{res.get('code')}]: {res.get('message')}", file=sys.stderr)

    if not res.get("ok"):
        raise SystemExit(1)


register_specs((CommandSpec(
    "storage",
    cmd_storage,
    configure=configure_parser,
    owner=__name__,
    scope="global",
    predispatch_policy=lambda _args: True,
    help="inspect, preview, and reclaim owned storage authority objects",
),))


__all__ = ["cmd_storage", "configure_parser"]
