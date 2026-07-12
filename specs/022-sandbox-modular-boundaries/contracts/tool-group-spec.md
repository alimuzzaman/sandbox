# Contract: MCP Tool Group Specification

Each group declares a stable identifier, owner, registration callback, dependency keys, project scope, capability metadata, order, and compatibility aliases.

The MCP composer loads one explicit package-owned manifest, injects dependencies, rejects duplicate groups/tools, and preserves public tool names, required parameters, and response contracts. Registration cannot depend on project-provided code, filesystem enumeration, broad mutable globals, or incidental import order.
