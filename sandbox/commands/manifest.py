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


def load_builtin_commands() -> tuple[str, ...]:
    for module_name in BUILTIN_COMMAND_MODULES:
        importlib.import_module(module_name)
    return BUILTIN_COMMAND_MODULES
