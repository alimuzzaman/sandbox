"""Closed, explicit nonsecret publisher diagnostic; no controller fallback."""
from __future__ import annotations

import json
from pathlib import Path
import re

from sandbox.registry import CommandSpec, register_specs
from sandbox.hosting.publisher_proof import CONTRACT


def configure_parser(parser):
    parser.add_argument("--remote", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--json", action="store_true", required=True)


def dispatch(args):
    from sandbox.application.server_cli import server_cli_context_if_selected, dispatch_server_cli
    from sandbox.hosting.server_cli_target import selected_owner_target
    from sandbox.hosting.publisher_proof import run_proof
    started = False
    try:
        if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.remote) is None
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", args.request_id) is None):
            raise ValueError("invalid diagnostic identity")
        # Shared CLI composition adds instance/label options. They must not
        # select unrelated configuration or silently broaden this purpose.
        values = list(getattr(args, "_server_cli_argv", ()))
        if values[:1] == ["--strict-runtime"]:
            values.pop(0)
        if values[:1] != ["publisher-proof"]:
            raise ValueError("invalid diagnostic command")
        values.pop(0)
        seen = set()
        while values:
            token = values.pop(0)
            option, equals, value = token.partition("=")
            if option not in {"--remote", "--request-id", "--json"} or option in seen:
                raise ValueError("unexpected diagnostic option")
            seen.add(option)
            if option == "--json":
                if equals:
                    raise ValueError("unexpected diagnostic value")
            else:
                if not equals:
                    if not values:
                        raise ValueError("missing diagnostic value")
                    value = values.pop(0)
                if value != (args.remote if option == "--remote" else args.request_id):
                    raise ValueError("diagnostic value mismatch")
        if seen != {"--remote", "--request-id", "--json"}:
            raise ValueError("incomplete diagnostic command")
        argv = ("publisher-proof", "--remote", args.remote, "--request-id", args.request_id, "--json")
        context = server_cli_context_if_selected()
        if context is not None:
            if (tuple(context.argv) != argv or context.request_id != args.request_id
                    or context.origin_remote_name != args.remote):
                raise ValueError("diagnostic original binding mismatch")
            target = selected_owner_target(args.remote)
            if target is None:
                raise ValueError("diagnostic owner unavailable")
            started = True
            payload = run_proof(target)
        else:
            started = True
            payload = dispatch_server_cli(args.remote, argv,
                project_dir=str(Path(__file__).resolve().parents[2]),
                request_id=args.request_id, timeout_seconds=180,
                required_capabilities=(CONTRACT,))
    except Exception as error:
        payload = {"schema_version": 1, "contract": CONTRACT, "ok": False,
                   "status": "unconfirmed" if started else "unavailable",
                   "request_id": args.request_id if re.fullmatch(
                       r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", args.request_id) else None}
        if getattr(error, "code", None) == "server_cli_capability_unavailable":
            payload["status"] = "unavailable"
    print(json.dumps(payload, sort_keys=True))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise SystemExit(1)
    return True


def cmd_publisher_proof(_cfg, args):
    dispatch(args)


register_specs((CommandSpec(name="publisher-proof", handler=cmd_publisher_proof,
    configure=configure_parser, owner=__name__, order=208, scope="global",
    predispatch_policy=lambda _args: True, preconfiguration_dispatch=dispatch,
    help="Run three fixed nonsecret publisher cases in a retained Tailnet owner job"),))
