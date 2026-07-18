# CLI-first Sandbox operation

Sandbox can be used entirely through `sb`; an MCP server is optional client
integration, not a requirement for local development or remote deployment.

## Durable remote-first execution

Projects can opt into a configured remote default:

```json
{"runtime":{"default":"remote","remote":"scaleway-sandbox","workspace":"default"}}
```

Use `--local` as an explicit override. Remote execution deploys the exact local
working tree before acceptance, then the remote supervisor drains process pipes
to durable local files. CLI/MCP callers read bounded retained output by cursor;
they do not hold test pipes open across SSH.

```sh
./sb exec --remote scaleway-sandbox --workspace node-unit --timeout 3600 --detach -- npm test
./sb job-status <job-id> --json
./sb job-output <job-id> --follow
./sb workspace create --local --workspace node-unit
./sb test matrix --local --workspace node-20 --workspace node-22 --timeout 3600 -- npm test
```

Use a named persistent workspace for development. Use deterministic isolated
labels for parallel matrix cells, retain failures for diagnosis, and reset or
destroy workspaces explicitly. For live remote operations, prefer the
co-located remote MCP server and its durable job status/output tools.

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
