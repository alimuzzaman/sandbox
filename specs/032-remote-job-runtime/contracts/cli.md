# Contract: CLI

## Target and policy options

Commands that resolve a runtime target share these options:

```text
[--local | --remote NAME]
[--workspace LABEL]
[--timeout SECONDS | --execution-profile NAME]
[--output PROFILE]
[--request-id ID]
```

`--local` and `--remote` are mutually exclusive. Explicit options override project
configuration. When no explicit target exists, `runtime.default` is used. A remote is
never inferred merely because one remote is registered. Every result reports resolved
target/workspace and each deadline's source.

## Generic execution

```text
sb exec [target/policy options] [--parallel-safe] [--detach|--wait]
        [--artifact PATH ...] -- <argv...>

sb test [target/policy options] [--mode unit|integration|e2e|declared]
        [--parallel-safe] [--detach|--wait] [--artifact PATH ...]
        [-- <argv...>]
```

- `exec` always requires explicit non-empty argv after `--`.
- `test` accepts explicit argv, or a project-declared named test plan/mode. It never
  discovers and executes package scripts automatically.
- Remote execution deploys before job submission.
- Default mode returns the accepted job then follows with the resolved output profile
  when attached to a terminal. `--detach` returns immediately; `--wait` waits for final
  status using resumable output pages.
- Missing explicit timeout is allowed only when a named profile/workflow fallback
  resolves one; CLI prints a reminder to stderr.

Examples:

```bash
./sb exec --remote scaleway-sandbox --workspace node-unit \
  --timeout 1800 --output smart -- npm test

./sb test --remote scaleway-sandbox --workspace php-unit \
  --execution-profile unit --output errors -- ./vendor/bin/phpunit

./sb test --local --timeout 900 --quiet -- npm run test:unit
```

## Job control

```text
sb job status JOB_ID [--remote NAME] [--json]
sb job list [--remote NAME] [--workspace LABEL] [--status STATE] [--limit N] [--json]
sb job output JOB_ID [--remote NAME] [--stream combined|stdout|stderr]
              [--cursor CURSOR|--offset N|--tail N|--lines N|--since TIME]
              [--output PROFILE] [--max-bytes N] [--encoding utf8|base64] [--json]
sb job follow JOB_ID [--remote NAME] [--output PROFILE]
              [--cursor CURSOR] [--poll-seconds N] [--no-status]
sb job metrics JOB_ID [--remote NAME] [--since TIME] [--json]
sb job artifacts JOB_ID [--remote NAME] [--json]
sb job artifact get JOB_ID ARTIFACT_ID [--remote NAME] --output-file PATH
sb job cancel JOB_ID [--remote NAME] --reason TEXT [--force] [--wait SECONDS]
sb job retry JOB_ID [--remote NAME] [--request-id ID] [--workspace-policy reuse|reset|new]
sb job cleanup JOB_ID [--remote NAME] [--logs] [--artifacts] [--workspace] --yes
```

`status`, `output`, `metrics`, and artifact list are observational. Successful
`job status --json` always includes `ok:true`; human status rendering is unchanged.
`cancel`, retry, cleanup, reset, and destroy are explicit mutations. Full output is
obtained through `job output --output full`; quiet execution never removes retained
output. Artifact offset must be non-negative and each requested page must be 1..1 MiB;
invalid bounds fail before local or remote reads. `job artifact get --output-file`
downloads all bounded chunks to a temporary file and publishes only after total-size and
SHA-256 validation.

Human follow rendering writes stdout events to local stdout and stderr events to local
stderr when `--stream` is not combined. JSON/NDJSON mode emits structured events only.
Transport reconnect resumes from the last acknowledged cursor.

## Workspace lifecycle

```text
sb workspace create --remote NAME --workspace LABEL [--ensure]
sb workspace list --remote NAME [--project-dir DIR] [--json]
sb workspace status --remote NAME --workspace LABEL [--json]
sb workspace reset --remote NAME --workspace LABEL --yes
sb workspace destroy --remote NAME --workspace LABEL --yes
```

- Create is idempotent for the same project/remote/label.
- Reset/destroy fail with `workspace_busy` while leases/jobs are active.
- Failed persistent workspaces are retained by default.
- Generated isolated labels are displayed before any create/apply operation.

## Matrix and declared plans

```text
sb test matrix --remote NAME --plan PLAN_NAME --timeout SECONDS
               [--max-parallel N] [--cleanup retain|always|on-success]

sb exec plan --remote NAME --plan PLAN_NAME --timeout SECONDS
              [--output PROFILE]
```

Every child has its own job ID. Matrix cells always use isolated workspace labels.
Declared multi-step plans may run independent steps in parallel only when dependencies
and parallel safety are explicit in project configuration.

## Remote CI

```text
sb ci preflight --remote NAME --workflow PATH [--event EVENT]
                [--accept-difference ID ...] [--json]

sb ci run --remote NAME --workflow PATH --timeout SECONDS
          [--job NAME ...] [--event EVENT] [--input KEY=VALUE ...]
          [--accept-difference ID ...] [--safe|--unsafe-authorized]
          [--output PROFILE] [--detach|--wait]
```

Safe mode is default. `preflight` performs no workflow side effect. `run` refuses any
blocking difference not named by `--accept-difference`. Production/release execution
requires separate explicit authority and is not implied by `--unsafe-authorized` alone;
the flag only permits configured non-production behavior.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | requested operation succeeded; waited job succeeded |
| 1 | job/test/workflow completed unsuccessfully |
| 2 | invalid usage/config/target/preflight |
| 3 | job cancelled |
| 4 | job timed out |
| 5 | transport unreachable or observation incomplete |
| 6 | storage/output/artifact integrity failure |

Detached submission exits 0 when acceptance succeeds, regardless of later job outcome.
All JSON responses include `ok`, stable error `code`, and safe `message` when applicable.

## Compatibility

- Existing `sb async-job`, `sb job` WordPress forms, `sb e2e`, and `sb ci` syntax remain
  accepted during migration.
- Existing incremental fields (`stdout`, `bytes_read`, `truncated`) remain present in
  compatibility output responses.
- Existing `sb exec -- <argv>` remains local unless project runtime configuration
  explicitly selects remote; `--local` always forces local.

