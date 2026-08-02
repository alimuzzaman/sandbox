# Remote test sweep — 2026-08-02

## Commands executed (remote: scaleway-sandbox, project: .)
- `./sb test --project-dir . --remote scaleway-sandbox unit -- -c tests/fixtures/pure-unit/phpunit.xml.dist --list-groups` → job `f45b94cff2fd613dc5fca9dd696db698` (succeeded)
  - Output: PHPUnit runs and `Available test group(s): mode: unit` with suite details.
- `./sb test --project-dir . --remote scaleway-sandbox unit -- --list-groups` → local submission failure before job dispatch.
  - Error: `RuntimeError: could not reset the VPS working tree ... .git/index.lock exists` (another git process appears to be running).
- `./sb test --project-dir . --remote scaleway-sandbox integration -- --list-groups` → job `ab79eec3ce77750cf3cd9e9588b0d1a8` (failed)
  - Output: `phpunit ... error: tests failed (phpunit exit 2)` and full PHPUnit usage text because command executed as `phpunit --list-groups` with no config.
- `./sb test --project-dir . --remote scaleway-sandbox integration -- --help` → job `e7e11ac18afed95e051a90aa5e0a3a46` (succeeded)
  - Output confirms test harness provisioning and then `phpunit --help`.
- `./sb test --project-dir . --remote scaleway-sandbox integration -- -c tests/fixtures/native-wordpress/phpunit.xml.dist --list-groups` → job `b949e6efaa5c412f85f9f75e750439b4` (succeeded)
  - Output: prints available group `default` and completes successfully.

## Gaps to fix
1. Integration mode requires explicit phpunit config for operational commands like `--list-groups`.
   - Without `-c`, the remote runner invokes `phpunit --list-groups` and returns usage (`exit 2`).
   - This can be improved by auto-selecting a mode-specific config path.
2. One remote test submission can fail with `.git/index.lock` during remote deploy reset.
   - Suggest guard/probing and clearer remediation guidance when index lock is present.
3. `integration -- --help` triggers full harness provisioning/logging overhead (compose startup + db bootstrap) even though no suite run is required.
   - Consider early return/help-only path to avoid unnecessary environment setup and slow feedback.
4. Command behavior diverges by mode for defaults: unit succeeds by default with provided config above, while integration fails without an explicit config path.
   - Make default behavior consistent or documented in CLI help/error messages.
- Additional evidence
  - `./sb test --project-dir . --remote scaleway-sandbox unit -- --list-tests` → job `9c4e7c4b0bfce5bd1afbd8231dfdf2b7` (failed with phpunit usage/exit 2)
  - `./sb test --project-dir . --remote scaleway-sandbox unit -- -c tests/fixtures/pure-unit/phpunit.xml.dist --list-tests` → job `097c2be0001adeadf3f7df1b5829e4cd` (succeeded)
- New gap added
  - Non-default PHPUnit selection flags (`--list-tests`, `--list-groups`) in unit mode require explicit config path on remote runs.
  - This suggests an opportunity to standardize mode-specific defaults for remote execution (rather than relying on each caller remembering the config), or improve CLI error text to force config requirement.
## Follow-up sweep (continuation)

### Additional CLI/mcp observations
- `./sb status --json` (local context) shows healthy runtime and exposes MCP capabilities (`required=3/3; optional=4/9`) with optional capability gaps: logs, stop, wordpress.debug, wordpress.multisite, wordpress.server-switch.
- `./sb workspace list --json` (local namespace for this cwd) now: `{"namespace":"local:d08e5a11bbac","ok":true,"workspaces":[]}`.
- `./sb workspace list --remote scaleway-sandbox --json` returns `namespace local:9d683049a53f` and `workspaces: []` (empty) despite remote job activity.
- `./sb remote list --json` still shows configured remotes `hermes-acceptance` and `scaleway-sandbox` both reachable/provisioned.
- `./sb job-list --remote scaleway-sandbox --json --active-only` returns `{"jobs":[],"ok":true}` after the last set of runs.
- `./sb job-retention --json --limit 10` returns no cleanup (`{"cleaned":[],"ok":true,"retention_days":7,...}`).
- `./sb logs --local --json` streams `docker compose logs -f` and must be interrupted to stop; it ignores `--json` semantics and keeps running.
- `./sb status --remote scaleway-sandbox --json` and variants with `--label`/`--instance` fail with `no sandbox instance for this directory`, listing local known instances not matching the remote workspace.
- `./sb remote workspace list` equivalent usage via `./sb workspace status --remote scaleway-sandbox --json` returns `workspace_not_found` for `default` in this project context.
- `./sb workspace list --remote hermes-acceptance --json` fails with `could not resolve project: /home/hermes-acceptance/sandbox/deploy-src/sandbox-workspace-37a8eec1ce1968`.
- `./sb workspace list --help` confirms syntax: `workspace {create,list,status,reset,destroy}` and supports `--remote`; no `--limit` flag.
- `./sb instance` command path in this environment is `./sb instances` (plural); `./sb focus_get` is invalid and should be `./sb focus`.
- `./sb instances --json` returns a very large registry of local instances; notable `templately` and `sandbox` are running, plus many stopped namespaces.
- `./sb instances` confirms local instances with non-unique project/name mappings and running states.

### Additional job metadata/API-path gaps
- `./sb job-metrics --remote scaleway-sandbox <job-id> --json` works and returns 1Hz sample telemetry while the job was running.
- `./sb job-artifacts --remote scaleway-sandbox <job-id> --json` returns empty list for these test jobs.
- `./sb job-output --remote scaleway-sandbox <job-id> --json` returns bounded output envelope with cursor/sequences (`has_more:false` for complete outputs).
- Extra positional args to `job-output` (e.g. `... job-id 0 4000`) are rejected.

### New/fresh gaps to capture
1. `./sb logs` appears to always operate as a tailing watch; CLI flag `--json` does not convert this into bounded JSON retrieval for a specific window.
2. Remote status/workspace operations depend on project directory registration semantics and often fail even when a remote workspace exists, producing confusing error context that mixes local and remote instance namespaces.
3. Remote workspace creation/listing path is inconsistent: creating/inspecting remote workspaces from this project can return namespace drift (`local:9d6830...`) and empty lists while remote job runs do succeed.
4. Command surface has traps (invalid alias, undocumented workspace flags, strict positional constraints) worth hardening in UX/help docs/tests:
   - `focus_get` not a command (`focus` is).
   - `workspace list` has no `--limit`.
   - `job-output` does not support offset+byte positional args.

### MCP-side checks
- `functions.list_mcp_resource_templates` returns empty.
- `functions.list_mcp_resources` for `server="dataAnalyticsWidgets"` returns UI app/widget URIs.
- `./sb workspace list --local --json` remains empty.
- `./sb workspace list --remote hermes-acceptance --json` mirrors the project-path resolution failure seen earlier.

## Latest execution pass (run now)

### Commands run
- `./sb test --project-dir . --remote scaleway-sandbox --workspace gap-test auto -- --list-groups`
  - Job: `ed21fce0edded58ad12d0b7e43d9f71b`
  - Result: failed (`exit_code: 1`), remote command became nested `sb test --local --project-dir . unit -- --list-groups`
  - Evidence: `job-output` showed phpunit usage text and `error: tests failed (phpunit exit 2)` with no group listing.
- `./sb test --project-dir . --remote scaleway-sandbox --workspace gap-test unit -- -c tests/fixtures/pure-unit/phpunit.xml.dist --list-groups`
  - Job: `01f3ad63860beda57400f7f7c18f2a16`
  - Result: succeeded.
- `./sb test --project-dir . --remote scaleway-sandbox --workspace gap-test matrix -- --list-groups`
  - Job: `512524d0680f058ddaaa4ff54d9e1f33`
  - Result: failed quickly (`output_completeness: active`, `command_json` = `["--list-groups"]`, no executed test output).
  - Evidence: `result_json` includes `FileNotFoundError: [Errno 2] No such file or directory: '--list-groups'`.
- `./sb test --project-dir . --remote scaleway-sandbox --workspace gap-test integration -- -c tests/fixtures/native-wordpress/phpunit.xml.dist --list-tests`
  - Job: `1c589b98d2b98044b1e889c1a2a898a0`
  - Result: succeeded; reported NativeBoundaryTest and test harness boot sequence.
