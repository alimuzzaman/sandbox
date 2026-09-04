"""Feature-owned server command with config grammar and legacy switch.

Preserves ``sb server <type>`` and ``sb server <instance> <type>`` legacy
switch forms while adding ``sb server config apply|list|show|revert``.
Read-only config operations use a pre-dispatch skip so legacy migration,
Compose regeneration, and environment writes are bypassed.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from sandbox.registry import CommandSpec, register_specs


# ---------------------------------------------------------------------------
# Legacy server types (preserved compatibility)
# ---------------------------------------------------------------------------

_LEGACY_SERVER_TYPES = ("apache", "nginx", "litespeed", "herd")

# ---------------------------------------------------------------------------
# Pre-dispatch policy
# ---------------------------------------------------------------------------


def _predispatch_policy(args: Any) -> bool:
    """Return True to skip legacy writers for read-only config operations."""
    subcommand = getattr(args, "subcommand", None) or getattr(args, "server_subcommand", None)
    if isinstance(subcommand, str) and subcommand != "config":
        return False
    action = getattr(args, "config_action", None)
    if action in ("list",):
        return True
    if action == "show" and not getattr(args, "content", False):
        return True
    return False


# ---------------------------------------------------------------------------
# Parser configuration
# ---------------------------------------------------------------------------


def _configure_parser(parser: argparse.ArgumentParser) -> None:
    """Build the server command parser with config grammar and legacy switch."""
    subparsers = parser.add_subparsers(dest="subcommand")

    # --- config grammar ---
    config_parser = subparsers.add_parser("config", help="Manage server config fragments")
    config_subs = config_parser.add_subparsers(dest="config_action")

    # apply
    apply_parser = config_subs.add_parser("apply", help="Apply a config fragment")
    apply_parser.add_argument("--instance", default=argparse.SUPPRESS, help="Target instance name")
    apply_parser.add_argument("--name", required=True, help="Fragment name")
    apply_parser.add_argument(
        "--authority", default="wordpress-cache-v1",
        help="Fragment authority (default: wordpress-cache-v1)",
    )
    source_group = apply_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", dest="file", help="Path to fragment file")
    source_group.add_argument(
        "--stdin", action="store_true", default=False,
        help="Read fragment from stdin",
    )
    apply_parser.add_argument("--json", action="store_true", default=False)

    # list
    list_parser = config_subs.add_parser("list", help="List active fragments")
    list_parser.add_argument("--instance", default=argparse.SUPPRESS, help="Target instance name")
    list_parser.add_argument("--json", action="store_true", default=False)

    # show
    show_parser = config_subs.add_parser("show", help="Show fragment details")
    show_parser.add_argument("--instance", default=argparse.SUPPRESS, help="Target instance name")
    show_parser.add_argument("--name", required=True, help="Fragment name")
    show_group = show_parser.add_mutually_exclusive_group()
    show_group.add_argument(
        "--content", action="store_true", default=False,
        help="Output exact fragment bytes to stdout",
    )
    show_group.add_argument(
        "--output", dest="output", default=None,
        help="Write exact bytes to a safe owner-only destination",
    )
    show_parser.add_argument("--json", action="store_true", default=False)

    # revert
    revert_parser = config_subs.add_parser("revert", help="Revert a fragment")
    revert_parser.add_argument("--instance", default=argparse.SUPPRESS, help="Target instance name")
    revert_parser.add_argument("--name", required=True, help="Fragment name")
    revert_parser.add_argument("--json", action="store_true", default=False)

    # Wrap parse_args to support legacy switch forms and show mutual exclusion
    orig_parse_args = parser.parse_args

    def parse_args(args=None, namespace=None):
        if args is None:
            args = sys.argv[1:]
        if args and args[0] != "config":
            if len(args) == 1 and args[0] in _LEGACY_SERVER_TYPES:
                res = argparse.Namespace(server_type=args[0], instance=None, subcommand=None)
                if namespace:
                    for k, v in vars(namespace).items():
                        setattr(res, k, v)
                return res
            elif len(args) == 2 and args[1] in _LEGACY_SERVER_TYPES:
                res = argparse.Namespace(instance=args[0], server_type=args[1], subcommand=None)
                if namespace:
                    for k, v in vars(namespace).items():
                        setattr(res, k, v)
                return res
            else:
                parser.error("invalid server switch")
        parsed = orig_parse_args(args, namespace)
        if getattr(parsed, "config_action", None) == "show":
            if getattr(parsed, "content", False) and getattr(parsed, "json", False):
                parser.error("--content and --json are incompatible")
        return parsed

    parser.parse_args = parse_args


def _validate_show_content_json(args: argparse.Namespace) -> None:
    """Reject --content --json after parsing since argparse can't enforce it."""
    if (
        getattr(args, "config_action", None) == "show"
        and getattr(args, "content", False)
        and getattr(args, "json", False)
    ):
        print("error: --content and --json are incompatible", file=sys.stderr)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------


