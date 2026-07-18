---
name: "sandbox-cli"
description: "Operate Sandbox through its CLI first; MCP is optional client integration."
---

# Sandbox CLI-first operation

Use this skill when MCP is unavailable, unnecessary, or would load tools for a
different runtime. The `sb` CLI is the primary operational interface; MCP is an
optional adapter for MCP-capable clients.

## Start with the runtime guide

From the project root, run:

```bash
./sb guide --project-dir .
```

Use `--json` when a structured command catalog is useful. The guide is runtime
aware: generic Compose projects receive generic lifecycle and execution
commands; WordPress projects receive WordPress commands.

## Generic Compose projects

```bash
./sb init --type compose          # first-time project setup
./sb ensure                       # create/start/reconcile the local instance
./sb status                       # runtime state and URL
./sb logs                         # public service logs
./sb exec -- <argv...>            # explicit argv in the declared service
./sb deploy --remote <name> --ensure --expose
```

Pass an argv list to `sb exec`; do not rely on an implicit shell. If a shell is
required, make the boundary explicit, for example `./sb exec -- sh -lc 'npm
test'`.

## WordPress projects

```bash
./sb init
./sb ensure
./sb status
./sb wp -- plugin list
./sb test
./sb deploy --remote <name> --ensure --expose
```

Use WordPress-specific commands only when the project guide reports a
WordPress runtime. Do not use `wp`, database, or plugin commands against a
generic Compose project.

## Delivery

After required verification succeeds, stage the relevant files, commit, and
push the active branch automatically. Never force-push, tag, release, deploy,
open/merge a PR, or include secrets in a commit. Report the commit and remote
after the push succeeds.

## MCP remains available

Use `./sb mcp --project-dir .` only when an MCP client needs live tool calls.
It remains runtime-scoped, so a generic project does not receive WordPress
tools and a WordPress project does not receive generic container-exec tools.