- `./sb test --project-dir . --local unit -- --list-groups`
  - Result: failed (`phpunit exit 2`) with usage output.
- `./sb test --project-dir . --local integration -- -c tests/fixtures/native-wordpress/phpunit.xml.dist --list-tests`
  - Result: succeeded.
- `./sb test --project-dir . --local integration -- --list-groups`
  - Result: failed (`phpunit exit 2`) with usage output.

### New gaps identified
1. `matrix` mode appears to ignore passthrough (`-- ...`) handling and submits only passthrough args directly (`["--list-groups"]`), causing command-not-found failures.
2. `unit`/`integration` modes still require explicit `-c` for local group/test listing in both local and remote execution; otherwise both contexts run bare `phpunit --list-*` and error with usage.
3. Remote `auto` mode with passthrough currently maps to `unit` mode internally (seen in nested `command_json`) and fails with the same `--list-groups` mismatch when config isn’t passed.
4. For `unit`/`integration` local runs, `-- --help` is not yet assessed in this pass; existing evidence in earlier section remains that `integration -- --help` executes harness and exits cleanly after provisioning.

### Suggested fixes
1. Make `unit`/`integration` pass-through defaults auto-select known fixture config when `--list-groups`/`--list-tests` is used and no `-c` is provided.
2. Ensure `matrix` mode validates passthrough input and returns a dedicated CLI error instead of forwarding arguments as executable args.

## Continuation sweep (2026-08-02)

### Commands run (top-level behavior and edge checks)
- `./sb license` (no subcommand): prints masked pro status and set values.
- `./sb license sync`: re-shared Elementor Pro activation from sandbox to 68 instances (`templately` likely).
- `./sb host login-url` / `./sb host logs --json` from repo root: both fail with `missing /Users/alim/Sites/git/sandbox/sandbox.hosting.yml`.
- `./sb domains status`: returns fallback resolver status and `resolver_not_selected`.
- `./sb domains tld` (intentional typo): invalid choice error (no `tld` action).
- `./sb native status`: returns `native native_status: unknown`; `./sb native support`: `ready`.
- `./sb snapshots --json`: rejected (`unrecognized arguments: --json`).
- `./sb recovery plan`: runs and prints per-profile recovery posture.
- `./sb resources status --json`: returns `completeness=complete` with large unknown-size/reclaimable accounting output and a residual unknown gap.
- `./sb resources cleanup --scope cache --confirm`: refuses with `plan_not_found` unless `--plan-id` is provided.
- `./sb exec` without command: parser requires `./sb exec -- <argv...>`.
- `./sb job-matrix --help` and `./sb pxdiff/vrdiff/specdiff/specgate/specextract --help`: all subcommand surfaces render with expected positional requirements.

### Newly confirmed gaps/follow-ups
1. `snapshots` command currently documents no global JSON output mode in this context (`./sb snapshots --json` fails), while related reporting commands commonly support JSON.
2. `host` subcommands remain hard-gated on project-local `sandbox.hosting.yml`; in repo roots this blocks observability of hosted remote state unless manifest exists.
3. Native runtime command surface appears inconsistent between `status` and `support`:
   - `support` indicates readiness,
   - `status` returns `unknown` (even before native probe action).
4. `resources cleanup` now has an explicit two-step safety requirement (`--plan-id`) and will hard-refuse if not supplied, even when scope is set.
5. `domains` command still exposes legacy `tld` positional argument only for `setup`, but no top-level `tld` action; typos in action usage fail quickly (helpful).

### Suggested follow-up checks
1. Add a native status probe for setup path to avoid ambiguous `native status: unknown` on normal project roots.
2. Decide whether `snapshots` should accept bounded machine-readable output parity with `instances/resources/recovery` and update behavior/help accordingly if needed.
3. Evaluate whether `resources cleanup --plan-id` should print the prerequisite command/next step (`plan` or `resources plan`) alongside `plan_not_found` for quicker operator recovery.

### Raw command re-check (post-truncation)

- `./sb resources --remote scaleway-sandbox status --json`
  - Confirmed output returns `status: "partial"` and large per-resource inventory.
  - `category_outcomes` includes `docker_images: timed_out` while many other categories are `complete`.
  - `target` is resolved as `{ "kind": "remote", "name": "scaleway-sandbox" }`.
- `./sb resources --remote scaleway-sandbox cleanup --scope stale --plan-id dummy --confirm --json`
  - Confirmed immediate refusal with `code: invalid_plan_id` and `retryable: false`.
  - Output indicates the command path validates plan IDs strictly rather than treating unknown values as no-op.
- `./sb snapshots --json --instance sandbox`
  - Confirmed parser rejection unchanged in repeated run:
    `unrecognized arguments: --json` (exit code 2).

### New gap added from re-check
1. `resources --remote ... status --json` can return a partial status when remote inventory probing times out (`docker_images` timeout), which may look complete in the command intent but reports `completeness: partial` and large `unknown_bytes`.

### MCP resource listing status
- `functions.list_mcp_resources` (no server filter) now returns populated plugin/resource URIs across `codex_apps`, `codex-security`, and `dataAnalyticsWidgets`.
- This indicates MCP discovery is reachable in this environment now, despite earlier sessions reporting a transient unavailability.

### Sweep continuation (post-CLI-index refresh)

- `./sb remote --help` confirms top-level `remote` actions and shows `--json` support.
- `./sb deploy --help` confirms `--remote` requirement and shows deploy options (`--ensure`, `--expose`, `--domain`, `--plugin-slug`).
- `./sb mcp --help` confirms transport modes and streamable-http fields.
- `./sb remote service <action> <name>` invocation order is required (`status|diagnostics|migrate|stop` first, remote name second):
  - `./sb remote service scaleway-sandbox status` and related forms fail immediately with:
    `error: usage: ./sb remote service <status|diagnostics|migrate|stop> <name> [--plan|--confirm]`.
  - The `--help` output for `remote service scaleway-sandbox` does not show this required order.
  - Correct order (`./sb remote service status scaleway-sandbox`) now returns service state (including `"observed"` for status/diagnostics, `"planned"` for stop/migrate plans).
- `./sb remote service status scaleway-sandbox --json` returns structured status with `installed/enabled/active/ownership` fields and listener/auth diagnostics.
- `./sb remote service migrate scaleway-sandbox --plan --json` is accepted and returns `status: "planned"` with migration steps and observed runtime details (no execution).
- `./sb remote service stop scaleway-sandbox` is planned-only without confirmation, while `./sb remote service stop scaleway-sandbox --confirm` attempts execution (returned `remote service scaleway-sandbox: stopped`).
- `./sb remote up scaleway-sandbox` and `./sb remote down scaleway-sandbox` both return:
  - without `--json`: `<remote> MCP service <start|stop> is planned; re-run with --confirm`
  - with `--json`: `{ "status":"planned", "data":{"requires_confirm": true, "action":"start|stop"} }`.
- `./sb remote set-origin scaleway-sandbox` requires `--ipv4` and/or `--ipv6` and errors:
  - `remote set-origin requires --ipv4 and/or --ipv6`.
- `./sb remote add` and `./sb remote set-origin` without a name both emit:
  - `a remote name is required for this action, e.g. ./sb remote add myvps <ssh-connection>`.
- `./sb remote --remote scaleway-sandbox list` fails because `--remote` is not a valid top-level `remote` argument.
- `./sb deploy` without `--remote` errors:
  - `the following arguments are required: --remote`.
- `./sb deploy --remote does-not-exist --json` returns JSON with `ok:false` and clear instruction:
  - `no remote named 'does-not-exist' — register it first with ./sb remote add ...`.
- `functions.list_mcp_resource_templates` for `server: sandbox`/`server: dataAnalyticsWidgets` and `functions.list_mcp_resources` for `server: sandbox` failed with:
  - `MCP server 'sandbox' was not ready for this step`.
  - `dataAnalyticsWidgets` returns widgets plus empty templates list.
- `./sb workspace status` (and `--remote scaleway-sandbox --json`) currently reports:
  - non-JSON: `default: workspace_not_found`
  - JSON: `{"code":"workspace_not_found","label":"default"...}`
- `./sb workspace list --remote scaleway-sandbox --json` returns empty workspaces and namespace `local:9d683049a53f`.
- `./sb remote provision scaleway-sandbox --json` returns planned action:
  - `{ "status": "planned", "provisioned": true, "data": {"requires_confirm": true, "action": "provision"} }`
