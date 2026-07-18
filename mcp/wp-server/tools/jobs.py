"""Runtime-neutral durable job MCP group.

The group is registered before tools are added so its dependency ownership is explicit
throughout the implementation sequence.
"""

from __future__ import annotations

from dependencies import ToolDependencies


def register(_server, dependencies: ToolDependencies) -> None:
    dependencies.require("job_service")
    dependencies.require("target_service")
    dependencies.require("workspace_service")