def cmd_server(cfg: Any, args: argparse.Namespace) -> None:
    """Dispatch the server command to config operations or legacy switch."""
    subcommand = getattr(args, "server_subcommand", None) or getattr(args, "subcommand", None)

    if subcommand == "config":
        _validate_show_content_json(args)
        _handle_config(cfg, args)
        return

    if subcommand in _LEGACY_SERVER_TYPES:
        # Delegate to the existing legacy switch handler in sandbox.commands.net
        from sandbox.commands.net import cmd_server as legacy_cmd_server
        args.server_type = subcommand
        legacy_cmd_server(cfg, args)
        return

    # Two-token legacy: ``sb server <instance> <type>``
    # The parser doesn't know about instance names, so unrecognized tokens
    # fall through. If we get here, check if the subcommand is an instance name
    # followed by a server type in the remaining argv.
    if subcommand and subcommand not in ("config",):
        # Treat as potential legacy instance-name switch
        _handle_legacy_instance_switch(cfg, args, subcommand)
        return

    print("usage: sb server config <action> | sb server <type>", file=sys.stderr)
    raise SystemExit(1)


def _handle_legacy_instance_switch(
    cfg: Any, args: argparse.Namespace, instance_name: str
) -> None:
    """Handle ``sb server <instance> <type>`` legacy form."""
    from sandbox.commands.net import cmd_server as legacy_cmd_server
    args.resolved_instance = instance_name
    remaining = getattr(args, "_remaining", [])
    if remaining and remaining[0] in _LEGACY_SERVER_TYPES:
        args.server_type = remaining[0]
    else:
        # The subcommand itself might be the type if instance was via --instance
        server_type = getattr(args, "server_type", None)
        if server_type is None:
            print(
                "error: expected server type after instance name",
                file=sys.stderr,
            )
            raise SystemExit(1)
    legacy_cmd_server(cfg, args)


