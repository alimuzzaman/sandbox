# Quickstart Validation: Owned Storage Authority

> **Blocked: do not execute.** Planning is NOT READY because the required
> lifecycle transaction port does not exist. See [analysis.md](./analysis.md).
> The commands below remain proposed acceptance design, not authorization.

This is the Phase 1 validation guide for a future implementation. It does not
authorize service installation, remote mutation, deployment, support promotion,
legacy migration, cleanup, release, or production adoption. Use a newly created
disposable remote and obtain explicit authorization before the live sections.

## 1. Preconditions and evidence identity

Work on the exact non-`main` feature revision and record it without exposing
credentials or host paths:

```bash
git branch --show-current
git rev-parse HEAD
git status --short
./sb guide --project-dir .
```

Required preconditions:

- source branch is non-`main` and clean for the accepted revision;
- the implementation and matching docs/contracts/package assets are in that
  exact revision;
- a newly provisioned disposable Ubuntu 24.04/systemd 255 remote is registered;
- the remote Sandbox service revision is checked through the supported
  lifecycle and matches the client protocol revision;
- no production credentials, projects, storage, or resolver state are used;
- live service installation/qualification and any cleanup have explicit human
  authorization;
- the exact registered disposable fixture and explicit authorization are ready
  for the supported lifecycle to mint one sealed, short-lived qualification
  admission bound to exact revisions, controller, budget, and evidence candidate;
- a before-test bounded inventory of unrelated jobs, workspaces, storage, and
  resolver state is retained for comparison.

Use placeholders below:

```text
REMOTE=<registered-disposable-remote>
PROJECT_ID=<opaque-project-identity>
FIXTURE_ID=<opaque-disposable-fixture-identity>
PROJECT_DIR=<registered-project-root>
WORKFLOW=<fixed-owned-storage-acceptance-workflow>
RELATIONSHIP_ID=<opaque-relationship-id>
WORKSPACE_ID=<opaque-workspace-id>
```

Do not place those values in shell history if local policy treats them as
sensitive. Never use a path as an authority object identity.

## 2. Static and architecture gates

Run the focused suites with synthetic subprocess environments:

```bash
python3 -m unittest \
  tests.test_owned_storage_models \
  tests.test_owned_storage_repository \
  tests.test_owned_storage_protocol \
  tests.test_owned_storage_linux \
  tests.test_owned_storage_recovery \
  tests.test_owned_storage_application \
  tests.test_owned_storage_cli \
  tests.test_owned_storage_review \
  tests.test_owned_storage_mcp \
  tests.test_owned_storage_packaging \
  tests.test_owned_storage_architecture \
  tests.test_sync_owned_storage \
  tests.test_job_owned_storage \
  tests.test_workspace_owned_storage
```

Required results:

- exact request replay returns one operation/object/outcome;
- request digest conflicts make zero changes;
- codec rejects unknown/missing/extra/unbounded/path/command fields;
- repository crash/race tests preserve intent and terminal truth;
- Linux adapter uses private directory FDs, no-follow/beneath resolution, and
  no-replace quarantine/publication;
- application services own policy; storage mechanisms do not open other
  repositories or registry JSON;
- CLI/MCP are thin manifest-registered adapters with identical safe fields;
- no new imports of compatibility roots/facades appear;
- package manifests include runtime service assets and continue excluding
  `.specify/`, `specs/`, and `speckit-*` skills;
- all captured subprocesses use the repository synthetic environment helper.

Run existing compatibility suites:

```bash
python3 -m unittest \
  tests.test_sync_service \
  tests.test_sync_state \
  tests.test_workspace_contracts \
  tests.test_workspace_retention \
  tests.test_workspace_resource_ownership \
  tests.test_remote_job_runtime_acceptance \
  tests.test_job_observation_contracts \
  tests.test_job_mcp \
  tests.test_sync_mcp \
  tests.test_secret_service \
  tests.test_secret_mcp \
  tests.test_redaction_parity
```

Expected: all prior legacy outcomes remain unchanged. No static result promotes
the capability above `implemented_unproven`.

Run the accepted Feature 048–051 and integration contracts with this exact,
deterministic command. Do not replace it with broad discovery or omit a module:

