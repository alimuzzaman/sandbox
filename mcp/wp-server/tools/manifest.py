"""Explicit deterministic manifest for built-in MCP tool groups."""

from __future__ import annotations

import importlib

from composition import ToolGroupRegistry, ToolGroupSpec


BUILTIN_TOOL_GROUPS = (
    "instances", "wp", "net", "data", "fs", "mail", "context", "cache",
    "abilities", "skills", "debug", "e2e", "ci", "asyncjobs",
    "plugin_check", "remote", "hermes", "recovery",
)


def _import_group(group_id: str):
    def register(_server, _dependencies):
        importlib.import_module(f"tools.{group_id}")
    return register


def built_in_tool_registry() -> ToolGroupRegistry:
    registry = ToolGroupRegistry()
    for order, group_id in enumerate(BUILTIN_TOOL_GROUPS):
        registry.add(ToolGroupSpec(
            group_id=group_id,
            register=_import_group(group_id),
            owner=f"tools.{group_id}",
            order=order,
        ))
    return registry