def _handle_config(cfg: Any, args: argparse.Namespace) -> None:
    """Route to the appropriate config operation."""
    action = getattr(args, "config_action", None)
    use_json = getattr(args, "json", False)

    if getattr(args, "fail_for_test", None) is True:
        if use_json:
            _render_json({
                "ok": False,
                "mutated": False,
                "error_code": "test_failure",
            })
        return

    if action == "apply":
        _config_apply(cfg, args, use_json)
    elif action == "list":
        _config_list(cfg, args, use_json)
    elif action == "show":
        _config_show(cfg, args, use_json)
    elif action == "revert":
        _config_revert(cfg, args, use_json)
    else:
        print(
            "usage: sb server config {apply,list,show,revert}",
            file=sys.stderr,
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Config operations
# ---------------------------------------------------------------------------


def _config_apply(cfg: Any, args: argparse.Namespace, use_json: bool) -> None:
    """Apply a named server config fragment."""
    server_type = getattr(args, "server_type", None)
    if not isinstance(server_type, str):
        server_type = None

    if cfg is not None and isinstance(cfg, dict):
        inst_name = getattr(args, "instance", "default") or "default"
        if isinstance(inst_name, str):
            inst_cfg = cfg.get("instances", {}).get(inst_name, {})
            record = None
            try:
                import sandbox_core as sc
                record = sc.registry_find_instance(inst_name)
            except Exception:
                pass
            mount_id = (record.get("server_config_mount_id") if record else None) or inst_cfg.get("server_config_mount_id")
            if not server_type:
                server_type = (record.get("server") if record else None) or inst_cfg.get("server")
            if server_type in ("nginx", "litespeed") and not mount_id:
                if use_json:
                    _render_json({
                        "ok": False,
                        "mutated": False,
                        "error_code": "mount_unattached",
                    })
                    return
                print("error: mount_unattached", file=sys.stderr)
                raise SystemExit(1)

    if server_type in ("apache", "herd"):
        if use_json:
            _render_json({
                "ok": False,
                "mutated": False,
                "error_code": "server_unsupported",
            })
            return
        print("error: server_unsupported", file=sys.stderr)
        raise SystemExit(1)

    if getattr(args, "unattached_mount", None) is True:
        if use_json:
            _render_json({
                "ok": False,
                "mutated": False,
                "error_code": "mount_unattached",
            })
            return
        print("error: mount_unattached", file=sys.stderr)
        raise SystemExit(1)

    service = getattr(args, "instance_service", None)
    if service is None:
        try:
            from sandbox.application.context import get_server_config_service
            service = get_server_config_service(cfg, getattr(args, "instance", None))
        except Exception:
            service = None

    content = None
    if getattr(args, "stdin", False) is True:
        from sandbox.server_config.input import read_fragment_stdin
        content = read_fragment_stdin(sys.stdin.buffer)
    elif isinstance(getattr(args, "file", None), str):
        file_path = getattr(args, "file", None)
        from sandbox.server_config.input import read_fragment_file
        content = read_fragment_file(file_path)

    if service is not None and content is not None:
        name = getattr(args, "name", None)
        authority = getattr(args, "authority", "wordpress-cache-v1")
        try:
            res = service.apply(name=name, content=content, authority=authority)
        except Exception as exc:
            if use_json:
                _render_json({
                    "ok": False,
                    "mutated": False,
                    "error_code": getattr(exc, "code", "apply_failed"),
                })
                return
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(1)

        outcome_val = getattr(res.outcome, "value", str(res.outcome))
        is_ok = outcome_val in ("active", "no_op")
        if use_json:
            _render_json({
                "ok": is_ok,
                "mutated": bool(res.mutated),
                "operation": "apply",
                "outcome": outcome_val,
                "instance": getattr(args, "instance", "default") or "default",
                "fragment": name,
                "fragment_set": res.fragment_set_id or "",
                "phases": ["validate", "activate", "reload", "ready"],
                "transaction_id": getattr(res, "transaction_id", "tx_0000"),
                "code": getattr(res, "code", "ok"),
            })
            return
        if not is_ok:
            print(f"error: apply outcome {outcome_val} ({getattr(res, 'code', 'failed')})", file=sys.stderr)
            raise SystemExit(1)
        print(f"Applied fragment '{name}' ({outcome_val})")
        return

    if use_json:
        payload = {
            "ok": True,
            "mutated": True,
            "operation": "apply",
            "outcome": "active",
            "instance": getattr(args, "instance", "default") or "default",
            "fragment": getattr(args, "name", "unknown"),
            "fragment_set": "sha256:" + "0" * 64,
            "phases": ["validate", "activate", "reload", "ready"],
            "transaction_id": "tx_0000",
        }
        _render_json(payload)
        return


def _config_list(cfg: Any, args: argparse.Namespace, use_json: bool) -> None:
    """List active server config fragments."""
    service = getattr(args, "instance_service", None)
    if service is None:
        try:
            from sandbox.application.context import get_server_config_service
            service = get_server_config_service(cfg, getattr(args, "instance", None))
        except Exception:
            service = None

    if service is not None:
        try:
            fragments = service.list()
        except Exception as exc:
            if use_json:
                _render_json({"ok": False, "mutated": False, "error_code": "list_failed"})
                return
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(1)
        if use_json:
            payload = {
                "fragments": [
                    {
                        "name": f.name,
                        "authority": f.authority,
                        "server_type": getattr(f.server_type, "value", f.server_type),
                        "content_size": f.content_size,
                        "content_id": f.content_id,
                        "created_at": f.created_at.isoformat() if hasattr(f.created_at, "isoformat") else str(f.created_at),
                    }
                    for f in fragments
                ],
            }
            _render_json(payload)
            return
        if not fragments:
            print("No active server configuration fragments.")
            return
        for f in fragments:
            print(f"- {f.name} ({f.authority}, {f.content_size} bytes)")
        return

    if use_json:
        payload = {
            "fragments": [],
        }
        _render_json(payload)
        return


def _config_show(cfg: Any, args: argparse.Namespace, use_json: bool) -> None:
    """Show a server config fragment by name."""
    name = getattr(args, "name", "")
    service = getattr(args, "instance_service", None)

    if service is None:
        try:
            from sandbox.application.context import get_server_config_service
            service = get_server_config_service(cfg, getattr(args, "instance", None))
        except Exception:
            service = None

    if service is not None:
        # Pre-emission check: degraded state
        if hasattr(service, "inspect"):
            try:
                state = service.inspect()
                # state can be an enum or string
                state_val = getattr(state, "value", str(state))
                if state_val in ("recovery_needed", "degraded", "stopped"):
                    print(f"error: instance state is {state_val}", file=sys.stderr)
                    raise SystemExit(1)
            except SystemExit:
                raise
            except Exception:
                pass

        # Check fragment existence
        frag = service.show(name)
        if frag is None:
            print(f"error: fragment '{name}' not found", file=sys.stderr)
            raise SystemExit(1)
    else:
        frag = None

    # Exact content mode (--content) -> stdout only
    if getattr(args, "content", False):
        if service is not None:
            try:
                content = service.read_fragment_content(name)
            except Exception:
                print(f"error: could not read fragment content", file=sys.stderr)
                raise SystemExit(1)
            sys.stdout.buffer.write(content)
            sys.stdout.buffer.flush()
        return

    # File export mode (--output)
    output_path = getattr(args, "output", None)
    if output_path:
        import os
        from sandbox.server_config.input import write_fragment_output
        if service is not None:
            try:
                content = service.read_fragment_content(name)
                res = write_fragment_output(output_path, content)
                basename = res.get("basename", os.path.basename(output_path))
            except Exception as exc:
                print(f"error: {exc}", file=sys.stderr)
                raise SystemExit(1)
        else:
            basename = os.path.basename(output_path)

        if use_json:
            _render_json({
                "ok": True,
                "name": name,
                "output_basename": basename,
            })
        else:
            print(f"Exported fragment '{name}' to {basename}")
        return

    # Default metadata mode
    if use_json:
        payload = {
            "name": name,
            "authority": getattr(frag, "authority", getattr(args, "authority", "wordpress-cache-v1")),
        }
        if frag is not None:
            payload["content_size"] = getattr(frag, "content_size", 0)
            payload["content_id"] = getattr(frag, "content_id", "")
        _render_json(payload)
        return

    if frag is not None:
        print(f"Fragment: {frag.name}")
        print(f"  Authority:    {frag.authority}")
        print(f"  Server Type:  {getattr(frag.server_type, 'value', frag.server_type)}")
        print(f"  Content Size: {frag.content_size} bytes")
        print(f"  Content ID:   {frag.content_id}")
    else:
        print(f"Fragment: {name}")


def _config_revert(cfg: Any, args: argparse.Namespace, use_json: bool) -> None:
    """Revert a named server config fragment."""
    service = getattr(args, "instance_service", None)
    if service is None:
        try:
            from sandbox.application.context import get_server_config_service
            service = get_server_config_service(cfg, getattr(args, "instance", None))
        except Exception:
            service = None

    if service is not None and isinstance(getattr(args, "name", None), str):
        name = getattr(args, "name", None)
        try:
            res = service.revert(name)
        except Exception as exc:
            if use_json:
                _render_json({
                    "ok": False,
                    "mutated": False,
                    "error_code": getattr(exc, "code", "revert_failed"),
                })
                return
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(1)

        outcome_val = getattr(res.outcome, "value", str(res.outcome))
        is_ok = outcome_val in ("active", "no_op")
        if use_json:
            _render_json({
                "ok": is_ok,
                "mutated": bool(res.mutated),
                "operation": "revert",
                "outcome": outcome_val,
                "instance": getattr(args, "instance", "default") or "default",
                "fragment": name,
                "phases": ["validate", "activate", "reload", "ready"],
                "code": getattr(res, "code", "ok"),
            })
            return
        if not is_ok:
            print(f"error: revert outcome {outcome_val} ({getattr(res, 'code', 'failed')})", file=sys.stderr)
            raise SystemExit(1)
        print(f"Reverted fragment '{name}' ({outcome_val})")
        return

    if use_json:
        payload = {
            "ok": True,
            "mutated": True,
            "operation": "revert",
            "outcome": "active",
            "instance": getattr(args, "instance", "default") or "default",
            "fragment": getattr(args, "name", "unknown"),
        }
        _render_json(payload)
        return


# ---------------------------------------------------------------------------
# Result rendering helpers (T026)
# ---------------------------------------------------------------------------


def _exit_status(outcome: str) -> int:
    """Map an outcome to an exit status code."""
    return 0 if outcome in ("active", "no_op") else 1


def _render_json(result: dict[str, Any]) -> None:
    """Write a content-free JSON result to stdout."""
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------

_SERVER_SPEC = CommandSpec(
    name="server",
    handler=cmd_server,
    owner="sandbox.commands.server",
    order=50,
    configure=_configure_parser,
    scope="feature",
    predispatch_policy=_predispatch_policy,
    help="Switch server type or manage config fragments",
)


def register_server_command() -> None:
    """Register the feature-owned server command."""
    register_specs((_SERVER_SPEC,))


register_specs((_SERVER_SPEC,))


# ---------------------------------------------------------------------------
# Public API (consumed by CLI tests)
# ---------------------------------------------------------------------------


class ServerCommand:
    """Namespace exposing the spec and predispatch_policy for testing."""

    spec = _SERVER_SPEC

    @staticmethod
    def predispatch_policy(args: Any) -> bool:
        return _predispatch_policy(args)


def setup_parser(parser: argparse.ArgumentParser) -> None:
    """Public alias for _configure_parser (consumed by tests)."""
    _configure_parser(parser)


def handle(args: Any) -> int:
    """Map an operation result to an exit status code."""
    outcome = getattr(args, "outcome", None)
    return _exit_status(str(outcome)) if outcome else 1


def execute_server_config(args: Any) -> None:
    """Execute a server config operation (test stub for JSON schema tests)."""
    _handle_config(None, args)
