"""Global CLI feedback intake shared with MCP clients."""

from __future__ import annotations

import json

from sandbox.feedback.context import feedback_service
from sandbox.registry import CommandSpec, register_specs


def configure_parser(parser) -> None:
    parser.description = "Submit or inspect durable Sandbox feedback"
    parser.add_argument(
        "action", choices=("submit", "list", "show", "detail", "export", "retention", "prune"),
    )
    parser.add_argument(
        "feedback_id", nargs="?",
        help="record ID or unique 8-32 character lowercase hex prefix for show/detail",
    )
    parser.add_argument(
        "--feedback-id", "--id", dest="feedback_id_option",
        help="record ID or unique 8-32 character lowercase hex prefix for show/detail",
    )
    parser.add_argument("--summary")
    parser.add_argument("--details", default="")
    parser.add_argument("--category", choices=("bug", "incident", "idea", "usability", "other"))
    parser.add_argument("--severity", choices=("low", "medium", "high", "critical"))
    parser.add_argument("--source")
    parser.add_argument("--project-dir")
    parser.add_argument("--project-name")
    parser.add_argument("--project", dest="project_filter", help="filter by safe project name or identity")
    parser.add_argument("--remote")
    parser.add_argument("--reference", default="")
    parser.add_argument(
        "--limit", type=int, default=20,
        help="maximum records to return (1-100; default: 20)",
    )
    parser.add_argument("--cursor")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--format", choices=("json", "jsonl"), default="json")
    parser.add_argument("--max-bytes", type=int, default=1_000_000)
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument(
        "--confirm", action="store_true",
        help="explicitly request a prune plan; records remain append-only",
    )
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
    if payload.get("action") in {"show", "detail"}:
        record = data.get("feedback") or {}
        print(f"feedback {payload.get('action')}: {record.get('feedback_id')}")
        print(f"  {record.get('created_at')} {record.get('severity')}/{record.get('category')}")
        print(f"  {record.get('summary')}")
        if record.get("details"):
            print(f"  {record.get('details')}")
        return
    if payload.get("action") == "export":
        # Export content is already bounded and path-free by the service.
        print((data.get("content") or "").rstrip("\n"))
        return
    if payload.get("action") in {"retention", "prune"}:
        print(
            f"feedback {payload.get('action')}: {payload.get('status')}; "
            f"{data.get('count', 0)} candidate(s), deleted {data.get('deleted', 0)}"
        )
        if data.get("requires_confirmation"):
            print("  deletion requires explicit --confirm and is never automatic")
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
    action = args.action
    if action == "submit":
        submit_kwargs = {
            "details": args.details,
            "category": args.category or "other",
            "severity": args.severity or "medium",
            "source": args.source or "agent",
            # CLI and MCP intentionally pass only explicit context.  Inferring
            # cwd here while MCP defaults to no context produced different
            # project identities for the same report.
            "project_dir": args.project_dir,
            "remote": args.remote,
            "reference": args.reference,
        }
        if getattr(args, "project_name", None) is not None:
            submit_kwargs["project_name"] = args.project_name
        payload = service.submit(args.summary or "", **submit_kwargs)
    elif action in {"show", "detail"}:
        method = getattr(service, action, service.show)
        payload = method(
            getattr(args, "feedback_id", None)
            or getattr(args, "feedback_id_option", None)
            or "",
        )
    elif action == "export":
        payload = service.export(
            args.limit,
            getattr(args, "cursor", None),
            format=getattr(args, "format", "json"),
            max_bytes=getattr(args, "max_bytes", 1_000_000),
            category=getattr(args, "category", None),
            severity=getattr(args, "severity", None),
            source=getattr(args, "source", None),
            remote=getattr(args, "remote", None),
            project=getattr(args, "project_filter", None),
            project_dir=getattr(args, "project_dir", None),
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
        )
    elif action == "retention":
        payload = service.retention(
            retention_days=getattr(args, "retention_days", 30),
            limit=args.limit,
            category=getattr(args, "category", None),
            severity=getattr(args, "severity", None),
            project=getattr(args, "project_filter", None),
            project_dir=getattr(args, "project_dir", None),
        )
    elif action == "prune":
        payload = service.prune(
            retention_days=getattr(args, "retention_days", 30),
            limit=args.limit,
            confirm=bool(getattr(args, "confirm", False)),
            category=getattr(args, "category", None),
            severity=getattr(args, "severity", None),
            project=getattr(args, "project_filter", None),
            project_dir=getattr(args, "project_dir", None),
        )
    else:
        # Keep the original no-options call shape for injected test doubles and
        # older integrations while forwarding filters when explicitly chosen.
        filters = {
            "cursor": getattr(args, "cursor", None),
            "category": getattr(args, "category", None),
            "severity": getattr(args, "severity", None),
            "source": getattr(args, "source", None),
            "remote": getattr(args, "remote", None),
            "project": getattr(args, "project_filter", None),
            "project_dir": getattr(args, "project_dir", None),
            "since": getattr(args, "since", None),
            "until": getattr(args, "until", None),
        }
        if not any(value is not None for value in filters.values()):
            payload = service.list(args.limit)
        else:
            payload = service.list(args.limit, **filters)
    _emit(payload, bool(args.json))
    if not payload.get("ok"):
        raise SystemExit(1)


register_specs((CommandSpec(
    name="feedback", handler=cmd_feedback, configure=configure_parser,
    owner=__name__, order=206, scope="global", destructive=False,
),))
