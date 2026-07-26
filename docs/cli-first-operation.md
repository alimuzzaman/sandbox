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

Once a remote job is running on its selected VPS, Sandbox invokes its nested
project commands with `--local`. In that context, `--local` means the selected
VPS's co-located runtime, not the developer workstation; it prevents a
remote-first project policy from recursively submitting another remote job.

```sh
./sb exec --remote scaleway-sandbox --workspace node-unit --timeout 3600 --detach -- npm test
./sb job-status <job-id> --json
./sb job-output <job-id> --follow
./sb job-output <job-id> --stream stderr --tail-bytes 8192 --wait-seconds 2
./sb workspace create --local --workspace node-unit
./sb test matrix --local --workspace node-20 --workspace node-22 --timeout 3600 -- npm test
./sb ci run .github/workflows/tests.yml --remote scaleway-sandbox --timeout 3600 --json
```

Use a named persistent workspace for development. Use deterministic isolated
labels for parallel matrix cells, retain failures for diagnosis, and reset or
destroy workspaces explicitly. For live remote operations, prefer the
co-located remote MCP server and its durable job status/output tools.

Generic Compose instances are resource-bounded by default (2 CPUs, 4 GiB RAM,
and 512 PIDs); override those values only through `compose.resources` in the
project descriptor. The remote scheduler admits at most two jobs and refuses
new work below its free-memory/disk floors. When SSH is unavailable but the
HTTPS control plane responds, use the authenticated, log-free host snapshot:

```sh
./sb remote service diagnostics scaleway-sandbox --json
```

Output observation is control-plane only: `job-output` reads durable files in
bounded cursor pages, including a selected stream, a tail, or a bounded
long-poll. It never keeps the test process's stdout/stderr pipes open over SSH
or MCP. MCP `run_tests(..., remote=..., workspace=..., timeout_seconds=...)`
uses the same detached runtime and returns a job ID; use `job_status` and
`job_output` to observe it.

`ci run --remote` applies the same durable model to compatible Linux workflows:
preflight first, deploy once, then submit an aggregate parent and isolated child
job for every selected workflow matrix cell. The parent status includes child
counts while each child retains its own output, deadline, workspace, result, and
artifact declarations. Use `--local` to force the local `act` path.

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