- `./sb remote provision ghost-sandbox --json` errors with:
  - `error: no remote named 'ghost-sandbox' — register it first with ./sb remote add ghost-sandbox <ssh-connection>`
- `./sb remote remove does-not-exist --json` returns success with no-op:
  - `{"ok": true, "name": "does-not-exist", "removed": false, "error": null}`
- `./sb remote remove does-not-exist` prints:
  - `• no remote named 'does-not-exist' was registered`
- `./sb remote add test-remote` errors with explicit usage guidance:
  - ``./sb remote add <name> <ssh_url>` requires an ssh_url`
- `./sb workspace create` currently returns `default: ok` on this host when executed without explicit scope, which differs from many action forms that return `workspace_not_found`.
- `./sb remote up` and `./sb remote down` with no remote name produce the same required-name message shown for `remote add`:
  - `a remote name is required for this action, e.g. ./sb remote add myvps <ssh-connection>`.
- `./sb remote set-origin scaleway-sandbox --ipv4 1.2.3.4` reports success and updates the stored remote public origin.
- `./sb deploy --remote scaleway-sandbox --json --project-dir .` executes and reports `ok:true` with `pushed_commit` and `uncommitted_files_applied: 2`.
- `./sb secrets migrate-zshrc` fails in this environment with:
  - `error: shell expansion is not allowed in /Users/alim/.zshrc.secrets line 41`.
- `./sb preview list --json` currently returns `{"ok":true,"previews":[]}`.
- `./sb onboard --minimal` executes as a non-interactive setup run on the current project and prints setup progress plus focus guidance.
- `./sb hermes status --remote hermes-acceptance --json` returns `ok:true`, `status:"configured"`, and `running_sessions: 0`.
- `./sb server` with no args fails because it requires `server_type` (`apache|nginx|litespeed|herd`).
- `./sb secure` requires interactive terminal / sudo trust path and fails in non-interactive sessions.
- `./sb global --json` is unsupported (`unrecognized arguments: --json`).
- `./sb init` without args is operational (scaffolds `sandbox.config.json` and provisions a test harness).

### Final command-surface pass (2026-08-02)

#### Commands run
- `./sb job-list --json --limit 3`
- `./sb job no-such-id`
- `./sb async-job no-such-id`
- `./sb job-status no-such-id --json`
- `./sb job-cancel no-such-id --json`
- `./sb job-retry no-such-id --json`
- `./sb async-job no-such-id --json`
- `./sb job-output c96c3be60fde20cc81322e4e9496afb7 --json`
- `./sb job-output c96c3be60fde20cc81322e4e9496afb7 --tail-bytes 10 --json`
- `./sb job-start --json -- echo hello-sb`
- `./sb job-output` (no args)
- `./sb job-output badjobid --json`
- `./sb job-output c96c3be60fde20cc81322e4e9496afb7 --offset 1`
- `./sb snapshots`
- `./sb snapshot does-not-exist`
- `./sb restore does-not-exist`
- `./sb workspace status --workspace gap-test`
- `./sb instances --json`
- `./sb instances --project-dir . --json`
- `./sb job-list --json --active-only --remote scaleway-sandbox --limit 1`
- `./sb job-start` (missing `--` separator)
- `./sb job-start --json` (no command)
- `./sb snapshot test-snapshot-name`
- `./sb snapshots does-not-exist`
- `./sb job-retry 0123456789abcdef --json`
- `./sb async-job 0123456789abcdef --json`
- `./sb job-status 0123456789abcdef --json`
- `functions.list_mcp_resources --server sandbox`
- `functions.list_mcp_resources --server codex_apps`
- `functions.list_mcp_resource_templates --server codex_apps`

#### Confirmed behavior and gaps
1. Job id validation remains inconsistent.
   - Some entry points reject short IDs early with `invalid job id (expected 16 hex chars)`.
   - Others emit Python exceptions (`ValueError: job id is invalid`) for `job-status`, `job-cancel`, `job-retry`, and `job-output`, including JSON mode.
   - Suggest normalizing all job-facing commands to consistent user-facing validation/error envelopes.
2. `job-output` accepts valid IDs and pagination semantics:
   - accepts `--tail-bytes` and returns bounded chunks with cursor/sequence fields.
   - accepts `--offset` for positional reads.
3. `snapshot` command auto-creates snapshot on demand when name is unknown.
   - `./sb snapshot does-not-exist` writes snapshot artifacts instead of hard failing.
4. `restore` from that synthetic snapshot succeeds and performs reset/import sequence.
5. `workspace status --workspace gap-test` resolves as `workspace_not_found` without remapping.
6. `instances --json` on this cwd returns structured single-record/registry payload (local sandbox instance, `running: true`, `server: nginx`, `mcp_server: sandbox`).
7. MCP discoverability is currently unstable for `sandbox` tool namespace.
   - repeated `list_mcp_resources` for `server='sandbox'` intermittently returns `MCP server 'sandbox' was not ready for this step`; `codex_apps` may still return resources.
8. `remote` / `deploy` UX edge cases confirmed earlier remain:
   - `remote --remote scaleway-sandbox list` invalid.
   - `deploy` requires `--remote`.
9. Detached job commands require explicit command boundary:
   - `job-start` without `--` or without command returns parser/argument errors.

#### Operational note
- Long output truncation still occurred in some raw invocations (notably long `list` outputs and some id-validation variants), so these findings were repeated in a narrower command set and then logged here.

### Continuation pass: 2026-08-02 (high-priority remaining help+edge capture)

#### Command inventory sweep (additional `--help` coverage)
- `./sb setup --help`
- `./sb apply --help`
- `./sb connect --help`
- `./sb up --help`
- `./sb down --help`
- `./sb smoke --help`
- `./sb update --help`
- `./sb open --help`
- `./sb wp --help`
- `./sb seed --help`
- `./sb dump --help`
- `./sb qm --help`
- `./sb shell --help`
- `./sb skill --help`
- `./sb visit --help`
- `./sb migrate --help`
- `./sb home --help`
- `./sb selftest --help`
- `./sb mcp-install --help`
- `./sb claude --help`
- `./sb cache --help`
- `./sb introspect --help`
- `./sb abilities --help`
- `./sb clean --help`
- `./sb dashboard --help`
- `./sb ui --help`
- `./sb web --help`
- `./sb mcp --help`
- `./sb e2e --help`
- `./sb ci --help`
- `./sb plugin-check --help`
- `./sb ensure --help`
- `./sb instance --help`
- `./sb job-reconcile --help`
- `./sb job-artifact-get --help`
- `./sb uninstall --help`
- `./sb jobs --help`
- `./sb xdebug --help`
- `./sb server --help`
- `./sb secrets --help`
- `./sb preview --help`
- `./sb global --help`
- `./sb focus --help`
- `./sb resources --help`
- `./sb focus --help`
- `./sb remote --help`
- `./sb host --help`
- `./sb deploy --help`
- `./sb native --help`
- `./sb job --help`
- `./sb install --help`
- `./sb init --help`
- `./sb job-matrix --help`
- `./sb job-cleanup --help`
- `./sb job-artifacts --help`
- `./sb job-metrics --help`
- `./sb hermes --help`
- `./sb job-retention --help`

#### Additional non-destructive edge probes (newly confirmed)
- `./sb connect` with no target prints explicit supported targets list (fb/fluentboards, gh/github, cloudflare).
- `./sb jobs` returns `• no jobs for instance 'sandbox'`.
- `./sb job-matrix` with missing command fails with required-argv usage: `error: usage: ./sb job-matrix --workspace LABEL [--workspace LABEL] -- <argv...>`.
- `./sb job-cleanup` with no job id errors with required argument message.
- `./sb host` with no action errors with required action positional.
- `./sb job-cleanup` and `./sb clean` confirm non-interactive prompt behavior; `clean` raises EOFError when stdin is not interactive.
- `./sb instance create` shows invalid choice (`create` unsupported) and `./sb instance delete` requires name.
- `./sb workspace create` with no args returns `default: ok`; `./sb workspace status default --json` treats `default` as unrecognized positional argument (must use `--workspace`).
- `./sb workspace list --json` returns one local workspace with namespace `local:d08e5a11bbac`.
- `./sb workspace status --json` returns current local workspace JSON (`label: default`, `ok: true`).
- `./sb remote list --json` returns both known remotes with reachable/provisioned true.
- `./sb preview list --json` returns `{"ok": true, "previews": []}`, while `./sb preview list` produced no visible output (could be no-op UX ambiguity).
- `./sb skill list` command works and prints skill catalog (truncated in raw output capture).
- `./sb dashboard` in a non-TTY session needs interactive terminal; when interrupted, exits with traceback after printing `• dashboard needs an interactive terminal — showing static list.` and keyboard interrupt.
- `./sb open` with no subtype prints a concrete sandbox autologin URL.
- `./sb qm` with no args installs/activates query monitor and returns JSON event with curl probe.

