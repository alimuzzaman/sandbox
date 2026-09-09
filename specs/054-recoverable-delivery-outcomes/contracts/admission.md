# Contract: Ordinary Hosted Admission v1

Applies to every ordinary host apply, including CLI, durable child and MCP-mediated callers. Existing immutable activation authority remains separate.

## Public plan

host plan adds recovery_eligibility with state eligible_at_plan/ineligible/requires_submission, authenticated target identity evidence, independent prerequisite failures, missing bindings, checked_at, and submission_requirements. It is read-only and never says admitted. A valid clean application and target without a current durable child normally reports requires_submission.

The plan names the concrete supported job-start invocation, using the application checkout as --project-dir and the absolute Sandbox executable as the child command. Running a job from the control checkout merely to obtain a job ID is not a valid application-source binding.

## Required ordering

1. Before CLI compatibility writers or command setup that can mutate target/config state, classify host plan/apply and delivery inspect into their owned predispatch path. Plan/inspect use only read-only configuration and owner accessors.
2. Resolve exact registered target, application root/source, configuration, desired route/edge intent and current owner through existing services. Read required controller capability and stable machine identity through authenticated, read-only accessors. No source transfer, remote config preparation, runtime work, route mutation or generation advance.
3. Read only the fixed durable environment keys to locate a retained local-controller job. A read-only jobs owner projection validates schema, exact job/request/project/root/source fields, running lifecycle, live child boot/start/group identity and current process-group membership. Compare source identity, full commit and clean state with the actual application root; require no dirty digest. A maximum five-second wait may resolve a missing just-published child identity; it performs no scheduling or mutation.
4. Reuse existing target-mutation/state/registered-remote lock order. Under these fences, revalidate source/config/registration/machine identity and the absence of a conflicting active/uncertain owner. Check permanent delivery request guards and available capacity before admission. Identical retained request/intention returns its existing status, or delivery_request_expired when detail was evicted, without restarting effects; conflicting intent refuses even after detail expiry. Recheck guard capacity atomically with step 6 persistence; a failure still forbids protected effects and preserves any already committed recovery admission as described there.
5. Resolve the already-authorized secret binding through the existing broker as needed for the existing recovery receipt. Admission-only key/receipt persistence must be fail-closed. Do not stage secrets or deployment files. Retain the existing valid recovery receipt, including its target/source/config/generation/edge bindings, atomically through its owner and read back its exact digest.
6. Retain the initial diagnostic delivery record with the same immutable intent and authority reference. If this fails after recovery admission, report committed_admission with no protected effects and retain its owner. Do not remove it, advance generation, or generate another request.
7. Only now call _prepare_host_apply, source staging/transfer and the remaining authorized runtime/initializer/route workflow. Preserve all existing revalidation and recovery fences. Writer hooks retain progress and terminal evidence without changing their authority.

There is no operation=None execution branch, no swallowed identity error and no nonrecoverable mode. Missing receipt never clears hosting_operation or uncertainty. A malformed/oversized receipt fails before effects. Any existing active fence survives a journal or acknowledgment failure.

## Nested source binding correction

For a nested selected source, preserve the original durable job/source commit `C` and derive the deployed Git artifact commit `T` before admission with the existing `_source_tree_commit`. The closed artifact has exactly `schema_version=1`, `kind`, `root_relative` and `revision`; `git_commit` means `root_relative="."` and `revision=C`, while `git_subtree` means a normalized nonempty proper prefix and `revision=T`. Derive the kind from the actual checkout/root relationship, never from `C != T` or caller input. Compare `T^{tree}` to the selected tree under `C` with bounded read-only Git probes, then freeze the T-derived configuration and the identical artifact in recovery `source.artifact` and delivery `application.source_artifact` (`source_schema=2`).

The artifact and all locally detectable source, prefix, tree, request and configuration conflicts must be validated before `_prepare_host_apply`, source staging/transfer, reset, Compose, initializer, route or edge effects. After admission is committed and read back, publish literal `T` from the returned checkout root and require the pushed SHA to equal `T` before reset or later effects. An unexpected post-dispatch SHA may mean source publication occurred; retain that possible effect and stop, rather than claiming zero effects. Initial extended admission evidence has `source_revision=null`; recovery/diagnosis fills it only from an independent remote observation that proves `T`.

