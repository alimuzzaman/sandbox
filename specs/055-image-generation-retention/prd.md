# Product Requirements Draft: Release Retention and Restore

**Status**: Discovery

**Created**: 2026-09-16

**Last Refined**: 2026-09-16

**Input**: "Image generation retention on deploy. When a new image is deployed the old image should be deleted, keeping one previous generation for backup/restore. The sandbox CLI/runtime should do this, not an operator by hand."

**Drafting Configuration**: Root Opus 5 (1M context), no delegation for drafting; one independent readiness reviewer under the repository delegation policy.

**Final Validation**: `REOPEN` — independent readiness review, 2026-09-16; scope materially revised since, so a new review is required.

**Validated On**: 2026-09-16

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Terminology

A **release** is the complete set of service image digests activated together on
one deploy target, together with the schema state recorded at that activation.
A release is the unit this feature retains, bounds and restores.

The word *generation* is deliberately avoided: image staging already uses it for
an optimistic-concurrency token, and reusing it would produce ambiguous
downstream requirements.

## Problem and Motivation

A deployment replaces the images a set of services run, but nothing in the
runtime ever removes the images it replaced, and nothing records what the target
was running beforehand.

Two distinct costs follow. Storage grows without bound on every deployment host.
And recovery from a bad deployment is unreliable: the prior images may happen to
still exist, or may have been removed by an unrelated reclaim, and nothing tells
the operator which set of digests constituted the last known-good state.

Measured on the `scaleway-sandbox` host on 2026-09-16:

- 75 unique images totalling 47.18 GB, of which 10.68 GB (22%) is genuinely
  reclaimable once shared layers are accounted for.
- One representative deploy target comprises roughly eighteen services activated
  together — a web tier, a database, a queue, a dozen workers, and two one-shot
  jobs — not a single image.
- Six superseded digest-pinned images each of three of those services, all 8-9
  days old and still resident. They are untagged but not dangling, so routine
  dangling-image pruning does not remove them.
- Every long-running service in that target defines a container healthcheck and
  currently reports healthy, including non-HTTP workers and the queue. One-shot
  jobs define no healthcheck and are judged by exit status.
- A schema migration job runs as part of every deploy of that target and mutates
  persistent state shared by all of its services.
- No image deletion capability exists outside the confirmed reclaim path, and
  the deployment path has no image lifecycle at all.
- No capability restores a target to a previous release. Recovery today means
  rebuilding or re-pulling, which is slow and may not reproduce the same
  artifacts.

Why now: this host is at 227.9 GiB used with images as the second largest
managed consumer, and the same unbounded pattern applies to every deployment
host the runtime manages.

**Expected near-term impact, stated plainly.** Because images that predate this
feature are reported rather than deleted, it frees little or nothing on the
measured host the day it ships; that backlog stays until the reclaim path runs.
What it delivers immediately is a bound on future growth across every service of
a target, an exact record of what each release consisted of, and an atomic
restore path that does not exist today. The large one-time storage win on that
host comes from the reclaim path and from the out-of-scope build cache.

Source-tree citations supporting these statements are kept in the review record
rather than here, since they date faster than the product intent does.

## Users and Desired Outcomes

- **Operator deploying a target**: a deploy leaves behind a predictable number of
  releases, without a separate reclamation step.
- **Operator recovering from a bad deploy**: the previous release is known to
  exist as a complete, exactly identified set, and can be put back in one
  deliberate action rather than by rebuilding.
- **Operator avoiding an untested combination**: restoring never produces a mix
  of service images that were not built and verified together.
- **Operator who has run a migration**: restore tells them plainly when the
  schema has moved past what the target release expects, rather than appearing to
  succeed and misbehaving afterwards.
- **Operator managing host capacity**: image growth is bounded by the number of
  deploy targets and the configured retention depth, not by how many times they
  have been deployed.
- **Operator choosing what to restore**: the retained releases for a target can
  be listed before acting, each by exact identity and activation time.
- **Operator auditing what the runtime deleted**: every image the runtime removed
  is attributable after the fact, to the evidence standard the reclaim path
  already meets.

## Goals

- Bound resident releases per deploy target to the current one plus a configured
  number of previous releases, defaulting to one.
- Record each release as an explicit, durable, exactly identified set of service
  image digests plus the schema state at activation.
- Reclaim the images that no retained release still needs, as part of deploying,
  with no second operator action.
