# Implementation Evidence: Sandbox Modular Boundaries

## Trace

- Task class: cross-cutting architecture and behavior-preserving refactor
- Planning model/effort: Hermes Sol / high
- Evidence model/effort: Hermes Luna / read-only
- Implementation route: one bounded writer at a time; Terra-compatible task scope for mutations
- Workspace: `/Users/alim/Sites/git/sandbox`
- Branch: `codex/hermes-public-access`
- Baseline commit: `e52eb8d`
- Started: 2026-07-13 Asia/Dhaka
- Retry limit: two evidence-driven retries per failing gate
- High-risk stop conditions: unexplained state drift, authorization/public exposure change, data-loss risk, missing rollback, or overlapping writer

## Phase 1 baseline

### Repository and specification

- Working tree contained only the new uncommitted feature-022 artifacts and managed context updates when implementation began.
- Spec quality checklist: 16/16 passing.
- Cross-artifact analysis: 30 FRs, 12 SCs, 111 sequential tasks, zero duplicate IDs, zero malformed task lines, zero unresolved clarifications.
- Remediation: aligned descriptor-before-registry phase order and added exact artifact paths to eight evidence/review tasks.

### CLI baseline

Command:

```text
python3 -c 'import sandbox.cli; from sandbox.registry import COMMANDS; ...'
```

Result: 67 registered root commands. Exact ownership is in [cli-inventory.md](cli-inventory.md).

### MCP baseline

Command:

```text
cd mcp/wp-server && ./.venv/bin/python -c 'import server; ...'
```

Result: 51 registered tools across 17 imported tool groups. Exact ownership is in [mcp-inventory.md](mcp-inventory.md).

### Dependency baseline

AST/text inventory found 40 shipped wildcard imports across 40 files: 24 `sandbox.core` consumers (including the package documentation match) and 16 MCP `app` consumers. Central ownership and hotspots are recorded in [dependency-inventory.md](dependency-inventory.md).

### Local live baseline

Commands:

```text
./sb status
./sb doctor
```

Initial observation: the registered `sandbox` WordPress instance resolved to `http://localhost:8200`, but Docker Desktop was not running. Web, DB, and Mailpit containers were missing; REST could not connect. Docker Desktop was then started as the platform prerequisite; all stack operations below used Sandbox commands.

Commands and results:

- `./sb up`: started DB, Mailpit, WordPress FPM, and nginx; reported `https://sandbox.tst` and Mailpit on port 8135.
- `./sb status`: all containers running; project and MCP instance resolution correct.
- `./sb doctor`: WordPress core installed and REST application-password authentication returned 200; the only issue was optional GitHub organization configuration.
- `./sb wp core version`: `7.0.1`.
- `./sb wp option get siteurl`: `https://sandbox.tst` before and after lifecycle restart.
- `./sb snapshot modularity-baseline --force`: captured DB and uploads under the Sandbox snapshot directory before apply/reconciliation.
- `./sb apply --project-dir "$PWD" --json`: reconciled and recreated the local services without dropping the database.
- `./sb domains`: listed the `sandbox` HTTPS route and the running proxy.
- `./sb down` followed by `./sb up`: clean stop/remove and recreate/start cycle succeeded.
- `./sb selftest`: completed successfully through the Sandbox test command.
- `./sb test`: expected non-applicability for this tooling repository; the WordPress plugin harness found no plugin `composer.json` or PHP test configuration and exited with PHPUnit usage code 2. This is not used as a modularization pass gate; `./sb selftest` and the Python suites are the product gates.

### Remote Hermes baseline

Command:

```text
./sb hermes status --remote scaleway-sandbox --json
```

Results through `./sb hermes ... --json`:

- `status`: stable success envelope; lifecycle `configured`; Hermes Agent v0.18.2; no running Sandbox-managed sessions.
- `gateway status`: Sandbox-owned gateway unit inactive, matching the current public-dashboard architecture.
- `dashboard status`: ready, enabled, loopback-bound on port 9119, and last health `healthy`.
- `policy show`: max jobs 2, max worktrees 12, minimum free disk 1024 MB, minimum free memory 512 MB.
- `health`: all executable/profile/MCP-contract checks passed; v2 gate passed; no stale jobs; one stale session record was reported for later non-destructive cleanup review.
- `backup list`: ten local Hermes recovery archives were listed; no archive was created or restored.
- `drive list`: two encrypted-backup manifest names were listed; no Drive object was deleted or restored.

No secret value was printed or recorded.

## Phase evidence

Append exact commands, results, retries, rollback evidence, and residual risks beneath a heading for each completed phase. A passing unit test is not a substitute for the required live checks.

## Phase 2 — Foundational contracts

Tests were written before contract implementation and initially failed at the intended missing seams: `sandbox.runtimes`, `sandbox.project_registry`, `sandbox.services`, command specifications, and MCP composition did not exist. The wildcard guard also initially counted a documentation string; it was corrected to parse actual Python imports through the AST.

Implemented contracts/skeletons:

- immutable descriptor and operation request/result/error types;
- deterministic schema/adapter registries with duplicate/conflict rejection;
- registry repository protocol and in-memory implementation;
- process, HTTP, port, path, and proxy protocols plus recording fakes;
- backward-compatible `CommandSpec` metadata while preserving `COMMANDS` dispatch;
- MCP `ToolGroupSpec`, dependency bag, and deterministic composer skeleton;
- no-new-wildcard and composition-root dependency guards.

Evidence:

```text
.cli-venv/bin/python -m unittest \
  tests.test_runtime_contracts tests.test_registry_repository \
  tests.test_service_contracts tests.test_command_composition \
  tests.test_mcp_composition tests.test_architecture_boundaries -v
Ran 15 tests — OK

.cli-venv/bin/python -m unittest tests.test_sandbox tests.test_cli -v
Ran 92 tests — OK

mcp/wp-server/.venv/bin/python -m unittest tests.test_mcp -v
Ran 1 test — OK; all 51 tools still register
```

No production config, registry, runtime, CLI parser, MCP server bootstrap, or Hermes path has switched. Rollback is deletion of the unused contracts and restoration of the small command-registry metadata wrapper.

## User Story 1 — Descriptor/schema ownership

Tests first failed because `sandbox.config` did not exist. The implementation added side-effect-free native descriptor kind discovery, an explicit schema registry, a WordPress compatibility schema, and a facade that selects the kind before calling the legacy normalizer. Existing internal callers continue using `sandbox_core.load_project_config`; they do not import schema internals.

Behavior:

- omitted kind selects WordPress;
- explicit WordPress and omitted kind normalize equivalently;
- non-WordPress test schemas bypass WordPress plugin-slug validation;
- unknown kinds fail before the legacy loader;
- duplicate schema kinds fail closed;
- descriptor discovery does not execute subprocesses or project code.

Evidence:

```text
.cli-venv/bin/python -m unittest \
  tests.test_config_descriptors tests.test_config_facade tests.test_sandbox \
  tests.test_bridge tests.test_cli tests.test_architecture_boundaries -v
Ran 122 tests — OK
```

Retry: the first combined command referenced a planned but nonexistent `tests.test_project_config`; the corrected command used `test_config_facade` and existing `test_sandbox`, then passed. No product change was needed for that retry.

Live checks through Sandbox commands:

- `./sb ensure --json`: idempotent WordPress ensure completed.
- `./sb status`: WP, DB, Mailpit, and nginx remained running.
- `./sb doctor`: core installed and REST authentication status 200.
- `./sb wp option get siteurl`: remained `https://sandbox.tst`.

Rollback: restore `sandbox_core.load_project_config` as the direct legacy implementation and remove the unused `sandbox/config/` package. No persisted format changed.

## User Story 2 — Registry repository

The JSON repository tests first failed because the implementation and corruption/version errors did not exist. The repository now owns lock acquisition, v1→v2 compatibility migration, canonical root/label identity, same-filesystem temporary writes, flush/fsync, atomic replacement, CRUD, ambiguity reporting, and unknown compatible fields. A matching in-memory repository supports isolated tests.

State safety evidence:

- v1 fixture migrates under lock and preserves record-level unknown fields;
- v2/current records round-trip without broad eager rewrite;
- unsupported future version raises and leaves bytes unchanged;
- corrupt JSON raises and leaves bytes unchanged;
- injected `os.replace` failure leaves the previous valid registry unchanged;
- lock file is separate from the registry document;
- existing 400-increment concurrency self-test, multi-label behavior, and legacy migration tests pass.

Focused evidence:

```text
.cli-venv/bin/python -m unittest \
  tests.test_registry_repository tests.test_sandbox tests.test_bridge \
  tests.test_cli tests.test_architecture_boundaries -v
Ran 124 tests — OK

mcp/wp-server/.venv/bin/python -m unittest tests.test_mcp -v
Ran 1 test — OK; all 51 tools still register
```

MCP `_instance_server` and `_instance_php_version` now use the public repository facade rather than reading `registry.json` directly.

Live checks through Sandbox commands:

- `./sb instances --project-dir "$PWD"`: resolved the existing `sandbox/default` record and HTTPS URL.
- `./sb status`: all four services remained running.
- `./sb doctor`: core/REST/MCP checks unchanged.
- `./sb wp option get siteurl`: remained `https://sandbox.tst`.

Rollback: point `registry_all`, `registry_put`, and `registry_remove` back to the retained legacy private functions. Registry path and version remain compatible.

## User Story 3 — Capability-based runtime dispatch

The runtime service now resolves a descriptor, selects one explicitly registered
adapter, checks its declared capability, and only then invokes an operation. The
WordPress adapter delegates the existing ensure/apply/status implementations;
CLI and MCP transports share the same boundary. Unsupported kinds and
capabilities return structured errors before helper or subprocess calls.

Security regression: live verification exposed that legacy `ensure --json`
included a one-time autologin URL. The transport now excludes both the URL and
token from JSON output, and a regression test proves the sensitive sentinel is
absent.

Remote/deploy review:

