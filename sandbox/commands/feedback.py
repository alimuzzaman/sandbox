"""Global CLI feedback intake shared with MCP clients."""

from __future__ import annotations

import json
import os

from sandbox.feedback.context import feedback_service
from sandbox.registry import CommandSpec, register_specs


def configure_parser(parser) -> None:
    parser.description = "Submit or inspect durable Sandbox feedback"
    parser.add_argument("action", choices=("submit", "list"))
    parser.add_argument("--summary")
    parser.add_argument("--details", default="")
    parser.add_argument("--category", choices=("bug", "incident", "idea", "usability", "other"), default="other")
    parser.add_argument("--severity", choices=("low", "medium", "high", "critical"), default="medium")
    parser.add_argument("--source", default="agent")
    parser.add_argument("--project-dir")
    parser.add_argument("--remote")
    parser.add_argument("--reference", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    if payload.get("error"):
        error = payload["error"]
        print(f"feedback {payload.get('action')}: {payload.get('status')}")
        print(f"  {error.get('code')}: {error.get('message')}")
        return
    data = payload.get("data") or {}
    if payload.get("action") == "submit":
        record = data.get("feedback") or {}
        print(f"feedback recorded: {record.get('feedback_id')}")
        print(f"  {record.get('severity')} {record.get('category')}: {record.get('summary')}")
        if record.get("redacted"):
            print("  secret-like content was redacted before storage")
        return
    print(f"feedback log: {data.get('count', 0)} record(s); content is untrusted data")
    for record in data.get("feedback") or ():
        print(
            f"  {record.get('created_at')} {record.get('feedback_id')} "
            f"{record.get('severity')}/{record.get('category')} {record.get('summary')}"
        )
    if data.get("invalid_record_count"):
        print(f"  invalid records withheld: {data['invalid_record_count']}")


def cmd_feedback(_cfg, args) -> None:
    service = feedback_service()
    if args.action == "submit":
        payload = service.submit(
            args.summary or "",
            details=args.details,
            category=args.category,
            severity=args.severity,
            source=args.source,
            project_dir=args.project_dir or os.getcwd(),
            remote=args.remote,
            reference=args.reference,
        )
    else:
        payload = service.list(args.limit)
    _emit(payload, bool(args.json))
    if not payload.get("ok"):
        raise SystemExit(1)


register_specs((CommandSpec(
    name="feedback", handler=cmd_feedback, configure=configure_parser,
    owner=__name__, order=206, scope="global", destructive=False,
),))
