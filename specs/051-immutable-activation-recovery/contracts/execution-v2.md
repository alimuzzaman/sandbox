# Complete v2 deployment execution

This amendment implements FR-052–065. It is additive to historical v1/v2 values.
New execution uses `ordered-init-v1`; private input uses `candidate-v1`. These
unreleased revisions must be complete across every consumer before activation is
enabled. Unknown revisions refuse. Legacy digest bytes and terminal results stay
unchanged; absent execution evidence never means successful zero-init execution.

## Private preparation

The target owner admits a replay-safe preparation identity before private transfer.
It validates exact application SHA, tracked manifest/Compose bytes, registered target
and reserved loopback port. Application bytes come from the selected clean local
application checkout, not a shared remote development checkout. Source and broker
guards protect one captured set. The preparation lock order is target owner, then
source/broker guard; release the guard after capture and do not nest a stage lock
inside broker access. Host-state and stage mutations retain their existing order.

Publish the complete candidate with descriptor-relative no-follow access, exclusive
owner-only files/directory, a no-replace atomic publication and parent fsync. Bound
the private frame and each file; reject incomplete or external inputs. Retain an
opaque preparation/input binding, exact application revision and closed selector.
Private paths, source-secret keys, values, raw hashes and arbitrary child output
never enter public state or results.

Supported `secrets.environment` entries are materialized as candidate-owned files
from captured registered values. The private effective render binds exact names,
service/mount mapping and content HMAC. Preserve application `_FILE` semantics.
Uncaptured file/external secrets, configs, includes, extends, external networks and
production source bind mounts refuse. The candidate is the production project
directory. The helper removes the derived HMAC key before starting children; the
machine master never crosses the local broker boundary.

Exact preparation replay returns retained input without another broker read. A
changed input under the same identity conflicts. A new preparation identity may
rotate input only with no active conflicting owner. Keep files referenced by active,
current, previous or unresolved incident records even after admission expiry.

## Graph and receipts

Closed public declarations bind target/snapshot, service, ordered index, exact
image/config/platform, dependency names and conditions, bounded deadline and a
target-keyed identity of the full private configuration. They contain no raw command,
entrypoint, environment value, label or mount path. Limits remain at most 64
persistent services, 16 initializers and 3600 seconds per admitted phase.

The execution graph includes prerequisite runtime groups, initializers and consumer
groups. It is acyclic, covers exact declared membership once and preserves explicit
initializer order. A dependency cannot silently become a Compose side effect.
For Lenzora production: exact ready queue prerequisite → migrate → storage init →
queue topology gate → dependent consumers → all 17 services ready → edge → commit.

Before creation persist deterministic ownership/preparation identity. After creating
stopped init, privately compare exact image, user, privileges, command/entrypoint,
mount/network, environment/secret and target/daemon identity. Only then persist
`effect_entered` and start once. Persist zero-exit/termination evidence before
cleanup, then persist owned cleanup completion before any dependent phase. Foreign
or unproved name collisions are never removed. A possibly entered effect stays true
through errors; missing exit or cleanup evidence prevents advancement.

Bind graph, preparation selector, per-phase receipts and pre-forward compatibility
subject/grant through snapshot, request, acceptance, replacement intent, generation
subject, edge receipt, committed generation and recovery projection. Distinguish
aggregate possible effects, prerequisite effects, init effects, consumer effects and
edge effects. Receipt substitution, index gaps, reordered steps and missing members
refuse. Terminal replay is checked before unsupported-contract refusal.

## Readiness, registration and edge

Reserve first-target ports durably through the shared host-state owner. Use existing
registered provider mechanisms for runtime directory and Caddy/DNS/TLS setup with
bound intent/effect/terminal receipts; never bootstrap immutable activation through
ordinary `host apply`. Known provider/zone capability failures refuse before runtime
or initializer effects. Capability preflight is read-only and never purges.

After runtime submission, poll only read-only identity/readiness evidence until the
admitted deadline. Starting is pending, identity drift is uncertainty/refusal, and
partial/mixed observations are never success. Waiting never repeats Compose. Commit
requires all dependency receipts, complete health and unchanged post-edge proof.
Purge preparation retains every zone before POST; durable acknowledgements can
reconstruct a complete aggregate without another POST. Unknown delivery stays fenced.

## Observation recovery and rollback

The existing two-observation provisional/commit protocol covers every graph phase.
Resolve retained selectors without current source, secret rotation or preparation.
An empty runtime cannot establish absence of earlier prerequisite, init, worker or
edge effects. Only demonstrably pre-effect work can close as `recovery_no_effect`.
Every promotion requires its complete original receipt chain and coherent evidence.

Forward acceptance retains the pre-forward signed compatibility subject/grant from
FR-034, including current generation, candidate input/graph, plan/proof, target,
data/schema contract and authority revision. The resulting generation references it.
Rollback uses that retained authority, previous private input and still-local images;
it runs no forward init. Admission lease expiry is distinct from explicit retained
rollback validity. Missing historical authority refuses; a new signature cannot
manufacture pre-forward approval.

## Explicit incident settlement

Settlement is a distinct plan/apply operator operation, never automatic deployment
or ordinary observation recovery. Its closed plan binds active transaction,
target/daemon/generation, exact process/container set and quiescence, preserved
volume/layer identities, backup receipts, data-assessment identity and fixed reason.
A separately installed operator approval binds that exact plan and explicit forward
remediation/data compatibility scope. Changed evidence or missing approval refuses.

Apply reobserves and commits `abandoned_with_effects` through the shared owner. It
preserves original uncertainty, leaves generation/current unchanged, performs no
runtime/data/provider/delete operation and releases custody only after durable
terminal ownership. Settlement replay has zero effects. A subsequent forward
activation must name the settlement and separately approved data assessment; it
cannot silently replay the uncertain operation or use settlement for rollback/adoption.

The CLI exposes `host image settle --settlement-phase` with closed phases
`observe`, `plan`, `install-approval`, `install-forward-approval`, and `apply`.
Every phase requires explicit project, environment, registered remote, request
and generation selectors. Only observe/plan are read-only; the other phases
require `--confirm`. Artifact arguments and replay rules are specified in
[`docs/image-activation-settlement.md`](../../../docs/image-activation-settlement.md).
Approval installation accepts an operator-supplied signature and public key;
neither settlement nor deployment signs its own authority.

## Development and release control

Opt-in development uses one owner for its effective dependency closure and exactly
one initializer execution. Environment-scoped source is prepared separately before
controlled replacement; legacy mounted source stays intact. Generic hosting and the
development server mode remain compatible.

Application A, control checkout D and Sandbox S are exact independent identities.
The signed receipt, application bytes, images and runtime evidence always match A;
the executing wrapper matches D and its required Sandbox revision matches S. Defaults
retain A=D. The deployment entrypoint reports the complete tuple and installed
capability, and refuses dirty or relabeled inputs. A new request/revision never
bypasses an active uncertainty fence.