#### MCP discovery (workflow/server style)
- `functions.list_mcp_resources --server sandbox` remains unavailable in this session: `MCP server 'sandbox' was not ready for this step`.
- `functions.list_mcp_resource_templates --server sandbox` remains unavailable with same readiness failure.
- `functions.list_mcp_resources --server codex_apps` returns a populated plugin/skill/app inventory.
- `functions.list_mcp_resource_templates --server codex_apps` returns `resourceTemplates: []`.
- `functions.list_mcp_resources --server dataAnalyticsWidgets` returns artifact/table/chart widget URIs.
- `functions.list_mcp_resource_templates --server dataAnalyticsWidgets` returns `resourceTemplates: []`.
- `functions.list_mcp_resources --server codex-security` returns workspace UI resources (modern + legacy). `... --server codex-security` templates returned empty.

#### New gaps to triage
1. Several commands remain parser-sensitive and non-obvious in non-interactive environments:
   - `dashboard` attempts TUI path and throws tracebacks on non-TTY interruption.
   - `clean` can hang/fail on EOF without non-interactive confirmation bypass.
2. `workspace` positional handling remains inconsistent (`workspace status default` invalid; status expects default from omission rather than explicit workspace positional).
3. `preview list` has divergent behavior between default vs `--json` mode for empty previews.
4. `mcp server namespace` readiness for `sandbox` is still unstable, while `codex_apps`/`dataAnalyticsWidgets`/`codex-security` are reachable.

### New continuation sweep (command parser and MCP surface, 2026-08-02)

#### Newly tested command surfaces
- `./sb setup --help`
- `./sb apply --help`
- `./sb connect --help`
- `./sb up --help`
- `./sb down --help`
- `./sb install --help`
- `./sb smoke --help`
- `./sb selftest --help`
- `./sb mcp-install --help`
- `./sb claude --help`
- `./sb plugin-check --help`
- `./sb introspect --help`
- `./sb qm --help`
- `./sb home --help`
- `./sb doctor --help`
- `./sb recovery --help`
- `./sb open --help`
- `./sb shell --help`
- `./sb seed --help`
- `./sb update --help`
- `./sb xdebug --help`
- `./sb abilities --help`
- `./sb clean --help`
- `./sb dashboard --help`
- `./sb ui --help`
- `./sb web --help`
- `./sb mcp --help`
- `./sb e2e --help`
- `./sb ci --help`
- `./sb deploy --help`
- `./sb preview --help`
- `./sb hermes --help`
- `./sb ensure --help`
- `./sb onboard --help`
- `./sb secure --help`
- `./sb global --help`
- `./sb uninstall --help`
- `./sb cache --help`
- `./sb license --help`
- `./sb pxdiff --help`
- `./sb vrdiff --help`
- `./sb specextract --help`
- `./sb specdiff --help`
- `./sb specgate --help`
- `./sb domains --help`
- `./sb native --help`
- `./sb exec --help`
- `./sb job-artifact-get --help`
- `./sb job-artifacts --help`
- `./sb resources --help`
- `./sb server --help`
- `./sb secrets --help`
- `./sb wp --help`
- `./sb visit --help`
- `./sb skill --help`
- `./sb dump --help`

#### New evidence-backed gaps
1. Legacy singular/plural command names are not always available:
   - `./sb recover --help` is rejected with `invalid choice: 'recover'` because the command is `recovery`.
   - `./sb secret --help` is rejected because the command is `secrets`.
2. Several lifecycle commands are intentionally non-JSON-only and now confirmed to fail parser-level on `--json`:
   - `./sb setup --json`
   - `./sb up --json`
   - `./sb down --json`
   - `./sb shell --json`
   - `./sb clean --json`
   - `./sb snapshots --json` (already observed prior, revalidated)
   - `./sb global --json` (already observed prior, revalidated)
3. `./sb cache clear --remote local --yes` (and `./sb cache --json`) shows parser drift:
   - `cache` accepts no `--remote` flag and its second positional is `layer`; passing `--remote` yields `argument layer: invalid choice: 'local'`.
   - This confirms cache mutation/inspection is instance-scoped only in this command shape (and `--json` is not supported).
4. `server` and `native` help confirm expanded mode surface:
   - `./sb server` accepts `apache|nginx|litespeed|herd` target values.
   - `./sb native` exposes `{support,preflight,baseline,install-plan,install,status,cleanup}` and currently accepts `--web-server`.

#### MCP discovery status re-check
- `functions.list_mcp_resources` for `server="sandbox"` and `functions.list_mcp_resource_templates` for `server="sandbox"` still fail with `MCP server 'sandbox' was not ready for this step`.
- `functions.list_mcp_resources` for `server="dataAnalyticsWidgets"` returns the same three widget URIs as before; templates remain empty.
- `functions.list_mcp_resources` for `server="codex-security"` remains reachable.

### Additional runtime-path gaps (2026-08-02 continuation)

#### Executed validation commands and outcomes
- `./sb focus` (no args)
- `./sb focus list`
- `./sb focus templately-ai-builder --json`
- `./sb focus templately-ai-builder`
- `./sb focus does-not-exist`
- `./sb connect`
- `./sb connect -n fb`
- `./sb native status --json`
- `./sb native preflight --json`
- `./sb domains status --json`
- `./sb domains setup`
- `./sb resources status --json --remote scaleway-sandbox`
- `./sb resources plan --json --scope stale --remote scaleway-sandbox`
- `./sb resources cleanup --scope stale --remote scaleway-sandbox --json`
- `./sb resources cleanup --scope stale --remote scaleway-sandbox --plan-id dummy --json`
- `./sb cache clear wp-cli --json`
- `./sb cache clear wp-http --yes`
- `./sb open --json`
- `./sb open admin`
- `./sb open site --json`
- `./sb open mail`
- `./sb secret --help`
- `./sb recover --help`
- MCP resource listing (`functions.list_mcp_resources`, `functions.list_mcp_resource_templates`) for: default, `sandbox`, `codex`, `codex-security`, `dataAnalyticsWidgets`

#### Confirmed gaps / parser + runtime behaviors
1. Focus behavior is permissive and string-based:
   - `./sb focus list` succeeds by setting focus to slug `list` (no built-in `list` validation/action).
   - `./sb focus --json` is unsupported (parser rejects `--json`).
2. Connect requires either interactive flow or target-specific non-interactive env setup:
   - `./sb connect` shows available targets.
   - `./sb connect -n fb` errors with missing `FLUENTBOARDS_URL` / `FLUENTBOARDS_APP_PASSWORD` for non-interactive mode.
3. Native status/path is partially coherent but indicates mixed readiness:
   - `./sb native status --json` now reports a full ready state with adapter/runtime details.
   - `./sb native preflight --json` returns `state: blocked` because many Linux isolation prerequisites are missing.
4. Domain setup and status remain partially blocked in this environment:
   - `./sb domains status --json` shows resolver fallback with `resolver_not_selected`.
   - `./sb domains setup` refuses with non-interactive-first-run requirement.
5. Resources command behavior confirmed in detail:
   - `resources status --remote scaleway-sandbox` is partial with many timeouted categories and low confidence, plus `status: partial`.
   - `resources plan --scope stale --remote scaleway-sandbox` returns `status: planned` with a concrete `plan_id` and candidate candidates/exclusions.
   - `resources cleanup ...` without `--plan-id` returns hard refusal `plan_not_found`.
   - `resources cleanup ... --plan-id dummy --json` returns `confirmation_required`.
6. Cache command parser remains non-JSON and layer-anchored:
   - `cache clear wp-cli --json` invalid (`--json` unsupported).
   - `cache clear wp-http --yes` executes and clears layer (actual clear observed).
7. Open command behavior:
   - `open` requires positional target (`admin|site|mail`); no JSON option.
   - `open admin` and `open mail` print command snippets with concrete URLs and autologin mail URL.