- project deployment and WordPress previews preflight their WordPress capability
  before Git, SSH, DNS, container, or proxy mutation;
- `remote` remains machine-scoped and therefore has no project-kind filter;
- `host` remains intentionally runtime-neutral because it deploys declared
  production Compose services, including non-WordPress applications.

Evidence:

```text
.cli-venv/bin/python -m unittest tests.test_runtime_service \
  tests.test_runtime_transport tests.test_sandbox tests.test_cli \
  tests.test_architecture_boundaries -q
Ran 103 tests — OK

mcp/wp-server/.venv/bin/python -m unittest tests.test_mcp -q
Ran 1 subprocess suite — OK; 51 tools register and MCP WordPress/database
rejections record zero helper/subprocess calls

.cli-venv/bin/python -m unittest tests.test_remote tests.test_hosting -q
Ran 99 tests — OK
```

Live checks through Sandbox commands:

- `./sb ensure --project-dir "$PWD" --json`: ready, stable ports/URL, and no
  autologin token or login URL in output.
- `./sb status`: WordPress, DB, Mailpit, and nginx remained running.
- `./sb doctor`: core installed and REST authentication returned 200; only the
  pre-existing optional GitHub organization hint remained.
- `./sb wp option get siteurl`: remained `https://sandbox.tst`.

One test-transport retry was required: MCP modules depend on their dedicated
virtual environment, so the zero-side-effect assertions were moved from the
general Python suite into the existing MCP subprocess probe. Product code did
not change for that retry.

Rollback: restore direct lifecycle calls in the migrated command handlers and
remove the adapter/service composition. No persistent runtime format changed.

## US5 safe MCP composition completion — 2026-07-14

### Completed tasks

- T058: `tests/test_mcp_composition.py` now locks the exact ordered 18-group
  manifest, deterministic composition, duplicate group rejection, duplicate
  tool ownership rejection, and registration of a test-only group.
- T059: `instances` and `hermes` are import-safe modules with no `app` import
  or decorator-registration side effect. Their `register(server, dependencies)`
  functions were exercised with isolated fake server/dependency contexts.
- T060: `tests/test_mcp.py` snapshots all 75 public FastMCP tool names, their
  required parameters, order, and current `null` output-schema contract.
- T064: the instance group receives eight named helpers and the Hermes group
  receives `sandbox_root`; `server.py` constructs those named dependencies.
  No wildcard `app` import is used by either migrated group.
- T065: every one of the 18 groups is represented once by
  `BUILTIN_TOOL_GROUPS`/`BUILTIN_TOOL_NAMES`; the remaining 16 groups remain
  explicitly marked app-backed compatibility wrappers pending independent
  migrations. Composer duplicate detection now covers group IDs and tool names.

### Verification

```text
python3 -m unittest tests.test_mcp_composition -v
Ran 9 tests in 0.036s — OK

mcp/wp-server/.venv/bin/python -m unittest tests.test_mcp -v
Ran 2 tests in 2.854s — OK

mcp/wp-server/.venv/bin/python -m unittest tests.test_mcp_composition tests.test_mcp -v
Ran 11 tests in 2.717s — OK

mcp/wp-server/.venv/bin/python -m compileall -q mcp/wp-server
git diff --check
exit 0
```

No WordPress runtime, vendor file, remote, live stack, destructive operation,
commit, or push was performed. Residual risk: the 16 compatibility groups still
couple their implementation to `app`; they are covered by exact registration and
schema snapshots but are not yet independently injectable. FastMCP currently
reports `null` output schemas for these `-> dict` tools, so key-level response
envelope compatibility remains covered by existing behavioral probes rather than
an inferred output schema.

## Completion checklist

- [X] Scope stayed within feature 022 and its required documentation/downstream handoff files.
- [X] User changes were preserved.
- [ ] Required focused/full/live checks passed.
- [X] Fresh correctness and security/data-loss review completed.
- [X] No secret was recorded.
- [X] No unapproved commit, push, release, deployment, backup deletion, or applied restore occurred.
- [X] Downstream features remain blocked until explicit human approval.

## Incremental boundary hardening — 2026-07-14

This increment remained within feature 022 and did not commit, push, alter a
remote, edit `runtime/wp/`, or edit `vendor/`.

### Implemented and tested

- `BoundedProcessRunner` now converts a subprocess timeout into a bounded,
  redacted `ProcessResult` with return code `124`; it does not propagate a raw
  `TimeoutExpired` that could expose partial output.
- Hermes state parsing now rejects a non-object JSON root through
  `HermesStateError`, preserving a stable corruption contract.
- Hermes restore planning now requires every planned artifact to carry a
  64-character hexadecimal SHA-256 digest; normalization lowercases the digest
  in the non-mutating plan.
- The migrated MCP `instances` group now imports only its explicit app helpers;
  an architecture test prevents it and the Hermes group from reintroducing a
  wildcard `app` import.
- Added the compatibility-facade ledger at
  [compatibility-facades.md](compatibility-facades.md), including owner,
  rollback, compatibility evidence and removal gates (T092 complete).

### Verification

```text
python3 -m unittest tests.test_hermes_state tests.test_hermes_backup \
  tests.test_service_process tests.test_service_http_ports \
  tests.test_service_paths_proxy -v
Ran 10 tests in 0.116s — OK

python3 -m unittest tests.test_mcp_composition -v
Ran 5 tests — OK

python3 -m unittest discover -s tests -v
Ran 593 tests in 22.345s — OK (skipped=2)

./sb selftest
selftest: passed

git diff --check
exit 0
```

The complete suite emitted existing `ResourceWarning` messages for async-job
child processes but finished successfully. The MCP-specific virtual environment
is absent in this workspace, so live FastMCP registration/schema replay could
not be performed; only the manifest/composer unit checks ran. `./sb selftest`
reported a local port conflict and saved an adjusted port mapping to the
machine-local `sandbox.local.yml`; no repository runtime file was changed.

### Remaining block

US5 is not complete: most tool groups still use the app-backed compatibility decorator bridge rather than group-specific injected dependencies, and an end-to-end MCP schema snapshot cannot run without its dedicated environment. US6 and US7 remain incomplete because
production service injection, full live domain/HTTPS parity, Hermes facade
routing, and required remote non-destructive acceptance checks have not all
been completed. Consequently, no downstream feature is unblocked.

## US4 completion and US5 bounded composition — 2026-07-14

### Completed CLI bridge (T056–T057)

- `sandbox/commands/manifest.py` now maps all 68 registered handlers to an
  explicit feature-owner bridge. `validate_builtin_command_coverage()` fails
  the composition test if a registered command is absent from that inventory.
  No handler or parser behavior was changed.
- Updated `cli-inventory.md` with the 68-command replay and recovery ownership.

### Completed MCP composition foundation (T061–T063)

- The built-in MCP manifest has one deterministically ordered entry for each
  of 18 tool groups. The composer validates the required `app` server
  dependency before invoking its bounded decorator compatibility wrapper.
- `server.py` now creates the explicit dependency bag and composes the
  manifest; it no longer has a manual tool import list.
- All current MCP tool groups now use named app imports rather than
  `from app import *`; instances and Hermes retain their earlier explicit
  helper imports. This is intentionally not marked as T064 because the groups
  have not yet been converted to group-specific injected dependencies.

### Verification

```text
python3 -m unittest tests.test_command_composition tests.test_mcp_composition -v
Ran 11 tests — OK

./sb --help
exit 0

./sb selftest
Ran 594 tests — OK (skipped=2); selftest: passed

python3 -m compileall -q mcp/wp-server sandbox
git diff --check
exit 0
```

The MCP virtual environment is absent (`mcp/wp-server/.venv/bin/python` does
not exist), so FastMCP registration, exact schema/required-parameter snapshots,
and transport parity were not run. The inventory records the historical 51/17
baseline and the current 18 manifest groups without inventing a fresh tool
count. `./sb selftest` again adjusted only machine-local port mappings after a
local conflict; no repository runtime file was changed.

## US6 bounded service mechanisms — 2026-07-14

### Completed safe unit scope (T066–T071)

- `BoundedProcessRunner` is covered for argument-list-only execution,
  cwd/environment propagation, bounded/redacted output, and timeout conversion
  to return code `124` without exposing partial secret output.
- `UrlHttpProbe` returns `False` for local transport failure and HTTP 404.
  `SocketPortAllocator.reserve()` now holds a loopback socket until the context
  exits, closing the allocator's previous allocate-then-release race; preferred
  port collisions still fail closed.
- `AllowedRootPathPolicy.artifact_path()` validates both the supplied artifact
  root and the resolved joined path, rejecting traversal outside an allowed
  root. Recording fakes now cover reservation, artifact paths, and full proxy
  plan/apply/remove calls.
- `CallbackProxyManager` preserves an apply failure if its best-effort rollback
  also fails. Its plan/apply/remove/rollback behavior is exercised only through
  callbacks; no Caddy, domain, DNS, HTTPS, Docker, or live route operation ran.
- `runtime_neutral_dependencies()` supplies injectable production defaults for
  process/HTTP/ports/paths while requiring a caller-owned proxy adapter, so it
  introduces no WordPress or proxy policy.

New failure tests were observed red for the missing reservation, artifact-path,
rollback-error-preservation, recorder completeness, and composition seams;
they then passed after the bounded implementations. Existing process timeout
coverage was retained and re-run.

### Verification

```text
python3 -m unittest tests.test_service_contracts tests.test_service_process \
  tests.test_service_http_ports tests.test_service_paths_proxy -v
Ran 14 tests in 0.637s — OK

python3 -m compileall -q sandbox/services sandbox/application/context.py \
  tests/fakes/sandbox_services.py
exit 0

git diff --check
exit 0

python3 -m unittest discover -s tests -v
Ran 599 tests in 30.319s — OK (skipped=1)
```

The full Python suite emitted pre-existing async-job `ResourceWarning` messages.
Its selftest coverage adjusted a port mapping only in machine-local
`$SANDBOX_HOME/sandbox.local.yml` after finding a local conflict; no repository
runtime file, WordPress file, or live service was deliberately changed.

