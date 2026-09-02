# Remote hosting and development boundaries

The remote features share a VPS but serve four distinct workflows. Keeping the
boundaries explicit prevents a test job, a source snapshot, or an MCP connection
from being mistaken for a production change.

## Feature 049 OCI trust boundary

`sandbox.hosting.images` is an effect-free value/policy package. Its verifier accepts
three separate closed channels: a private machine-boundary-issued policy token,
untrusted `ProjectImageIntent`, and untrusted `ReleaseReceipt`. The token type and its
issuer are not public package contracts, and ordinary construction lacks the
module-owned capability and refuses. The verifier imports no config loader,
credential broker, filesystem, Docker, process, transport, clock, random source, or
state repository. Receipt, machine-policy, and plan hashes use separate domain strings
over bounded canonical JSON.

Raw project, machine, and receipt JSON is accepted only as exact built-in
dict/list/scalar values at its owning boundary. Canonical traversal charges a running
node and encoded-byte budget before copying each value. The pure verifier accepts only
the three exact immutable channel types and safe-projects every exception without
retaining its text. V1 provenance is not extensible metadata: builder, workflow,
invocation, materials, and build identities are fixed lowercase SHA-256 digests.
Source repository is a canonical lowercase owner/repository name, and source revision
is exact lowercase 40- or 64-hex identity.

The config manifest registers two explicit owners. `hostingImages` is preserved by
both project schema providers and normalized only by
`sandbox.config.hosting_images`. `hosting.images.policies` is normalized only through
the machine provider. Both project schemas preserve one explicit raw primary-project
layer. Only that selected primary descriptor may declare `hostingImages`; global,
machine override, and label files are ignored for this channel. If the primary key is
absent, the final descriptor adds no key and has no behavior change.

Machine policy owns the primary service and the maximum persistent/one-shot service
partitions. Project intent can only choose subsets, must retain the primary service in
the persistent partition, and cannot move a service between partitions. The resulting
`DeliveryIdentityProjection` freezes target scope, canonical GHCR repository-qualified
manifest digest, configuration digest, OCI image-manifest media type, exact platform,
selected topology, and intended-private declaration. Every selected service consumes
that one identity.

Features 050 and 051 may call `validate_verified_image_plan` and copy the projection.
They may not recompute or weaken policy, provenance, media type, platform, signature,
or topology. Feature 047 image journals and Feature 048 recovery state enter only the
explicit legacy refusal adapter; the adapter does not traverse or mutate them. A valid
plan still is not credential, registry-observation, staging, runtime, deployment, edge,
or production evidence.

## 1. Source deploy: make a current checkout available remotely

```sh
./sb deploy --project-dir /path/to/project --remote NAME
./sb deploy --project-dir /path/to/project --remote NAME --ensure --expose
```

`deploy` is a one-way, on-demand source snapshot. It pushes the current commit and
supported local changes to the remote checkout; a later deploy replaces that snapshot.
`--ensure --expose` may boot a sandbox instance and publish its requested sandbox URL.
It does not watch files, synchronize continuously, create a durable development job,
or promote the checkout to a declared production environment.

## 2. Remote development jobs: run and recover bounded work

```sh
./sb test --remote NAME --workspace node-unit --timeout 1800 --detach -- npm test
./sb job-status JOB_ID --remote NAME --json
./sb job-output JOB_ID --remote NAME --cursor 0 --max-bytes 65536
```

Remote test, `exec`, E2E, matrix, and compatible CI commands use the durable job
runtime. The selected source is deployed to the named development workspace before
the supervisor accepts the job. The supervisor owns process pipes and retained output,
so a caller can reconnect with the job ID and cursor after SSH, terminal, or MCP
disconnection. Workspace cleanup remains explicit.

These commands are for development and verification. They do not update a hosting
manifest, DNS, Caddy configuration, or production secrets.

## 3. Remote MCP: use the co-located control plane

After `./sb remote provision NAME --confirm`, register the separate `sandbox-NAME`
MCP server with the client through its supported secret mechanism. For live remote
work, prefer that co-located server to a long-lived SSH command: submit or inspect
work with durable job APIs, then page retained status/output by job ID and cursor.

The remote MCP server is a control-plane endpoint. It neither watches local files nor
turns a job request into a production deployment. Its credential is managed outside
Git and must never be placed in command output, source, or an MCP tool argument.

## 4. Production hosting: apply a declared, reviewed environment

