# Research: Front-door-only refresh

## R1. Why a same-revision apply cannot refresh the route

- Decision: build a separate refresh rather than relax `_runtime_apply_decision`.
- Rationale: `edge_only` there requires `exact_runtime_proven`, which needs a declared
  source revision. Relaxing it would let an apply skip Compose on an unproven runtime and
  then record delivery evidence it never observed. The front door does not depend on the
  runtime revision, so the refresh needs no proof at all.
- Alternatives: `host apply --front-door-only` (rejected: the apply path accepts a hosting
  operation, publishes source and resets the checkout before its decision point; a flag
  would have to bypass most of it); `host recover --continue-edge` (rejected: it needs a
  proven runtime and a pending edge phase, and it also writes DNS).

## R2. Delivery attempt or none

- Decision: no delivery attempt and no hosting-operation change.
- Rationale: the delivery journal records source/runtime delivery and fences the next
  apply on its predecessor. A refresh delivers neither. Recording one would replace the
  hosting operation the next apply's predecessor check reads. The refresh is instead
  serialized with every writer (effect lock + remote lease), audited, and recorded on the
  target (`front_door_refresh`).

## R3. What "recorded state" can be trusted

- Decision: bind to fields only a successful apply or edge continuation writes:
  `loopback_port`, `records` (applied name/content/proxied), `certificate`, `caddy_name`,
  `front_door`, `dns_coverage`, and the new `front_door_declaration` digest.
- Rationale: a refused apply rewrites `hosting_operation`, `hostnames`,
  `requested_revision`/`staged_revision` and sets `runtime`/`edge` to `pending` before it
  refuses (hosting.py `_apply_host_unleased`), so those fields say what was attempted, not
  what is live.
- Legacy targets (no digest yet) are additionally bound by the installed route's privacy
  mark and basic-auth presence (no weakening), then the digest is recorded.

## R4. The fence left by a refused apply

- Evidence (controller store, read-only): job `8b474591` (wp-test-stacks) is
  `execution_state: unknown`, `pinned_reason: effect_unknown`, `failure_stage: source`;
  job `40ab97ea` (test-stacks) the same, under checkout `source/10c2f692ff93-…`.
- The fence is `HostingAttempt.require_previous_snapshot`: the next apply refuses
  `required_evidence_missing` until that predecessor is terminal. Recovery's
  `active_operation` is not set by an apply, so `_assert_no_active_host_operation` passes.
- Decision: the refresh runs on such a target and leaves the record alone (spec
  clarification 1). `retire-delivery` marks `retired_unproven_runtime`, so the next apply
  recreates containers; operators retire only before their next real apply.

## R5. Stack delivery selectors

- Evidence: `delivery inspect --project-dir <checkout>` reads the checkout's
  `sandbox.hosting.yml`, which is the stack template; the manifest reader raises
  `StackTemplateError`, which `inspect_with_factory` maps to `delivery_store_unavailable`.
  With the rendered stack dir, the scope is the current checkout, while the Hub's attempt
  lives under a checkout a later `stack up` replaced, so inspect answers `missing` with the
  advisory `binding_mismatch`.
- Decision: `--name`/`--target` resolve the target (as `host status` does), and the scope
  comes from the target's `hosting_operation` (`project_identity`, `project_root_digest`,
  remote, environment) through one shared helper also used by `require_previous_snapshot`.
  Retire falls back to that scope only for the exact request id the record names.

## R6. Certificates

- Decision: check-only. Caddy proxied: origin files present; Caddy DNS-only: Caddy's own
  ACME, nothing to check. nginx proxied: origin files present and the recorded hostname list
  equals the declared names; nginx DNS-only: the public certificate's SANs cover every name.
  Anything else refuses `front_door_refresh_certificate_missing`; no issuance.

## R7. Audit

- Decision: controller-side append-only JSONL opened before the first remote change
  (pattern from `host_exec.AuditLog`), fixed fields only, plus the remote `apply.log` lines
  the edge adapters already write.