```bash
python3 -m unittest \
  tests.test_host_recovery_models \
  tests.test_host_recovery_policy \
  tests.test_host_recovery_repository \
  tests.test_host_recovery_service \
  tests.test_host_recovery_cli \
  tests.test_hosting_image_trust \
  tests.test_hosting_image_contracts \
  tests.test_hosting_image_boundaries \
  tests.test_hosting_image_staging_policy \
  tests.test_hosting_image_staging_repository \
  tests.test_hosting_image_staging_service \
  tests.test_hosting_image_staging_secrets \
  tests.test_hosting_image_staging_process \
  tests.test_remote_hosting_images \
  tests.test_hosting_image_activation_models \
  tests.test_hosting_image_activation_policy \
  tests.test_hosting_image_activation_repository \
  tests.test_hosting_image_activation_service \
  tests.test_hosting_image_activation_init \
  tests.test_hosting_image_activation_recovery \
  tests.test_hosting_image_activation_runtime \
  tests.test_hosting_image_activation_races \
  tests.test_hosting_image_activation_private_source \
  tests.test_hosting_image_activation_cli \
  tests.test_sync_service \
  tests.test_sync_state \
  tests.test_remote_ci_jobs \
  tests.test_ci_workspace_cleanup \
  tests.test_workspace_contracts \
  tests.test_workspace_retention \
  tests.test_workspace_resource_ownership \
  tests.test_command_composition \
  tests.test_mcp_composition \
  tests.test_owned_storage_packaging
```

This is the T076 regression selector. Feature 052 implementation may add its
new packaging test, but it must not edit the accepted Features 048–051 suites or
any source below `sandbox/hosting/**`.

## 3. Closed default and unsupported platforms

Before live installation or qualification:

```bash
./sb storage authority capability --remote "$REMOTE" --project-identity "$PROJECT_ID" --json
./sb storage authority status --remote "$REMOTE" --project-identity "$PROJECT_ID" --json
```

Expected:

- capability is `unavailable`, `unsupported`, or `implemented_unproven`;
- `adoptable=false` and evidence ID is null unless a prior exact reviewed proof
  legitimately exists;
- status may be read-only/partial but exposes no paths or host internals;
- mutation policy stays `legacy`.

Verify fail-closed mutation on an unqualified remote and on every unsupported
adapter fixture:

```bash
./sb storage authority policy --remote "$REMOTE" --project-identity "$PROJECT_ID" \
  --mode future --request-id authority-policy-negative-1 --confirm --json
```

Expected: nonzero stable unsupported/unproven result, zero authority objects,
zero policy promotion, zero legacy adoption, and no fallback represented as
owned/immutable. Repeat the contract fixture for macOS, Windows, Herd,
Compose-local, generic host jobs, NFS/unknown filesystems, missing private mount,
missing syscall, revision mismatch, and service/root/owner drift.

## 4. Separately authorized service lifecycle proof

Only after explicit authorization, use the supported remote Sandbox lifecycle
to install/update the exact accepted revision. Do not use raw SSH/systemd edits.
Then run the read-only capability probe again.

The bounded evidence bundle must prove:

- one static non-login service UID distinct from the submitting owner/workload;
- fixed executable, service/socket/sysusers assets and exact revision/digests;
- private authority root, database, object/staging/quarantine parents;
- submitting owner and workload cannot write root/records/object parents;
- unprivileged service, no ambient capabilities, no Internet address families,
  and only fixed writable state/runtime directories;
- exact peer credentials plus registered project/operation authorization;
- exact supervised-controller UID/GID, PID/start, executable, unit/cgroup,
  config, and connection identity; direct CLI/workload/same-UID socket calls
  are refused;
- exact runtime mount-controller identity, descriptor-only transfer, no path
  input, and mount authority confined to the disposable job user/mount namespace;
- `openat2` beneath/no-symlink and `renameat2` no-replace behavior on the
  selected local filesystem;
- service starts mutation-closed, reconciles, and stops while preserving state;
- process/socket absence is proven after stop; unreadable is not absence;
- restart/update returns the same retained object/operation truth.

