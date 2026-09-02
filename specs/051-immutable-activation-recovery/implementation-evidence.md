# Feature 051 Implementation Evidence

## RED-first waiver

The task owner explicitly required all production and documentation work before test
authoring. That instruction waived the normal RED-first order in `tasks.md`.

No RED suite was observed. T024 is complete only as the durable record of that explicit
waiver; it is not evidence of an observed RED failure.

## Observed validation

The T060 repair passes now bind registered target identity, Compose project, and a
machine-keyed, target-scoped opaque HMAC over the complete private render; keep the raw
render, raw hash, master key, and derived key out of state/output; return only a closed
allowlisted projection; execute
`compose up` from those exact private bytes; verify target-scoped protected per-service
Compose configuration-hash identities during replacement and every fresh running/recovery
observation; and refuse top-level configs/secrets and external networks
without snapshot authority. Commands, entrypoints, arbitrary labels/annotations, health
checks, URLs, logging values, extensions, inline content, rendered map keys, and all
duplicate-name/overlapping private values stay out of remote output. The owner-only machine
master remains local; only a machine/target-derived key crosses private stdin. Raw `ps`
labels, inspect environment/arbitrary labels, and raw Compose hashes remain remote.
Custody fully decodes and canonical-byte compares the retained proof, exact stage-ledger
authority, and record revision under target -> host -> stage locks before policy admission.
The stage ledger rejects malformed proofless or proved records, mismatched active ownership,
invalid phase/effect/process/cleanup/result relationships, negative or over-limit counters,
and overlapping record/proof/tombstone/pin authority.
Initializer cleanup requires a target/image/declaration-bound name and owner label,
rollback rejects a changed prior Compose project before effects, and early recovery uses a
persisted closed target/project/service context even when no candidate exists. Rollback
grants use a bound Ed25519 public key, machine bundles have owner/no-follow/single-link/
stability checks, persisted target/tombstone schemas are closed, and reachability-only edge
evidence refuses as `edge_incomplete`. The signing key remains outside Feature 051. The
fresh independent source review is complete; live activation/rollback proof remains required.

Observed after the final configuration-identity and retained-proof custody repair pass
(2026-09-02):

- Focused Feature 051, real private Compose helper, and architecture tests:
  **103 tests OK in 21.239s**.
- Feature 051 plus Feature 050 staging coordination: **120 tests OK in 29.062s**.
- Complete Feature 050 synchronized selector: **73 tests OK in 12.322s**.
- Existing hosting and Feature 048 compatibility tests: **282 tests OK in 14.202s**.
- Synchronized config, CLI, and runtime-mode tests: **132 tests OK in 115.797s**.
- Isolated MCP composition/resource/secret/redaction tests: **43 tests OK in 0.589s**.
- Modularity inventory and architecture boundaries: **23 tests OK in 7.346s**.
- Targeted `compileall` and `git diff --check`: **passed**.

One concurrent config/CLI/runtime attempt timed out only
`TestResolutionGate.test_no_main_in_help_command_list` after 90 seconds while other gates
were running. The exact test passed alone in 56.725 seconds, and the complete 132-test gate
then passed sequentially as recorded above. It is not counted as a green concurrent run.

An additional repository-wide discovery run was not green: **4,834 tests ran with
14 failures, 90 errors, and 21 skips**. Representative failures in untouched Hermes,
migration, generic-init, remote-guidance, skill-mirror, and remote-pool tests reproduce
on the unmodified `latest` checkout. They are recorded as existing repository baseline
debt, not as passing Feature 051 evidence. The Feature 051 acceptance and compatibility
gates above remain independently green.

- Focused Feature 051 tests, the real private Compose helper tests, and the Feature 051
  architecture boundary tests: **70 tests OK in 10.367s**.
- Narrow compatibility tests covering existing hosting, all Feature 048 host-recovery
  models/policy/repository/service/CLI behavior, command composition, subprocess guards,
  and architecture boundaries: **282 tests OK in 12.451s**.
- Targeted `compileall` over activation, recovery, transport, hosting, and their focused
  tests: **passed**.
- `git diff --check`: **passed**.
- Production import scan: the activation package imports only its models, Feature 050
  staging contracts, Feature 048 recovery contracts, and the Python standard library.
  It imports no credential, broker, helper, registry, pull, or build authority. Literal
  `pull` and `build` occurrences are limited to refusal/projection fields and the runtime
  no-effect arguments `--pull never` and `--no-build`.

These are local focused and compatibility results. They are not live registered-host,
edge, rollback, deployment, or production evidence.

## Post-merge validation

After merging `origin/latest` into the feature branch without a rebase or force-push:

- The same focused Feature 051 and architecture set: **70 tests OK in 10.682s**.
- The same hosting and Feature 048 compatibility set: **282 tests OK in 12.757s**.
- The synchronized per-repository config, CLI, and runtime-mode modules:
  **132 tests OK in 176.275s**.
- The hostile Git environment regression for repository descriptor selection:
  **1 test OK in 0.288s**.
- Targeted `compileall` and `git diff --check`: **passed**.
- Automated Sol High merge review and the repository-identity repair review: **GO**.