- Let an operator list the retained releases for a target before acting.
- Provide a deliberate, single-action restore that returns a target to a named
  retained release atomically.
- Refuse a restore that would place a release against a schema it does not
  expect.
- Preserve existing deletion-safety properties: exact identity, no inferred
  families, no broad or wildcard removal, and a durable record written before
  anything is deleted.
- Cover every service of a target, using verification signals that already
  exist.

## Non-Goals

- **Restoring data, volumes, or persistent state.** A restore returns service
  images. It does not roll back database contents, object storage, queue state,
  or any volume. Rolling back persistent data discards everything written since
  the target release and is a backup and point-in-time-recovery problem with a
  different recovery objective and different safety requirements. Conflating the
  two under the word "restore" is the failure mode this non-goal exists to
  prevent.
- **Reversing schema migrations.** The feature records schema state and refuses
  on mismatch; it never attempts a down-migration.
- **Guaranteeing that a restored release works.** Only backward-compatible
  migration discipline can make image rollback safe. The feature detects and
  refuses the clearly unsafe case; it cannot certify the rest.
- **BuildKit build cache.** It is the larger consumer on the measured host
  (96.63 GB, 86.19 GB reclaimable) and is governed by unrelated policy.
- **Automatic rollback.** Restore is an operator action, never triggered by a
  failed deploy on its own.
- **Local development instance images.** The unbounded growth is in deployment
  images; local instances share common base images.
- **Changing what `sb resources cleanup` does.** It remains the tool for every
  image this feature never recorded, including the pre-existing backlog.

## Product Scenarios

### Scenario 1 — Ordinary redeploy

- **Starting state**: A target runs release R3. R2 is retained at the default
  depth of one.
- **User action**: The operator deploys R4.
- **Expected outcome**: The target runs R4, R3 is retained, and R2's images are
  deleted except any still referenced by R4 or R3. Deletions are recorded before
  they happen.

### Scenario 2 — Images shared between retained releases

- **Starting state**: Only three of eighteen services changed between R3 and R4.
- **User action**: The operator deploys R4.
- **Expected outcome**: The fifteen unchanged service images are not deleted,
  because retained releases still reference them. Only images no retained release
  needs are removed, and the reported saving reflects that.

### Scenario 3 — Releases that predate the feature

- **Starting state**: A target has superseded images resident from before the
  feature existed, belonging to no recorded release.
- **User action**: The operator deploys.
- **Expected outcome**: The new release is recorded and the previous one
  retained. Images belonging to no recorded release are **not** deleted; they are
  reported as unmanaged and remain the reclaim path's business.

### Scenario 4 — A service in the new release fails verification

- **Starting state**: A target runs R3 with R2 retained.
- **User action**: The operator deploys R4; one worker never reaches a healthy
  state.
- **Expected outcome**: R4 is not recorded as a verified release and nothing is
  deleted. R3 and R2 remain intact, so a partly failed deploy never reduces the
  operator's fallback options. The report names the service that failed.

### Scenario 5 — One-shot job fails

- **Starting state**: The migration job of a new release exits non-zero.
- **User action**: The operator deploys.
- **Expected outcome**: The release is not treated as verified and nothing is
  deleted.

### Scenario 6 — Superseded image is still referenced by a container

- **Starting state**: An image belongs to no retained release but a stopped
  container still references it.
- **User action**: The operator deploys.
- **Expected outcome**: The image is not deleted; the deploy reports that it was
  kept and why.

### Scenario 7 — Image is shared with another deploy target

- **Starting state**: Two deploy targets have at some point run the same image.
  It falls outside retention for one and is part of a retained release of the
  other.
- **User action**: The operator deploys the first target.
- **Expected outcome**: The image is not deleted, because another target's
  retained release claims it. Reference checks against containers alone would not
  have caught this.

### Scenario 8 — Shared layers mean little is reclaimed

- **Starting state**: The deleted images share almost all layers with retained
  ones.
- **User action**: The operator deploys.
- **Expected outcome**: The reported reclaimed amount reflects bytes actually
  freed, not apparent per-image size.

### Scenario 9 — Concurrent deploys of the same target

- **Starting state**: Two deploys of the same target overlap.
- **User action**: Both complete.
- **Expected outcome**: Retention never deletes an image a release another
  in-flight deploy just made current. One operation refuses rather than
  interleaving.

### Scenario 10 — Deletion fails after the record is written

- **Starting state**: A deploy has recorded its intent to delete images.
- **User action**: Removal fails partway, for example because the host becomes
  unreachable.
