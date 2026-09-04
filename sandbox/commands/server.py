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
    subcommand = getattr(args, "server_subcommand", None)
    if subcommand != "config":
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
    subparsers = parser.add_subparsers(dest="server_subcommand")

    # --- config grammar ---
    config_parser = subparsers.add_parser("config", help="Manage server config fragments")
    config_subs = config_parser.add_subparsers(dest="config_action")

    # apply
    apply_parser = config_subs.add_parser("apply", help="Apply a config fragment")
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
    list_parser.add_argument("--json", action="store_true", default=False)

    # show
    show_parser = config_subs.add_parser("show", help="Show fragment details")
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
    revert_parser.add_argument("--name", required=True, help="Fragment name")
    revert_parser.add_argument("--json", action="store_true", default=False)

    # --- legacy server switch ---
    # Accept ``sb server <type>`` and ``sb server <instance> <type>``
    # by making instance optional and server_type a positional that's
    # recognized only when the first token is not ``config``.
    for server_type in _LEGACY_SERVER_TYPES:
        switch_parser = subparsers.add_parser(
            server_type,
            help="Switch to %s server" % server_type,
        )
        switch_parser.set_defaults(server_type=server_type)


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
    subcommand = getattr(args, "server_subcommand", None)

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
# Config operation stubs - will be connected to service in T026/T032
# ---------------------------------------------------------------------------


def _config_apply(cfg: Any, args: argparse.Namespace, use_json: bool) -> None:
    """Apply a named server config fragment."""
    # T026: Connect to ServerConfigService.apply()
    raise NotImplementedError("server config apply not yet implemented")


def _config_list(cfg: Any, args: argparse.Namespace, use_json: bool) -> None:
    """List active server config fragments."""
    # T026: Connect to ServerConfigService.list()
    raise NotImplementedError("server config list not yet implemented")


def _config_show(cfg: Any, args: argparse.Namespace, use_json: bool) -> None:
    """Show a server config fragment by name."""
    # T026: Connect to ServerConfigService.show()
    raise NotImplementedError("server config show not yet implemented")


def _config_revert(cfg: Any, args: argparse.Namespace, use_json: bool) -> None:
    """Revert a named server config fragment."""
    # T026: Connect to ServerConfigService.revert()
    raise NotImplementedError("server config revert not yet implemented")


# ---------------------------------------------------------------------------
# Result rendering helpers (T026)
# ---------------------------------------------------------------------------


def _exit_status(outcome: str) -> int:
    """Map an outcome to an exit status code."""
    return 0 if outcome in ("active", "no_op") else 1


def _render_json(result: dict[str, Any]) -> None:
    """Write a content-free JSON result to stdout."""
    json.dump(result, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")


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