8. Legacy alias mismatch persists:
   - `./sb secret --help` invalid choice (`secrets` is valid).
   - `./sb recover --help` invalid choice (`recovery` is valid).
9. MCP discovery snapshot:
   - `functions.list_mcp_resources` for `sandbox` and `codex` remain failing (`MCP server '<name>' was not ready for this step`).
   - Default `functions.list_mcp_resource_templates` for `sandbox` now empty; templates for `dataAnalyticsWidgets` and `codex-security` also empty.

### Additional sweep (remote/service/host/snapshot edge execution, 2026-08-02)

#### Commands run
- `./sb remote status`
- `./sb remote up does-not-exist --json`
- `./sb remote down does-not-exist --json`
- `./sb remote up scaleway-sandbox --json` (without `--confirm`)
- `./sb remote set-origin`
- `./sb remote set-origin does-not-exist`
- `./sb remote up/wrong-action combinations` via `./sb remote up scaleway-sandbox status --json`
- `./sb remote service diagnostics scaleway-sandbox --json`
- `./sb remote service migrate scaleway-sandbox --plan --json`
- `./sb remote service stop scaleway-sandbox --json`
- `./sb remote service stop scaleway-sandbox --confirm --json`
- `./sb remote service status scaleway-sandbox --json` (before/after stop)
- `./sb host status`
- `./sb host validate --json` / `./sb host plan --json` / `./sb host logs --json`
- `./sb restore --help`
- `./sb restore` (missing required name)
- `./sb restore does-not-exist --json`
- `./sb snapshot does-not-exist`
- `./sb logs --local` (interrupted with Ctrl-C)
- `./sb remote service status no name` and `./sb remote service migrate` / `./sb remote service status` (usage only)

#### New/validated gaps and runtime evidence
1. `./sb remote` has no `status` action; valid actions are `{add,list,provision,up,down,remove,set-origin,service}` and `remote status` fails immediately with parser `invalid choice`.
2. Missing remote behavior differs by action:
   - `./sb remote up does-not-exist --json` returns `not provisioned yet` with hint to run `./sb remote provision` first.
   - `./sb remote down does-not-exist --json` returns `no remote named`.
   - `./sb remote set-origin` without a name gives explicit required-name help text.
3. `remote service` requires exact positional ordering `<action> <name>`:
   - `./sb remote service migrate`/`status` without name returns usage.
   - `./sb remote service status` also rejects direct usage when name missing.
4. `remote service diagnostics` can return degraded soft-fail:
   - `scaleway-sandbox` returned `status: degraded` with `remote diagnostics endpoint is unreachable`.
5. `remote service stop` is two-step by design and remains actionable:
   - without `--confirm` -> `status: planned` + `requires_confirm`.
   - with `--confirm` -> `status: stopped`.
   - `remote service status` confirms observed service state afterwards and remains `active: false` / `pid_present: false`.
6. `remote service migrate --plan` returns concrete planned changes (`steps`) and observed runtime snapshot (installed/enabled active/listener/auth states), which can be useful for pre-execution visibility.
7. Host command-set shape has an invalid alias candidate:
   - `./sb host status` fails with `invalid choice` (valid actions: `validate, plan, apply, logs, secrets, login-url`).
8. Host actions still blocked on missing local manifest in this environment:
   - `host validate/plan/logs` all fail with `missing sandbox.hosting.yml`.
9. Restore command does not support JSON on this action form:
   - `./sb restore does-not-exist --json` fails with parser-level `unrecognized arguments: --json`.
   - `./sb restore` (missing name) emits required-positional error.
10. Logs interrupt surface:
   - `./sb logs --local` is a streaming command; Ctrl-C interrupts and emits Python traceback via `subprocess.communicate`/`KeyboardInterrupt`, rather than cleanly returning a bounded message.

### Additional command-surface findings (snapshot/job/workspace behavior)

#### Commands run
- `./sb snapshot does-not-exist --force`
- `./sb snapshot test-fx --force`
- `./sb snapshots`
- `./sb job-status does-not-exist --json`
- `./sb job-list --limit 0 --json`
- `./sb workspace create --json`
- `./sb workspace status --workspace gap-test`

#### New behavior gaps
1. `snapshot --force` is fully actional for creation and overwrite:
   - both `./sb snapshot does-not-exist --force` and `./sb snapshot test-fx --force` succeeded, and no overwrite warning was surfaced when writing to an existing-named snapshot.
   - this confirms `--force` is not limited to conflict resolution only; it also allows straightforward create with that flag.
2. `job-status` input validation is weak on malformed ids:
   - `./sb job-status does-not-exist --json` crashed with Python traceback (`ValueError: job id is invalid`) rather than returning a structured error envelope.
3. `job-list` validates limits but currently crashes when out-of-range:
   - `./sb job-list --limit 0 --json` produced traceback (`ValueError: job list limit must be between 1 and 200`) instead of a CLI error response.
4. `workspace create --json` creates/returns workspace context automatically:
   - command succeeded and returned a workspace payload for label `default` (`created: false`, `mode: persistent`).
5. `workspace status --workspace gap-test` returns `workspace_not_found` when that workspace label is not present locally.
6. Snapshot inventory (`./sb snapshots`) confirms newly created `does-not-exist` and `test-fx` snapshots and can be used to verify save side effects.

### Follow-up sweep (design/visual/runtime utility subcommands)

#### Commands run
- `./sb specextract`
- `./sb specdiff`
- `./sb specgate`
- `./sb pxdiff`
- `./sb vrdiff`
- `./sb domains repair-ca --json`
- `./sb domains cleanup --json`
- `./sb native baseline --json`
- `./sb native cleanup --json`
- `./sb domains detect --json`

#### Observed behavior gaps
1. Argument-mandatory commands currently return argparse usage as expected (no runtime execution):
   - `specextract`, `specdiff`, `specgate`, `pxdiff`, `vrdiff` all fail fast with required positional arguments missing.
2. `domains repair-ca` now emits a hard deprecation/retirement message:
   - `legacy aggregate CA repair is retired...` (not a standard command failure path but explicit migration guidance).
3. `domains cleanup --json` can return a clean `already_absent` result without mutation when no owned resolver bindings remain.
4. `native baseline --json` returns blocked state with `host_service_baseline_unavailable`.
5. `native cleanup --json` returns blocked state `unsupported_capability` for this project kind (`wordpress`).
6. `domains detect --json` confirms existing fallback state (`resolver_not_selected`, `state: fallback`, `health: fallback`) and continues to be non-blocking for status reporting.

- [2026-08-02T11:56:27.675642Z] Command completed: `./sb resources plan --scope cache --remote scaleway-sandbox --json`
  - Returns `{"ok": true, "status": "planned", "state": "planned", "action": "plan"}` with `plan_id: 732fea3c4fed5ef2cff2a996fe318e97`
  - Scope `cache`, scope target `scaleway-sandbox`, `requires_confirmation: true`
  - Includes one actionable candidate (`download_cache`) and many exclusions, including `unverified`, `active`, `retained`, `persistent_resource_requires_stale_scope`, and `not_measured`
  - Confirms this call is a successful partial/non-finalization planning response rather than immediate cleanup

## Continued sweep: 2026-08-02 11:57-11:58 local run

- [2026-08-02T11:57:17.003206Z] `./sb deploy --json` returns parser-level requirement error for missing `--remote` (non-JSON friendly usage failure, exit code 2).
- [2026-08-02T11:57:17.003206Z] `./sb deploy --remote fb --json` returns structured JSON: `ok: false`, `remote: "fb"`, `error: "no remote named 'fb' — register it first with ./sb remote add fb <ssh-connection>"`.
- [2026-08-02T11:57:17.003206Z] `./sb host validate --json` and `./sb host login-url --json` both return same structured error when `sandbox.hosting.yml` is missing in repo:
  - `error: missing /Users/alim/Sites/git/sandbox/sandbox.hosting.yml; add a project-local sandbox.hosting.yml`