- **Expected outcome**: The deploy's own success is not reversed. Failures are
  reported per image, the record shows them as intended but not completed, and a
  later deploy neither double-deletes nor loses track.

### Scenario 11 — The release record cannot be written

- **Starting state**: The durable record is unwritable.
- **User action**: The operator deploys.
- **Expected outcome**: No image is deleted and the deploy says why. Without
  evidence there is no deletion authority.

### Scenario 12 — Restore after a bad deploy, schema unchanged

- **Starting state**: A target runs R4, which is faulty. R3 is retained and the
  schema has not advanced since R3.
- **User action**: The operator restores the target to R3.
- **Expected outcome**: Every service of R3 is activated together, verified by
  the same signals a deploy uses. R4 becomes a retained release rather than being
  deleted, so the operator can inspect it or return to it.

### Scenario 13 — Restore blocked by a schema that moved

- **Starting state**: A target runs R4. R3 is retained, but R4's deploy ran a
  migration and the schema has advanced past what R3 expects.
- **User action**: The operator attempts to restore R3.
- **Expected outcome**: The restore refuses before changing anything, names the
  recorded schema state of R3 and the current one, and explains that restoring
  images would place R3's code against a schema it does not expect.

### Scenario 14 — Restore when nothing is retained

- **Starting state**: A target has only ever been deployed once, or no previous
  release was recorded.
- **User action**: The operator attempts a restore.
- **Expected outcome**: The restore refuses, states that no retained release
  exists, and changes nothing. It never falls back to an inferred set of images.

### Scenario 15 — A retained release is incomplete on the host

- **Starting state**: A release is recorded as retained, but at least one of its
  service images is no longer present.
- **User action**: The operator attempts a restore.
- **Expected outcome**: The restore refuses, names the exact missing images, and
  leaves the running target untouched. It never restores a partial release.

### Scenario 16 — Restore fails verification

- **Starting state**: The operator restores to a retained release.
- **User action**: One or more services of the restored release do not verify.
- **Expected outcome**: The restore ends in exactly one of an enumerated set of
  reported end states, each naming which release the target is running
  afterwards. No retained release is deleted as a consequence.

### Scenario 17 — Repeated restore

- **Starting state**: Depth is one. A target runs R4 with R3 retained.
- **User action**: The operator restores R3, then restores forward to R4.
- **Expected outcome**: The behavior is defined and stated: whether each restore
  promotes the replaced release and evicts the other, and whether the operator
  can end with nothing to return to.

### Scenario 18 — Retention depth raised

- **Starting state**: An operator sets retention depth to two for a target.
- **User action**: The operator deploys repeatedly.
- **Expected outcome**: Three releases remain retained. The deploy reports the
  resolved depth and where the value came from.

### Scenario 19 — Listing retained releases

- **Starting state**: A target has retained releases.
- **User action**: The operator lists them.
- **Expected outcome**: Each is shown by exact identity, activation time, its
  per-service image digests, its recorded schema state, and whether every image
  it needs is still present, so the operator can tell in advance which are
  actually restorable.

## Proposed Product Behavior

- A deploy records the set of service image digests it activated, together with
  the schema state at activation, as a release for that deploy target.
- A release is treated as verified only when every service of the target reaches
  its success condition: a healthy container health status for services that
  define a healthcheck, a successful exit for one-shot jobs, and the existing
  route and release-identity verification for the web tier.
- Once the new release is verified, the deploy deletes images that no retained
  release and no container still needs, so retention converges on the current
  release plus the configured depth.
- Retention depth is configurable and defaults to one previous release. The
  resolved depth and its source are reported.
- Deletion is automatic and on by default. This is a deliberate departure from
  the runtime's existing confirmed-deletion posture, bounded by the three
  authority constraints below.
- Deletion never proceeds on an image that any container references, that any
  retained release of any target still needs, that no recorded release ever
  contained, or that is identified by tag or inferred pattern rather than exact
  digest.
- Every deletion is durably recorded before it occurs.
- A deploy that does not reach a verified release deletes nothing.
- The deploy reports the release it recorded, what it deleted, what it kept and
  why, and how much space was actually freed.
- An operator can list retained releases for a target, including whether each is
  still complete on the host.
- A restore activates every service of a named retained release together, as one
  operation, verified by the same signals a deploy uses. It treats the release it
  replaced as a retained release rather than deleting it.
