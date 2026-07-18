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

For remote CI, preflight first, then submit a finite-timeout durable parent and
inspect its retained child jobs: `sb ci preflight <workflow> --remote <name>
--project-dir . --json`; `sb ci run <workflow> --remote <name> --workspace
<label> --timeout 1200 --json`; then `sb job-status`, `sb job-output`, and
`sb job-artifacts` with `--remote <name>`. Remote provision installs `act`.
Safe mode neutralizes deploy/publish steps; `actions/upload-artifact` becomes
Sandbox retained-artifact collection because self-hosted `act` has no GitHub
runtime token. Retry only terminal jobs with `sb job-retry <id> --request-id
<id>`, and retrieve evidence before `sb job-cleanup <id> --logs --artifacts
--metrics`.

After required verification succeeds, stage, commit, and push the active
branch automatically. Never force-push, tag, release, deploy, open/merge a
PR, or commit secrets.