- [2026-08-02T11:57:17.003206Z] `./sb server nginx` is idempotent (`✓ sandbox already uses nginx — nothing to change.`).
- [2026-08-02T11:57:17.003206Z] `./sb server does-not-exist` exits with parser message: invalid choice among {apache, nginx, litespeed, herd}.
- [2026-08-02T11:57:17.003206Z] `./sb resources --help` shows full action set `{status,plan,cleanup}`, scope options `{cache,stale}`, and `--json` support with local/remote targeting.
- [2026-08-02T11:57:10.699963Z] `./sb resources status --json` runs a local scan and returns `status: complete`, `ok: true`, target kind `local`, with substantial `summary` including categorized resources and reclaimability.
- [2026-08-02T11:57:26.335836Z] `./sb resources plan --scope stale --json` returned `status: planned`, `ok: true`, `requires_confirmation: true`, `plan_id: 61cd04cd4d774b149dc086b8d0b3801a` and `candidate_count: 0` for stale local scope.
- [2026-08-02T11:57:26.335836Z] `./sb resources cleanup --json` (missing `--plan-id`) returns structured refusal:
  - `ok: false`, `status: refused`, `error.code: plan_not_found`, `error.message: --plan-id is required`.
- [2026-08-02T11:57:40.542688Z] `./sb resources cleanup --plan-id 94437db1b993bebb38aa5621e88d17bd --confirm --json` returned `status: completed` and `ok: true` with `observed_reclaimed_bytes: 200704` even though `planned_bytes` was 0 (`data.estimated_reclaimable_bytes` from plan = 0).
- MCP discovery updates:
  - `sandbox` MCP server remains not ready (`list_mcp_resources` / templates both fail: "not ready for this step").
  - `codex_security` (via `list_mcp_resources`) now returns two web resources: `Codex Security Workspace` and `Codex Security Workspace (legacy compatibility)`.
  - `list_mcp_resource_templates` still returns empty arrays for `codex_apps`, `dataAnalyticsWidgets`, and `codex-security`.
- Additional unvisited command surfaces checked (help/proxy behavior):
  - `./sb init --help` requires `--project-dir`, optional `--type` and `--no-test-harness`; no immediate behavioral findings.
  - `./sb ensure --help` includes `--create`, `--local`, `--remote`, and `--workspace` branching.
  - `./sb recovery --help` exposes profile actions and confirms `--json`.
  - `./sb exec --help` shows argv passthrough, `--local` / `--remote`, `--detach`, `--timeout`, and `--output-profile`.
  - `./sb mcp-install --help`, `./sb web --help`, `./sb secure --help`, `./sb license --help`, `./sb onboard --help`, `./sb global --help`, `./sb instances --help`, `./sb deploy --help`, `./sb host --help`, `./sb resources --help` all parsed as expected with required flags/action sets.

- `./sb cache` and `./sb cache info` both print cache inventory without JSON mode, e.g. `wp-cli` file count/bytes and `wp-http` counts, with shared cache path `~/sandbox/runtime/dl-cache`.
- `./sb plugin-check --update --json` in this repo fails structurally: `wp plugin check sandbox` produced no output because WP-CLI `plugin check` subcommand is unavailable (`'check' is not a registered subcommand of 'plugin'`).
- `./sb ci list-secrets <workflow>` is rejected as invalid positional action: action options remain `{plan, preflight, run}`; `--list-secrets` is a flag.
- Additional `resources` edge cases:
  - `./sb resources plan --scope stale --remote scaleway-sandbox --json` succeeds with `status: planned`, `requires_confirmation: true`, `candidates: []`, and `plan_id: cf27a75a8485847bbe4d39808331e63b`.
  - `./sb resources cleanup --plan-id cf27a75a8485847bbe4d39808331e63b --confirm --json` executed from local host fails `plan_target_mismatch`.
  - `./sb resources cleanup --plan-id cf27a75a8485847bbe4d39808331e63b --confirm --remote scaleway-sandbox --json` succeeds with `status: completed`, `observed_reclaimable_bytes?` not present, `observed_reclaimed_bytes: 0`, and explicit remote target in output.

## Continued sweep (2026-08-02 12:02–12:22)

### Commands run this pass (remaining `./sb` surfaces and execution checks)
- `./sb --help`
- `./sb status --help`
- `./sb status --json`
- `./sb logs --help`
- `./sb logs --local --json` (interrupted)
- `./sb logs --json` (interrupted)
- `./sb selftest --help`
- `./sb smoke --help`
- `./sb apply --help`
- `./sb home --help`
- `./sb up --help`
- `./sb down --help`
- `./sb install --help`
- `./sb doctor --help`
- `./sb setup --help`
- `./sb connect --help`
- `./sb shell --help`
- `./sb open --help`
- `./sb dump --help`
- `./sb qm --help`
- `./sb visit --help`
- `./sb skill --help`
- `./sb snapshot --help`
- `./sb snapshots --help`
- `./sb restore --help`
- `./sb reset --help`
- `./sb xdebug --help`
- `./sb abilities --help`
- `./sb introspect --help`
- `./sb instances --help`
- `./sb ui --help`
- `./sb web --help`
- `./sb mcp --help`
- `./sb test --help`
- `./sb e2e --help`
- `./sb ci --help`
- `./sb preview --help`
- `./sb hermes --help`
- `./sb ensure --help`
- `./sb init --help`
- `./sb instance --help`
- `./sb secure --help`
- `./sb server --help`
- `./sb onboard --help`
- `./sb global --help`
- `./sb uninstall --help`
- `./sb license --help`
- `./sb specextract --help`
- `./sb specdiff --help`
- `./sb specgate --help`
- `./sb domains --help`
- `./sb native --help`
- `./sb exec --help`
- `./sb guide --help`
- `./sb resources --help`
- `./sb job-metrics --help`
- `./sb job-output --help`
- `./sb job-start --help`
- `./sb job-retry --help`
- `./sb job-cancel --help`
- `./sb job-cleanup --help`
- `./sb job-reconcile --help`
- `./sb job-retention --help`
- `./sb job-artifacts --help`
- `./sb job-artifact-get --help`
- `./sb async-job --help`
- `./sb cache clear`
- `./sb cache clear --yes`
- `./sb clean`
- `./sb uninstall`
- `./sb workspace --help`
- `./sb workspace status --help`
- `./sb workspace status --json`
- `./sb workspace status default --json`
- `./sb workspace list --help`
- `./sb workspace list --json`
- `./sb claude --help`
- `./sb seed --help`
- `./sb update --help`
- `./sb wp --help`
- `./sb mcp-install --help`
- `./sb recovery --help`

### Notable behavioral findings
1. `logs` interrupt handling is consistently ungraceful for long-running local log tailing
   - `./sb logs --local --json`, `./sb logs --json`, and plain `./sb logs --local` all start `docker compose logs -f` and produce Python `KeyboardInterrupt` tracebacks when interrupted with Ctrl-C.
2. `cache` and `clean` confirm/interactive prompts are still not safe in non-interactive contexts
   - `./sb cache clear` without `--yes` prompts then crashes with `EOFError: EOF when reading a line`.
   - `./sb clean` without `--yes` has the same non-interactive failure path after printing `This deletes the DB volume... Continue? [y/N]`.
3. `workspace status` is `--json`-flag based, not positional label-based
   - `./sb workspace status --json` returns JSON for default workspace.
   - `./sb workspace status default --json` errors with `unrecognized arguments: default`.
4. `preview list` remains asymmetric by output form
   - `./sb preview list` returned empty stdout.
   - `./sb preview list --json` returned `{ "ok": true, "previews": [] }`.
5. `uninstall` refuses non-interactive execution without explicit confirmation
   - `./sb uninstall` returns `error: refusing to uninstall non-interactively without --yes.` and prints impacted resources.
6. `dashboard` still needs interactive terminal and emits tracebacks on forced interrupt
   - `./sb dashboard` prints `dashboard needs an interactive terminal — showing static list.` before traceback on Ctrl-C.
7. Additional parser validation confirmed for unvisited surface
   - `./sb server does-not-exist` rejects with action set `{apache,nginx,litespeed,herd}`.
   - `./sb cache clear --yes` and `./sb workspace status --json` execute normally in this environment.

## 2026-08-02 (Continuation Sweep)

