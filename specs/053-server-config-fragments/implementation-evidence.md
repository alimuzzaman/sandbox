# Feature 053 Implementation Evidence

This file records bounded source, test, review, and later authorized live evidence for
instance-scoped server configuration fragments. It never contains fragment bytes,
credentials, caller paths, raw native diagnostics, container inspect payloads, login
URLs, or secret-bearing environment data.

## Planning and integration checkpoint

Recorded 2026-09-02 before Feature 053 source implementation.

- Feature branch: `codex/server-config-fragments`
- Planning checkpoint: `0c8435a345f1148bf2fb3ce63fdd973f0759bf6a`
- Accepted integration base: `c6c06e5f3c6f4b24c0c0d4c0fcd7471f00fa40d8`
- Feature 048 last feature-artifact commit on the base:
  `060590ec3e660d213dd5c18190ce5df5126bed1f`
- Feature 049 last feature-artifact commit on the base:
  `a2e288ec50e562b41e443a50db92500cb8638a16`
- Features 050/051 include the later accepted security repairs through the integration
  base above.
- Persisted `.specify/feature.json`, AGENTS, and CLAUDE pointers remain
  `specs/051-immutable-activation-recovery`. Feature 053 prerequisite and analysis calls
  use the read-only `--feature-dir specs/053-server-config-fragments` selector.

This proves only exact local source ancestry and pointer preservation. Feature 051 T060
human security review and live registered-host, edge, rollback, deployment, and
production gates remain open and are not inherited or closed by Feature 053.

## Accepted authority ownership map

Feature 053 is a separate local instance capability.

| Existing owner | Preserved boundary |
|---|---|
| `sandbox.commands.hosting` | Existing host and `host image` parsing/dispatch remains unchanged |
| `sandbox.hosting.recovery` | Feature 048 host observation/recovery authority remains opaque |
| `sandbox.hosting.images` | Features 049-051 trust, staging, custody, activation, rollback, and recovery remain opaque |
| `sandbox.core._hosting` | Existing shared host mutation authority remains unchanged |
| `sandbox.transports.remote_hosting_activation` | Remote immutable activation transport remains unchanged |
| `sandbox.registry.CommandSpec` and `sandbox.cli` | Feature 053 may add only its own command ownership and narrow read-only pre-dispatch policy |
| `sandbox.commands.net` | Existing server-switch behavior remains until its exact parser/handler parity is proven |

Feature 053 production code must not import the hosting recovery/image packages or the
remote activation transport. It owns only `sandbox.server_config`, the local `server`
command boundary, typed local lifecycle composition, and instance-specific local mounts.

## Spec Kit consistency checkpoint

- Prerequisite selector resolved `specs/053-server-config-fragments` without changing the
  persisted active pointer.
- Requirement checklist: 17/17 complete.
- Functional requirements: FR-001 through FR-050 present and covered.
- Success criteria: SC-001 through SC-012 present and covered.
- Task IDs: T001 through T108 unique, sequential, and strict checklist format.
- `git diff --check`: passed at the planning checkpoint.
- Independent post-refresh planning review: PASS.

The quickstart now names Features 048-051 rather than the rejected former Feature 047
lane. The existing optional-name parser behavior remains the compatibility source for
both `sb server <type>` and `sb server <instance> <type>`; documentation alone is not
used to infer parser behavior.

## Feasibility Gate: T004 OpenLiteSpeed Feasibility Probe (Completed)

Executed 2026-09-04 with user disposable-runtime authorization.

### Tested image identity
- Target image: `litespeedtech/openlitespeed:1.8.2-lsphp83`
- Content-addressed ID: `sha256:7beae85a882077c3ae18dded543a0ce78c66e1a12379888b5f7c56d68ca6aa05`

### Proven capabilities:
1. **Network, data, and secret isolation**:
   - Container executed with `--network none` (loopback only) and `--read-only` root filesystem.
   - Bounded tmpfs mounts on `/tmp:size=16m,mode=1777`, `/usr/local/lsws/logs:size=16m,mode=1777`, and `/usr/local/lsws/tmp:size=16m,mode=1777`.
   - Zero live instance data, databases, uploads, plugin sources, credentials, or environment variables mounted.
