---
name: "sandbox-cli"
description: "Operate Sandbox through its CLI first; MCP is optional client integration."
---

# Sandbox CLI-first operation

Use this skill when MCP is unavailable, unnecessary, or would load tools for a
different runtime. Run `sb guide --project-dir .` first and follow its
runtime-aware command catalog.

For generic Compose projects use `sb ensure`, `sb status`, `sb logs`,
`sb exec -- <argv...>`, and `sb deploy --remote <name> --ensure --expose`.
For WordPress projects use `sb ensure`, `sb wp -- <wp-cli args...>`,
`sb test`, and the same deploy command. Use `sb mcp --project-dir .` only
when an MCP client specifically needs live tools.

After required verification succeeds, stage, commit, and push the active
branch automatically. Never force-push, tag, release, deploy, open/merge a
PR, or commit secrets.