Expected report remains `implemented_unproven`, `adoptable=false`, and normal
policy `legacy` until all live scenarios and independent human review are
complete. The sealed proof admission is the only mutation admission during the
matrix; it cannot promote itself or be used by ordinary CLI/MCP policy calls.

## 5. Qualification admission and legacy compatibility

Create one legacy sync generation/workspace before the proof admission and
record its existing status/replay result. Start the authorized fixed acceptance
harness. The supported lifecycle first verifies the exact disposable fixture,
revisions, and controller, then mints the sealed qualification admission. The
harness routes the fixture through the ordinary application/storage path without
changing normal project policy or exposing a force/unproven option.

```bash
./sb storage authority acceptance --remote "$REMOTE" --project-identity "$PROJECT_ID" \
  --fixture-id "$FIXTURE_ID" --request-id authority-acceptance-1 \
  --confirm --json
```

Expected during the proof run:

- public capability remains `implemented_unproven` and `adoptable=false`;
- normal project policy remains `legacy`;
- only exact fixture operations inside the admission budget can create proof
  objects; another project, caller, operation, revision, or expired admission
  is refused;
- the earlier legacy object remains `legacy_not_owned` and is neither moved nor
  reclassified.

Retain the returned opaque `EVIDENCE_CANDIDATE_ID` for the independent review;
it is not an authorization credential.

The fixed command executes the scenarios described in sections 6 through 13
under that one admission and closes it with exact cleanup evidence. No normal
policy transition occurs during qualification.

## 6. Immutable publication ordinary path

Under the active qualification admission, the controller-resident harness
launches the exact ordinary `sync once` command with a synthetic subprocess
environment and a sealed inherited admission descriptor, never argv or an
environment token:

```bash
./sb sync once --project-dir "$PROJECT_DIR" --remote "$REMOTE" \
  --workspace-id "$WORKSPACE_ID" --request-id authority-proof-publish-1 --json
```

The submitting user cannot launch an admitted copy. Do not call the private
service protocol directly.

Expected accepted evidence:

- exact remote/project/relationship/workspace/request/generation/manifest/file-
  count/byte-count binding;
- one authority object and one operation receipt;
- current selection changes only after complete durable acceptance;
- existing sync envelope fields are unchanged and the authority block is
  additive/path-free;
- consumer reads reproduce the accepted content digests.

As the submitting identity, ordinary control-plane process, and CI workload,
attempt edit, rename, replace, remove, authority-record change, and cross-project
access. Expected: every attempt changes zero accepted bytes/evidence and creates
no cleanup eligibility.

## 7. Publication interruption, replay, and storage exhaustion

Run at least 100 deterministic trials spanning each durable phase:

```text
reserved
receiving
verified
effect_intent
private no-replace publication
accepted/current transaction
response serialization/transport
service restart
```

For each trial:

1. preserve the same canonical request ID;
2. interrupt or drop the acknowledgement at the selected phase;
3. restart when the case requires it;
4. run the public reconcile/exact replay path;
5. verify one operation, at most one accepted object, and one current selection;
6. verify readers saw only the previous complete or new complete generation;
7. verify no partial generation became current.

Repeat negative replay with each canonical field missing/changed, concurrent
duplicate calls, insufficient capacity, short write, extra stream bytes,
unsafe archive member, link/device, count overflow, byte mismatch, and database/
flush failure. Expected: zero false acceptance/current changes and explicit
refused/unknown/indeterminate evidence.

## 8. Bounded writable CI materialization

Under the same qualification admission, the controller-resident harness
launches the exact ordinary CI path with its sealed inherited descriptor:

```bash
./sb ci run "$WORKFLOW" --project-dir "$PROJECT_DIR" --remote "$REMOTE" \
  --timeout 900 --json
```

The qualified runtime must mount only the authority-created writable interior
into the job's private namespace and mount accepted/managed source read-only.

Inside the workload, verify create/change/remove succeeds in the declared
interior. Verify all of these change zero protected bytes:

- replace/delete the materialization root;
- alter the authority record or object marker;
- mutate an accepted generation or managed read-only source;
- reach another workspace/project object;
- retain access after terminal lease close/revoke;
- use a copied/stale/foreign materialization or lease ID.

