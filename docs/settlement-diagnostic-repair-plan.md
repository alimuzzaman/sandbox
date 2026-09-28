# Bounded settlement refusal diagnostics

Status: source preparation only; implementation not started. Integration owner must accept ownership before coding.

## Outcome and evidence

Make existing read-only settlement refusals explain the failed safety predicate without weakening that predicate. Before: `ok:false, code:evidence_changed`. After: the same refusal plus a closed diagnostic such as `reason:container_process_disappeared`, `subject:owned_container`, and a validated full container ID. A failure must never become a successful observation or a containment plan.

Base: clean `c7ed709ba35ef2485c4e3d71ccb80ad09207be2c`. Isolated checkout: `/private/tmp/sandbox-settlement-diagnostics-20260909`; branch: `codex/settlement-diagnostic-plan-20260909`. The installed remote was independently matched to runtime `bebe6ee03db17fb10c4c1b73` on 2026-09-09. These are baseline facts, not acceptance evidence for this change.

Observed production owner: request `lenzora-prod-fa945494a20852a46697fc1f105efe4233895c44-activate`, transaction `sha256:dcc9f85b3d8a155f6589290acb96d8382aff1b79e6ab006f88d7e7d4d61247b9`, generation 0. Exact-owner `observe` returned `not_quiescent`; `containment-plan` returned `evidence_changed`. No durable job ID is proven for this activation. Do not infer one or require a fabricated job wrapper.

Source explains ambiguity, not the actual live cause. `private_containment._process_binding` and the second snapshot comparison both emit `evidence_changed`; the adapter also uses that code for target identity mismatch. Settlement inventory emits `not_quiescent` for matching helper activity, owned container state/restart policy, or another running consumer of retained data. Existing envelopes discard this distinction.

## Proposed ownership

This task owns one bounded implementation package, after coordination:

| File | Change |
| --- | --- |
| `sandbox/hosting/images/activation/settlement_diagnostics.py` (new) | Small pure closed diagnostic constructor/validator and typed private refusal; no I/O or authority logic. |
| `sandbox/hosting/images/activation/private_containment.py` | Attach reasons at existing read-only checks and compare already collected snapshots. |
| `sandbox/hosting/images/activation/private_settlement.py` | Attach reasons at existing inventory predicates; preserve scan limits and stop behavior. |
| `sandbox/hosting/images/activation/settlement_observer.py` | Include the pure helper in standalone program assembly; validate private diagnostics and attach adapter identity-check reasons. |
| `sandbox/hosting/images/activation/settlement_service.py` | Carry an optional validated diagnostic through `SettlementError` and `_observe` without changing codes. |
| `sandbox/hosting/images/activation/settlement_cli.py` | Add diagnostics to failures only for `observe` and `containment-plan`. |
| `docs/image-activation-settlement.md` | Contract, interpretation, compatibility, and exact command examples. |
| `tests/test_hosting_image_activation_settlement_diagnostics.py` (new) | Closed contract and disclosure regression coverage. |
| `tests/test_hosting_image_activation_private_containment.py` | Predicate and snapshot-difference cases. |
| `tests/test_hosting_image_activation_settlement_observer.py` | Standalone helper envelope and adapter validation cases. |
| `tests/test_hosting_image_activation_settlement_service.py` | Refusal propagation without authority changes. |
| `tests/test_hosting_image_activation_settlement_cli.py` | Supported read-only CLI output and unchanged mutation-phase envelopes. |
| `tests/integration/hosting_settlement_observer_canary.py` | After actual command runs, add regression cases derived from observed output. |

The plan document is also owned here. No other files are preauthorized. In particular exclude all `hosting.py` files, Feature054 files, job lifecycle/boot identity code, runtime revision implementation, transport registration, recovery/settlement repositories, signing/policy/model contracts, activation graph, source schema recovery WIP, Lenzora, runtime/vendor files, and the dirty `24eb` checkout. No edits to `settlement_containment.py` or its apply loop are needed. If an outer shared hosting wrapper loses the diagnostic, report that exact boundary to its owner; do not edit it here.

## Output contract

Keep the existing outer `schema_version:1`, exit status, `ok:false`, phase, and top-level code. Add one optional `diagnostic` object on the two read-only phases. It is advisory and never accepted as approval, observation, plan, receipt, replay identity, or persistence input.

