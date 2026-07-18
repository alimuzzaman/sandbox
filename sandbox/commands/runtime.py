"""CLI-first runtime operations shared by non-WordPress Compose projects."""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.application.context import preflight_instance_capability, runtime_service
from sandbox.core import _core, die
from sandbox.registry import CommandSpec, register_specs
from sandbox.runtimes.base import OperationError, OperationRequest


_GUIDES = {
    "compose": (
        ("init", "./sb init --type compose", "Create a local Compose project instance."),
        ("ensure", "./sb ensure", "Start or reconcile the local instance."),
        ("status", "./sb status", "Inspect the declared runtime."),
        ("logs", "./sb logs", "Read service logs."),
        ("exec", "./sb exec -- <argv...>", "Run an argv list in the declared public service."),
        ("deploy", "./sb deploy --remote <name> --ensure --expose", "Deploy to a provisioned remote."),
    ),
    "wordpress": (
        ("init", "./sb init", "Create a local WordPress project instance."),
        ("ensure", "./sb ensure", "Start or reconcile the local instance."),
        ("status", "./sb status", "Inspect the declared runtime."),
        ("wp", "./sb wp -- <wp-cli args...>", "Run WP-CLI."),
        ("test", "./sb test", "Run the configured test mode."),
        ("deploy", "./sb deploy --remote <name> --ensure --expose", "Deploy to a provisioned remote."),
    ),
}


def configure_exec_parser(parser) -> None:
    parser.description = "Run an argv list in a generic Compose public service."
    parser.add_argument("command", nargs="...", help="argv after --; shell text is not inferred")
    parser.add_argument("--json", action="store_true", help="emit the runtime result as JSON")


def configure_guide_parser(parser) -> None:
    parser.description = "Show the CLI-first workflow for a project runtime."
    parser.add_argument("--project-dir", default=None,
                        help="project to inspect (default: current directory when configured)")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable command catalog")


def cmd_exec(cfg, args) -> None:
    """Execute explicit argv in a generic Compose service without MCP."""
    command = list(getattr(args, "command", ()) or ())
    # argparse REMAINDER deliberately retains the conventional separator. It
    # is syntax, not part of the command passed to the container.
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        die("usage: ./sb exec -- <argv...>")
    if any(not isinstance(item, str) or not item or "\x00" in item for item in command):
        die("exec requires a non-empty argv list without NUL bytes")

    capability_error = preflight_instance_capability(cfg, args.resolved_instance, "compose.exec")
    if capability_error is not None:
        die(capability_error.message)
    owner = _core().registry_find_instance(args.resolved_instance) or {}
    result = runtime_service(cfg).invoke(OperationRequest(
        project_root=owner["root"], operation="exec",
        label=owner.get("label", "default"), arguments={"argv": command},
    ))
    if isinstance(result, OperationError):
        die(result.message)
    data = {"ok": result.ok, "operation": result.operation, **dict(result.data)}
    if getattr(args, "json", False):
        print(json.dumps(data))
        return
    print(data.get("output", ""), end="")


def cmd_guide(_cfg, args) -> None:
    """Print the no-MCP command catalog, optionally tailored to a project."""
    project_dir = getattr(args, "project_dir", None)
    kind = None
    root = None
    if project_dir:
        try:
            project = _core().load_project_config(project_dir)
        except Exception as exc:
            die(f"invalid --project-dir {project_dir!r}: {exc}")
        kind = project.get("kind")
        root = project.get("root")
    else:
        try:
            project = _core().load_project_config(Path.cwd())
        except Exception:
            project = None
        if project:
            kind = project.get("kind")
            root = project.get("root")

    selected = (kind or "compose").lower()
    if selected not in _GUIDES:
        die(f"no CLI guide for project kind {selected!r}")
    commands = [
        {"name": name, "command": command, "purpose": purpose}
        for name, command, purpose in _GUIDES[selected]
    ]
    payload = {
        "mode": "cli-first",
        "project_kind": selected,
        "project_root": root,
        "skill": "./sb skill show sandbox-cli",
        "commands": commands,
        "mcp": "optional; use ./sb mcp only when an MCP client needs live tools",
    }
    if getattr(args, "json", False):
        print(json.dumps(payload))
        return
    print(f"CLI-first Sandbox guide ({selected})")
    if root:
        print(f"  project: {root}")
    print("  skill:   ./sb skill show sandbox-cli")
    print("  MCP is optional; start it only for MCP clients.")
    for item in commands:
        print(f"  {item['command']:<58} {item['purpose']}")


register_specs((
    CommandSpec(name="exec", handler=cmd_exec, configure=configure_exec_parser,
                owner=__name__, scope="instance", required_capability="compose.exec"),
    CommandSpec(name="guide", handler=cmd_guide, configure=configure_guide_parser,
                owner=__name__, scope="global"),
))