```sh
./sb host validate --project-dir /path/to/site
./sb host plan --project-dir /path/to/site --environment production --remote NAME
./sb host apply --project-dir /path/to/site --environment production --remote NAME --confirm
```

Production hosting is intentionally a separate workflow. `validate` and `plan` are
offline/read-only preparation; `apply --confirm` is the explicit action that transfers
the approved checkout, runs declared health checks, and changes only the hosting
manifest's Caddy, DNS, and secret mappings. It must not be inferred from `deploy`, a
remote test, CI completion, an exposed sandbox URL, or an MCP request.

Use the environment's configured branch and clean-tree policy before applying. Treat
production credentials and one-time login URLs as secrets, and keep each public change
within the declared manifest scope.

## 5. Recover a current-contract failed apply without replaying it

`sb host recover` requires an explicit remote, project, environment, terminal failed
durable job, original request, distinct recovery request, and the generation reported
by `host status --json`. The supervisor supplies fixed job/request/project/source
identity without enumerating or copying the parent environment. Eligible applies bind
that identity before the first hosting effect.

Recovery validates the canonical job snapshot and exact `host apply --confirm` argv,
clean source, project, target, and pre-effect receipt before remote observation. One
bounded observer captures start/end runtime inventory epochs, deployed config-file
digests, immutable image IDs, configured/running topology, source revision, health, and
the apply-owned one-shot phase receipt. Apply and recovery share a target lock and
reload the generation under it. Attempts are bounded; compacted request tombstones stay
non-reusable.
Target identity binds the normalized registered SSH endpoint, control transport and URL,
Tailscale host, MCP port, remote name, and runtime home; bearer credentials are excluded.
That endpoint identity is not machine authority. Eligible apply also records Feature 046's
authenticated stable `target_identity`, and recovery measures it before and after the
bounded runtime observation. Missing, legacy, rebuilt, or repointed identity refuses.
The authenticated controller derives that opaque identity from the machine ID, never a
hostname or endpoint string; invalid or unavailable machine ID makes the projection fail.
After target ownership, recovery resolves the registration again and holds the shared
registration guard through durable commit; supported re-registration uses the same guard.
An apply can continue without Feature 046 identity, but it mints no recovery authority.
Apply also recomputes its complete registration-derived plan, DNS records, origin checks,
and Cloudflare preconditions from the guarded entry. Original authority stores the
canonical non-secret edge intent and digest. Observation and pre-edge authority compare
that exact intent, and the edge adapter consumes its bound records rather than recomputing
from an unguarded entry. A same-machine origin change therefore refuses before effects.
The intent also binds certificate hostnames and supplies them to Origin CA issuance. It is
limited to 64 routes, 128 DNS records, 64 unique certificate hostnames, and 64 KiB; the
complete persisted hosting operation is limited to 128 KiB. Overflow creates no recovery
authority, binding key, or metadata directory. A new key/version is prepared in memory and
the exact prospective full envelope is bounded before either is published.
The recovery-only command predispatch policy skips compatibility migration, finalization,
Compose, and environment writers before the observer can refuse.

Secret equality comes from separate owner-only broker metadata produced during an
eligible apply. Recovery reads no secret source or value. Its opaque metadata ID and
secret-file epoch plus key identity must remain exact; environment-backed or stale metadata
refuses. The selected secret source and binding key must each be a real, owner-only regular
file; missing files and symbolic links are never authorizing.
The target lock precedes the broker revision lock, which covers metadata validation
through durable recovery commit. `environment.env`'s raw SHA-256 never enters a receipt;
it is transformed into an owner-keyed opaque HMAC identity first.
The finite broker guard also holds the same per-source `.<name>.sb-secrets.lock` as the
generic secret writer. Apply reads/caches secrets, computes its keyed config identity,
writes metadata, and accepts durable authority without releasing that transaction.
Legacy/job/source ineligibility is decided before broker acquisition.