Expected: the authority records the exact job/workspace/materialization/mount
lease; every non-closed/unknown lease protects cleanup.

## 9. Terminal cleanup and immutable job result

Before cleanup, the fixed harness captures the immutable terminal job result
digest and a bounded inventory of unrelated authority and legacy objects. It
calls the same application preview and single-object cleanup ports used by the
public commands, but supplies the sealed admission internally; an ordinary
project reclaim command cannot consume it. The harness confirms the target is
eligible and active/retained/foreign/legacy/current objects are protected.

Expected:

- exact object is quarantined and fully removed under the private authority;
- known observed reclaimed bytes are reported; unknown/partial bytes are not;
- authority cleanup intent/outcome remains durable after the object is absent;
- terminal job lifecycle/outcome/result digest is byte-for-byte unchanged;
- unrelated authority, legacy, resolver, network, project, and host state is
  unchanged;
- exact replay returns `already_completed`/original receipt and does not act on
  a replacement.

## 10. Negative cleanup and retention matrix

For every case, preview and/or cleanup must remove zero protected objects and
return one stable non-success reason:

- unauthorized, revoked, cross-project, substituted caller;
- active/nonterminal job, live lease, private mount, process, container, open
  reader, pin, or reference;
- current, pending, unknown-acknowledgement, retained, or unexpired generation;
- retained cleanup policy, missing/invalid/stale policy;
- foreign/legacy object, ambiguous identity, degraded/incomplete index;
- changed object, path replacement, marker/device/inode/mount mismatch;
- stale/expired/incomplete preview or object not in preview;
- unknown size, storage pressure, service/root/revision drift;
- partial/timed-out observer and transport loss.

Change a candidate after preview by making it current or adding a reference.
Expected: final fresh check retains it. Create a replacement at the former
location during each cleanup phase; expected replacement is untouched.

## 11. Cleanup interruption/restart matrix

Run at least 100 deterministic trials across:

```text
intent committed
private object opened/verified
no-replace quarantine
quarantined phase committed
recursive descendant removal
final private-parent identity check
flushed final-remove intent
empty quarantine name removal
private-parent flush
terminal outcome commit
response serialization/transport
service restart
```

For each trial, reconcile only the original request/object. Expected:

- at most one terminal cleanup outcome;
- unchanged original safely resumes or remains indeterminate;
- proven already-removed original is idempotent;
- replacement is never removed;
- absence without a terminal receipt or matching flushed final-remove intent is
  not success;
- no unreviewed staging/quarantine is pruned by age.

## 12. Preview scale, bounds, and disclosure

Populate synthetic authority/legacy projections up to 10,000 records. Measure
status/preview: at least 95% complete within 30 seconds, default page 100,
maximum 500, stable ordering, exclusive scope-bound cursor, and maximum 15-
minute preview expiry. Requests beyond bounds report partial/timeout and cannot
authorize cleanup.

Scan CLI JSON/text, MCP results/errors/progress, operation evidence, retained
job output, service journal projections, and test failures. Expected zero:

- source content or entry names;
- credential/token/private-key values;
- unrestricted argv/environment/host configuration;
- raw UID/GID/PID, unit/socket/mount details;
- filesystem or SSH paths/targets;
- resolver/network state or unrelated project data.

Known aggregate bytes may appear. Unknown bytes are null and excluded from
reclaimable/reclaimed totals. CLI and MCP allowed fields/omissions are equal.

## 13. Resolver separation

Record resolver capability/state before the storage journey. Exercise storage
capability, policy, publication, status, preview, and cleanup. Expected:

- storage service has no resolver/network adapter or Internet address family;
- no resolver/DNS/ingress/network record changes;
- storage capability explicitly reports resolver authority excluded;
- a platform proven for storage but unproven for resolver gains no resolver
  mutation/cleanup eligibility.

## 14. Final review and adoption gate

Collect a bounded evidence bundle tied to exact source and installed revisions,
including test counts, interruption matrices, platform/package/kernel/filesystem
summary, service/root ownership digests, capability output, publication/cleanup
receipts, before/after unrelated-state digest, performance results, and
no-secret/no-path scan result.

