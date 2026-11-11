# Tasks: Shared server secret use, full source increment

**Input**: Expanded spec.md, plan.md, research.md, data-model.md and contracts/authenticated-consumption-v1.md. Generated through installed speckit-tasks setup and active template; user execution order overrides template test-first examples.

**Status**: All T033-T063 pending. T001-T032 and their original evidence remain verbatim in [history](history/disabled-groundwork-20261111/tasks.md). Historical completed boxes are not completion of this increment. Original helper reports and the prior reported 27 failures remain separate, unrerun evidence. No tests/builds/checks were run by the design agent.

One root owner acf08da6 integrates and accepts. This document schedules source work, not new agents or live operations. All source+docs precede short supported CLI checks, then focused regressions. Each executed check requires explicit <=60-second timeout and actual walltime. No full suites, builds, soak, secrets/metadata/native store, network, runtime/jobs, deployment, migration or GS retry authorized. Installation lifecycle/remote.py and sibling085/088 remain exclusive and untouched.

## Phase 1: Setup

- [ ] T033 Record original dirty ownership and historical evidence in specs/086-shared-server-secret-use/history/disabled-groundwork-20261111/; preserve earlier logs and do not treat original reviewed scope as expanded acceptance.
- [ ] T034 Arrange independent expanded PRD-scope/spec/design review of specs/086-shared-server-secret-use/spec.md and contracts/authenticated-consumption-v1.md through root; apply supported findings without rewriting historical prd.md or claiming original PASS covers this expansion.

## Phase 2: Foundation

- [ ] T035 Implement immutable closed records and canonical nonsecret intent in sandbox/hosting/shared_credentials/models.py; bind every accepted installation field, actor, project, request, source/artifact/target/copy/incarnation, mapping/destination/consumer, revisions and finite lifetime; reject client authority and provider values.
- [ ] T036 Implement accepted late-import read/validate adapter in sandbox/hosting/shared_credentials/installation.py; propagate InstallationBindingError codes, compare FULL binding and capability version, never initialize IDs; coordinate with exclusive installation owner, no binding.py/remote.py edits.
- [ ] T037 Extend sandbox/jobs/registry.py and sandbox/jobs/models.py with versioned credential-operation tables and public transaction APIs for unique scoped request+intent, frozen preflight refusal, committed admission, status/cancel, step fences and retained tombstones; preserve generic job behavior and unknown outcomes.
- [ ] T038 Implement private issuer and server admission constructors in sandbox/hosting/shared_credentials/admission.py; only real authenticated receipt plus retained committed row can construct a credential operation; restart path validates retained admission/current policy/worker identity before fresh private proof.

## Phase 3: US1 honest capability and clean client

**Independent criterion**: US1 acceptance scenarios in spec.md and the corresponding quickstart.md cases. Source and installed outcomes are reported separately.

- [ ] T039 [US1] Retain immutable actual ControlGate admission in mcp/wp-server/tailnet_access.py and mcp/wp-server/server.py request.state; preserve gate decisions, bind tailnet login/node, token service provenance without fabricated human, ignore supplied actor headers/body.
- [ ] T040 [US1] Implement fixed authenticated client in sandbox/transports/hosting_credentials.py using existing selected remote transport/auth contract; tokenless tailnet stays supported, no owner bearer distribution, authenticated request-key POST /hosting/credentials/lookup on lost responses without operation ID, retained lookup before mutable metadata resolution, no SSH/self-remote.
- [ ] T041 [US1] Register parser/command handling in sandbox/cli.py and sandbox/commands/manifest.py and integrate sandbox/hosting/credential_selection.py; preserve static validate, negotiate installed capability rather than unconditional source refusal, bounded fixed errors and personal-local dispatch unchanged.

## Phase 4: US2 real server consumption

**Independent criterion**: US2 acceptance scenarios in spec.md and the corresponding quickstart.md cases. Source and installed outcomes are reported separately.