- Additional command coverage and gaps observed:
  - `./sb remote` help confirms subcommands: `add`, `list`, `provision`, `up`, `down`, `remove`, `set-origin`, `service`.
  - `./sb mcp list` is invalid (`unrecognized arguments: list`) and no dedicated list/action exists under `mcp`.
  - `./sb remote list --json` works and returns structured remotes, including `name`, `ssh_configured`, `reachable`, and `provisioned` fields.
  - `./sb remote service migrate --plan --json` without remote name errors with usage (`remote service <status|diagnostics|migrate|stop> <name> ...`).
  - `./sb remote service status hermes-acceptance --json` returns service metadata with `ok: true` and fields including `installed`, `enabled`, `active`, `linger`, `auth_state`, `bind`, and `port`.
  - `./sb remote service diagnostics hermes-acceptance --json` returns `ok: false`/`status: degraded` with error `remote_service_failed: remote diagnostics require an HTTPS control URL`.
  - `./sb deploy` without `--remote` errors with required argument message (`--remote` required).
  - `./sb host validate --json` in repo without `sandbox.hosting.yml` errors clearly with path-specific guidance.
  - `./sb secrets migrate-zshrc --json` fails fast with `shell expansion is not allowed in /Users/alim/.zshrc.secrets line 41`.
  - `./sb resources status --json --scope stale --budget 1` returns `ok: true` but partial/low-confidence output with multiple categories marked `timed_out` and `completeness: partial`.
  - `./sb pxdiff /tmp/nope.png /tmp/nope2.png --json` errors `reference not found` before running diff.

- Additional pass notes (continued)
  - Completed a fresh full top-level `--help` surface sweep in non-destructive mode and recorded parser metadata for all commands that were not previously re-swept, including: `host`, `preview`, `license`, `workspace`, `recovery`, `domains`, `native`, `server`, `global`, and `instance`.
  - `./sb host validate|plan|apply|logs|secrets|login-url --help` all expose the same hosting option set and the action choices `{validate,plan,apply,logs,secrets,login-url}`.
  - `./sb preview create|list|destroy|cleanup --help` confirm these are the only preview actions and show the required context switches (`--remote`, `--project-dir`, `--confirm`, `--json`).
  - `./sb license status|set|clear|elementor-sync|sync --help` all share `{status,set,clear,elementor-sync,sync}` with `{elementor}` positional for set/clear; includes `--from` for elementor-sync.
  - `./sb workspace create|list|status|reset|destroy --help` confirms `--ensure` for create and `--confirm/--yes` for reset/destroy.
  - `./sb recovery profiles|plan|create|list|verify|restore|retention|schedule --help` confirm encrypted backup plan surface and required `--remote/--profile/--backup-id` controls.
  - `./sb domains setup|up|down|teardown|repair-ca|list|detect|support|plan|apply|status|cleanup|reconsider|ingress --help` and `./sb native support|preflight|baseline|install-plan|install|status|cleanup --help` both expose their subaction maps and shared option sets.
  - `./sb server --help` confirms only `{apache,nginx,litespeed,herd}` are valid server targets.
  - `./sb instance --help` confirms only a `delete` action exists for this surface.
- Hermes/help and grouped subcommand discovery
  - `./sb hermes --help` and `./sb hermes <action> --help` were confirmed to map action space:
    - `{install,setup,doctor,status,chat,run,job,cron,repo,gateway,worktree,update,backup,cleanup,policy,health,acceptance,dashboard,dashboard-ui,state,drive,authorization}`
    - with grouped subactions for `repo`, `job`, `cron`, `gateway`, `worktree`, `update`, `backup`, `dashboard-ui`, `state`, and `drive`.
  - This surface remains remote-first (`--remote` is required).
- MCP CLI/runtime discovery
  - `./sb mcp --json` returns parser rejection (`unrecognized arguments: --json`), confirming the current CLI surface has no JSON mode on the top-level `mcp` action.
  - `./sb mcp list` is also rejected (`unrecognized arguments: list`), matching earlier findings and indicating no sibling `mcp` list action in this session.
  - `list_mcp_resources` returns active resource/tool-like surfaces for `codex_apps`, `dataAnalyticsWidgets`, and `codex-security`; no MCP resources were returned for the `sandbox` namespace in this environment.
  - `list_mcp_resource_templates` is empty.

## Continuation pass (2026-08-02): mcp/command parser and tool-namespace follow-up

### MCP command parser findings
- `./sb mcp` is implemented as a transport bootstrap surface only (help shows `--transport`, `--bind`, `--port`, `--token`, `--public-url`, `--instance`, `--label`).
- Any positional argument to `./sb mcp` is rejected at parser level:
  - `./sb mcp status --json` -> `unrecognized arguments: status --json`
  - `./sb mcp diagnostics --json` -> `unrecognized arguments: diagnostics --json`
  - `./sb mcp --json` -> `unrecognized arguments: --json`
- `./sb mcp up --help` and `./sb mcp down --help` both show the same transport-only usage, confirming no direct subcommand actions there.
- `./sb mcp --help status` still exits successfully with same top-level usage (ignores action-like token).

### `connect`/`mcp-install` parser consistency
- `./sb mcp-install --json` and `./sb connect --json` both fail with parser-level `unrecognized arguments: --json`.
- `./sb mcp-install --help` remains a single-surface command with only server/session selection options.

### Focus command behavior
- `./sb focus --help` confirms `slug` is positional and no `--json` support in usage.
- `./sb focus does-not-exist --json` is rejected as parser args (`--json` is not a supported flag), while direct positional behavior for unknown slugs is still to route as focus input.

### MCP-tool namespace status
- Calling MCP tool list endpoints as shell commands (e.g. `functions.list_mcp_resources`) fails because those are tool bindings, not shell binaries.
- `list_mcp_resources` on `server: dataAnalyticsWidgets` still returns expected widget URIs.
- `list_mcp_resources` on `server: sandbox` still returns `MCP server 'sandbox' was not ready for this step` in tool mode.
- `list_mcp_resource_templates` remains empty for `codex_apps`, `dataAnalyticsWidgets`, and `codex-security`.

## Continuation pass (2026-08-02): Additional parser/behavior deltas

### job-family and workspace parser gaps
- `./sb job no-such-id` now exits with consistent parser validation (`invalid job id (expected 16 hex chars)`) while other job commands still diverge:
  - `./sb job-status no-such-id` raises a traceback inside `jobs/registry.py`.
  - `./sb job-cancel no-such-id` raises a similar traceback.
- `./sb job status no-such-id` is rejected at argparse level (`unrecognized arguments`) because `job` command does not accept subcommands.
- `./sb jobs --json` remains unsupported (`unrecognized arguments: --json`).
- `./sb workspace status gap-test` with positional workspace still fails (`unrecognized arguments: gap-test`).
- `./sb workspace status --workspace gap-test` cleanly returns `workspace_not_found`.
- `./sb workspace status default --json` fails parser-style (`unrecognized arguments: default`), while `./sb workspace status --json --remote scaleway-sandbox` now reaches runtime with `workspace_not_found`.
- `./sb workspace create --workspace abc123` creates a named workspace (`ok`) as side effect.

### remote service action syntax
- `./sb remote service` and `./sb remote service status` without `<name>` still require action+name and report usage: `./sb remote service <status|diagnostics|migrate|stop> <name> [--plan|--confirm]`.
- `./sb remote service status does-not-exist --json` returns explicit missing-remote error.

### cache command behavior
- `./sb cache` prints cache inventory and totals.
- `./sb cache clear` without `--yes` still prompts and throws `EOFError` in non-interactive context.
- `./sb cache clear unknown-layer` rejects unknown layer with argparse valid choices (`wp-cli, wp-http`).
- `./sb cache clear wp-cli --json` and `./sb cache clear wp-http --yes --json` are unsupported (`unrecognized arguments: --json`).
- `./sb cache info wp-cli` is accepted; same output as default `cache`.

### secrets and open
- `./sb secrets` requires action; help shows only `{migrate-zshrc}`.
- `./sb secrets list` is invalid choice.
- `./sb secrets migrate-zshrc --json` fails with shell-expansion error in `/Users/alim/.zshrc.secrets` line 41 in this environment.
- `./sb open unknown` invalid target with parser choices `admin|site|mail`.
- `./sb connect` usage and invalid `foo` target already consistent with known targets; `foo` returns `unknown target`.

### resources command nuances
- `./sb resources plan --json --scope unknown` rejects with argparse choice validation (`cache` or `stale`).
- `./sb resources cleanup --json --scope stale --remote scaleway-sandbox` returns refusal JSON with `plan_not_found` and `--plan-id is required`.
- `./sb resources status` accepts `--scope stale` (previously thought to be status-only), and returns complete/payload JSON; this looks like a tolerated/likely no-op filter alias. It does not fail and returns full object.
- `./sb resources status --json --remote does-not-exist` returns `unknown_remote`.
- `./sb resources status --json --scope stale --remote scaleway-sandbox` returns large partial payload with status `partial` and non-measured inventory (`host_filesystem`, `docker_storage`, low confidence/large unknown bytes), confirming remote partial-result behavior.