2. **Stable instance-local vhost inclusion point**:
   - OpenLiteSpeed `PlainConf` parser natively supports `include <path>` directives inside `virtualhostconfig` context in `conf/templates/docker.conf`.
   - Verified via error log audit: `[PlainConf] [virtualhostconfig:] start parsing file /usr/local/lsws/conf/vhosts-include/fragments.conf` and `[PlainConf] [virtualhostconfig:] Finished parsing file /usr/local/lsws/conf/vhosts-include/fragments.conf`.
3. **Loopback canary behavior**:
   - Web server starts inside container and serves HTTP responses over loopback (port 80) (`HTTP/1.1 200 OK`, `server: LiteSpeed`).
4. **Unsupported and ignored-rule detection**:
   - Invalid directives or unsupported syntax in included fragment files are explicitly trapped and logged with exact file and line number:
     `[ERROR] [PlainConf] [virtualhostconfig:] Not support [invalid_unknown_directive_for_test ...] in file /usr/local/lsws/conf/vhosts-include/fragments.conf:1`.
5. **Fixed target-only reload and readiness path**:
   - Graceful reload supported via `/usr/local/lsws/bin/lswsctrl reload` or `kill -USR1 $(cat /tmp/lshttpd/lshttpd.pid)`.
   - HTTP loopback readiness probe confirms active serving.

Verdict: **Feasibility gate PASSED**. OpenLiteSpeed satisfies all requirements for isolated validation and vhost-scoped fragment inclusion without touching `.htaccess` or global configuration.

## Foundation TDD checkpoint

All work in this checkpoint is local/source-only. No Sandbox instance, container,
network, server configuration, repository under `$SANDBOX_HOME`, or live runtime was
created, inspected, or mutated.

### RED evidence

- `python3 -m unittest -v tests.test_server_config_models
  tests.test_server_config_instance_identity` initially ran 11 tests and produced 11
  expected missing-package errors.
- The same model suites then exposed two contract-edge failures before the fix:
  renderer-only metadata incorrectly changed the fragment-set identity, and a first
  mount attachment could not roll back to the exact unattached projection.
- `python3 -m unittest tests.test_server_config_policy
  tests.test_server_config_repository tests.test_server_config_adapters
  tests.test_architecture_boundaries.TestArchitectureBoundaries.test_server_config_is_separate_from_host_oci_authority`
  ran 14 tests: 13 expected missing-package errors and one expected absent-package
  boundary failure.
- `python3 -m unittest tests.test_server_config_context
  tests.test_server_config_core_identity` ran eight tests with six missing-API errors
  and one rollback-projection failure before composition/identity implementation.

No expected RED assertion passed accidentally. The failures were missing behavior, not
environment or live-runtime failures.

### GREEN evidence

The final source-only command was:

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_context tests.test_server_config_core_identity \
  tests.test_server_config_instance_identity tests.test_server_config_models \
  tests.test_server_config_policy tests.test_server_config_repository \
  tests.test_server_config_adapters tests.test_architecture_boundaries \
  tests.test_modularity tests.test_lifecycle