### Remaining US6 work

- T072 remains open: the callback proxy is a safe generic adapter, not yet a
  reviewed injection over existing Caddy/domain behavior.
- T073 remains open: the new composition factory is injection-tested, but no
  focused WordPress core caller has been migrated; preserving current domain
  policy requires a separate review.
- T074 remains open: the requested live domain/HTTPS/lifecycle parity was not
  run, per this bounded worker's no-live-mutation scope. No destructive or
  remote operation was attempted.

## US7 tests-only characterization preparation — 2026-07-14

### Completed test deliverables

- T075: expanded isolated state coverage for schema defaults, private lock permissions, interrupted same-directory atomic replacement, JSON/root corruption, and legacy state compatibility preservation.
- T076: expanded pure policy coverage and added the missing transport-independent `resolve_target(target, remotes)` characterization with mutation/import-side-effect guards.
- T077: added deterministic injected job fakes and status/cancel/cleanup delegation, explicit worktree run, and duplicate-run race characterizations.
- T078: added deterministic gateway fakes and plan/no-side-effect, authentication-before-route, reverse rollback, and explicit remove characterizations.
- T079: added injected artifact-store create/list/integrity and non-mutating retention-hook characterizations while retaining restore-plan validation/non-mutation coverage.
- T080: added facade public-function identity/composition-factory tests and AST guards preventing bounded Hermes modules from importing the legacy control plane; the CLI is required to import the public facade.

### Test-first result

```text
python3 -m unittest tests.test_hermes_state tests.test_hermes_routing \
  tests.test_hermes_jobs tests.test_hermes_gateway tests.test_hermes_backup \
  tests.test_hermes_service tests.test_architecture_boundaries -v
Ran 26 tests in 0.729s
FAILED (failures=9) — expected characterization failures; no test harness error.

python3 -m compileall -q tests/test_hermes_state.py tests/test_hermes_routing.py \
  tests/test_hermes_jobs.py tests/test_hermes_gateway.py tests/test_hermes_backup.py \
  tests/test_hermes_service.py tests/fakes/hermes.py tests/test_architecture_boundaries.py
git diff --check
exit 0
```

The nine intentional red failures identify: legacy `repositories`/`gates` loss on state rewrite; missing pure target resolver; missing job `run` and idempotency/race boundary; legacy monolithic gateway apply/rollback and missing removal operation; missing injected backup service/retention hook; and missing Hermes composition factory. Existing isolated checks that do have seams passed (17/26), including state atomic replacement/corruption, routing policy purity, job status/cancel/cleanup delegation, gateway plan validation, restore-plan non-mutation/integrity validation, facade public identity, and no bounded-module legacy import.

## US7 bounded-seam audit remediation — 2026-07-14

The recovery audit completed the nine intentionally red isolated characterizations
without contacting a remote, applying a gateway route, restoring/deleting backup
state, or changing the legacy public facade.

- `HermesState` now preserves unknown compatible top-level collections during a
  read/write round trip, including the legacy `repositories` and `gates` fields.
- `routing.resolve_target()` is a pure validated target resolver; job execution
  now exposes an injected backend seam with lock-protected idempotency keys.
- Gateway application installs access before the route and reverses route then
  access on failure or explicit removal. The adapter is still not connected to
  the existing public gateway implementation.
- Backup artifact create/list/verify and retention candidate selection are
  injected, integrity-validated, and non-destructive. `compose_hermes_service()`
  creates only bounded service objects with unavailable default adapters.

Verification:

```text
python3 -m unittest tests.test_hermes_state tests.test_hermes_routing \
  tests.test_hermes_jobs tests.test_hermes_gateway tests.test_hermes_backup \
  tests.test_hermes_service tests.test_architecture_boundaries -v
Ran 26 tests in 0.671s — OK

python3 -m unittest tests.test_hermes tests.test_hermes_catalog_integrity -v
Ran 138 tests in 0.588s — OK

python3 -m compileall -q sandbox/hermes tests/fakes/hermes.py
git diff --check
exit 0
```

Tasks T081–T091 remain unchecked intentionally. The new seams do not yet route
legacy state, remote target resolution, jobs, public gateway, backup commands,
CLI, or MCP through the composed service. T084, T086, and T093 also require
read-only remote parity evidence. Those operations are authorization/public-
access sensitive and were not performed in this unattended audit. T072–T074
and all US8/Phase 11 tasks remain open for the same integration and final-gate
work. Downstream specifications remain blocked.

No production module, `sandbox/core/_hermes.py`, CLI/MCP handler, remote configuration, gateway/tunnel/auth setting, `runtime/wp/`, or `vendor/` was edited. No remote/live-stack, restore, deletion, commit, or push action was performed. T081–T091 and T093–T094 remain open.

## US7 explicit facade and transport integration — 2026-07-14

### Completed implementation tasks

- T081–T083: state preserves compatible unknown collections under private locking
  and atomic replacement; routing resolves configured targets without provider I/O;
  jobs expose injected run/status/cancel/cleanup with lock-protected idempotency.
- T085: gateway planning and application require access controls before route
  exposure and unwind route then access on failure/removal.
- T087–T088: backup create/list/integrity and non-mutating restore planning are
  composed with state/routing/jobs/gateway through explicit injected services.
- T089–T090: the public facade now owns explicit aliases for the migrated public
  functions while retaining legacy callable identity. The existing CLI imports
  this facade, so argument checks, confirmation/authorization order, and legacy
  errors remain unchanged.
- T091: the MCP Hermes group now declares only `hermes_service`; it no longer owns
  subprocess execution or a Sandbox root global. The MCP composition root injects
  a bounded command adapter, preserving argument order, JSON parsing, timeout
  handling, public tool names, and required-parameter schemas.

Tests were added before the final transport behavior. The first focused run failed
at the intended seams: missing `HermesCommandService`, implicit facade exports,
old `sandbox_root` MCP dependency, and old direct subprocess ownership. After the
implementation, the same tests passed.

### Exact verification

```text
python3 -m unittest -v tests.test_hermes_service tests.test_mcp_composition tests.test_mcp
Ran 15 tests in 2.624s — OK

python3 -m unittest -v tests.test_service_contracts tests.test_service_process \
  tests.test_service_http_ports tests.test_service_paths_proxy \
  tests.test_hermes_state tests.test_hermes_routing tests.test_hermes_jobs \
  tests.test_hermes_gateway tests.test_hermes_backup tests.test_hermes_service \
  tests.test_hermes tests.test_hermes_catalog_integrity \
  tests.test_mcp_composition tests.test_mcp tests.test_architecture_boundaries
Ran 191 tests in 4.440s — OK

python3 -m unittest -v tests.test_setup_idempotency \
  tests.test_service_paths_proxy tests.test_service_contracts
Ran 14 tests in 0.013s — OK

python3 -m compileall -q sandbox/services sandbox/application/context.py \
  sandbox/hermes mcp/wp-server tests/test_hermes_service.py \
  tests/test_mcp_composition.py tests/test_mcp.py
git diff --check
exit 0
```

Final combined rerun after reviewing the WordPress lifecycle integration:

```text
python3 -m unittest -q tests.test_setup_idempotency tests.test_service_contracts \
  tests.test_service_process tests.test_service_http_ports \
  tests.test_service_paths_proxy tests.test_hermes_state \
  tests.test_hermes_routing tests.test_hermes_jobs tests.test_hermes_gateway \
  tests.test_hermes_backup tests.test_hermes_service tests.test_hermes \
  tests.test_hermes_catalog_integrity tests.test_mcp_composition \
  tests.test_mcp tests.test_architecture_boundaries
Ran 198 tests in 4.515s — OK
```

This scheduled run then replayed the complete repository suite and the read-only
local domain/status probes against the final reviewed diff:

```text
python3 -m unittest discover -s tests -v
Ran 625 tests in 25.542s — OK (skipped=1)

python3 -m compileall -q sandbox/services sandbox/application/context.py \
  sandbox/commands/lifecycle.py tests/test_service_paths_proxy.py \
  tests/test_setup_idempotency.py
git diff --check
exit 0

./sb domains list
No custom domains; proxy not running on 127.0.0.77.

./sb status
exit 1 — no sandbox instance for this directory
Known instances: 3d6c733fb94dac1c, html-social-share-button
```

The complete suite emitted the pre-existing async-job `ResourceWarning` messages.
Its existing port-conflict fixture adjusted only machine-local
`$SANDBOX_HOME/sandbox.local.yml`; no repository runtime file was changed.

### T074/T084/T086 live evidence and blockers

The repository root is no longer a registered Sandbox project in this machine's
current registry. `./sb status`, `doctor`, `wp`, `down`, and `up` from the root all
failed closed with `no sandbox instance for this directory`; `./sb domains`
reported no custom domains and the proxy not running. No route or certificate was
created to manufacture parity evidence.

A registered disposable instance was checked explicitly through `./sb --instance
3d6c733fb94dac1c ...`. `wp option get siteurl` returned
`http://localhost:8188` both before and after `./sb down` / `./sb up`; down, up,
status, and both WP-CLI reads returned zero. The instance's displayed mapped URL
remained `http://localhost:8190`, so the pre-existing siteurl/port mismatch is
recorded rather than normalized. The post-up stack had healthy DB plus running WP,
nginx, and Mailpit. This proves local lifecycle persistence only, not domain/HTTPS
parity; T074 remains open.

Read-only Hermes checks against the previously documented `scaleway-sandbox`
remote failed closed before network access with `unknown_remote` in the active
profile. Therefore remote job lifecycle, `hermes.asb.bd` authentication/route,
WebSocket reconnect, and backup-list parity were not claimed; T084, T086, and T093
remain open. No public route was created or enabled, no restore or deletion ran,
and no remote configuration was changed.

### Remaining integration risks