Legacy identity records remain `source_schema=1` and omit the optional artifact. Missing artifact never implies a historical subtree mapping and cannot make `C` join an observed `T`. The original job, branch authorization and source cleanliness continue to bind to `C`; runtime/edge observations bind to actual `T`.

## Identity-only service

resources.context exposes an authenticated stable-target projection that does not construct a local HostMemoryRepository. It reuses HostMemoryRemote's validated envelope, schema, ownership marker and runtime-identity validation. Eligibility requires an explicit allowed evidence state known/partial/unmanaged and valid identity format. Unknown/malformed/unsupported/missing/unrecognized states refuse. Required resource policy uses its existing separate check and result; this accessor cannot make that check pass.

Use the same identity semantics in _authenticated_machine_identity, registered recovery revalidation and ordinary recovery observation. Do not leave an admitted partial-telemetry operation unrecoverable because a later helper still requires full telemetry.

## Read-only job evidence

sandbox/jobs/registry.py owns read_delivery_job_evidence. It opens existing schema v7 or an explicitly supported additive successor in SQLite mode=ro; it does not call constructors, migrate, reconcile, launch, signal, clean up, or read output payloads. The allowlist contains request/project/source bindings, lifecycle/times, target/controller scope and recorded process identity. No argv, environment, credentials or descriptor payload is returned.

Admission and diagnostic reads have different policies: admission requires the matching live child; diagnosis may describe terminal/missing/partial evidence. A failed read never causes another job submission. Compare both the original retained source evidence and the resolved current application; one cannot stand in for the other.

## Typed refusals and reconnect

Codes include recovery_context_required, recovery_context_invalid, recovery_source_dirty, recovery_source_mismatch, recovery_source_artifact_invalid, recovery_source_artifact_mismatch, recovery_source_artifact_unavailable, recovery_source_publication_mismatch, recovery_target_identity_unavailable, recovery_target_changed, recovery_owner_conflict, recovery_receipt_invalid, recovery_receipt_unavailable, delivery_history_capacity and controller_capability_unsupported.

Return safe reason, checked target/request/job when known, admission_state (not_admitted/committed/unknown), effects_started=false when proven, current owner reference and a supported preparation/inspect action. A request flag alone produces recovery_context_required. A missing acknowledgment returns unknown rather than absence or success.

After an interrupted admission, inspect by the original request/operation and exact target. The query reads existing authority and journal independently, showing any gap. Recovery continues only through the existing separately authorized host recover command with its distinct recovery request and original request link. No diagnostic command obtains retry, initializer or cleanup authority.

## Compatibility and proof

Required capability is ordinary_recovery_admission_v1 together with delivery_outcomes_v1 on the executing controller. Extended nested-source writes additionally require ordinary_source_artifact_v1; missing capability refuses before effects and never treats `C` and `T` as interchangeable. Target identity/receipt-dependent remote helpers must advertise their version before effects. Old direct callers receive a typed refusal and durable invocation guidance. No downgrade or force flag.

Negative acceptance records zero source transfers, prepare/runtime/initializer/route calls and generation changes for each missing/mismatched/dirty/unsupported/oversized/persistence/owner case, including pre-effect `C`/`T`/prefix/tree/config conflicts. A post-dispatch unexpected push result is recorded as possible publication and must show zero reset/runtime/initializer/route/edge calls, without claiming zero transfer. Positive evidence retains the admission timestamp/digest before the first protected effect, and recovery keeps initializer execution at most once. Live/remote proof must name original `C` and observed deployed `T`; source tests alone do not satisfy it.

When the original child command selects a manifest directory or file outside the
selected source directory, the recovery receipt additionally retains its
`invocation_root_digest`. This hashes the resolved original command selector;
the job and source root still bind the selected source checkout. Recovery compares
the retained original argv with that optional digest. Its absence preserves the
legacy source-root comparison and is never filled from a later invocation.