Pre-effect apply authority and direct-apply revocation use the atomic recovery writer,
including file and parent-directory fsync. Sync watch uses a target effect lease plus a
short state transaction rather than retaining the shared state lock for its full run.
Every state mutation in locked apply, including init completion, observation refresh,
runtime reconciliation, and final edge success, uses that durable writer. First key
publication, newly created authority directories, and broker metadata replacement fsync
the required parent-directory entries before host authority can rely on them.
The bounded login URL writer also commits its receipt through the same durable repository
writer while holding its existing target/state lock.
Recovery writes a bounded `authorizing: false` provisional marker, immediately observes again
under ownership, then uses a separate atomic commit to promote matching evidence. The marker
has no receipt, generation advance, terminal success, or edge reachability. Only the same
observation request/digest may resume from `observation_pending`; the same provisional owner
may resume only its post-write observation. Edge entry is persisted before adapter invocation;
malformed or effect-entered state stays fenced. Exact edge replay returns its recorded terminal
edge result and does not use the observation-only `already_reconciled` alias.
Recovery lock directories stay exact `0700`, but a trusted controller-owned, non-writable
runtime parent may remain `0755`; the state file is exact owner-only regular `0600`. Parent
components are walked before lock creation so user-controlled symlinks produce no child.
The shared registration lock uses a canonical owner-only directory and rejects directory
or file symlinks, unsafe ownership/mode, non-regular files, and multiply linked files.

Observation success reconciles only local receipt state. Confirmed edge continuation
is a distinct request and needs an authorizing governance projection, a successful
observation reference, unchanged evidence/generation, and edge as the sole pending
phase. Its adapter can reach only existing Caddy, Cloudflare record/certificate, and
edge verification helpers. Unknown edge outcome is fenced against every later request.
Feature 047 has no implemented authorizing projection yet, so the public edge path is
currently unreachable and returns `governance_unavailable`; only the bounded adapter
seam is present and covered by an authorized synthetic fixture.
Local tests do not prove an installed remote runtime or Lenzora deployment.
# Immutable image delivery boundaries

Feature 049 is pure trust policy. It validates release authority and emits a closed
`VerifiedImagePlan`; it never reads credentials or contacts a registry or host.

Feature 050 consumes that plan only through its public validation/projection API. A
separate owner-only stage ledger durably accepts a request before a credential is
resolved. A fixed measured helper is launched in a transient systemd cgroup-v2 unit
before `BrokerLease.consume` delivers bytes through its private frame. The helper pulls
only the exact repository-qualified digest, removes its volatile Docker credential
workspace, and returns one coherent daemon observation. Feature 050 emits a
secret-free `StagedImageProof` and stops.

The installed helper is content-addressed and rehashed on every install. Installer and
transport require provisioning-owner-owned, non-symlink, mode-constrained directories, artifact, and
manifest. Launch opens those components no-follow, hashes the artifact descriptor, and
executes that same inode through `/proc/self/fd`; the closed manifest is checked in the same
launch boundary. The GHCR adapter atomically derives the configured opaque revision and
one-use lease bytes from one source snapshot before helper launch. Local observation derives the config digest from Docker's immutable image
ID, keeps that local ID as a separate evidence field, and reads topology from the immutable
config label rather than echoing the request. Start/end machine and daemon epochs must match.
The model requires local image ID equality with config digest even after all digests are
recomputed.
Revision-bound lease consume checks revoked/used state, marks the lease used, and detaches
the required snapshot under the same lock. Thus concurrent invalidation has one deterministic
winner: it either wipes before consume, or loses after consume has detached the exact bytes.
There is no registered-source fallback for this lease type, and detached mutable material is
wiped after every callback path. Legacy generic leases are explicitly separate.

Feature 051 may receive only the verified plan, staged proof, proof validator, and
authenticated proof-custody port. It must prepare a durable lease/pin before proof
validation, then acquire locks in this order: target mutation, shared host-state
transaction, stage-ledger target. The stage lock stays held through durable host-state
acceptance and pin promotion. Expiry never auto-unpins; only the exact durable
activation owner can cancel, promote, or release under the contract.
Custody has no stage-lock-only facade. It is yielded only inside target mutation, atomic
host-state, then stage-ledger transactions. Promotion/cancellation/release require evidence
objects authenticated by the active host-state transaction. A lease binds the retained
proof's stored staging generation and commit ledger revision, so a later successful stage
does not invalidate exact custody of an older retained proof.

Feature 048 remains observation-only recovery for the existing host path. Its
`hosts.json` repository, receipts, and target lock are not staging authority and are
never read, parsed, or written by Feature 050.

None of these layers is public production proof. Runtime health, edge readiness, and
direct public behavior remain separate evidence gates.

## Feature 051 immutable activation state

Feature 049 alone decides trust and emits the closed delivery plan. Feature 050 alone
owns registry credential use, pull, local-image proof, its stage ledger, and proof-custody
pins. Feature 051 validates only the public 049 plan/projection and retained 050 proof,
then performs init/runtime/edge effects and owns activation generations. Feature 048 adds
one read-only activation observer; it does not deploy, pull, run init, update edge, or write
activation state.