- [ ] T042 [US2] Implement sandbox/hosting/shared_credentials/service.py admission sequence: authenticate/resolve installation+nonsecret refs, atomic original lookup/reserve, all-required-provider preflight, durable refusal or genuine committed admission, then lifecycle/source/target authority and effects. No staging/claims/generation before preflight/admission.
- [ ] T043 [US2] Implement callback-only registered revision-bound broker entry in sandbox/secrets/service.py and sandbox/hosting/shared_credentials/broker.py; preflight names/status only, private grants at committed admission, consume checks full binding/policy/source/profile/mapping/recipient/deadline/cancel/fence; no raw getter or generic escalation changes.
- [ ] T044 [US2] Implement sandbox/hosting/shared_credentials/consumers.py fixed in-process provider clients with explicit broker credentials and typed actions; disable ambient/local fallback, raw provider responses and secret exception chains; preserve server owner/sudo through existing lifecycle executor.
- [ ] T045 [US2] Implement sandbox/hosting/shared_credentials/application.py and sandbox/hosting/shared_credentials/adapters/ trusted application credential-channel-v1 source and fixed runtime launcher integration; reviewed installation-owned protected runtime-secret facility/minimal explicit application environment, exact artifact/recipient binding, owner-only permissions, bounded retention/cleanup and backup/export exclusions. No arbitrary staging/config/temp files or client/log/image leakage; no ambient env inheritance.
- [ ] T046 [US2] Inspect the actual required application source and exact committed manifest/artifact through authorized source inputs, then select the registered standard application environment/runtime-secret profile and verify its real launcher support, with no Hub repository edits or mandatory socket rewrite. Root has authorized no application-repository edits; no guessed GS mappings, no claiming getenv images compatible. This required source integration remains incomplete until the actual application adapter is wired.
- [ ] T047 [US2] Integrate capable stack/hosting driver in sandbox/commands/host_stack.py and sandbox/commands/hosting.py against the same admitted operation, target+artifact and broker; bypass legacy provider render_env_file only via real app channel; preserve separate generated delivery and every existing Docker/runtime choke point.
- [ ] T048 [US2] Register authenticated credential operation/inventory/lookup/cancel routes including closed request-key lookup in mcp/wp-server/server.py through sandbox/hosting/shared_credentials/manifest.py; capability advertises only real installed composition, body/response bounds and strict fields; coordinate installation discovery owner without editing their route.

## Phase 5: US3 refusal, replay and recovery

**Independent criterion**: US3 acceptance scenarios in spec.md and the corresponding quickstart.md cases. Source and installed outcomes are reported separately.

- [ ] T049 [US3] Complete sandbox/hosting/shared_credentials/service.py and sandbox/jobs/registry.py replay behavior: frozen missing-key refusal survives source repair, same accepted intent retains operation, changed intent conflicts, explicit linked retry only from known no-effect refusal, uncertain admission/effects inspect original identity and never auto-redispatch.
- [ ] T050 [US3] Connect existing durable job/recovery service APIs in sandbox/hosting/shared_credentials/service.py without creating a generic scheduler; retain original job/operation identity and step checkpoint, serialize cancel against next effect start, unknown crash outcome requires inspection rather than another identity.

## Phase 6: US4 full binding at consumption and effects

**Independent criterion**: US4 acceptance scenarios in spec.md and the corresponding quickstart.md cases. Source and installed outcomes are reported separately.

- [ ] T051 [US4] Complete consume/effect boundaries in sandbox/hosting/shared_credentials/broker.py and service.py: re-read full installation policy/capabilities, exact registered source/profile revisions, immutable actor+intent+artifact+destination and current target/source fence; prevent later effects on revoke/drift/expiry and preserve separate concurrent actor grants.
- [ ] T052 [US4] Finalize application channel cleanup and safe errors in sandbox/hosting/shared_credentials/application.py and consumers.py for provider denial, malformed response, app failure, timeout and cancellation; no raw stdout/stderr, traces, provider bodies, value-derived lengths/fingerprints or retained credential buffers.

## Phase 7: US5 inventory, generation and compatibility

**Independent criterion**: US5 acceptance scenarios in spec.md and the corresponding quickstart.md cases. Source and installed outcomes are reported separately.

- [ ] T053 [US5] Implement admitted names/status-only inventory in sandbox/hosting/shared_credentials/service.py and sandbox/commands/hosting.py using broker safe projection; unavailable remains distinct from missing, no metadata mode or laptop fallback.
- [ ] T054 [US5] Complete separate generated authority in sandbox/commands/host_stack.py and sandbox/hosting/shared_credentials/service.py; successful provider preflight+committed admission first, generate only explicit generated mappings, retain existing values and local compatibility, never invent/overwrite provider credentials.

## Phase 8: Docs and source implementation barrier