Closed fields: `schema_version:1`, `reason`, `subject`, `sample`, and optional `container_id`. All strings except the ID are enums. `sample` is `identity_before`, `first`, `second`, `identity_after`, or `comparison`. `subject` is `target`, `daemon`, `owned_container`, `container_set`, `helper_activity`, `data_consumer`, or `observation`. The sole public host identifier is a lowercase 64-hex container ID, only for an already positively owned container. For an external data consumer, return the category only. Do not emit PID, names, paths, mount names, command text, environment, label values, image configuration, process start values, raw exception text, binding keys or digests of private data. Existing outer request/transaction selectors already supply incident correlation.

Initial reason allowlist and existing predicates:

| Reason | Subject | Evidence used |
| --- | --- | --- |
| `target_identity_changed` | target | Existing pre/post identity equality checks. |
| `daemon_identity_changed` | daemon | Existing Docker daemon comparison. |
| `container_set_changed` | container_set | Existing IDs/rows checks or two-snapshot membership difference. No IDs from an unvalidated set. |
| `container_process_disappeared` | owned_container | Existing missing `/proc` file after validated Docker ownership. |
| `container_process_owner_unavailable` | owned_container | Existing cgroup/start identity predicate. |
| `container_binding_changed` | owned_container | First differing existing binding in sorted IDs after membership equality; no raw binding output. |
| `container_paused`, `container_state_invalid` | owned_container | Existing state predicates. |
| `container_not_stopped` | owned_container | Existing quiescence condition for running/restarting/PID/status. |
| `container_restart_enabled` | owned_container | Existing restart policy refusal after stopped-state validation. |
| `helper_activity_present` | helper_activity | Existing bounded process marker match; no argv/PID. |
| `retained_data_consumer_running` | data_consumer | Existing overlap check; no consumer ID or paths. |

Do not diagnose by parsing exception messages. Raise a typed refusal at the predicate with a validated diagnostic. For compound conditions, retain the same refusal code and evaluate safe structural/type/ownership guards first, then the existing conditions in a deterministic order. Missing/malformed private responses, unknown codes/reasons, extra keys, unsafe IDs, transport failures, incomplete samples, and legacy responses retain the existing refusal with no diagnostic. Absence means unavailable, never “no drift.” Preserve existing early termination: do not continue scanning after a failure merely to produce more reasons, and do not add a second observation if the current path fails before it. Sample labels describe only checks actually reached.

The small pure module must work both as a normal import and as source prepended to the existing standalone helper program. It must not import the Sandbox package in the generated remote program. Reuse one validator rather than copying reason lists into helpers. No dependency, manifest consumer, new CLI flag, command, new MCP group, or generic diagnostics framework.

## Sequence and invariants

1. Integration owner accepts file ownership and base. Preserve the plan-only checkpoint. Inspect differences from the eventual integration head; do not merge or deploy.
2. Complete all production code and matching documentation across the owned files. No tests written or run and no feature acceptance commands during this coding phase. Existing source and the recorded refusals guide implementation.
3. Freeze the implementation diff. Exercise real supported commands in a disposable registered Linux environment with matching candidate control/runtime. This requires separate authorization for provisioning or lifecycle updates; current authorization is preparation only. Do not use the shared production controller to test an unaccepted candidate.
4. Only after those actual command runs, write focused regression tests based on the behavior, then run focused and repository-required gates via finite durable jobs. Preserve request IDs and returned job IDs. Do not interleave individual code edits and tests; if actual runs expose defects, finish a correction batch before rerunning the workflow, then update regression coverage.
5. Review the final diff and observed outputs for correctness and disclosure. Commit and push verified source under repository policy. Report exact SHA/runtime digest and limits. Integration, controller installation, production diagnostics, containment, settlement, and forward activation are separate decisions.

Preserve target/generation checks, locks, fixed budgets, strict host ownership, HMAC bindings, process proof, restart handling, double-snapshot equality, all confirmation gates, failure codes, nonzero exits, and durable records. No retries, sleeps, polling loops, persistence, inventory expansion, stop/update commands, privilege changes, credential access, or new identity allocation from diagnostics. Mutating paths may encounter the same typed refusal internally but must not change their public or persisted schemas.