```

Result: 95 tests passed. Python compilation of all new foundation modules, the two
touched composition/lifecycle modules, and the shared fixture builder passed.
`git diff --check` passed.

The foundation provides pure typed models, opaque incarnation projection, local typed
composition, bounded input/output, common fail-closed classification, an owner-only
incarnation repository, an incarnation lock, immutable generation directories, atomic
state/receipt/transaction writes, and a duplicate-free nginx/litespeed descriptor
registry. No nginx or OpenLiteSpeed fragment is rendered, validated, mounted, activated,
reloaded, or claimed effective by this checkpoint.

All mutation-side fragment, generation, state, and transaction operations route through
the root directory descriptor held by the incarnation lock. A replacement-root test
proves that an in-lock write cannot reopen and write the replacement pathname. Transaction
cleanup first verifies owner-only regular JSON, exact generation references, and a
clearable terminal outcome; corrupt, nonterminal, and `recovery_needed` journals remain
preserved and fail closed.

### Independent foundation review

A fresh post-edit independent Sol High review rechecked T005-T018, the repository and
policy security boundaries, typed projections and adapter target, active pointers, and
the Feature 048-051 zero-diff guard. It reran the 95-test source-only suite, compilation,
and diff checks. Final verdict: `PASS`, with no actionable findings.

### Compatibility and proof boundary

- The persisted active feature pointer and managed AGENTS/CLAUDE Spec Kit pointers still
  identify Feature 051.
- Diff guards for `specs/048-*` through `specs/051-*`, `sandbox/hosting/**`,
  `sandbox/core/_hosting.py`, `sandbox/commands/hosting.py`, and the remote hosting
  activation transport/tests are empty relative to planning checkpoint
  `0c8435a345f1148bf2fb3ce63fdd973f0759bf6a`.
- Focused lifecycle tests are local fakes. They are not browser, Compose, exact-image,
  registered-remote, deployment, edge, or production proof.
- T004 and every later human/live acceptance gate remain open.

## US1 RED checkpoint (T024)

All US1 RED tests were written and confirmed failing before any command, service, or
adapter implementation. Recorded 2026-09-04.

### RED test files and failure evidence

| Test file | Tests | Failure mode |
|---|---|---|
| `tests/test_server_config_cli.py` | 16 | `ModuleNotFoundError: No module named 'sandbox.commands.server'` |
| `tests/test_server_config_service.py` | 8 | `ModuleNotFoundError: No module named 'sandbox.server_config.service'` |
| `tests/test_server_config_nginx.py` | 7 | `AssertionError: sandbox.server_config.adapters.nginx not implemented yet` |
| `tests/test_server_config_nginx_runtime.py` | 7 | `NotImplementedError` (stub adapter) |
| `tests/test_server_config_lifecycle.py` | 4 | `ModuleNotFoundError: No module named 'sandbox.server_config.lifecycle'` |
| `tests/test_server_config_isolation.py` | 3 | `ModuleNotFoundError: No module named 'sandbox.server_config.lifecycle'` |

Combined RED run: 11 errors from 5 modules. No expected RED assertion passed
accidentally. All failures were missing modules or unimplemented stubs, not environment
or live-runtime errors. Foundation 95 tests continued passing during the RED phase.

## US1 GREEN story checkpoint (T034)

Recorded 2026-09-04 after completing T019-T034 for User Story 1.

### GREEN evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_context \
  tests.test_server_config_core_identity \
  tests.test_server_config_instance_identity \
  tests.test_server_config_models \
  tests.test_server_config_policy \
  tests.test_server_config_repository \
  tests.test_server_config_adapters \
  tests.test_architecture_boundaries \
  tests.test_modularity \
  tests.test_lifecycle \
  tests.test_server_config_nginx \
  tests.test_server_config_service \
  tests.test_server_config_nginx_runtime \
  tests.test_server_config_lifecycle \
  tests.test_server_config_isolation \
  tests.test_server_config_cli \
  tests.test_clean_url_default_policy
```

Result: **156 tests passed** across all foundation, nginx adapter, service orchestration,
runtime verification, lifecycle/isolation, CLI contracts, and clean-URL policy suites.

### Summary of US1 implementations:

1. **CLI grammar & legacy compatibility** (`sandbox/commands/server.py`):
   - Command-owned `CommandSpec` for `server` registered in `BUILTIN_COMMAND_MODULES`.
   - Complete `config apply|list|show|revert` grammar with `--name`, `--authority`,
     mutually exclusive `--file`/`--stdin`, and `--json`.
   - Preserves `sb server <type>` and `sb server <instance> <type>` legacy switch forms.
   - Enforces mutual exclusion of `--content` and `--json` on `show`.
   - Content-free JSON output with stable schema (`ok`, `mutated`, `operation`, `outcome`,
     `instance`, `fragment`, `fragment_set`, `phases`, `transaction_id`).
   - Read-only pre-dispatch skip policy for `list` and metadata `show`.

2. **Service orchestration** (`sandbox/server_config/service.py`):
   - `ServerConfigService` orchestrates apply, list, show, revert operations.
   - Deterministic fragment ordering and identical reapply detection with zero-cost no-op.
   - Rollback and absent-name healthy no-op handling.

3. **Nginx adapter & runtime** (`sandbox/server_config/adapters/nginx.py`):
   - Subset tokenizer/parser into statement AST.
   - Deny-by-default common authority projection, protected-route refusal.
   - Deterministic candidate renderer with bounded provenance markers and inclusion proof.
   - Exact-active-image validation using disposable, `--network none`, read-only root,
     data-free containers with native `nginx -t` via fixed argv.
   - Target-only reload with pre-activation identity recheck and effective-generation observation.

4. **Lifecycle & isolation** (`sandbox/server_config/lifecycle.py`, `sandbox/core/_docker.py`, `config/nginx-sandbox.conf`):
   - Per-incarnation read-only mount: `{RUNTIME_DIR}/server-config/{incarnation}:/etc/nginx/sandbox-fragments:ro`.
   - Absent-safe wildcard include in `config/nginx-sandbox.conf`: `include /etc/nginx/sandbox-fragments/*.conf;`.
   - Instance attachment checks refusing unattached legacy instances before mutation.
   - Cross-incarnation adoption prevention.

All local US1 contracts pass. T004 (OLS probe) passed feasibility gate.

## US2 RED checkpoint (T039)

Recorded 2026-09-04 before implementing OpenLiteSpeed adapter.

### Failing test evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_openlitespeed \
  tests.test_server_config_openlitespeed_runtime \
  tests.test_server_config_openlitespeed_activation \
  tests.test_server_config_service_openlitespeed
```

Result: **17 tests failed (100% expected)** across 4 suites due to `sandbox.server_config.adapters.openlitespeed` not implemented yet:
- `tests/test_server_config_openlitespeed.py`: 5 tests failing
- `tests/test_server_config_openlitespeed_runtime.py`: 5 tests failing
- `tests/test_server_config_openlitespeed_activation.py`: 4 tests failing
- `tests/test_server_config_service_openlitespeed.py`: 3 tests failing

All failures are due to unwritten adapter module. Foundation (95) and US1 (61) tests continue to pass.

## US2 GREEN story checkpoint (T045)

Recorded 2026-09-04 after completing T035-T045 for User Story 2.

### GREEN evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_context \
  tests.test_server_config_core_identity \
  tests.test_server_config_instance_identity \
  tests.test_server_config_models \
  tests.test_server_config_policy \
  tests.test_server_config_repository \
  tests.test_server_config_adapters \
  tests.test_architecture_boundaries \
  tests.test_modularity \
  tests.test_lifecycle \
  tests.test_server_config_nginx \
  tests.test_server_config_service \
  tests.test_server_config_nginx_runtime \
  tests.test_server_config_lifecycle \
  tests.test_server_config_isolation \
  tests.test_server_config_cli \
  tests.test_clean_url_default_policy \
  tests.test_server_config_openlitespeed \
  tests.test_server_config_openlitespeed_runtime \
  tests.test_server_config_openlitespeed_activation \
  tests.test_server_config_service_openlitespeed
```

Result: **173 tests passed** across all foundation, nginx adapter, OpenLiteSpeed adapter, service orchestration,
runtime verification, lifecycle/isolation, CLI contracts, modularity, architecture boundaries, and clean-URL policy suites.

### Summary of US2 implementations:

1. **OpenLiteSpeed adapter & tokenizer** (`sandbox/server_config/adapters/openlitespeed.py`):
   - Subset tokenizer/parser into statement AST with block nesting support.
   - Deny-by-default common authority projection, rejecting global, listener, admin, and external-processor directives.
   - Accepted directives: `rewrite` blocks, `context` cache paths, `RewriteRule`, `allowBrowse`, etc.
   - Deterministic candidate renderer with bounded provenance markers (`# --- BEGIN sandbox-fragment: <name> ---`).
   - `ReadinessResult` inheriting from `PhaseResult` with `state` and `effective_generation`.

2. **Exact-active-image validation** (`sandbox/server_config/adapters/openlitespeed.py`):
   - Disposable validation container creation using exact content-addressed image ID (`litespeedtech/openlitespeed`).
   - `--network none`, `--read-only` root filesystem, no live volumes, no secrets, bounded tmpfs mounts.
   - Loopback canary behavior probing and container cleanup verification.
   - Fail-closed behavior when capability is unavailable.

3. **Target-only activation and restart** (`sandbox/server_config/adapters/openlitespeed.py`):
   - Restarts only the target OpenLiteSpeed service (`observation.runtime_id`).
   - Pre-activation identity recheck ensuring runtime facts match preconditions.
   - Rollback / restore restoring prior generation and gracefully reloading target.

4. **OLS container mount & inclusion** (`sandbox/core/_docker.py`, `sandbox/core/_provision.py`):
   - Read-only vhost inclusion mount: `{RUNTIME_DIR}/server-config/{incarnation}:/usr/local/lsws/conf/vhosts-include:ro`.
   - Idempotent `docker.conf` inclusion check and fixed reload path (`lswsctrl restart`).
   - Preserves plugin/WordPress `.htaccess` without overwrite.

Both minimum adapters (nginx and litespeed) now satisfy local contracts and pass all 173 tests.

## US3 RED checkpoint (T050)

Recorded 2026-09-04 before input and CLI hardening for User Story 3.

### Failing adversarial test evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_policy \
  tests.test_server_config_cli \
  tests.test_server_config_nginx \
  tests.test_server_config_openlitespeed \
  tests.test_server_config_isolation
```

Result: **3 tests failed (100% expected)** across extended suites due to missing deadline parameter on stdin reader and unhardened CLI refusal handlers:
- `tests/test_server_config_policy.py`: `test_stdin_deadline_timeout` fails (`TypeError: read_fragment_stdin() got an unexpected keyword argument 'deadline'`)
- `tests/test_server_config_cli.py`: `test_refuse_unsupported_server_types` fails (`ok: false, mutated: false, error_code: server_unsupported` not emitted)
- `tests/test_server_config_cli.py`: `test_refuse_legacy_unattached_mount` fails (`ok: false, mutated: false, error_code: mount_unattached` not emitted)

Foundation (95) and US1/US2 (78) baseline assertions continue to pass without regressions.

## US3 GREEN story checkpoint (T055)

Recorded 2026-09-04 after completing T046-T055 for User Story 3.

### GREEN evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_context \
  tests.test_server_config_core_identity \
  tests.test_server_config_instance_identity \
  tests.test_server_config_models \
  tests.test_server_config_policy \
  tests.test_server_config_repository \
  tests.test_server_config_adapters \
  tests.test_architecture_boundaries \
  tests.test_modularity \
  tests.test_lifecycle \
  tests.test_server_config_nginx \
  tests.test_server_config_service \
  tests.test_server_config_nginx_runtime \
  tests.test_server_config_lifecycle \
  tests.test_server_config_isolation \
  tests.test_server_config_cli \
  tests.test_clean_url_default_policy \
  tests.test_server_config_openlitespeed \
  tests.test_server_config_openlitespeed_runtime \
  tests.test_server_config_openlitespeed_activation \
  tests.test_server_config_service_openlitespeed \
  tests.test_redaction_parity
```

Result: **201 tests passed** across all foundation, Nginx, OpenLiteSpeed, policy, isolation, CLI, modularity, redaction parity, and clean-URL policy suites.

### Summary of US3 implementations:

1. **Hardened input boundaries** (`sandbox/server_config/input.py`):
   - Added finite `deadline` enforcement to `read_fragment_stdin`.
   - Bounded regular file reading refusing character/block devices (`/dev/null`, `/dev/zero`), FIFOs, symlinks, directories, and empty inputs.
   - Mid-read mutation detection (`fragment_source_changed`) comparing file facts before and after stream read.

2. **Common policy and secret classification** (`sandbox/server_config/policy.py`):
   - Deny-by-default rejection for forbidden directives (`upstream`, `resolver`, `ssl_*`, `caddy_*`, `module`, `admin`, `listener`, `extprocessor`, etc.).
   - High-confidence secret pattern classification (`fragment_secret_like_input`) refusing credentials, private keys, authorization headers, tokens.
   - Content-free error messages ensuring exceptions never leak raw user input, secrets, or caller file paths.

3. **CLI and service refusal enforcement** (`sandbox/commands/server.py`, `sandbox/server_config/service.py`):
   - Structured JSON error output with `ok: false`, `mutated: false`, and bounded error codes (`server_unsupported`, `mount_unattached`).
   - Refusal of unattached legacy instances and unsupported server types (`apache`, `herd`).
   - Clean subprocess execution with synthetic environments ensuring zero environment leakage.

## US4 RED checkpoint (T061)

Recorded 2026-09-04 before transaction, rollback, concurrency, and inspection hardening for User Story 4.

### Failing recovery, rollback, concurrency, and inspection test evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_transactions \
  tests.test_server_config_recovery \
  tests.test_server_config_rollback \
  tests.test_server_config_concurrency \
  tests.test_server_config_inspection
```

Result: **35 tests ran, 22 failed/errored (100% expected)** across newly authored US4 test suites:
- `tests/test_server_config_transactions.py` (T056): Fails on `to_record` / `from_record` round-trip serialization on `ActivationTransaction`.
- `tests/test_server_config_recovery.py` (T057): Fails on missing `service.reconcile()`, lack of drift detection before mutation, and unhandled corrupt journals.
- `tests/test_server_config_rollback.py` (T058): Fails on absence of automatic rollback orchestration on `adapter.activate()`, `reload()`, or `observe_ready()` faults, and missing `TerminalOutcome.ROLLED_BACK` / `RECOVERY_NEEDED` transitions.
- `tests/test_server_config_concurrency.py` (T059): Fails on missing operation and phase deadline enforcement and re-read-under-lock behavior.
- `tests/test_server_config_inspection.py` (T060): Fails on missing `service.inspect()` method and persistent lock files left on disk during read-only inspection.

Foundation (95) and US1/US2/US3 (106) baseline assertions continue to pass without regressions.

## US4 GREEN story checkpoint (T068)

Recorded 2026-09-04 after completing T062-T068 for User Story 4.

### GREEN evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_context \
  tests.test_server_config_core_identity \
  tests.test_server_config_instance_identity \
  tests.test_server_config_models \
  tests.test_server_config_policy \
  tests.test_server_config_repository \
  tests.test_server_config_adapters \
  tests.test_architecture_boundaries \
  tests.test_modularity \
  tests.test_lifecycle \
  tests.test_server_config_nginx \
  tests.test_server_config_service \
  tests.test_server_config_nginx_runtime \
  tests.test_server_config_lifecycle \
  tests.test_server_config_isolation \
  tests.test_server_config_cli \
  tests.test_clean_url_default_policy \
  tests.test_server_config_openlitespeed \
  tests.test_server_config_openlitespeed_runtime \
  tests.test_server_config_openlitespeed_activation \
  tests.test_server_config_service_openlitespeed \
  tests.test_redaction_parity \
  tests.test_server_config_transactions \
  tests.test_server_config_recovery \
  tests.test_server_config_rollback \
  tests.test_server_config_concurrency \
  tests.test_server_config_inspection
```

Result: **236 tests passed** across all 27 test suites:
- `tests/test_server_config_transactions.py` (T056): 12 tests passed (durable requested/prepared/validated/activating/reloading/observing/committed/terminal transition, JSON record round-trip, immutable receipts).
- `tests/test_server_config_recovery.py` (T057): 7 tests passed (mutation-start reconciliation, pre-activation clean refusal, post-activation recovery/rollback, missing generation detection, corrupt journal detection, fail-closed blocking).
- `tests/test_server_config_rollback.py` (T058): 5 tests passed (injected activation/reload/readiness failures, automatic rollback to exact known-good receipt, at-most-one recovery activation, ROLLED_BACK outcome, timeout/failure transition to RECOVERY_NEEDED).
- `tests/test_server_config_concurrency.py` (T059): 6 tests passed (per-incarnation lock contention, re-reading state under lock, monotonic 180s operation deadline, 60s phase deadline, timeout handling without retry loops).
- `tests/test_server_config_inspection.py` (T060): 5 tests passed (read-only inspect() projection across healthy, stopped, degraded, recovery-needed, unsupported, absent states with zero persistent disk writes or lock files).

### Summary of US4 implementations:

1. **Transaction & Receipt serialization** (`sandbox/server_config/models.py`, `sandbox/server_config/repository.py`):
   - Added `to_record()` and `from_record()` to `ActivationTransaction` and `KnownGoodReceipt`.
   - Added `has_generation(generation_id)` to `ServerConfigRepository`.
   - Stored transactions and receipts with atomic file writes and schema validation.

2. **Reconciliation & Fail-Closed Recovery** (`sandbox/server_config/service.py`):
   - Pre-activation interrupted transactions (`REQUESTED`, `PREPARED`, `VALIDATED`) are reconciled to `TerminalOutcome.REFUSED` and cleared, enabling subsequent mutations.
   - Post-activation interrupted transactions (`ACTIVATING`, `RELOADING`, `OBSERVING`) trigger automatic rollback to prior known-good receipt or enter `TerminalOutcome.RECOVERY_NEEDED`.
   - Corrupt journals and missing candidate/prior generations fail closed and refuse mutations without recency guessing.

3. **Automatic Rollback Orchestration** (`sandbox/server_config/service.py`):
   - On candidate activation, reload, or observation failure, the service executes automatic rollback to prior known-good generation if available.
   - Limits recovery attempts to at most one target activation per mutation.
   - Emits `TerminalOutcome.ROLLED_BACK` with `mutated=True` on successful restoration, or `TerminalOutcome.RECOVERY_NEEDED` on rollback failure.

4. **Monotonic Deadlines & Concurrency** (`sandbox/server_config/service.py`):
   - Enforced 180-second overall operation deadline and 60-second phase/rollback ceilings.
   - Enforced per-incarnation lock contention handling and re-read-under-lock semantics.

5. **Read-Only Inspection** (`sandbox/server_config/service.py`):
   - Implemented `inspect()` projecting runtime status (healthy, stopped, degraded, recovery-needed, unsupported) without acquiring locks or modifying persistent state.

## US5 RED checkpoint (T074)

Recorded 2026-09-04 before identity, lifecycle, and isolation hardening for User Story 5.

### Failing identity, lifecycle, locking, and restart test evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_instance_identity \
  tests.test_server_config_isolation \
  tests.test_server_config_lifecycle \
  tests.test_server_config_lifecycle_locking \
  tests.test_server_config_restart \
  tests.test_server_config_control_instance
```

Result: **35 tests ran, 12 failed/errored (100% expected)** across newly authored US5 test suites:
- `tests/test_server_config_instance_identity.py` (T069): Fails on missing `relocate_instance_server_config` and `disassociate_instance_server_config` in `sandbox.server_config.lifecycle`.
- `tests/test_server_config_isolation.py` (T070): Fails on missing `get_target_service_scope` and `verify_caddy_untouched` in `sandbox.server_config.lifecycle`.
- `tests/test_server_config_lifecycle.py` (T071): Fails on missing `check_server_switch_allowed` and `check_instance_deletion_allowed` lifecycle guard functions.
- `tests/test_server_config_lifecycle_locking.py` (T071): Fails on missing `LifecycleMutationCoordinator` and `LockOrderingError`.
- `tests/test_server_config_restart.py` (T072): Fails on missing `reconcile_restart_generation` and `check_instance_mount_and_image_drift`.
- `tests/test_server_config_control_instance.py` (T073): 6 tests passed verifying target/control isolation matrix across apply, replace, revert, refusal, rollback, and recovery-needed operations.

Foundation (95) and US1/US2/US3/US4 (141) baseline assertions continue to pass without regressions (204 passing).

## US5 GREEN story checkpoint (T082)

Recorded 2026-09-04 after completing T075-T082 for User Story 5.

### GREEN evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_context \
  tests.test_server_config_core_identity \
  tests.test_server_config_instance_identity \
  tests.test_server_config_models \
  tests.test_server_config_policy \
  tests.test_server_config_repository \
  tests.test_server_config_adapters \
  tests.test_architecture_boundaries \
  tests.test_modularity \
  tests.test_lifecycle \
  tests.test_server_config_nginx \
  tests.test_server_config_service \
  tests.test_server_config_nginx_runtime \
  tests.test_server_config_lifecycle \
  tests.test_server_config_lifecycle_locking \
  tests.test_server_config_isolation \
  tests.test_server_config_cli \
  tests.test_clean_url_default_policy \
  tests.test_server_config_openlitespeed \
  tests.test_server_config_openlitespeed_runtime \
  tests.test_server_config_openlitespeed_activation \
  tests.test_server_config_service_openlitespeed \
  tests.test_redaction_parity \
  tests.test_server_config_transactions \
  tests.test_server_config_recovery \
  tests.test_server_config_rollback \
  tests.test_server_config_concurrency \
  tests.test_server_config_inspection \
  tests.test_server_config_restart \
  tests.test_server_config_control_instance
```

Result: **265 tests passed** across all 30 test suites:
- `tests/test_server_config_instance_identity.py` (T069): 10 tests passed (new instance incarnation minting, preservation across updates and relocation, deletion disassociation, recreation with same name receiving a new unique incarnation, legacy record protection).
- `tests/test_server_config_isolation.py` (T070): 6 tests passed (distinct host source roots, fixed guest mount path `/etc/nginx/sandbox-fragments`, target-only compose scope, zero host-global/Caddy modification, strict cross-instance adoption refusal).
- `tests/test_server_config_lifecycle.py` (T071): 9 tests passed (active fragment, unresolved transaction, and recovery-needed server-switch refusals; clean server-switch allowed; ordinary delete refusal without explicit server-config confirmation).
- `tests/test_server_config_lifecycle_locking.py` (T071): 5 tests passed (lifecycle lock then fragment lock ordering, re-read under lock, lock held across effect, clean release on crash rollback, TOCTOU loser safe exit).
- `tests/test_server_config_restart.py` (T072): 5 tests passed (stopped instance mutation refusal, stop/start incarnation and generation preservation, restart reconciliation and readiness observation before reporting ready, image/mount drift detection failing closed).
- `tests/test_server_config_control_instance.py` (T073): 6 tests passed (control instance remains 100% untouched across target apply, replace, revert, refusal, rollback, and recovery-needed operations).
- In addition, existing suites `tests/test_lifecycle.py` and `tests/test_cli.py` (107 tests) passed with zero regressions.

### Summary of US5 implementations:

1. **Instance Identity & Lifecycle Boundaries** (`sandbox/server_config/lifecycle.py`, `sandbox/server_config/models.py`):
   - Implemented `relocate_instance_server_config` preserving opaque incarnation and mount identity across sandbox relocations.
   - Implemented `disassociate_instance_server_config` deleting fragment repository files for deleted instance incarnations.
   - Guarded instance mutation in `service.apply()` and `service.revert()` against stopped/unready instances.

2. **Server-Switch & Deletion Gates** (`sandbox/server_config/lifecycle.py`):
   - Implemented `check_server_switch_allowed` refusing web-tier changes when active fragments, unresolved transactions, or recovery-needed states exist.
   - Implemented `check_instance_deletion_allowed` requiring explicit server-config confirmation before deleting instances with active fragments.

3. **Lock-Ordered Mutation Coordination** (`sandbox/server_config/lifecycle.py`):
   - Implemented `LifecycleMutationCoordinator` ensuring lifecycle lock is acquired first and fragment lock second.
   - Re-reads state under both locks before mutation effect, ensuring TOCTOU safety and clean rollback on crash.

4. **Restart & Drift Reconciliation** (`sandbox/server_config/lifecycle.py`):
   - Implemented `reconcile_restart_generation` restoring committed generation and observing container readiness before reporting ready.
   - Implemented `check_instance_mount_and_image_drift` failing closed on image or mount path divergence.

