# CLI-first Sandbox operation

Sandbox can be used entirely through `sb`; an MCP server is optional client
integration, not a requirement for local development or remote deployment.

Start in any configured project with:

```bash
./sb guide --project-dir .
./sb skill show sandbox-cli
```

The guide detects the runtime and emits only its useful commands.

## Generic Compose

```bash
./sb init --type compose
./sb ensure
./sb status
./sb logs
./sb exec -- sh -lc 'npm test'
./sb deploy --remote <name> --ensure --expose
```

`sb exec` accepts an explicit argv list and runs it in the configured public
Compose service. It does not invent a shell, service, or package command.

## WordPress

```bash
./sb init
./sb ensure
./sb status
./sb wp -- plugin list
./sb test
./sb deploy --remote <name> --ensure --expose
```

WordPress-only commands remain capability-gated and are not valid for generic
Compose projects.

## MCP

Run `./sb mcp --project-dir .` only when an MCP-capable client needs live tool
calls. Its catalog remains scoped to the detected runtime.
