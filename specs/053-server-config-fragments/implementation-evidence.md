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

## Open gate: T004 OpenLiteSpeed feasibility

T004 is not run and remains open. No disposable instance, exact-image validator,
container, runtime, or live configuration was created or changed. The parent instruction
allows safe source-only work to proceed while retaining this as a hard gate before any
claim of OpenLiteSpeed feasibility, runtime proof, release readiness, or live acceptance.

If the later explicitly authorized probe cannot prove one stable instance-local vhost
inclusion point, network/data/secret-isolated boot and canary, ignored-rule detection,
and a fixed target-only reload/readiness path, implementation must stop for design
revision. No `.htaccess`, host-global, raw Docker, or assumed-image fallback is allowed.

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

## US1 partial implementation checkpoint

Recorded 2026-09-04 after T025, T027, and T028 implementation.

### GREEN evidence

```text
.cli-venv/bin/python -m unittest \
  tests.test_server_config_context tests.test_server_config_core_identity \
  tests.test_server_config_instance_identity tests.test_server_config_models \
  tests.test_server_config_policy tests.test_server_config_repository \
  tests.test_server_config_adapters tests.test_architecture_boundaries \
  tests.test_modularity tests.test_lifecycle \
  tests.test_server_config_nginx tests.test_server_config_service
```

Result: **110 tests passed** in 10.3 seconds. The 95 foundation tests plus 7 nginx
adapter tests and 8 service orchestration tests are green.

### Implemented modules

- `sandbox/commands/server.py` (T025): Feature-owned `CommandSpec` with config grammar
  (`apply|list|show|revert`), legacy switch preservation (`sb server <type>`,
  `sb server <instance> <type>`), `--json` flag, `--stdin`/`--file` mutual exclusion,
  `--authority` default, and `predispatch_policy` for read-only operations.
- `sandbox/server_config/service.py` (T027): Apply/list/show/replace/revert/no-op
  orchestration with identical-reapply detection, deterministic fragment ordering,
  adapter policy/render delegation, and repository state persistence.
- `sandbox/server_config/adapters/nginx.py` (T028): Subset tokenizer/parser, common
  policy projection via `validate_common_authority`, deterministic candidate renderer
  with provenance markers, inclusion proof, protected base-route detection, and
  duplicate/conflict detection.

### Still RED (expected)

- `tests/test_server_config_cli.py`: 16 tests (T025 module exists but T026 wiring and
  the test's expected `ServerCommand` class API differ from actual implementation)
- `tests/test_server_config_nginx_runtime.py`: 7 tests (runtime methods are stubs)
- `tests/test_server_config_lifecycle.py`: 4 tests (lifecycle module absent)
- `tests/test_server_config_isolation.py`: 3 tests (lifecycle module absent)

### Remaining US1 tasks

T026, T029, T030, T031, T032, T033, and T034 remain open.