- T072–T073 are complete in the reviewed current diff: `wordpress_proxy_facade()`
  validates exact declared WordPress domain/port identity and delegates apply/remove
  to the existing aggregate Caddy owner; `wordpress_runtime_dependencies()` injects
  the bounded mechanisms; and `cmd_up` uses that proxy dependency instead of
  directly invoking `_ensure_proxy_up`. Focused facade and lifecycle tests pass.
- T074 remains blocked on an existing registered domain/HTTPS fixture; creating a
  route is outside this run's hard limits.
- T084/T086/T093 remain blocked because the active profile has no configured
  `scaleway-sandbox` remote. No remote parity evidence was fabricated.
- Bounded Hermes modules are integrated at facade/composition boundaries, while
  legacy `_hermes.py` remains the rollback implementation. Facade removal remains
  prohibited until final parity and approval gates pass.

## Scheduled follow-up — proxy action-boundary validation

This run re-inspected the complete current diff and task ledger before changing
behavior. It found one fail-closed gap in the T072 adapter: a caller could bypass
`wordpress_proxy_facade()` declaration validation by passing a hand-built plan
straight to `CallbackProxyManager.apply()` instead of using `plan()`.

Test-first evidence:

```text
python3 -m unittest -v \
  tests.test_service_paths_proxy.TestPathsAndProxy.test_wordpress_proxy_facade_rejects_undeclared_apply_and_declared_remove
FAILED (failures=1) — direct undeclared apply did not raise
```

`CallbackProxyManager.apply()` now revalidates the plan immediately before the
side-effect callback. The WordPress facade test covers both planning and direct
apply bypasses, while declared-route removal remains rejected until configuration
ownership has first removed the route.

Final focused checks:

```text
python3 -m unittest -v tests.test_service_paths_proxy \
  tests.test_setup_idempotency tests.test_service_contracts
Ran 14 tests — OK

python3 -m unittest -q tests.test_hermes_state tests.test_hermes_routing \
  tests.test_hermes_jobs tests.test_hermes_gateway tests.test_hermes_backup \
  tests.test_hermes_service tests.test_hermes tests.test_hermes_catalog_integrity \
  tests.test_mcp_composition tests.test_mcp tests.test_architecture_boundaries
Ran 177 tests — OK

git diff --check
exit 0
```

Current non-mutating parity discovery:

- `./sb remote list --json` returned `{"ok": true, "remotes": [], "error": null}`.
- `./sb domains list` reported no custom domains and the proxy not running.
- `./sb --instance 3d6c733fb94dac1c status` showed DB healthy and WP/nginx/Mailpit
  running; `wp option get siteurl` remained `http://localhost:8188`, while the
  mapped nginx port remained 8190. This is the already-recorded pre-existing
  siteurl/port mismatch, not new drift.
- A requested read-only Sol review worker could not authenticate to its provider
  (HTTP 401) and made no changes; no review result was fabricated.

No additional task was marked complete. T074 still lacks a pre-existing domain/HTTPS
fixture, and T084/T086/T093 still lack a configured remote. Creating a route or
remote to manufacture evidence is prohibited. T072–T073 and T081–T083/T085/T087–T092
remain complete; their focused checks pass. No public route, restore, deletion,
remote mutation, commit, push, `runtime/wp/` edit, or `vendor/` edit occurred.

## Final feasible gates and downstream handoff — 2026-07-14

### T094 audit and final compatibility tests

T094 remains blocked. The scoped-recovery plan targets the Hermes backup contract and
bounded services, but the current implementation is not solely against those
boundaries: `sandbox/recovery/inventory.py` imports the legacy remote control plane
and performs direct subprocess discovery, while `sandbox/recovery/crypto.py` invokes
`subprocess.run` directly for passphrase-FD handling. This confirms the concrete
boundary blocker already recorded below. Refactoring those recovery adapters requires
an explicitly approved recovery replan; this feature-022 pass did not silently expand
scope or claim T094.

Final regression coverage now locks:

- the exact 68-command CLI manifest and exact 18-group/75-tool MCP inventory,
  required parameters, deterministic composition, and current null output schemas;
- global/project/override/label and legacy WordPress config compatibility through
  the public facade, plus memory/JSON registry contracts and legacy registry CRUD;
- WordPress adapter result/capability parity, the machine-scoped remote-list JSON
  envelope with no SSH target disclosure, and Hermes error-envelope parity.

```text
python3 -m unittest -v tests.test_runtime_service \
  tests.test_remote.TestFeature022FinalRemoteRegression \
  tests.test_hermes_service tests.test_architecture_boundaries
Ran 21 tests in 1.291s — OK

python3 -m unittest -v tests.test_cli tests.test_command_composition \
  tests.test_mcp_composition tests.test_mcp tests.test_project_config \
  tests.test_registry_repository tests.test_config_facade tests.test_sandbox
Final combined compatibility run: 145 tests; one new assertion incorrectly required
object identity rather than envelope equality. It was corrected without product-code
change, and the final focused 21-test rerun passed.
```

### Disposable state failure injection (T100)

```text
python3 -m unittest -v tests.test_registry_repository tests.test_hermes_state \
  tests.test_config_facade tests.test_project_config
Ran 17 tests in 0.211s — OK
```

All writes used temporary/copied state. Covered corrupt JSON, unsupported future
versions, interrupted atomic replacement, private lock creation, v1 migration,
unknown-field preservation, legacy Hermes collection preservation, and valid
round trips. Corrupt/future/interrupted cases preserved the prior bytes; no live
registry or Hermes state was overwritten.

### Drift review and non-destructive live discovery

```text
./sb remote list --json
{"ok": true, "remotes": [], "error": null}

./sb domains list
No custom domains; proxy not running on 127.0.0.77.

./sb status
exit 1 — no sandbox instance for this directory
Known instances: 3d6c733fb94dac1c, html-social-share-button

./sb --instance 3d6c733fb94dac1c status
DB healthy; WordPress, nginx, and Mailpit running

./sb --instance 3d6c733fb94dac1c wp option get siteurl
http://localhost:8188
```

There is no unexplained new baseline drift. The repository-root registration is
absent in current machine state, and the existing disposable instance still has the
previously recorded siteurl/mapped-port mismatch (`8188` versus nginx `8190`). Both
are documented rather than normalized. Facade consumer sets remain exactly those in
`compatibility-facades.md`; architecture and composition tests fail on growth. Every
facade removal remains blocked by missing live parity and explicit approval.

### Documentation, enforcement, and convergence

T104–T107 are complete: architecture/config/command/MCP/Hermes extension guidance is
present in `README.md`, `docs/sandbox-config-reference.md`, `AGENTS.md`, and
`CLAUDE.md`; durable implementation rules are in `skills/speckit-implement/SKILL.md`
and `workflows/build-feature/WORKFLOW.md`; Spec 021 remains implementation-blocked
with moved-owner notes; and exact inventories/facade consumers/boundaries are active
test gates.

The final Spec-Kit convergence pass found 30 functional requirements, 12 success
criteria, and 111 unique well-formed task IDs, with no duplicate IDs or malformed
open task lines. Review against the plan, quickstart, facade ledger, and current diff
found no missing automated task beyond the already-open T074/T084/T086/T093/T094 and
live/review gates; therefore
no new task ID was appended. T110 is complete without claiming feature completion.

Full automated evidence:

```text
python3 -m unittest discover -s tests -q
Ran 634 tests in 25.775s — OK (skipped=1)

./sb selftest
Ran 634 tests in 23.004s — OK (skipped=1); selftest: passed
```

Both runs emitted the pre-existing async-job `ResourceWarning` messages. The
port-conflict fixture adjusted only machine-local `sandbox.local.yml`, as in earlier
runs. The dedicated MCP environment was available and its two schema/registration
tests passed inside the full suite.

Scope audit (T109): this run used targeted edits in feature-022 tests/docs/task
evidence, preserved the pre-existing working tree, recorded no credential or secret,
and performed no commit, push, release, deployment, route/public-access change,
remote change, restore, deletion, `runtime/wp/` edit, or `vendor/` edit.

Downstream handoff (T111): Spec 021 must be re-planned against the owner table in its
updated plan/tasks and remains blocked pending successful live parity, independent
review, and explicit human approval. Scoped recovery is already a separately planned
feature in `specs/023-scoped-recovery-profiles/`; this handoff records its valid
feature-022 boundary but grants no additional activation, restore, prune, or public-
access authority.

### Gates that remain open

- T074: no existing domain/HTTPS fixture; lifecycle unit/failure tests pass, but
  creating a route or certificate is prohibited.
- T084, T086, T093: no configured remote; remote job, gateway authentication/route,
  WebSocket reconnect, no-exposure-drift, and complete Hermes acceptance remain
  unverified.
- T094: scoped recovery still has direct legacy-remote/subprocess adapter dependencies
  outside `sandbox.hermes.backup` and shared service contracts; recovery replan and
  authorization are required before changing them.
- T098: the root is not a registered WordPress project, and quickstart Scenario 6
  also requires domain/HTTPS, snapshot, apply, stop/start, and disposable destroy;
  this run did not mutate state to manufacture those fixtures.
- T099: no remote exists; no job, gateway, public-access, or backup create operation
  was attempted.
- T103: fresh independent correctness/regression and security/data-loss review is
  still required; the previously requested reviewer could not authenticate, so no
  review result is claimed.
- T108: focused/full/selftest/diff portions are feasible and pass, but the task stays
  open because every quickstart live scenario cannot run under the blockers above.

## Final-gate and documentation pass — 2026-07-14

### Completed automated and documentation scope

- T095: final CLI/MCP compatibility assertions enforce 68 owned CLI commands,
  18 deterministic MCP groups, and the exact 75-tool public name/required-parameter
  schema snapshot. FastMCP registration ran in its dedicated environment.
- T096: `tests/test_project_config.py` now replays global → project → override →
  label precedence through the public facade and separately locks legacy WordPress
  normalization; repository v1/v2/future/corrupt/atomic-failure coverage passed.
- T097: WordPress runtime delegation, zero-side-effect rejection, complete remote
  regression, and every legacy Hermes public callable/facade identity passed.