- A restore refuses, before changing anything, when the named release is
  unrecorded, incomplete on the host, or expects a schema state other than the
  current one.

## Constraints and Dependencies

### Deletion authority

Existing policy requires deletions to be recorded before they happen, forbids
inferring a family or emitting broad or wildcard reclaim, and keeps automatic
cleanup off by default. Automatic deploy-time deletion is a confirmed operator
decision that narrows, but does not remove, that posture. Three constraints make
the narrowing real:

1. **Only images of recorded releases are deletable.** This feature may delete
   only an image that a release it recorded contained, for a target it just
   deployed. Any other resident image is outside its authority, however it looks.
2. **Bounded blast radius per deploy.** A deploy may delete at most the images of
   that target's releases falling outside the resolved depth. If the computed set
   is larger, it deletes nothing and reports.
3. **Evidence before authority.** If the durable record cannot be written, no
   deletion occurs.

### Other constraints

- **Images are shared across releases and targets.** Only a subset of services
  changes between releases, so the same digest routinely belongs to several
  retained releases and sometimes to several targets. Eligibility must be
  determined by whether any retained release still needs an image, never by which
  release superseded it.
- **Atomicity of restore.** A release must be restored whole. Restoring a subset
  produces a combination that was never built or verified together.
- **Schema state is part of a release.** Because a migration job runs on every
  deploy of the measured target, a release is only meaningful together with the
  schema state it was activated against. Restore depends on that state being
  recorded at deploy time; without it, the refusal in Scenario 13 cannot be
  evaluated.
- **Exact identity is mandatory.** Superseded images are digest-pinned; removing
  by tag only untags and reclaims nothing. Image staging already carries exact
  repository digests and local image IDs.
- **Record locality.** The release record must be readable by any operator who
  can deploy the target, not only by the machine that performed the deploy. The
  existing deletions journal is control-machine-local while images live on the
  remote host; if the release record inherits that locality, an operator
  restoring from another checkout sees nothing retained while good images sit on
  the host. Either the record is reachable by every operator who can deploy, or
  the feature states plainly that retention and restore are per-operator-machine.
- **Verification signals already exist but are not consulted.** Today's deploy
  verifies route exposure and, when a release identity is declared, that the
  expected commit or artifact digest is served at the hostname. Container health
  status and one-shot exit status exist per service but are not part of any
  deploy decision. This feature depends on those being brought into the deploy's
  verification result.
- **Counting unit.** Retention is bounded per deploy target, so two targets on
  one host each keep their own releases even when they run identical digests.
- **Which operation is "a deploy".** Retention attaches to the operator-visible
  act of activating a new release. The runtime today splits this between the
  deploy command and the image staging and activation path; the specification
  must name one as the trigger and state what happens when images change through
  the other.
- **Evidence record ownership.** Deletions must be recorded to the evidence
  standard the reclaim path meets — exact identity, timestamp, reason,
  originating operation. Whether that shares the reclaim journal or is its own
  artifact is a specification decision, and deploy must not become a direct
  consumer of reclaim internals.
- **Configuration precedence.** Retention depth resolves through the existing
  configuration precedence, and the resolved value must be reported.
- **Layer sharing.** Per-image sizes overstate recoverable bytes substantially.
  Reported savings must be bytes actually freed.
- **Idempotency.** Re-running a deploy or restore must be safe, must not delete
  an image twice, and must never delete an image the current release needs.
- **Docs land with code**, per the repository constitution.
- **Live-stack evidence** is the only accepted proof, per the constitution.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Retained unit | The release: all service image digests activated together, plus schema state | A target is ~18 services; restoring a subset yields a combination never built or verified together | User |
| Retention depth | Configurable, default one previous release per target | Bounds steady state at two releases while allowing a deeper history when bisecting | User |
| Who deletes | The deploy path, automatically, after the new release verifies | Reclaims without a second operator step | User |
| Restore | In scope, as an explicit operator command, atomic across the release | A retained set nobody can activate delivers only the storage half of the intent | User |
| Restore trigger | Operator action only, never automatic | Automatic rollback on a failed deploy is a separate and riskier policy | User |
| Schema handling | Record schema state per release; refuse a restore on mismatch | A migration runs every deploy; restoring images alone would place old code against a newer schema | User |
| Coverage | All services, via container health, one-shot exit status, and existing route verification | Every long-running service already defines a healthcheck, including non-HTTP workers | User |
| Data and volumes | Out of scope for restore | Rolling back persistent state discards everything written since, and is a backup/PITR problem | User |
| Deletion eligibility | An image is deletable only when no retained release and no container needs it | Images are shared across releases and targets; superseded-by is not the same as unneeded | Evidence |
| Deletion authority | Only images of recorded releases, bounded per deploy, only with a writable record | Makes the departure from confirmed-deletion genuinely narrow | Existing policy |
| Identity | Exact image ID or repository digest only | Tag removal does not reclaim a digest-pinned image; inferred families are forbidden | Existing policy |
| Pre-existing images | Reported, never auto-deleted | Deleting an unrecorded image requires inferring a family, which policy forbids | User |
| Counting unit | Per deploy target | Two targets on one host each keep their own releases | User |
| Terminology | "Release", never "generation" | *Generation* is already an optimistic-concurrency token in image staging | Evidence |
| Build cache | Out of scope | Unrelated policy and lifecycle | User |
| Per-deploy opt-out | **Open** | Preserves bisecting workflows, but is another way the bound is not reached | Open question |