## Actual supported acceptance

After implementation and authorization for an isolated test target, use a registered disposable environment with a retained uncertain fixture activation. Fixture ownership and creation must use the existing approved canary facility, never synthetic production state. Before starting, prove that this facility can supply the retained owner through supported commands. If it cannot, report the missing fixture capability instead of directly writing activation state and claiming CLI acceptance.

From the matching clean candidate control, run:

```sh
./sb remote service status DISPOSABLE_REMOTE --json
./sb host image status --project-dir DISPOSABLE_PROJECT --environment DISPOSABLE_ENV --remote DISPOSABLE_REMOTE --json
./sb host image settle --settlement-phase observe --project-dir DISPOSABLE_PROJECT --environment DISPOSABLE_ENV --remote DISPOSABLE_REMOTE --request-id RETAINED_REQUEST --expected-generation RETAINED_GENERATION --activation-transaction RETAINED_TRANSACTION --json
./sb host image settle --settlement-phase containment-plan --project-dir DISPOSABLE_PROJECT --environment DISPOSABLE_ENV --remote DISPOSABLE_REMOTE --request-id RETAINED_REQUEST --expected-generation RETAINED_GENERATION --activation-transaction RETAINED_TRANSACTION --json
```

Retain bounded JSON plus exit codes and exact candidate/runtime revisions. A running owned fixture must still refuse observation while identifying `container_not_stopped`. A restart-enabled stopped fixture must identify `container_restart_enabled`. A quiet stable fixture must preserve existing successful observation/plan bytes. A real restart-churn fixture may return a plan when stable or a typed refusal when changing; do not require a fabricated deterministic race. Use regression fault injection later to distinguish process disappearance from snapshot mismatch. Any fixture setup/cleanup authority covers only owned disposable resources and must never be inferred for production. The existing containment restart canary mutates containers, so do not run it under a read-only acceptance claim.

Independent before/after supported status must show the same active request/transaction/generation and unchanged lifecycle/restart policy for deterministic fixtures. No plan or settled receipt may appear after a refusal. If only a private-helper canary runs, label it helper evidence, not supported CLI or production acceptance.

## Regression checks after real runs

Use `tests.subprocess_support.run_test_process` and synthetic environments for every captured subprocess. Test valid and invalid enum/type/ID combinations, extra keys, adversarial secret-like strings and exceptions, oversized/untrusted helper output, legacy replies without diagnostics, pre/post identity drift, missing process after ownership, differing snapshots, changed container membership, helper activity, and external data consumers. Assert output excludes sentinel credentials, private paths, argv and environment. Assert no new reads after early refusal and zero update/stop/state writes from the read-only workflow.

The falsifying case is a helper response with a valid top-level refusal but a malicious or mismatched diagnostic: it must keep the refusal and drop that diagnostic, rather than trusting or interpolating it. Verify `_observe` preserves only validated diagnostics; mutation phases and durable replay remain byte-compatible. Run the affected settlement modules, then `PYTHONPATH=.` full unittest discovery through the documented durable job workflow. Treat preexisting failures explicitly; do not weaken gates or equate test counts with live success.

## Revision, capability and release boundary

`sandbox/services/runtime_revision.py` discovers Python sources recursively, so these code changes naturally change the runtime digest. No hand-edited revision constant or shared command manifest is needed. The diagnostic object's version is its additive capability signal. Older replies remain usable as refusals with diagnostic unavailable; consumers must never infer detailed support from `ok:false` alone. Inspect actual wrapper pass-through as acceptance evidence. No new global capability flag or unsupported option.

Before any approved controller installation, finish required gates and security-control review of disclosure and unchanged authority. Install only via the supported lifecycle command and independently verify the installed digest equals the clean integrated source. Never install this older-base branch over concurrent Feature054 work. The integration owner chooses the final combined revision after both branches pass their relevant gates. This task has no present approval to merge, deploy, update the controller, stop services, apply containment/settlement, retrieve credentials, or modify production.

Completion handoff: files changed; exact commit and runtime digest; actual command outputs and retained job IDs; tests actually run and results; independent review findings; remaining integration/fixture/release limits. No live cause is established until a new accepted diagnostic actually observes it.
