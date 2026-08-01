# Contract: CLI and MCP Domain Operations

## CLI

Existing commands remain valid. The `domains` command gains project-scoped, structured
operations while legacy global actions delegate to the same service.

```text
./sb domains status [--project-dir DIR] [--label LABEL] [--json]
./sb domains plan   [--project-dir DIR] [--label LABEL] [--json]
./sb domains apply  [--project-dir DIR] [--label LABEL] [--json]
./sb domains cleanup [--project-dir DIR] [--label LABEL] [--json]
./sb domains reconsider [--resolver ID]
./sb domains support [--json]
```

- `status`, `plan`, and `support` are read-only and non-interactive.
- `apply` may ask for first-use consent only when stdin is a TTY; otherwise it emits a
  JSON `pending_consent`/`pending_privilege` result and exits without mutation.
- `cleanup` follows the existing destructive confirmation convention and still preserves
  drifted/ambiguous state.
- `setup/up/down/teardown/list/repair-ca` remain accepted during compatibility staging.
- `--json` emits exactly one final JSON object; diagnostics go to stderr and are bounded.

## MCP

An import-safe `domains` tool group is registered through the MCP manifest:

- `domain_status(project_dir, label="default")`
- `domain_plan(project_dir, label="default")`
- `domain_apply(project_dir, label="default")`
- `domain_cleanup(project_dir, label="default")`
- `domain_support()`

`domain_apply` and `domain_cleanup` never elicit terminal input or privilege. They may
apply only when the machine already has valid consent/privilege/credentials; otherwise
they return a pending result. The legacy `setup_domains` tool delegates to `domain_apply`
and retains its public name during the compatibility window.

All tools resolve project ownership through the shared application dependency and return
the `DomainResult` envelope from [domain-service.md](domain-service.md); they do not parse
CLI prose or read state JSON directly.