## Open Questions

1. **Per-deploy opt-out.** Should a flag disable retention for a single deploy?
   It preserves bisecting and investigation workflows, but is another way the
   bounded steady state is not reached. Not yet confirmed.

## Acceptance Outcomes

- After three or more consecutive deploys of a target at the default depth, the
  images retained for it are exactly those needed by the current release and one
  previous release, plus any image a container or another target's retained
  release still needs.
- No image is deleted while any retained release of any target, or any running or
  stopped container, still needs it.
- No image that no recorded release ever contained is deleted by this feature.
- Every image this feature deletes is recorded before removal; if the deploy is
  interrupted between recording and deletion, the record is present and marks the
  image as intended for deletion.
- A deploy in which any service fails to verify records no new release and
  deletes nothing, and names the failing service.
- Where only a subset of services changed between two retained releases, the
  unchanged images remain resident and the reported saving reflects only the
  images actually removed.
- Reported reclaimed bytes match the host's measured change in image storage for
  that deploy to within a stated tolerance, measured with no other image-mutating
  operation in flight, and never report the sum of apparent per-image sizes.
- With retention depth set to two, three releases remain retained, and the deploy
  reports the resolved depth and its source.
- Listing retained releases shows, for each, its per-service digests, activation
  time, recorded schema state, and whether it is still complete on the host.
- A restore activates every service of the named release, verified by the same
  signals the deploy uses, with the replaced release retained rather than
  deleted.
- A restore against an unrecorded release, an incomplete release, or a release
  whose recorded schema state differs from the current one refuses before
  changing anything and names the reason.
- A restore ends in exactly one of an enumerated set of reported end states, each
  naming the release the target runs afterwards.

## Risks and Assumptions

- **Risk**: Automatic deletion in the deploy path is authority the runtime has not
  previously exercised unattended. Mitigated by the three authority constraints,
  verification gating, cross-release and cross-target reference checks, and exact
  identity; residual risk remains.
- **Risk**: Operators may still read "restore" as reverting the whole system,
  including data. The schema refusal surfaces the most dangerous case, but a
  restore that succeeds still leaves all data as-is.
- **Risk**: Schema state may not be derivable for every target. A target with no
  migration step, or an opaque one, cannot support the Scenario 13 refusal, and
  the feature must state what it does then rather than assuming a state exists.
- **Risk**: At the default depth of one, a regression noticed only after a second
  deploy finds the last good release already beyond retention.
- **Risk**: Expected savings disappoint because retained releases share most
  images and most layers. On the measured host the apparent 54 GB of superseded
  images corresponds to 10.68 GB genuinely reclaimable across all images.
- **Risk**: Concurrent deploys of one target race over which releases are
  retained.
- **Assumption**: The measured superseded images were placed by the runtime's own
  deployment path. If any arrived through host-side pulls outside it, retention
  would not have bounded them and the measurement does not validate the
  mechanism. Provenance must be confirmed before treating it as the baseline.
- **Assumption**: Container health status and one-shot exit status are
  trustworthy enough to gate deletion. A service that reports healthy while
  misbehaving would let a good release fall out of retention.
- **Assumption**: The set of services constituting a target is knowable at deploy
  time, so a release can be recorded as a complete set rather than accumulated
  piecemeal.
- **Assumption**: Exact digests recorded by image staging are sufficient to
  identify images. Whether a durable per-target release history exists today is
  not established, and this feature likely introduces it.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [ ] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [ ] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent readiness review verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