- T100: copied/disposable registry and Hermes-state corruption, future-version,
  lock, interrupted replacement, path traversal, process timeout/redaction, port
  collision/reservation, and proxy apply/rollback failure scenarios all passed.
- T101: the baseline review found no unexplained drift. The only live discrepancy is
  the already-recorded registered fixture whose WordPress `siteurl` is port 8188 while
  its mapped nginx URL is port 8190; this run did not normalize or mutate it.
- T102/T107: the facade consumer sets are now executable architecture guards and the
  ledger records all deferred removals. Exact inventory checks also run in the
  architecture suite.
- T104–T106: architecture/config/Hermes guidance was added to README, config docs,
  AGENTS, CLAUDE, the Spec-Kit implementation skill, and the build-feature workflow.
  Spec 021 is explicitly implementation-blocked and maps responsibilities moved by
  feature 022; Compose/Astro remain unimplemented.
- T109: review of `git status`, the diff, commands, and evidence confirms user changes
  were retained; no secret, route, remote, restore, deletion, commit, push, tag,
  release, or production action was introduced. `runtime/wp/` and `vendor/` were not
  edited. `./sb selftest` changed only machine-local port assignments after detecting
  a conflict, as previously documented.
- T110: an artifact-scoped analysis/convergence pass rechecked 30 FRs, 12 SCs, eight
  user stories, the constitution, plan decisions, and all 111 tasks. No new task is
  needed: every remaining gap is already represented by T074, T084, T086, T093,
  T094, T098, T099, T103, or T108. The generic prerequisite helper points at the
  unrelated active feature 026 unless its persisted context is changed, so this pass
  deliberately used the explicit feature-022 artifact paths and did not alter
  `.specify/feature.json`.
- T111: downstream handoff is prepared. Spec 021 must be replanned against the moved
  owners and remains blocked. Existing Spec 023/recovery work is not treated as an
  authorization to continue, activate schedules, prune, restore, or expose anything;
  any downstream start/unblock still needs explicit human approval.

### Exact verification

```text
python3 -m unittest -v tests.test_cli tests.test_mcp \
  tests.test_command_composition tests.test_mcp_composition \
  tests.test_project_config tests.test_config_facade \
  tests.test_registry_repository tests.test_runtime_service tests.test_remote \
  tests.test_hermes_service tests.test_architecture_boundaries
Ran 141 tests in 17.357s — first run exposed two new guard-fixture mistakes
(expected app.py scan scope and dict-vs-tool count); no product defect.

python3 -m unittest -v tests.test_registry_repository tests.test_hermes_state \
  tests.test_service_process tests.test_service_http_ports \
  tests.test_service_paths_proxy tests.test_runtime_service \
  tests.test_project_config tests.test_hermes_service \
  tests.test_architecture_boundaries
Ran 47 tests in 2.156s — OK

python3 -m unittest discover -s tests -v
Ran 631 tests in 25.206s — OK (skipped=1)
The skip is the general-environment `test_server_transport`; dedicated MCP tests
above ran and passed. Pre-existing async-job ResourceWarnings remain non-fatal.

./sb selftest
Ran 631 tests in 24.357s — OK (skipped=1); selftest: passed

python3 -m compileall -q sandbox mcp/wp-server tests
git diff --check
exit 0
```

### Non-destructive live discovery and blockers

```text
./sb remote list --json
{"ok": true, "remotes": [], "error": null}

./sb domains list
No custom domains; proxy not running on 127.0.0.77.

./sb instances
3d6c733fb94dac1c and html-social-share-button are running, localhost-only.

./sb hermes status|gateway status|backup list --remote scaleway-sandbox --json
Each failed closed with error code unknown_remote.
```

Therefore T074 remains blocked on an existing domain/HTTPS fixture; T084/T086/T093
remain blocked on a configured remote; T098 cannot run every WordPress quickstart
scenario because it calls for snapshot/apply/disposable destroy and domain/HTTPS
state that this run may not create or mutate; T099 has no remote; T103 still needs a
fresh independent correctness/security/data-loss reviewer (the earlier Sol review
attempt failed authentication); and T108 depends on those full quickstart/live/review
gates.

T094 is also not complete. Recovery can use `sandbox.hermes.backup` for integrity and
non-mutating restore-plan concepts and uses shared process service composition, but
current scoped recovery is not specified/implemented **solely** against that module
and shared contracts: `sandbox/recovery/inventory.py` imports the legacy remote
control plane and embeds direct subprocess discovery, while
`sandbox/recovery/crypto.py` invokes `subprocess.run` directly for passphrase-FD
handling. Resolving those adapter boundaries belongs to an explicitly approved
recovery replan, not this compatibility refactor.

## Fresh Sol correctness/security/data-loss gate — 2026-07-14

### T094 dependency audit

T094 remains open with a narrower, source-backed blocker. No production recovery
module imports or calls `sandbox.hermes.backup`; the only production consumer of
`HermesBackupService` is `sandbox/hermes/service.py`. Recovery composition instead
constructs `SandboxRemoteInventory` directly. That adapter imports
`sandbox.core._remote`, calls its remote lookup/home/SSH helpers, and embeds remote
`docker` and `git` subprocess calls. `GpgCrypto` separately calls `subprocess.run`
instead of an injected shared process contract because it needs passphrase-FD
inheritance, while the shared `BoundedProcessRunner` contract has no pass-FD input.
Consequently scoped recovery cannot currently be specified solely against
`sandbox/hermes/backup.py` and shared contracts. The precise unblock is an approved
recovery replan that introduces a remote-inventory contract and a secret-safe process
contract capable of inherited descriptors, then composes those adapters without the
legacy remote control plane. No recovery, remote, or crypto code was changed here.

### T103 fresh review findings and resolutions

The full working-tree diff, facade ledger, architecture guards, state/process/path/
port/proxy mechanisms, MCP composition, Hermes boundaries, and prior test evidence
were re-reviewed for correctness, compatibility, authorization/exposure, secret
handling, atomicity, rollback, and deletion/restore risk.

Two concrete fail-open validation defects were found and resolved in the bounded
Hermes code:

1. `HermesGatewayService.plan()` accepted any string beginning with
   `http://127.0.0.1:`. A user-info payload such as
   `http://127.0.0.1:9119@public.example.test` passed that prefix test while parsing
   to a public host. It now parses the URL and requires exact HTTP, exact
   `127.0.0.1`, an explicit valid port, no credentials, and no path/query/fragment.
   Regression cases cover user-info host confusion, path/query suffixes, and invalid
   ports. This closes an origin-confusion/public-egress seam before any backend call.
2. `HermesStateRepository.read()` accepted non-object `installation` and `sessions`
   values, deferring failure until later conversion/rewrite and risking an invalid
   state document entering a mutation path. It now rejects those shapes before a
   write; tests cover list/string corruption while preserving unknown legacy fields.

No destructive path was exercised. Restore remains plan-first and confirmation-
gated; retention only returns candidates; proxy/gateway ordering keeps access before
route exposure; state writes retain locking, private modes, fsync, atomic replace,
and unknown fields. `HermesBackupService.verify()` currently verifies stored digest
metadata equality rather than independently hashing payload bytes, and retention
orders caller-supplied timestamp strings. Neither seam has a production store/delete
consumer in this diff, and T094 blocks scoped recovery from claiming or consuming
them; any downstream recovery implementation must supply physical-content
verification and validated timestamps before activation or prune authority. This is
a downstream gate, not evidence that deletion is safe.

Fresh non-destructive execution:

```text
python3 -m unittest discover -s tests -q
Ran 634 tests in 31.612s — OK (skipped=1)

python3 -m unittest -v tests.test_hermes_gateway tests.test_hermes_state \
  tests.test_hermes_backup tests.test_hermes_service tests.test_architecture_boundaries
Ran 26 tests in 1.671s — OK

python3 -m compileall -q sandbox mcp/wp-server tests
git diff --check
exit 0
```

The full run occurred before the two review fixes; the post-fix focused run covers
both changed modules plus backup/service and architecture regression gates. T103 is
complete as a fresh review with findings resolved or explicitly gated. T094 remains
open. No fixture domain/public route, external gateway, remote access/configuration,
restore, delete, commit, push, `runtime/wp/`, or `vendor/` action occurred.

## Scheduled Sol static re-review — 2026-07-14

### T094 final dependency gate

T094 remains open. Source search confirms that no production module under
`sandbox/recovery/` imports or calls `sandbox.hermes.backup`; the only production
consumer of `HermesBackupService` is `sandbox/hermes/service.py`. Recovery instead
composes `SandboxRemoteInventory`, whose `discover()` imports
`sandbox.core._remote`, calls legacy remote lookup/home/SSH helpers, and sends an
embedded discovery program containing direct `docker` and `git` subprocess calls.
`GpgCrypto._run()` separately invokes `subprocess.run(..., pass_fds=...)`; the shared
`ProcessRunner` contract has no inherited-descriptor field and therefore cannot
represent the secret-safe GnuPG operation. Scoped recovery consequently does not
depend solely on `sandbox/hermes/backup.py` and shared contracts. The precise unblock
remains an approved Spec 023 replan adding (1) a remote-inventory contract/adapter and
(2) a bounded process request that can inherit explicitly authorized descriptors,
then removing the legacy remote import and direct process ownership. No recovery code,
remote, crypto operation, restore, or deletion was used to reach this conclusion.

### T103 fresh correctness, regression, security, and data-loss review

A fresh pass over the full working-tree diff and existing evidence found one further
concrete action-boundary defect in `HermesGatewayService`: callers could bypass
`plan()` by constructing `GatewayPlan` directly, so `apply()` accepted an
origin-confusion URL and reached the access/route backend. The same boundary also
accepted non-hostname metacharacters in `fqdn`, leaving unsafe policy for a future
backend. A test was first added and failed because no exception was raised. The
service now centralizes exact hostname and loopback-origin validation and re-runs it
inside `apply()` before the first backend call. Regression coverage proves a
hand-built `http://127.0.0.1:9119@public.example.test` plan and malformed FQDN fail
closed with zero backend calls. Normal access-before-route and reverse rollback
behavior remains unchanged.

