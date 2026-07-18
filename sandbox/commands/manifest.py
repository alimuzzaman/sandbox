"""Explicit, deterministic built-in command-module manifest.

The CLI parser still contains a bounded compatibility bridge for old argument
definitions. New feature modules own a CommandSpec and need only be listed here.
"""

from __future__ import annotations

import importlib


BUILTIN_COMMAND_MODULES = (
    "sandbox.commands.lifecycle",
    "sandbox.commands.instances_cmd",
    "sandbox.commands.config_setup",
    "sandbox.commands.data",
    "sandbox.commands.wp",
    "sandbox.commands.net",
    "sandbox.commands.debug",
    "sandbox.commands.abilities",
    "sandbox.commands.jobs",
    "sandbox.commands.skill",
    "sandbox.commands.runtime",
    "sandbox.commands.integ",
    "sandbox.commands.ui_dash",
    "sandbox.commands.cache",
    "sandbox.commands.license",
    "sandbox.commands.migrate",
    "sandbox.commands.uninstall",
    "sandbox.commands.e2e",
    "sandbox.commands.ci",
    "sandbox.commands.plugin_check",
    "sandbox.commands.remote",
    "sandbox.commands.deploy",
    "sandbox.commands.hosting",
    "sandbox.commands.preview",
    "sandbox.commands.secrets",
    "sandbox.commands.hermes",
    "sandbox.commands.recovery",
)

# Every currently shipped parser still defined in ``sandbox.cli`` is named here
# as an explicit compatibility bridge. Feature modules can replace one of these
# entries with a CommandSpec/configure callback without changing the CLI
# composition root or a command handler.
LEGACY_BRIDGE_COMMANDS = {
    "up": "lifecycle", "down": "lifecycle", "status": "lifecycle",
    "logs": "lifecycle", "shell": "lifecycle", "install": "lifecycle",
    "smoke": "lifecycle", "doctor": "lifecycle", "update": "lifecycle",
    "open": "lifecycle",
    "init": "instances_cmd", "ensure": "instances_cmd",
    "instances": "instances_cmd", "instance": "instances_cmd", "focus": "instances_cmd",
    "setup": "config_setup", "apply": "config_setup", "onboard": "config_setup",
    "global": "config_setup", "connect": "config_setup",
    "snapshot": "data", "restore": "data", "snapshots": "data", "reset": "data",
    "clean": "data", "wp": "wp", "seed": "wp", "visit": "wp",
    "domains": "net", "secure": "net", "server": "net", "pxdiff": "net",
    "vrdiff": "net", "specextract": "net", "specdiff": "net", "specgate": "net",
    "xdebug": "debug", "dump": "debug", "qm": "debug", "introspect": "debug",
    "test": "debug", "selftest": "debug",
    "abilities": "abilities", "job": "jobs", "jobs": "jobs", "async-job": "jobs",
    "skill": "skill", "mcp": "integ", "claude": "integ", "mcp-install": "integ",
    "exec": "runtime", "guide": "runtime",
    "dashboard": "ui_dash", "ui": "ui_dash", "web": "ui_dash", "cache": "cache",
    "license": "license", "migrate": "migrate", "home": "migrate", "uninstall": "uninstall",
    "e2e": "e2e", "ci": "ci", "plugin-check": "plugin_check", "remote": "remote",
    "deploy": "deploy", "host": "hosting", "preview": "preview", "secrets": "secrets",
    "hermes": "hermes", "recovery": "recovery",
}


def load_builtin_commands() -> tuple[str, ...]:
    for module_name in BUILTIN_COMMAND_MODULES:
        importlib.import_module(module_name)
    return BUILTIN_COMMAND_MODULES


def validate_builtin_command_coverage() -> tuple[str, ...]:
    """Return unbridged registered command names, if a manifest was missed."""
    from sandbox.registry import COMMANDS
    return tuple(sorted(set(COMMANDS) - set(LEGACY_BRIDGE_COMMANDS)))