## Continuation pass (2026-08-02): command parser + capability sweep

### help/usage checks for additional command families
- `./sb pxdiff`, `./sb vrdiff`, `./sb specextract`, `./sb specdiff`, `./sb specgate` all require required positional paths/URLs and only `--json` where explicitly listed.
- `./sb license` action parser remains strict:
  - `./sb license` prints masked status
  - `./sb license bogus` errors with choice list.
- `./sb host`, `./sb deploy`, `./sb ensure`, `./sb init`, `./sb server`, `./sb global`, `./sb home`, `./sb mcp`, `./sb focus`, `./sb claude`, `./sb native`, `./sb instances`, `./sb test`, `./sb e2e`, `./sb xdebug`, `./sb abilities`, `./sb introspect`, `./sb recovery`, `./sb hermes`, `./sb secure`, `./sb domains`, `./sb dump`, `./sb qm`, `./sb visit`, `./sb wp`, `./sb seed`, `./sb migrate`, `./sb restore`, `./sb setup`, `./sb shell`, `./sb up`, `./sb down`, `./sb logs`, `./sb install`, `./sb update`, `./sb doctor`, `./sb status`
  all expose stable positional/subcommand surfaces as documented by `--help` and did not regress into parser exceptions during help-only checks.

### behavior changes from command execution checks
- `./sb home` (no args) prints current `SANDBOX_HOME` and relocation hints.
- `./sb global` reports when `sb` is already installed globally and points to `~/.local/bin/sb`.
- `./sb server` without `server_type` is a parser error, and `./sb instance` remains `delete`-only in `--help` coverage.
- `./sb instances` prints local-instance inventory text, and `./sb instances --json` returns machine-readable structure for all instances.
- `./sb uninstall` confirms safety gate: non-interactive run exits with `error: refusing to uninstall non-interactively without --yes.` after listing impacted resources.
- `./sb snapshot` and `./sb restore` show required positional `name`.
- `./sb async-job` is required to provide `job_id`.
- `./sb plugin-check` executes a `wp plugin check sandbox` command and in this environment fails with a `wp` subcommand registration error when plugin is absent.

### job command cluster (non-json/non-positional edge)
- `./sb job-list` prints job entries successfully without `--json` and without job id filter (history dump format begins with workspace-like ids + states).
- `./sb job-start` errors with `error: usage: ./sb job-start [target options] -- <argv...>` and requires delimiter usage when starting jobs.
- `./sb job-matrix` similarly requires `--workspace <label>` plus `-- <argv...>` before execution.
- `./sb job-cleanup`, `./sb job-retry`, `./sb job-metrics`, `./sb job-output`, `./sb job-artifacts`, and `./sb job-artifact-get` are all missing required positional args (`job_id` and `artifact_id` where applicable).
- `./sb job-reconcile` and `./sb job-retention` accepted no args in this session and returned status summaries (`interrupted=0 released=0` and `cleaned=0 retention_days=7`, respectively) instead of usage failures.
- `./sb job no-such-id` still validates job ID format at parser level, while some sibling commands still show inconsistent exception paths for bad IDs as previously logged.

### other command outputs worth carrying forward
- `./sb status` succeeded with focused JSON capability summary and reported `Optional runtime gaps: logs, stop, wordpress.debug, wordpress.server-switch`.
- `./sb onboard --minimal` auto-runs the onboarding flow and reports successful setup for this project; it did not require interactive confirmation in this run.

## Continuation pass (2026-08-02): runtime behavior deltas (continued)

### execution-path behavior
- `./sb test` executes immediately and defaults to unit mode; in this project, `./sb test --json` and `./sb test auto` both run PHPUnit and fail with usage/exit code 2 when required tests aren’t discoverable.
- `./sb e2e --json` still performs runtime validation and exits with:
  - `error: no playwright config found under ...` when no discoverable config exists.
- `./sb exec -- echo hello_exec` fails with:
  - `error: project kind 'wordpress' does not support 'exec'`.

### domains/native runtime checks
- `./sb native support` returns JSON when `--json` is present and includes support tiers by adapter.
- `./sb native support --json` worked without requiring project-specific extra flags.
- `./sb native status --json` returns optional capability matrix and reports native capability gaps (`logs`, `stop`, `wordpress.debug`, `wordpress.server-switch` unsupported in this environment).
- `./sb domains status --json` currently returns `ok: false` with `reason: resolver_not_selected` (fallback strategy in use).

### instance / job edge behavior
- `./sb instance` and `./sb instance delete` both enforce required positional arguments:
  - `sandbox instance: error: the following arguments are required: action, name`
  - `sandbox instance: error: the following arguments are required: name`
- `./sb job-list --json` is accepted (contrary to earlier expectation) and returns full job records with `ok: true`.
- `./sb job-status <id>` returns concise terminal summary with lifecycle/state and workspace metadata.
- `./sb job-cancel <id> --json` on a succeeded job returns `already_terminal`.
- `./sb job-retry <succeeded-id>` returns a new retry job id (not blocked by terminal state in this check).
- `./sb job-start -- echo ...` successfully starts a local exec job and prints job metadata.
- `./sb job-output <id>` returns captured command output when the job is terminal and available (example: `hello-sb`).

## Continuation pass (2026-08-02): remote/Hermes/edge execution delta

### remote command family
- `./sb remote add`, `provision`, `up`, `down`, `remove`, `set-origin`, and `service` all require required positional `name` when used without `list`.
- `./sb remote list --json` is supported and returns remotes with fields `name`, `ssh_configured`, `reachable`, and `provisioned`.
- `./sb remote add testremote foo@host` reports successful registration and next steps (`next: ./sb remote provision testremote`).
- `./sb remote remove testremote --yes` immediately reported `• no remote named 'testremote' was registered` in this environment, indicating the registration did not persist in a retrievable way.
- `./sb remote provision no-such-remote --json` returns human error: remote must be registered first.
- `./sb remote service status`, `diagnostics`, `migrate`, `stop` with missing remote return action usage.
- `./sb remote service migrate scaleway-sandbox --plan` returns `remote service ...: planned` (non-mutating plan mode).
- `./sb remote up scaleway-sandbox --json` and `./sb remote down scaleway-sandbox --json` returned `planned` with `requires_confirm: true`.
- After `./sb home /tmp/sandbox-test-home`, subsequent `./sb remote list` became empty (`• no remotes registered`) and `remote service status scaleway-sandbox` became `no remote named`, suggesting `home/migrate` flow can affect remote registry/config state.

### hermes
- `./sb hermes status` requires `--remote` and returns parser usage if omitted.
- `./sb hermes status --remote hermes-acceptance --json` returns configured status, reporting lifecycle `configured`, `reported_version`, `running_sessions`.
- `./sb hermes job status --remote hermes-acceptance --json` without `--job-id` returns `missing_job_id`.

### wp / snapshot / misc execution edges
- `./sb wp` without passthrough fails with usage (`usage: ./sb wp <wp-cli args>`).
- `./sb wp -- --version` and `./sb wp --async plugin list` attempt to invoke wp-cli; `--async` returns job id on success even when command context may be unsupported in that form.
- `./sb wp plugin list --allow-root --format=ids` failed with container exec mount-namespace error in this environment (`current working directory is outside of container mount namespace root`).
- `./sb seed does-not-exist.xml` validates importer availability and fails with WordPress Importer guidance (`Try 'wp plugin install wordpress-importer --activate'`) before import path handling.
- `./sb snapshot does-not-exist` now prompts `error: snapshot ... exists — pass --force to overwrite` (snapshot name currently reserved in this environment).
- `./sb restore does-not-exist` restored by resetting DB/importing from `/snapshots/does-not-exist/*` and reported success, even for missing snapshot label (operationally suspicious).
- `./sb home /tmp/sandbox-test-home` produced a live migration attempt and container recreation attempt (`lenzora-2`, port collision on 8258); command did not complete cleanly.
- `./sb dashboard --json` is rejected (`unrecognized arguments: --json`); `./sb dashboard` in non-interactive context prints static list with interactive warning.
- `./sb dashboard` still lists running instances and no JSON mode.