The prior T103 findings remain resolved: URL user-info/path/query origin confusion is
rejected during planning, and malformed Hermes state collections are rejected before
rewrite. Review of state atomic replacement/private modes, process output redaction,
path containment, reserved ports, proxy declaration checks/rollback, MCP dependency
composition, facade consumer guards, command/schema parity, restore confirmation, and
non-deleting retention found no additional actionable regression in feature 022.
Residual downstream gates remain explicit: `HermesBackupService.verify()` compares
digest metadata rather than hashing payload bytes; retention trusts timestamp strings;
and the state rename fsyncs file contents but not the containing directory. None has a
new production delete/restore consumer in this diff. Spec 023 must resolve physical
content verification, timestamp validation, and crash-durability requirements before
capture activation, restore, or prune authority.

Fresh execution against the reviewed diff:

```text
python3 -m unittest \
  tests.test_hermes_gateway tests.test_hermes_state tests.test_hermes_backup \
  tests.test_hermes_jobs tests.test_hermes_routing tests.test_hermes_service \
  tests.test_service_process tests.test_service_http_ports \
  tests.test_service_paths_proxy tests.test_runtime_service tests.test_remote \
  tests.test_mcp_composition tests.test_mcp tests.test_architecture_boundaries -v
Ran 138 tests in 5.022s — OK

python3 -m unittest discover -s tests -q
Ran 635 tests in 23.131s — OK (skipped=1)

python3 -m compileall -q sandbox mcp/wp-server tests
git diff --check
exit 0
```

The full run emitted the already-recorded async-job `ResourceWarning` messages and
its port-conflict fixture adjusted only machine-local `sandbox.local.yml`; no
repository runtime file changed. T103 remains fully satisfied and checked. T094 is
not checked. No domain fixture, public route, external gateway, remote access or
configuration, restore, delete, commit, push, `runtime/wp/`, or `vendor/` operation
occurred.

## Scheduled Sol full-diff gate — 2026-07-14

### T094 scoped-recovery dependency result

T094 remains open. A fresh production-source search found no import or call from
`sandbox/recovery/` to `sandbox.hermes.backup`; `HermesBackupService` still has only
the production composition consumer in `sandbox/hermes/service.py`. Recovery is
composed independently in `sandbox/recovery/context.py` with
`SandboxRemoteInventory` and, when configured, `BoundedProcessRunner` for the drive
adapter. The inventory adapter imports `sandbox.core._remote`, uses its remote/home/
SSH helpers, and sends a discovery program that directly owns `docker` and `git`
subprocess calls. `GpgCrypto._run()` also owns `subprocess.run(..., pass_fds=...)`;
the shared `ProcessRunner.run()` contract accepts only argv/cwd/env/timeout and cannot
represent an explicitly inherited passphrase descriptor.

Scoped recovery therefore cannot currently be specified solely against
`sandbox/hermes/backup.py` and shared contracts. The precise unblock remains an
explicitly approved Spec 023 replan that defines a remote-inventory contract and a
secret-safe bounded process request supporting explicitly authorized inherited file
descriptors, then composes those adapters without `sandbox.core._remote` or direct
process ownership. This audit performed no inventory discovery, crypto operation,
remote access, restore, or deletion, and T094 was not checked.

### T103 fresh correctness/regression and security/data-loss review

A fresh static pass covered the complete current working-tree diff, including CLI and
MCP manifests/composition, runtime dependency composition, process/path/port/proxy
mechanisms, every Hermes bounded module and facade, architecture guards, documentation,
and the accumulated test evidence. The previously repaired gateway origin/FQDN and
hand-built-plan validation remains fail-closed before backend access or route calls;
state collection shapes fail before rewrite; path traversal, undeclared proxy plans,
port collisions, process timeout output, facade growth, duplicate tool ownership, and
inventory drift remain guarded.

One bounded composition limitation was reproduced: `tools.hermes.register()` and
`tools.instances.register()` bind dependencies into module globals. Registering the
Hermes group against two fake servers in one interpreter and then invoking the first
server's retained `hermes_status` callable routed to the second service
(`service_one_calls=0`, `service_two_calls=1`). This is not a regression in the shipped
singleton MCP server, which composes one FastMCP instance once, and no authorization,
remote, or state operation was reached. Resolution for this gate is to retain the
single-server composition constraint and the MCP compatibility facade; multi-server
embedding or facade removal is not approved. A future migration must bind per-server
closures/context rather than rebinding module globals and add a two-composer isolation
test before claiming that broader lifecycle. This limitation does not change the
current 75-tool schema or singleton behavior and is not a reason to expose or activate
a downstream feature.

No additional actionable singleton-path correctness, authorization/exposure,
secret-handling, or data-loss regression was found. Existing downstream risks remain
explicitly gated: backup verification compares stored digest metadata rather than
hashing physical payload bytes, retention orders unvalidated timestamp strings, and
state replacement does not fsync the containing directory. There is no new production
restore/delete/prune consumer for those seams in this diff; Spec 023 must resolve them
before capture activation, restore, or prune authority. T103 remains checked because
the requested fresh review is complete, the current production lifecycle is covered,
and every broader limitation has a fail-closed approval gate.

Fresh non-destructive execution against the reviewed tree:

```text
python3 -m unittest \
  tests.test_hermes_gateway tests.test_hermes_state tests.test_hermes_backup \
  tests.test_hermes_jobs tests.test_hermes_routing tests.test_hermes_service \
  tests.test_service_process tests.test_service_http_ports \
  tests.test_service_paths_proxy tests.test_runtime_service tests.test_remote \
  tests.test_mcp_composition tests.test_mcp tests.test_architecture_boundaries -v
Ran 138 tests in 4.935s — OK

python3 -m unittest discover -s tests -q
Ran 635 tests in 23.917s — OK (skipped=1)

python3 -m compileall -q sandbox mcp/wp-server tests
git diff --check
exit 0
```

The full run emitted the already-recorded async-job `ResourceWarning` messages and its
port-conflict fixture changed only ignored machine-local `sandbox.local.yml`; a final
status/diff audit found no `runtime/wp/` or `vendor/` change. No domain fixture, public
route, external gateway, remote access/configuration, restore, delete, commit, push,
or remote configuration action occurred.

## Scheduled Sol static gate refresh — 2026-07-14

### T094 scoped-recovery dependency audit

T094 remains open and was not marked complete. The production dependency trace is
unchanged and does not satisfy the task:

- no module under `sandbox/recovery/` imports or calls `sandbox.hermes.backup`;
  `HermesBackupService` is composed only by `sandbox/hermes/service.py`;
- `sandbox/recovery/context.py` instead constructs `SandboxRemoteInventory` directly;
  `sandbox/recovery/inventory.py:13` imports the legacy `sandbox.core._remote` control
  plane, uses its remote lookup/home/SSH helpers, and sends a discovery program that
  directly owns `docker` and `git` subprocess calls; and
- `sandbox/recovery/crypto.py:40` invokes `subprocess.run(..., pass_fds=...)` directly.
  The shared `ProcessRunner.run()` contract accepts argv, cwd, env, and timeout only,
  so it cannot express the explicitly inherited passphrase descriptor required by the
  GnuPG adapter.

Scoped recovery therefore is neither specified nor implemented solely against
`sandbox/hermes/backup.py` and shared contracts. The precise unblock remains an
explicitly approved Spec 023 replan that defines a remote-inventory contract/adapter
and extends the bounded process request with explicitly authorized inherited file
descriptors, then composes recovery without `sandbox.core._remote` or adapter-owned
`subprocess.run`. This was a static trace only; no inventory discovery, remote access,
crypto operation, restore, or deletion was performed.

### T103 fresh correctness/regression and security/data-loss review

A new Sol pass reviewed the complete current working-tree diff and accumulated test
evidence, including command and MCP ownership/composition, WordPress runtime dependency
composition, bounded process/path/port/proxy mechanisms, all extracted Hermes modules,
facades and consumer guards, recovery boundary guards, state failure injection, and
the exact CLI/MCP compatibility assertions. The review specifically rechecked
authorization/exposure ordering, origin and hostname parsing, hand-built plan bypasses,
path containment, process argument/redaction/timeout behavior, state shape validation,
locking/private modes/atomic replacement, rollback ordering, duplicate ownership,
secret disclosure, restore confirmation, retention non-mutation, and public protocol
drift.

No new actionable feature-022 correctness, authorization/exposure, secret-handling, or
data-loss defect was found. Previously repaired gateway origin/FQDN validation still
runs in both `plan()` and `apply()` before backend calls; malformed state collections
still fail before rewrite; undeclared proxy plans, traversal, port collision, duplicate
tool ownership, facade growth, and schema/inventory drift remain fail-closed under the
focused and full suites. The known module-global dependency binding in the migrated MCP
`instances` and `hermes` groups remains limited to the shipped single-composition server
lifecycle; multi-server embedding remains unapproved and requires per-server closures
plus isolation tests before that lifecycle can be claimed.

Residual downstream gates are unchanged and are not treated as data-safety evidence:
`HermesBackupService.verify()` compares digest metadata rather than hashing physical
payload bytes, retention orders unvalidated caller timestamp strings, and Hermes state
replacement does not fsync the containing directory. There is no new production
restore/delete/prune consumer for these seams in the feature-022 diff. Spec 023 must
resolve them before capture activation, restore, or prune authority. T103 remains fully
satisfied because this fresh review found no unresolved defect in the currently shipped
singleton path and every broader lifecycle/data operation remains explicitly gated.

Fresh non-destructive execution against the reviewed tree:

