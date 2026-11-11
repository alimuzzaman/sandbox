# Research: Shared server secret use

Status: bounded source-design research complete on 2026-11-11. No network, runtime, credential or secret-metadata inspection. Source facts are not installed capability evidence.

## Existing source boundaries

- `sandbox/commands/host_stack.py::_up` resolves the remote and constructs/reads a lifetime store before the later `_secrets_gate`. Its plan path exports Compose source and calls lifetime checks. An early server branch must precede all of this, including `--plan`; bypassing only the missing-key error is insufficient.
- `sandbox/commands/hosting.py::_secret_status` merges required/generated mappings and resolves personal values. `_nonsecret_host_intent` hashes destination shape without exact source mappings. Keep both personal-local compatibility behaviors; neither becomes server admission.
- PRD/readiness evidence identifies separate Cloudflare fallback and env-file delivery. This shard does not reroute or enable those consumers; a partial replacement at the early key gate would leave later custody failures.
- `sandbox/commands/manifest.py` already registers hosting. `sandbox/cli.py` owns the current host parser bridge. New behavior must stay within that registered contract, without an unrelated parser rewrite.
- `.python-version` declares 3.12. Existing subprocess support provides synthetic captured-child environments.

## Decisions

### Explicit scope with declaration references

Decision: Use the existing host family with explicit server flags and pinned source/profile references. Preserve omitted scope as personal-local for compatibility. No global schema or template migration.

Rationale: The immediate value is an honest source-only result on a clean laptop and a refusal before side effects. CLI declarations are sufficient; pretending to resolve a server registry locally would add false authority.

Alternatives considered: Automatically use server credentials whenever `--remote` appears, or rewrite template/config schemas. Both widen behavior and risk changing personal flows. Requiring dummy local keys is forbidden.

### Static validation and capability refusal are separate results

Decision: `host validate` can validate declarations with availability unverified. All effectful server actions, including stack plan/up, refuse unconditionally in this slice. Server inventory reports unavailable.

Rationale: A declaration check can run without dispatch. Existing stack plan does remote/lifetime work and cannot become the static path merely by skipping secret lookup.

Alternatives considered: Reuse effectful plan and mask missing keys, inventory over SSH, environment inheritance, or a user capability switch. None establishes installed authenticated authority. No new transport is justified.

### Minimal implemented types

Decision: Implement immutable selection, exact mapping and bounded static/refusal results actually consumed by host branches. Define future binding and state transitions in contracts only.

Rationale: Unused grant stores, lock protocols, journals and admitted-operation constructors would be speculative and easy to mistake for security enforcement. Disabled branches need no request reservation or persisted replay state.

Alternatives considered: A generic distributed reservation/grant framework with synthetic success. It cannot prove genuine admission and expands attack/review scope without a working consumer.

### Canonical identity and shared ownership

Decision: Future installation identity is a stable installation authority recognized by the installed authenticated service, with owner context and runtime revision checked separately. No machine/uid/home digest alone, no client minting, no new identity file or migration in this slice.

Rationale: The read-only sibling owner-dispatch proposal explicitly distinguishes context proof from complete installation identity. Secret-use owns authenticated actor/broker binding; sibling owns target/source/lifecycle authority.

Alternatives considered: Treat `OwnerLocal.identity`, authority digest or context schema as a grant. They lack the required actor/installation/admission guarantees. No guessed import or callable adapter is added.

### Future replay and custody

Decision: Resolve nonsecret references before scoped atomic reservation; retain missing-key no-effect refusal; commit admission only after successful preflight. Frozen replay, explicit linked retry only after KNOWN terminal refusal, and original-id inspection for uncertainty are mandatory. All proposed wire remains unaccepted.

Rationale: Repair, lost responses or concurrent equal-rights actors must never reopen or exchange authority. Reservation is not admission. Provider values move only from the broker to its intended trusted server consumer, never through public transport or retained data.

Alternatives considered: Auto-new-id retry, value-hash intent, ambient secrets, env files or generic sudo secret runner. Each violates custody, identity or existing rights. Preserve generic escalation guards and require an approved hosting consumer integration later.

## Remaining dependency facts

The sibling proposal is PROPOSED/UNAVAILABLE. No admitted executor or supported server inventory was found or accepted by this plan. Exact future versioned wire schemas, canonical identity enrollment, durable atomic storage composition, finite policy values and complete lifecycle enforcement need combined design/acceptance before integration. They do not block implementing a permanently disabled source slice. Root reservation on 2026-11-11 resolves numbering: this feature uses 086 and sibling hosted-site-baselines retains 085. No new latency, capacity or retention promise is made.