An independent human reviewer must inspect:

- contracts and module boundaries;
- service UID/unit/sysusers/package/lifecycle privilege surface;
- peer authorization and no-path protocol;
- private-root publication and final-removal implementation;
- restart/replay/replacement recovery;
- CI mount isolation and access revocation;
- compatibility/rollback behavior;
- bounded evidence and resolver separation;
- exact clean source/installed revision.

Only an explicit accepted review may prepare promotion for this exact
disposable fixture. Any missing/contradictory result keeps `adoptable=false`
and existing material retained.

Record the independent decision through the protected lifecycle. This is the
only promotion path:

```bash
./sb remote service owned-storage-review "$REMOTE" \
  --project-identity "$PROJECT_ID" --evidence-candidate-id "$EVIDENCE_CANDIDATE_ID" \
  --decision accepted --request-id authority-review-1 --confirm --json
```

The protected lifecycle must prove the review replay binding and cross-store
sequence before returning an adoptable projection:

1. lifecycle review reservation is durable and binds candidate close
   generation/digest, cleanup digest, exact revisions/scope, reviewer
   authorization, decision, request ID/digest, and lifecycle generation;
2. the authority adoption binding is `prepared` and non-authorizing;
3. the lifecycle review decision, promotion receipt, and capability state are
   committed as one closed nested value through the shared hosting target
   transaction owner;
4. exact replay activates only that authority binding;
5. capability returns the exact promotion/binding generations with
   `support_tier=implemented_unproven`, `adoptable=false`, and
   `acceptance_state=pending_ordinary`; only the exact fixture-validation
   promotion/binding may open `future` for this disposable scope.

No authority `review` operation or cross-repository atomic transaction exists.
Exact review replay returns the same receipt. Changed input refuses. Review
consumes no qualification budget. Rejected candidate reuse refuses and requires
new evidence.

Now prove the missing post-promotion normal branch. These commands run outside
the acceptance harness and accept no admission or fixture-proof argument:

```bash
./sb storage authority policy --remote "$REMOTE" --project-identity "$PROJECT_ID" \
  --mode future --request-id authority-policy-future-1 --confirm --json

./sb sync once --project-dir "$PROJECT_DIR" --remote "$REMOTE" \
  --workspace-id "$WORKSPACE_ID" --request-id authority-normal-publish-1 --json

./sb ci run "$WORKFLOW" --project-dir "$PROJECT_DIR" --remote "$REMOTE" \
  --request-id authority-normal-ci-1 --timeout 900 --json
```

For this durable remote run, `authority-normal-ci-1` is the parent submission
replay identity. Each cell's materialization request identity is derived from
that parent and the canonical workflow/cell/project/workspace/job/source
binding. The result returns safe `materialization_request_id` values. Repeating
the exact parent returns the original job/materialization lineage; changing any
bound field under that parent refuses before effect. Existing CI calls without
`--request-id` retain their current behavior.

For both new objects, require:

- internal storage requests contain `qualification:null` and no admission or
  fixture ancestry;
- status binds the exact future-policy ID/generation, evidence ID, promotion
  ID, authority-binding ID/generation, and normal request identity;
- exact replay returns one original receipt, while changed request reuse
  conflicts before effect;
- sync content remains immutable; the CI writable interior works while its
  root, record, accepted source, and unrelated scopes remain protected.

After the CI job is terminal, exercise ordinary public cleanup, not the sealed
harness:

```bash
./sb storage authority preview --remote "$REMOTE" --project-identity "$PROJECT_ID" --json
./sb storage authority reclaim --remote "$REMOTE" --project-identity "$PROJECT_ID" \
  --preview-id "$PREVIEW_ID" --object-id "$CI_OBJECT_ID" \
  --request-id authority-normal-cleanup-1 --confirm --json
```

Require full removal, measured known bytes, unchanged immutable job result,
idempotent replay, replacement refusal, and unchanged unrelated-state digest.
Replay the CI materialization through the returned
`materialization_request_id` and the public reconcile port. Then repeat the
exact parent `ci run` and require the original job/materialization lineage. A
third call reuses the parent ID with a changed canonical workflow and must
return `request_id_conflict` with zero effect:

```bash
./sb storage authority reconcile --remote "$REMOTE" --project-identity "$PROJECT_ID" \
  --operation materialize --request-id "$MATERIALIZATION_REQUEST_ID" --json
./sb ci run "$WORKFLOW" --project-dir "$PROJECT_DIR" --remote "$REMOTE" \
  --request-id authority-normal-ci-1 --timeout 900 --json
./sb ci run "$CHANGED_WORKFLOW" --project-dir "$PROJECT_DIR" --remote "$REMOTE" \
  --request-id authority-normal-ci-1 --timeout 900 --json
```

The focused contract suite must also substitute each server-derived cell, job,
workspace, and source binding under the same parent identity and prove conflict
before any new job or materialization is reserved.

Repeat the exact reclaim command and require the original terminal receipt;
then reuse `authority-normal-cleanup-1` with a different object or preview and
require conflict before effect.

```bash
./sb storage authority reclaim --remote "$REMOTE" --project-identity "$PROJECT_ID" \
  --preview-id "$PREVIEW_ID" --object-id "$CI_OBJECT_ID" \
  --request-id authority-normal-cleanup-1 --confirm --json
./sb storage authority reclaim --remote "$REMOTE" --project-identity "$PROJECT_ID" \
  --preview-id "$OTHER_PREVIEW_ID" --object-id "$OTHER_OBJECT_ID" \
  --request-id authority-normal-cleanup-1 --confirm --json
```
Only after these new owned objects and evidence exist, test rollback:

```bash
./sb storage authority policy --remote "$REMOTE" --project-identity "$PROJECT_ID" \
  --mode legacy --request-id authority-policy-legacy-1 --confirm --json
```

Create one later object and prove it remains legacy while existing owned objects
remain untouched. Exact policy replay returns the same receipt; changed reuse
refuses.

On success, an independently authorized human invokes the protected finalizer:

```bash
./sb remote service owned-storage-acceptance-finalize "$REMOTE" \
  --project-identity "$PROJECT_ID" --promotion-id "$PROMOTION_ID" \
  --request-id authority-acceptance-finalize-1 --confirm --json
```

The command accepts no evidence or support-tier input. Through typed read-only
ports it derives every normal sync/CI/cleanup/replay/ancestry/rollback,
revision, and unrelated-state fact. It records one immutable ordinary-evidence
identity, changes the validation promotion to `supported`, and projects
`acceptance_state=complete`, `support_tier=proven`, and `adoptable=true` only
when all facts match. Exact replay returns the same result; changed promotion
under the same request ID conflicts. Crash recovery resumes the stored phase
under the shared target generation.

Any post-promotion failure means acceptance failed. Before support is claimed,
the protected lifecycle must commit non-adoptable/revoked state and then
deactivate the exact authority binding:

```bash
./sb remote service owned-storage-revoke "$REMOTE" \
  --project-identity "$PROJECT_ID" --promotion-id "$PROMOTION_ID" \
  --reason acceptance_failed --request-id authority-revoke-1 --confirm --json
```

A lost deactivation acknowledgement stays non-adoptable and exact replay
reconciles the original binding. Support, another remote/project, release,
rollout, and production remain unauthorized until separately approved.

Before the final human adoption decision, run the SC-014 comprehension gate.
Retain one bounded, path-free public status projection from the acceptance
evidence for each class: `current`, `protected`, `eligible`, `accepted`,
`reclaimed`, `refused`, `unsupported`, and `indeterminate`. Randomize the eight
records without their class labels. Every operator and maintainer assigned to
this acceptance must, using only those public projections:

1. assign the correct class to all eight records; and
2. state the exact recorded reason code for all eight records.

Record the participant role, evidence-bundle digest, answer digest, and score in
the acceptance evidence; do not retain names, host internals, source content, or
paths. The gate passes only when every assigned participant scores 8/8 for both
classification and reason. Any omission or wrong answer keeps
`adoptable=false`, blocks the final adoption decision, and does not authorize a
repeat live mutation merely to manufacture another sample.