```text
python3 -m unittest -v \
  tests.test_hermes_gateway tests.test_hermes_state tests.test_hermes_backup \
  tests.test_hermes_jobs tests.test_hermes_routing tests.test_hermes_service \
  tests.test_service_process tests.test_service_http_ports \
  tests.test_service_paths_proxy tests.test_runtime_service tests.test_remote \
  tests.test_mcp_composition tests.test_mcp tests.test_architecture_boundaries
Ran 138 tests in 6.014s — OK

python3 -m unittest discover -s tests -q
Ran 635 tests in 25.519s — OK (skipped=1)

python3 -m compileall -q sandbox mcp/wp-server tests
git diff --check
exit 0
```

The full run emitted the already-recorded async-job `ResourceWarning` messages and its
port-conflict fixture adjusted only ignored machine-local `sandbox.local.yml`. An
additional read-only Sol worker was requested for reviewer redundancy but could not
authenticate (HTTP 401) and made no changes; no result from that failed attempt is
claimed. This scheduled job itself ran on the Sol review model and completed the static
gate. No domain fixture, public route, external gateway, remote access/configuration,
restore, delete, commit, push, `runtime/wp/`, or `vendor/` operation occurred.

## Scheduled Sol static/full-diff gate refresh — 2026-07-14

### T094 scoped-recovery dependency gate

T094 remains open. A fresh source trace found zero production imports or references to
`sandbox.hermes.backup` or `HermesBackupService` under `sandbox/recovery/`; the backup
service is still composed only by `sandbox/hermes/service.py`. Scoped recovery instead
constructs `SandboxRemoteInventory` in `sandbox/recovery/context.py`. Its implementation
imports `sandbox.core._remote` inside `discover()`, delegates lookup/home/SSH to that
legacy control plane, and sends a remote inventory script that directly invokes
`docker` and `git` subprocesses. `sandbox/recovery/crypto.py` also directly invokes
`subprocess.run(..., pass_fds=...)`; the shared `ProcessRunner` contract accepts only
argv, cwd, env, and timeout and cannot safely model the inherited passphrase descriptor.

Scoped recovery therefore cannot currently be specified solely against
`sandbox/hermes/backup.py` and shared service contracts. The precise blocker is the
absence of (1) a remote-inventory contract/adapter independent of
`sandbox.core._remote`, and (2) a secret-safe bounded process request supporting an
explicit allowlist of inherited file descriptors. Unblocking T094 requires an approved
Spec 023 replan and implementation of those contracts before replacing the direct
legacy/process dependencies. This review performed static tracing only: it did not run
inventory discovery, remote access, GnuPG, restore, retention deletion, or capture.
T094 was correctly left unchecked.

### T103 correctness/regression and security/data-loss review

A fresh Sol review covered the complete current working-tree diff and accumulated test
evidence: CLI/MCP ownership and schemas, singleton composition, WordPress proxy
composition, process/path/port/proxy mechanisms, all Hermes bounded services and the
legacy facade, state persistence/failure injection, architecture guards, and recovery
handoff boundaries. The pass rechecked capability-before-side-effect behavior,
authorization/public-exposure ordering, direct `GatewayPlan` bypass resistance,
hostname/origin parsing, path containment, subprocess shell/redaction/timeout handling,
private state modes and atomic replacement, rollback order, duplicate ownership,
secret-bearing output, restore confirmation, and non-mutating retention selection.

No new actionable feature-022 correctness, compatibility, authorization/exposure,
secret-handling, or data-loss defect was found. Existing resolutions remain effective:
`HermesGatewayService` validates both planned and hand-built plans before access or
route calls; malformed state collections fail before rewrite; proxy apply rejects
undeclared routes; traversal and port collisions fail closed; and exact inventory,
facade-consumer, and duplicate-tool guards pass. The module-global dependency binding
in migrated MCP `instances`/`hermes` groups remains constrained to the shipped
single-composition server. Multi-server embedding is not approved and still requires
per-server closures plus an isolation regression before it can be claimed.

Residual downstream data-safety gates remain unresolved and must not be interpreted as
activation evidence: backup verification compares stored digest metadata rather than
hashing physical payload bytes, retention orders unvalidated timestamp strings, and
Hermes state replacement does not fsync the containing directory. None has a new
production restore/delete/prune consumer in this feature-022 diff. Spec 023 must resolve
physical-content verification, timestamp validation, and crash-durability policy before
capture activation, applied restore, or prune authority. T103 remains checked because
the fresh review completed with no unresolved defect in the currently shipped singleton
path and the broader/data-mutating lifecycles remain explicitly gated.

Fresh non-destructive execution against the reviewed tree:

```text
python3 -m unittest -v \
  tests.test_hermes_gateway tests.test_hermes_state tests.test_hermes_backup \
  tests.test_hermes_jobs tests.test_hermes_routing tests.test_hermes_service \
  tests.test_service_process tests.test_service_http_ports \
  tests.test_service_paths_proxy tests.test_runtime_service tests.test_remote \
  tests.test_mcp_composition tests.test_mcp tests.test_architecture_boundaries
Ran 138 tests in 4.696s — OK

python3 -m unittest discover -s tests -q
Ran 635 tests in 24.652s — OK (skipped=1)

python3 -m compileall -q sandbox mcp/wp-server tests
git diff --check
exit 0
```

The full run emitted the already-recorded async-job `ResourceWarning` messages and its
port-conflict fixture adjusted only ignored machine-local `sandbox.local.yml`; no
repository runtime file changed. A final status/path audit found no `runtime/wp/` or
`vendor/` diff. No domain fixture, public route, external gateway, remote access or
configuration, restore, delete, commit, push, or remote configuration action occurred.

## Scheduled Sol static gate and removal-boundary fix — 2026-07-14

### T094 scoped-recovery dependency gate

T094 remains open. A fresh production trace again found no import or call from
`sandbox/recovery/` to `sandbox.hermes.backup`; `HermesBackupService` is composed only
by `sandbox/hermes/service.py`. Recovery instead constructs `SandboxRemoteInventory`
in `sandbox/recovery/context.py`. Its `discover()` method imports
`sandbox.core._remote`, delegates remote lookup/home/SSH to that legacy control plane,
and sends an inventory program that directly invokes `docker` and `git` subprocesses.
`GpgCrypto._run()` separately calls `subprocess.run(..., pass_fds=...)`, while the
shared `ProcessRunner.run()` contract accepts only argv, cwd, env, and timeout and
cannot represent an explicitly inherited passphrase descriptor.

Scoped recovery therefore cannot currently be specified solely against
`sandbox/hermes/backup.py` and shared service contracts. The precise blocker is still
the absence of (1) a remote-inventory contract/adapter independent of
`sandbox.core._remote` and (2) a secret-safe bounded process request with an explicit
allowlist of inherited file descriptors. T094 requires an approved Spec 023 replan and
implementation of those contracts before replacing the legacy/direct-process
adapters. This was static tracing only; no inventory discovery, remote access, GnuPG,
capture, restore, or deletion ran, and T094 remains unchecked.

### T103 fresh correctness/regression and security/data-loss review

The complete working-tree diff and accumulated evidence were freshly reviewed across
CLI/MCP composition and exact ownership, WordPress runtime/proxy composition, bounded
process/path/port/proxy services, Hermes state/routing/jobs/gateway/backup/service and
facade boundaries, failure injection, authorization/public-exposure ordering, secret
handling, atomic replacement, rollback, restore confirmation, and retention
non-mutation.

One concrete destructive action-boundary defect was found and resolved. Although
`HermesGatewayService.apply()` revalidated hand-built `GatewayPlan` values,
`remove()` did not. A caller could bypass `plan()` and pass a malformed FQDN or
origin to the backend's route/access removal methods. `remove()` now invokes the same
exact hostname and loopback-origin validation before either backend call. A regression
test passes a hand-built metacharacter-bearing FQDN and proves rejection with zero
backend calls. Valid removal still executes route removal before access removal.

No other actionable feature-022 correctness, compatibility, authorization/exposure,
secret-handling, or data-loss regression was found. Existing downstream limitations
remain gates, not safety evidence: backup verification compares digest metadata rather
than hashing physical payload bytes; retention sorts unvalidated timestamp strings;
Hermes state replacement does not fsync the containing directory; and migrated MCP
instance/Hermes dependencies are module-global and support only the shipped singleton
composition lifecycle. No new restore/delete/prune consumer is introduced by this
diff, and broader recovery or multi-server use remains unapproved.

Fresh non-destructive execution against the reviewed diff:

```text
python3 -m unittest -v \
  tests.test_hermes_gateway tests.test_hermes_state tests.test_hermes_backup \
  tests.test_hermes_jobs tests.test_hermes_routing tests.test_hermes_service \
  tests.test_service_process tests.test_service_http_ports \
  tests.test_service_paths_proxy tests.test_runtime_service tests.test_remote \
  tests.test_mcp_composition tests.test_mcp tests.test_architecture_boundaries
Ran 139 tests in 5.523s — OK

python3 -m unittest discover -s tests -q
Ran 636 tests in 25.356s — OK (skipped=1)

python3 -m compileall -q sandbox mcp/wp-server tests
git diff --check
exit 0
```

The full suite emitted the already-recorded async-job `ResourceWarning` messages. Its
port-conflict fixture adjusted only ignored machine-local `sandbox.local.yml`; no
repository runtime file changed. T103 remains fully satisfied and checked; T094 was
not marked complete. No local/domain fixture was created, no public route or external
gateway was accessed, and no remote configuration/access, restore, delete, commit,
push, `runtime/wp/`, or `vendor/` action occurred.

## Scheduled Sol remaining static gate — 2026-07-14 17:39 CEST

### T094 scoped-recovery dependency result

T094 remains open. A fresh source trace found no production import or reference to
`sandbox.hermes.backup` or `HermesBackupService` anywhere under `sandbox/recovery/`;
the only production composition consumer remains `sandbox/hermes/service.py`.
Recovery instead constructs `SandboxRemoteInventory` directly in
`sandbox/recovery/context.py`. Its `discover()` implementation imports the legacy
`sandbox.core._remote` control plane, uses its remote lookup/home/SSH helpers, and
sends an inventory program that directly invokes `docker` and `git` subprocesses.
`GpgCrypto._run()` independently invokes `subprocess.run(..., pass_fds=...)`, while
the shared `ProcessRunner.run()` contract accepts only argv, cwd, env, and timeout.