- [ ] T055 Update README.md, docs/remote-hosting.md and skills/secret-inspection/SKILL.md with supported final CLI spelling, credential protocol/revision evidence, owner rights, exact custody/app adapter requirements, uncertainty recovery and installed rollout gates; historical refusal-only statements must not describe active source.
- [ ] T056 Root reviews complete diff against specs/086-shared-server-secret-use/contracts/authenticated-consumption-v1.md and confirms every source/docs task above complete before checks/regression authoring; do not mark complete with missing actual app integration, synthetic presence or placeholder callbacks.

## Phase 9: Short observed workflows, then focused regressions

- [ ] T057 Approved short helper exercises supported synthetic CLI and real in-process server-service paths from specs/086-shared-server-secret-use/quickstart.md, each command timeout <=60 seconds, recording actual walltime and full external logs; no live/network/secret/runtime/jobs/deploy, no builds/full suites/soak.
- [ ] T058 After observed workflows, author tests/test_host_server_credentials_admission.py for actual gate receipt propagation, durable commit-only construction, token provenance, duplicate/concurrent replay, frozen refusal/linked retry, cancel/expiry/effects_unknown; synthetic subprocess environments only.
- [ ] T059 After observed workflows, author tests/test_host_server_credentials_broker.py and tests/test_host_server_credentials_application.py for real intended callback invocation, exact recipient/artifact/source/profile/mapping/policy drift, confidential-channel recipient substitution, provider failure and no prohibited output/storage; mocks must never establish installed authority.
- [ ] T060 Update tests/test_host_server_credentials_cli.py, tests/test_host_server_credentials.py and tests/test_host_credential_selection.py for capable dispatch plus old-install refusal/static/personal compatibility, body bounds/unknown fields and current runtime revision format. Preserve prior logs; corrected prior version trap is not a passing rerun.
- [ ] T061 Approved helper runs only focused meaningful checks covering T058-T060 with explicit <=60-second timeout per command, counts/first failure <=40 lines/walltime/full log path recorded beside specs/086-shared-server-secret-use/quickstart.md evidence references; do not run full suites/builds/soak or claim unavailable gates passed.

## Phase 10: Review and handoff

- [ ] T062 Root arranges independent current-source review against specs/086-shared-server-secret-use/spec.md and contracts/authenticated-consumption-v1.md, with actual short-check outcomes and explicit unverified installed acceptance; fix findings and update affected checks only.
- [ ] T063 Root records source completion versus installed/runtime acceptance separately in specs/086-shared-server-secret-use/quickstart.md; actual rollout/deploy/credential access/GS inspection or replay needs its own authority and installed revision evidence. No enabling from synthetic tests alone.

## Dependencies and parallel opportunities

T033-T038 foundation -> US1 T039-T041 -> US2 T042-T048 -> US3 T049-T050 -> US4 T051-T052 -> US5 T053-T054 -> T055-T056 source/docs barrier -> T057 observed workflows -> T058-T060 regressions -> T061 focused helper checks -> T062 independent review -> T063 honest handoff. Installation binding.py is supplied by its exclusive owner before capable integration. Actual application adapter T046 is mandatory for the relevant app outcome, not a deferred optional follow-up. No source tests or fabricated capabilities replace that dependency.

No [P] markers: service, broker and server integration share mutable files. Potential independently owned authoring after T057: T058 admission file, T059 broker/application files and T060 CLI files, if root explicitly allocates writers. This packet authorizes no delegates. Each story can be evaluated through its own service boundary once its shared prerequisites exist; complete all stories before running feature checks.

## Implementation strategy

Deliver the real authenticated capable path as one source increment. Static/refusal compatibility remains valuable but is not the MVP acceptance for the expanded goal. Preserve original IDs and historical evidence. No task may be checked from a proposed interface, test double or copied manifest. Installed enablement stays gated until separately authorized, reviewed runtime evidence exists.

Review corrections D1-D3: T045/T046 implement approved intended-recipient environment/runtime-secret delivery, not an app socket rewrite. T047/T050 use the existing RecoveryRepository target_mutation_port via a registered shared_credentials local adapter; target lock precedes short jobs CAS and stays held across each effect, including distinct same-owner operations, with source-writer authority separately checked. T058/T060 cover request-key lookup after lost initial response and source disappearance; T059 covers target-wide contention and facility permissions/export/cleanup. Old SSH lease alone is insufficient.