`sandbox.hosting.images.activation.repository` is a nested codec and candidate validator.
It never opens or writes `hosts.json`. `RecoveryRepository` remains the only outer parser,
target/state locker, CAS owner, atomic replacer, and fsync owner. The optional
`image_activation` host field contains current and previous generations, one active common
activate/adopt/rollback transaction, bounded results/tombstones, and a non-authorizing
image-recovery provisional. Unknown host fields remain untouched.

Every registered target mutation uses the shared capability registry and target owner.
The custody lock order is target mutation, host-state transaction, then Feature 050 stage
lock. Feature 050 prepares and pins before proof validation; host acceptance is atomic;
then the same durable holder promotes the pin before effects. A deadline forbids new
acceptance but never auto-unpins. Exact crash replay may promote an already stored host
acceptance or cancel only proven absence. Only a terminal accepted owner releases custody.

Init is create-without-start, exact inspection, durable `effect_entered`, bounded start/wait,
complete termination, and cleanup. Possible execution without a terminal receipt stays
uncertain and is never replayed. Long-lived replacement accepts only repository-qualified
digests. The registered target and Compose project stay guarded from observation through
commit. The full private render stays inside the remote helper, which feeds those same bytes
on stdin to `up --no-build --pull never`, proves each resulting Compose configuration hash,
and rerenders afterward. A machine-keyed, target-scoped, domain-separated HMAC over the complete private
render is the only persisted configuration identity. Each raw service config hash also
stays remote; only its target-scoped HMAC enters the closed projection and every later
running/recovery observation must reproduce it. SSH output is a closed allowlist of
safe image/platform/topology and service/dependency/environment-key identities plus refusal
flags; arbitrary commands, labels, health checks, URLs, logging values, extensions,
environment values, raw hashes, inline content, and rendered map keys are removed. The
owner-only machine master remains local. Only its exact machine/target-derived binding key
uses private stdin and is removed before Docker runs. All top-level configs/secrets
and external networks refuse until their bytes or engine identity can be snapshotted. Exact
running observation projects `ps` and inspect inside the helper, so complete labels,
environment, arbitrary image/container labels, and raw Compose hashes do not cross SSH.

Before Feature 051 policy admission, Feature 050 custody fully decodes and canonical-byte
compares the retained proof and matches the fixed ledger authority plus committed record
revision under target -> host -> stage locks. Those locks remain held through policy
admission, lease preparation, durable host acceptance, and pin promotion. Ledger load
rejects nested proof/pin corruption, overlapping tombstone/retained authority, and the
64-record/proof, 4096-tombstone, or 64-pin maxima.
running image/config/platform/topology/
health evidence is required before the generation CAS. Initializer cleanup removes only a
container whose deterministic name and owner label bind target, image, and declaration; a
foreign or unproved name collision is never removed and returns `effect_unknown`. Output
redaction retains every rendered scalar independently, including duplicate environment keys,
and replaces longer values first so overlapping values cannot disclose suffixes.

Rollback grants are Ed25519 SSH signatures verified with the public key whose digest and
authority identity/revision are in the owner-only activation binding. Bundle reads are
no-follow, regular-file, single-link, owner-only, bounded, and stable. The signing key never
enters Feature 051. Public-route HTTP checks remain reachability diagnostics: required edge
success needs a receipt bound to the request, route, target, prospective generation, runtime
observation, and deployment identity, otherwise activation returns `edge_incomplete`.

`sb host image recover` is separate from failed-apply `sb host recover`. Feature 051 writes
one `authorizing: false` provisional after the first Feature 048 observation, immediately
observes again under the same owner, requires exact evidence and unchanged epochs, then
atomically stores a result plus only a legal promotion. `neither` and `ambiguous` never
promote. `exact_prior` never advances generation. `exact_new` promotes only from a receipt-
complete `runtime_proven` or `edge_pending` phase. Adoption is zero-init and effect-free.
Rollback selects only the retained previous local generation and requires the machine
pre-forward subject/grant referenced by the current generation.
An accepted transaction also retains its closed target, Compose project, and selected
services. Early phases with no candidate generation recover from that context: exact prior,
including an empty generation-zero runtime, closes as no-effect; all other observations stay
unpromoted. Rollback rejects a changed prior Compose project before its first effect.

Source/fake acceptance is not live registered-host, edge, rollback, deployment, or
production proof. Those gates remain open until separately authorized and observed.