Scoped recovery therefore cannot currently be specified solely against
`sandbox/hermes/backup.py` and shared service contracts. The precise blocker is the
absence of (1) a remote-inventory contract/adapter independent of
`sandbox.core._remote` and (2) a secret-safe bounded-process request with an explicit
allowlist of inherited file descriptors. T094 requires an approved Spec 023 replan
and implementation of those contracts before replacing the legacy/direct-process
adapters. This was static tracing only; no inventory discovery, remote access, GnuPG,
capture, restore, retention deletion, or other recovery operation ran. T094 remains
unchecked.

### T103 fresh correctness/regression and security/data-loss review

A fresh Sol pass reviewed the complete current working-tree diff and accumulated
evidence, including CLI/MCP ownership and schemas, singleton dependency composition,
WordPress runtime/proxy delegation, bounded process/path/port/proxy mechanisms, every
Hermes bounded service and facade, state failure injection, architecture guards, and
the recovery handoff. The review rechecked capability-before-side-effect behavior,
authorization/public-exposure ordering, direct plan construction, hostname/origin
parsing, path containment, subprocess shell/redaction/timeout behavior, private state
modes and atomic replacement, rollback order, duplicate ownership, secret-bearing
output, restore confirmation, and non-mutating retention selection.

No new actionable feature-022 correctness, compatibility, authorization/exposure,
secret-handling, or data-loss defect was found. Previously resolved gateway plan,
apply, and remove validation remains before all backend calls; malformed state
collections fail before rewrite; undeclared proxy plans, traversal, port collisions,
facade growth, duplicate tool ownership, and inventory drift remain fail-closed under
the focused and full suites. The MCP `instances`/`hermes` module-global dependency
binding remains limited to the shipped singleton composition lifecycle; multi-server
embedding remains unapproved pending per-server closure binding and an isolation test.

Residual downstream gates are unchanged and are not treated as activation or
data-safety evidence: backup verification compares digest metadata rather than hashing
physical payload bytes, retention sorts unvalidated timestamp strings, and Hermes
state replacement does not fsync the containing directory. No new production
restore/delete/prune consumer is introduced by this feature-022 diff. Spec 023 must
resolve physical-content verification, timestamp validation, and crash-durability
policy before capture activation, applied restore, or prune authority. T103 remains
fully satisfied and checked because the current singleton production path has no
unresolved review defect and every broader or data-mutating lifecycle remains gated.

Fresh non-destructive execution against the reviewed tree:

```text
python3 -m unittest -v \
  tests.test_hermes_gateway tests.test_hermes_state tests.test_hermes_backup \
  tests.test_hermes_jobs tests.test_hermes_routing tests.test_hermes_service \
  tests.test_service_process tests.test_service_http_ports \
  tests.test_service_paths_proxy tests.test_runtime_service tests.test_remote \
  tests.test_mcp_composition tests.test_mcp tests.test_architecture_boundaries
Ran 139 tests in 5.239s — OK

python3 -m unittest discover -s tests -q
Ran 636 tests in 24.812s — OK (skipped=1)

python3 -m compileall -q sandbox mcp/wp-server tests
git diff --check
exit 0
```

The full suite emitted the already-recorded async-job `ResourceWarning` messages and
its port-conflict fixture adjusted only ignored machine-local `sandbox.local.yml`.
Final status/path checks found no `runtime/wp/` or `vendor/` diff. No local/domain
fixture, public route, external gateway, remote access/configuration, recovery
operation, restore, delete, commit, push, `runtime/wp/`, or `vendor/` action occurred.

## Remote SSH latency increment — 2026-07-16

The remote control path already used OpenSSH connection multiplexing, but the
idle master lifetime was only 60 seconds and the runtime-upload path bypassed
the shared SSH wrapper. The implementation now keeps the control master for a
bounded 600 seconds, uses `ServerAliveInterval=30` with three missed probes,
limits connection attempts to one, and routes streamed runtime uploads through
the same multiplexed helper. Dirty deploy files are packaged once and extracted
in one remote session instead of one mkdir plus SCP session per file; deleted
files are removed in one `sh -s` batch.

This follows OpenSSH's documented `ControlMaster`/`ControlPersist` semantics
and the native OpenSSH strategy documented by Ansible. A tunnel was not added:
it is appropriate for a long-lived HTTP/control endpoint, but it does not
remove the per-command remote-shell channel cost and would add lifecycle and
port-forwarding state to ordinary SSH operations.

Evidence:

```text
./.cli-venv/bin/python -m unittest -q tests.test_remote
Ran 75 tests — OK

./sb remote list --json
ok=true; hermes-acceptance reachable=true; scaleway-sandbox reachable=true

three `ssh_run(..., "true")` probes per configured remote
hermes-acceptance: [2135.1, 651.6, 808.5] ms
scaleway-sandbox:   [665.4, 408.6, 404.2] ms
```

The first probe includes connection establishment; later probes reuse the
control socket. The remaining ~400–800 ms reflects remote command/network
latency rather than repeated authentication. No SSH target, credential, or
remote command output was recorded.

## Non-destructive remote/Hermes acceptance — 2026-07-16

Focused state/routing/jobs/gateway/backup/service tests passed in the 139-test
focused run above. Read-only remote replay also passed for both configured
targets with `./sb remote list --json`; the `hermes-acceptance` target returned
configured status, zero running sessions, clean worktrees, valid zero-job cron,
`job status` returned the expected `not_found` contract for a synthetic ID, and
backup list returned the existing manifest inventory without mutation.

The gateway plan correctly reported a real external acceptance blocker: the
remote currently has a conflicting unmanaged gateway process and no active
managed unit. `hermes gateway converge --plan` returned the exact actions and
`requires_confirm=true`; no converge/apply was run because it would mutate an
external VPS service. T086/T099 therefore remain open pending the explicit
external authorization gate, while the read-only contract evidence is recorded.

Local WordPress parity also passed non-destructively: `ensure --json` returned
the existing `https://sandbox.tst` identity, `down → up → apply --project-dir`
completed with the same ports/URL, `status` reported healthy `wp`, `nginx`,
`db`, and `mailpit`, and `https://sandbox.tst/wp-json/` returned HTTP 200.

## Focused Hermes and recovery-boundary verification — 2026-07-16

`.cli-venv/bin/python -m unittest -q tests.test_remote tests.test_hermes
tests.test_hermes_catalog_integrity tests.test_hermes_gateway
tests.test_hermes_state tests.test_hermes_backup tests.test_hermes_jobs
tests.test_hermes_routing tests.test_hermes_service
tests.test_hermes_dashboard_authorizations tests.test_recovery_catalog
tests.test_recovery_crypto tests.test_recovery_drive
tests.test_recovery_scheduler` — 279 tests passed. Negative tests emitted
expected CLI validation messages; no restore application or deletion ran.

`sandbox/hermes/backup.py` depends only on standard-library validation/types and
the injected `ArtifactStore` protocol. Its create/list/verify, retention,
restore-plan, and manifest behavior are covered without remote, Drive,
scheduler, or runtime imports. The scoped recovery boundary is therefore
specified by this module plus shared injected service contracts.
## WordPress live quickstart replay and deletion-path repair — 2026-07-16

- `./sb ensure --json` twice returned the same ready `sandbox` identity,
  ports, and `https://sandbox.tst` URL. `./sb status` showed healthy db,
  mailpit, nginx, and wp services. `./sb wp core version` returned `7.0.1`.
- HTTPS REST probes before and after lifecycle returned HTTP 200. The focused
  live sandbox suite passed 76 tests. `down → up → apply --project-dir` kept
  the same instance identity/ports/URL and completed without a DB reset.
  `./sb snapshots` listed existing snapshots read-only.
- A disposable labeled instance was created and then deleted by its exact
  instance name. During the first cleanup attempt, the command was invoked
  with the default instance name plus a label; the CLI ignored that label and
  deleted the existing default `sandbox` instance. This exposed a missing
  explicit `compose_file` import in the recently modularized delete path and
  an unsafe test invocation. The import was restored, the disposable instance
  was deleted correctly, and the default instance was recreated and verified
  healthy with HTTPS REST HTTP 200. No external/remote state was touched.
- Post-repair verification: `python3 -m unittest discover -s tests -q` — 686
  passed, `./sb selftest` — passed, and `git diff --check` — passed. The
  default instance's prior local database/snapshot contents were not
  recoverable from the recreated runtime; this is recorded as residual local
  state risk rather than claimed as parity.
- A regression guard now asserts that `instances_cmd` explicitly exposes the
  canonical `compose_file` resolver after wildcard-import removal; the focused
  post-fix pass (`tests.test_modularity`, CLI, generic Compose, and Sandbox)
  passed 102 tests.

## Authorized remote replay and final local gates — 2026-07-16

The later explicit authorization allowed non-Lenzora remote work. Sandbox
replayed remote list, Hermes/gateway status, V2 acceptance, dashboard doctor and
exposure plan, cron catalog/validation/reconcile preview, backup list, health,
and worktree inventory against `scaleway-sandbox`. Both configured remotes were
reachable. The managed gateway had one active owner and zero restarts; the
legacy unit was inactive/disabled. V2 acceptance passed, the dashboard stayed
loopback-only, the exact MFA-required Access policy and tunnel target were
reported, and an anonymous HTTPS probe redirected to Cloudflare Access. Cron
reconciliation failed closed because controlled-state fingerprints were
unavailable; no cron mutation was applied. Health reported pre-existing cron
drift/failure and one stale session outside this change's mutation scope.

Final local gates: `./.cli-venv/bin/python -m unittest discover -s tests -q`
passed 687 tests with one skip; `./sb selftest` passed; `git diff --check`
passed. T086 and T099 remain open because authenticated browser/WebSocket
reconnect evidence and a successful remote backup-create result were not
obtained. No backup, restore, deletion, or schedule claim is made.
