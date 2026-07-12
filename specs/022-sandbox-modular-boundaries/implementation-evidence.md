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

## Completion checklist

- [ ] Scope stayed within feature 022.
- [ ] User changes were preserved.
- [ ] Required focused/full/live checks passed.
- [ ] Fresh correctness and security/data-loss review completed.
- [ ] No secret was recorded.
- [ ] No unapproved commit, push, release, deployment, backup deletion, or applied restore occurred.
- [ ] Downstream features remain blocked until explicit human approval.