Admission also reserves bounded terminal-result storage before effects. Custody first
reconciles exact host acceptance; expired prepared custody with proven absence is cancelled,
while accepted custody replays from its durable phase. Every durable non-uncertain terminal
releases its pin, and uncertain or incomplete authority stays pinned. Each initializer has
an independent prepared/effect/receipt slot. Required edge work persists its exact prepared
request and terminal receipt. Recovery compares a complete persisted service projection—
unique selected services, health, platform, local image, topology, and unchanged epochs—and
provisional crash replay performs only the post-write observation. Apply, sync, login URL,
failed-apply recovery, edge continuation, staging, activation, adoption, rollback, and image
recovery all use the same registered target-mutation owner.

Terminal activation records retain a private, bounded, closed copy of the exact holder and
proof pin beside the public result. This permits terminal-before-release replay to reconcile
the accepted Feature 050 lease and release only that lease; the private pin is not added to
the public result. Missing activation state is seeded from the locked outer host generation.
Recovery results are capped and request identities cannot collide across active, terminal,
tombstone, provisional, or recovery-result authority.

The machine policy also binds the exact Compose service projection, edge-required bit, and
canonical edge route plan/digest. Adoption renders the bound Compose projection and performs
a fresh read-only exact edge observation; it never applies edge state. Init inspection ignores
unrequested image-default environment variables, requires every declared variable to be
present, and obtains target identity from the authenticated registered-target observer plus a
fresh daemon epoch instead of copying the requested target into the observation.

The third bounded review repair closes the terminal private-wrapper decoder by using the activation
model's recursive safe-mapping and secret-field validator. A missing prior generation is represented
as `null` plus an empty prior projection. Edge route evidence is normalized from the current manifest
after the read-only verifier succeeds. Activation and recovery obtain registered-target identity from
the authenticated host projection and daemon identity from Docker before and after observation; init
architecture is obtained from separate image inspection. Adoption receives the same bound Compose
files/project as activation. Recovery reserves its single provisional result slot before observation,
stores only version-1 SHA-256-bound results with exact generation relations, and returns the same
bounded `ok` semantics on fresh completion and terminal replay.

The fourth bounded repair makes recovery a terminal operation on both identities: the recovery request
gets its closed result, and the interrupted activation gets an immutable terminal result/private pin or
tombstone authority before `active` is cleared. Replay therefore cannot repeat protected work and can
finish releasing the exact accepted Feature 050 lease after a crash. Exact activation terminal lookup
precedes current admission checks and does not touch custody. `recovery_no_effect` is deliberately
`ok: false`; only exact-new promotion is recovery success. The init adapter obtains declared values
from the rendered bound Compose service or a narrow opaque provider, passes them only in the closed
synthetic Docker CLI environment, compares actual key/value pairs privately, and returns key-only
inspection evidence. Running platform proof inspects the exact container image ID independently.
Retained pins accept only canonical activation lease, activation owner, SHA-256 proof, and host
acceptance receipt identities.

The fifth bounded repair makes normal terminal replay reconcile the retained private pin before
projecting the public immutable result. Custody lookup is read-before-release and an absent lease is
not recreated. Init values are removed from SSH command serialization: a bounded JSON frame travels
only through SSH stdin, a fixed remote launcher constructs the closed child environment, and output is
redacted against private values before capture. Compose contributes only the declared initializer keys.
The state codec also permits exactly one special active/result overlap: the same request may retain its
active fence plus an exact uncertain result envelope with identical request digest, transaction, and
pin. All other active/result collisions remain invalid.

The sixth bounded repair moves Compose selection and inspect value comparison fully inside the fixed
remote stdin launcher. Compose JSON is stripped of environment maps before stdout; container inspect
JSON is stripped of `Config.Env` and returns only declared keys plus an exact-match boolean. The local
transport never parses a raw value-bearing response. Once `docker create` returns an identity, the
adapter caches only a private source descriptor/value frame and wraps all subsequent epoch/image/target
work with stopped-container cleanup and cache erasure. The narrow uncertain overlap now additionally
requires phase `uncertain` and exact equality of active and retained result envelopes.

The seventh bounded repair binds the opaque Compose value source to the admitted sanitized render.
The in-memory selector carries only Compose files, project, public synthetic image overrides, and the
canonical sanitized render digest. The remote source rerun uses those exact overrides and rejects
nonzero status, any stderr, malformed JSON, missing declared keys, or sanitized digest divergence before
the values can enter the private child environment. The selector and activation state contain no values.