The merge review found and repaired inherited/global Git configuration influence on
shared-descriptor selection. Repository identity now uses a closed subprocess
environment plus a repository-local origin lookup. These remain local source and test
results, not remote runtime or production evidence.

## Review evidence

- Automated Sol High final source review: **GO**.
- Automated Sol High review of the post-failure production delta: **GO**.
- Automated Sol High review after configuration-identity, retained-proof custody,
  complete ledger decoding, bounded-counter, and exact lease-schema hardening: **GO**.

Both are automated reviews. Neither is the human security review required by T060.

## Production provenance

- `sandbox/hosting/images/activation/models.py`: closed policy, authority, request,
  transaction, proof-pin, init, running, generation, rollback, recovery, and result
  values with exact public Feature 049/050 equality validation.
- `sandbox/hosting/images/activation/policy.py`: machine-bound narrowing, zero-init
  adoption, ordered init coverage, deterministic pre-forward rollback subjects, and
  machine grant validation.
- `sandbox/hosting/images/activation/repository.py`: nested-only activation codec and
  candidate validator; phase-aware Feature 050 custody coordination; admission storage
  reservation; immutable result/tombstone replay; recovery provisional/result bounds;
  and exhaustive recovery promotion rules. It has no outer filesystem writer.
- `sandbox/hosting/recovery/repository.py`: sole outer `hosts.json` parser, locker, and
  atomic writer; nested activation seeding/read/CAS transaction port; unknown-field
  preservation; and shared target mutation ownership.
- `sandbox/core/_hosting.py`: explicit fail-closed target-mutation capability registry.
- `sandbox/hosting/images/activation/init_runner.py` and
  `sandbox/transports/remote_hosting_activation.py`: create-before-start inspection,
  durable per-step effect fencing, independently observed target/runtime/image identity,
  bounded private Compose value selection, closed subprocess environments, deadlines,
  output bounds, cancellation, termination, and no-build/no-pull replacement.
- `sandbox/hosting/images/activation/runtime_observer.py` and
  `sandbox/hosting/images/activation/service.py`: exact rendered/local/running topology,
  platform, health, and edge proof plus the shared activate/adopt/rollback state machine.
- `sandbox/hosting/recovery/models.py`, `policy.py`, and `service.py`: additive read-only
  Feature 048 activation observation with exact-new/prior/neither/ambiguous classes.
- `sandbox/commands/hosting.py` and `sandbox/cli.py`: static `host image
  activate|adopt|rollback|recover` dispatch. `host image recover` remains distinct from
  failed-apply `host recover`.
- `docs/remote-hosting.md` and `docs/remote-hosting-implementation.md`: operator commands,
  authority boundaries, state ownership, and proof-level distinctions.

## Focused test provenance

- `tests/fixtures/hosting_image_activation.py`: closed Feature 049/050 fixtures,
  activation authority, rollback subject/grant, forbidden witnesses, effect fakes, crash
  points, and target-mutation race support.
- `tests/test_hosting_image_activation_models.py` and
  `tests/test_hosting_image_activation_policy.py`: caller non-authority, exact equality,
  closed schema, narrowing, adoption, and substitution refusal.
- `tests/test_hosting_image_activation_runtime.py` and
  `tests/test_hosting_image_activation_init.py`: exact topology/platform/health,
  independent identities, inspect-before-start, private input, bounded completion,
  cleanup, crash, and uncertainty mechanisms.
- `tests/test_hosting_image_activation_repository.py` and
  `tests/test_hosting_image_activation_recovery.py`: custody replay/release, nested CAS,
  capacity/retention, state invariants, terminalization, recovery crash resume, and the
  exhaustive activation/rollback observation matrix.
- `tests/test_hosting_image_activation_service.py`: activation, zero-effect adoption,
  rollback, edge, ordered init, terminal replay, and generation commit behavior.
- `tests/test_hosting_image_activation_races.py`: real shared-port loser/no-effect pairs,
  capability refusal, lock order, sole outer writer, and sibling preservation.
- `tests/test_hosting_image_activation_cli.py`,
  `tests/test_hosting_image_activation_private_source.py`, and
  `tests/test_architecture_boundaries.py`: static command separation, selector refusal,
  real private-helper refusal/redaction matrix, synthetic child environments, narrow
  exports, and forbidden import/writer boundaries.

## Remaining gates

- T060 human review remains open. It must review trust and credential unreachability,
  proof-custody TOCTOU/lock/crash safety, two-observation recovery, init and edge
  uncertainty, shared-owner races, rollback grants, state secrecy, and legacy behavior.
- Live registered-host, edge, rollback, deployment, and production validation remains
  open and unattempted. T061 is complete because this limitation is recorded explicitly;
  it does not claim live readiness.
- No live remote, edge, registry, credential, secret, deployment, or production mutation
  was performed by the recorded validation.

## Task evidence mapping

The supplied focused, compatibility, compile, diff, import-scan, and automated-review
results complete T032, T037, T043, T049, T052, T056, T059, T071, T078, T088, T096,
T101, T106, T110, T112, T114, T121, T128, T133, T139, and T146. T024 is complete solely
as the RED-first waiver record.
T061 is complete solely as the explicit live-gate record. T060 remains open.
