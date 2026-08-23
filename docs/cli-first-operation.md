# CLI-first Sandbox operation

Sandbox can be used entirely through `sb`; an MCP server is optional client
integration, not a requirement for local development or remote deployment.

## Durable remote-first execution

Projects can opt into a configured remote default:

```json
{"runtime":{"default":"remote","remote":"scaleway-sandbox","workspace":"default"}}
```

Instance lifecycle is exempt from that inference: `sb ensure`, `sb status`, and
`sb logs` with no selector always act on the LOCAL instance. They go remote only
for an explicit `--remote NAME` or a project whose `runtime.default` is
`remote`. Registering a single remote therefore never moves a plain dev boot
onto a VPS. Durable job execution keeps inferring the one configured remote.

Use `--local` as an explicit override. Remote execution deploys the exact local
working tree before acceptance, then the remote supervisor drains process pipes
to durable local files. CLI/MCP callers read bounded retained output by cursor;
they do not hold test pipes open across SSH.

Once a remote job is running on its selected VPS, Sandbox invokes its nested
project commands with `--local`. In that context, `--local` means the selected
VPS's co-located runtime, not the developer workstation; it prevents a
remote-first project policy from recursively submitting another remote job.
The internal `--in-instance` mode then runs the explicit command directly in
the declared Compose service, preserving the project's pinned container image.

```sh
./sb exec --remote scaleway-sandbox --workspace node-unit --timeout 3600 --detach \
  --request-id node-unit-tests-1 -- npm test
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
# direct SSH metrics: total/used/available RAM plus usage percentage
./sb remote service diagnostics scaleway-sandbox --ssh --json
# opt-in bounded process/app view plus optional non-sudo Docker stats
./sb remote service diagnostics scaleway-sandbox --ssh --processes --json
```

The process view uses only PID, PPID, CPU percentage, memory percentage, RSS,
and `comm`; it never reads command lines, arguments, environments, working
directories, Docker inspect/top, or sudo. Names and row counts are bounded and
unsafe/path-like names are redacted. CPU is the lifetime average reported by
`ps`, not an instantaneous sample. RSS may double-count shared memory, the
snapshot can drift immediately, and Docker rows overlap host processes and are
therefore never added to process totals. Grouping by `comm` is heuristic and
group CPU can exceed 100% on multicore hosts. Docker memory parsing supports only B,
KiB, MiB, GiB, and TiB. `--processes` is valid only with SSH diagnostics.

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
For durable submissions, pass a stable `--request-id` so an uncertain caller
can replay the same request without creating a duplicate. A request ID cannot
turn a direct local or internal `--in-instance` invocation into a durable job;
those calls fail with `--request-id requires durable execution; add --detach or
select --local/--remote`.

## WordPress

```bash
./sb init
./sb ensure
./sb status
./sb wp --timeout 60 -- plugin list
./sb test
./sb deploy --remote <name> --ensure --expose
```

WordPress-only commands remain capability-gated and are not valid for generic
Compose projects.

Synchronous `sb wp` waits up to 60 seconds by default. Pass an integer from 1
through 3600 with `--timeout` before the `--` delimiter to change that bound:
`./sb wp --timeout 120 -- plugin list`. The Compose client wait is a caller
bound only; it does not guarantee that the container process terminated. A
timeout therefore reports completion as unknown—inspect state before retrying,
or use `--async` for long work. Sandbox never retries a timed-out command
automatically, and synchronous WP stdout remains raw rather than wrapped in
JSON.

`./sb ensure --json` redacts every credential-shaped field, so `login_url`
arrives as `?sandbox_autologin=[REDACTED]`. A local test harness that needs a
password-free admin session passes `--reveal-login`: it restores `login_url`
alone and leaves the other credentials redacted. A local record must prove its
host is loopback-bound; a remote ensure record is revealed on the flag alone,
and the flag is forwarded to the VPS so its own redaction does not strip the
token first. A remote staged from a runtime that predates the flag ensures
without it and reports that — restage with `./sb remote provision <name>`.
Treat a revealed URL from a publicly exposed instance as an admin credential:
write it to a gitignored descriptor, never to a log or a commit.

When `login_url` contains a `sandbox_autologin` query parameter, the JSON also
includes the derived boolean `login_url_redacted`. It is `true` whenever the
placeholder remains, the URL is unusable or non-loopback for a local reveal,
the input was already redacted, or the reveal fails. It is `false` only after a
successful explicit `--reveal-login`; callers must not use a producer-supplied
status field as proof that a token was revealed. Remote-list reachability is a
separate read-only SSH probe: it runs exactly one non-multiplexed `ssh ... true`
with a ten-second bound and never falls back to the stateful SSH transport.

## MCP

Run `./sb mcp --project-dir .` only when an MCP-capable client needs live tool
calls. Its catalog remains scoped to the detected runtime.
