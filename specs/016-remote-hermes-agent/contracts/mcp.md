# MCP Contract: Hermes Control and Hermes-to-Sandbox Access

## Direction A: Hermes uses Sandbox MCP

The Sandbox-managed Hermes profile contains one local stdio server:

```yaml
mcp_servers:
  sandbox:
    command: "/absolute/remote/SANDBOX_HOME/sb-src/sb"
    args: ["mcp"]
    env:
      SANDBOX_HOME: "/absolute/remote/SANDBOX_HOME"
    enabled: true
    connect_timeout: 60
    timeout: 1200
    supports_parallel_tool_calls: false
    tools:
      resources: true
      prompts: true
```

Contract rules:

- No `include` or `exclude` key is generated. Hermes discovers the entire catalog exposed by that Sandbox revision.
- The stdio process runs as the existing remote Sandbox account and uses the same absolute home as direct CLI calls.
- Diagnostics compare discovered tool/resource/prompt names with a direct catalog query and fail if any Sandbox item is absent.
- `supports_parallel_tool_calls` remains false because calls can share registries, repositories, databases, files, containers, and service state.
- A tool that operates on a project still requires its normal `project_dir` and normal Sandbox lifecycle. Hermes calls `ensure_instance` before other WordPress tools.
- Full tool discovery does not bypass individual tool confirmation parameters such as `confirm: true`; Hermes policy additionally requires explicit user intent before destructive tools.

## Direction B: Sandbox controls remote Hermes

Two tools are added to the local Sandbox MCP server.

### `hermes_status`

```text
hermes_status(remote: string) -> HermesStatusResult
```

Read-only. Resolves a configured remote and returns the stable envelope defined by the CLI contract with:

```json
{
  "ok": true,
  "action": "status",
  "remote": "scaleway-sandbox",
  "version": "v2026.7.7.2",
  "commit": "<40-hex>",
  "status": "healthy",
  "repo": null,
  "path": null,
  "job_id": null,
  "data": {
    "configured": true,
    "mcp_catalog_complete": true,
    "gateway": "active",
    "dashboard": "not_installed",
    "gates": {"v1_core": "passed", "v2_operations": "not_run"}
  },
  "error": null
}
```

The result excludes raw SSH connection values, credentials, environment variables, repository URL userinfo, service unit bodies, and raw logs.

### `hermes_run`

```text
hermes_run(
  remote: string,
  repo: string,
  prompt: string,
  worktree: bool = true,
  async_: bool = true,
  timeout: int = 1200
) -> HermesRunResult
```

Rules:

- `remote` must resolve through the existing named-remote registry.
- `repo` is a managed repository name, never a path.
- `prompt` is passed directly to the child process and is not written to `hermes.json` or included in diagnostic output.
- `worktree` defaults true. Setting false is explicit in the returned metadata.
- `async_` defaults true to avoid holding MCP transport calls across model execution. It returns an existing Sandbox async job ID.
- Synchronous execution has a bounded timeout and bounded sanitized result.
- Cancellation uses the existing `async_job_kill` contract and targets the full process group.
- Repository locks and V2 resource policies apply before launch.

Asynchronous launch result:

```json
{
  "ok": true,
  "action": "run",
  "remote": "scaleway-sandbox",
  "version": "v2026.7.7.2",
  "status": "queued",
  "repo": "example",
  "path": null,
  "job_id": "0123456789abcdef",
  "data": {"worktree": true},
  "error": null
}
```

## Tool Registration and Compatibility

- `mcp/wp-server/tools/hermes.py` owns tool validation and calls the shared core orchestration; it does not construct raw SSH shell strings.
- The tool module is imported by the MCP server registration path and covered by catalog tests.
- Older remote Sandbox installations that lack Hermes support return a structured `unsupported_remote_runtime` error and recommend reprovision/reconcile; they do not attempt ad-hoc deployment from an MCP call.
- MCP calls never install Hermes, configure provider credentials, accept gateway pairing, update releases, restore backups, or expose the dashboard. Those remain explicit CLI/operator workflows.
