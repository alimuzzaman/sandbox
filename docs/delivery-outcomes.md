# Recoverable delivery outcomes

Feature 054 adds one bounded, read-only way to explain hosted apply, immutable
activation, deploy exposure, and preview attempts. It joins existing job,
hosting, activation, instance, runtime, and edge owner evidence. It does not
create another execution or activation authority.

Covered operations use the versioned capabilities
`delivery_outcomes_v1`, `ordinary_recovery_admission_v1`,
`instance_creation_receipt_v1`, and `delivery_route_verification_v1`. A
controller that cannot advertise the required capability refuses before the
dependent effect.

**Verification status:** the capability and W11 producer are candidate source
work in the current dirty batch. Supported command exercises, the same
controller executable and home, installed-controller capability, hosted
recovery, remote/runtime, and public-route acceptance remain pending
verification. This guide claims no pass from source inspection or local checks.

Original request IDs retain their owner namespace, including `/`, and remain bounded to 256 ASCII characters across delivery lookup, creation context, receipt and URL-result joins. They are opaque keys, never filesystem paths. Operation, job, instance and label identifiers keep their narrower limits.

## Ordinary hosted apply needs durable admission

Every ordinary `host apply` must enter through a durable local `job-start`
child whose retained job evidence binds the exact application checkout, full
source commit, request ID, live child identity, and controller. The existing
recovery receipt must then be committed and read back before source transfer,
initializer/runtime/route effects, or a generation advance. A delivery journal
record cannot replace that recovery receipt.

Request IDs retain the owning operation's exact spelling, including namespaced
activation IDs such as `activate/release-a` (up to 256 ASCII bytes). These are
opaque diagnostic selectors, never filesystem paths. Other diagnostic identifiers
retain their narrower limits.

Apply validates the local branch and declared source policy before reading
registered remote identity or recovery state. A disallowed branch needs no
remote access to refuse.

Use the application checkout as `--project-dir` and invoke the same Sandbox
control executable by its full absolute path in the child command:

```sh
/absolute/sandbox/sb job-start --local --project-dir /absolute/app \
  --source-commit FULLHEAD --request-id ORIGINAL --timeout 900 -- \
  /absolute/sandbox/sb host apply --project-dir /absolute/app \
  --remote registered-remote --environment staging --confirm --json
```

`FULLHEAD` must be the full application commit from `/absolute/app`. The
second `/absolute/sandbox/sb` is the child control executable; do not replace it
with a relative `./sb` or a different checkout. Starting a job from the Sandbox
control checkout merely to obtain a job ID does not bind the application source.
The application revision, Sandbox source/control revision, and installed
controller runtime revision are separate evidence fields and must be reported
separately.

Keep the returned job ID and inspect it through the supported job owner:

```sh
/absolute/sandbox/sb job-status ORIGINAL_JOB_ID --json
/absolute/sandbox/sb job-output ORIGINAL_JOB_ID --stream stderr \
  --tail-bytes 8192 --max-bytes 8192
/absolute/sandbox/sb delivery inspect --project-dir /absolute/app \
  --remote registered-remote --environment staging \
  --request-id ORIGINAL --json
```

`host plan` is read-only. A clean application and target normally report
`recovery_eligibility.state=requires_submission` until the durable child exists;
plan output is never admission. A direct `host apply` with only
`--request-id`, or a legacy caller without a durable receipt, is unsupported and
must be fenced with `recovery_context_required` before protected effects. There
is no force or nonrecoverable ordinary path.

Authenticated target identity is identity-only evidence. Valid `known`,
`partial`, or `unmanaged` identity states may be usable for admission while
optional memory/swap telemetry is incomplete. The separate required resource
policy still runs and can refuse the apply. Missing, malformed, copied, or
unrecognized identity cannot authorize admission, signaling, or rebinding.

If admission or the acknowledgement is interrupted, inspect the original
request first. Continue only through the existing separately authorized
`host recover` command with a distinct recovery request and the original
request link:

```sh
./sb host recover --project-dir DIR --environment ENV --remote NAME \
  --job-id JOB_ID --original-request-id APPLY_REQUEST \
  --request-id RECOVERY_REQUEST --expected-generation N --json
```

Diagnosis never grants retry, initializer, deployment, or cleanup authority.

## Deployment trace v1

The deployment trace is a separate diagnostic owner for one full command. It
does not replace job, activation, delivery, recovery, or instance authority.
The normative closed definitions, stage rules, joins, and compatibility
requirements live in [Deployment Trace v1](../specs/054-recoverable-delivery-outcomes/contracts/deployment-trace.md);
this section only explains how to use the boundary.

The trace capability is `deployment_trace_v1`, with producer
`lenzora-hosted-v1` and projection schema 1. The executing Sandbox controller
advertises the capability and runtime revision before a producer can write.
The producer must use the same absolute Sandbox executable, controller, and
`SANDBOX_HOME` for its capability, start, record, owner-status, owner-record,
and inspect calls. `--project-dir` stays the original W11 control checkout for
every trace call. A different checkout, controller revision, home, or project
root is a separate context and cannot continue the original trace.

Use the trace selectors through the CLI:

```sh
./sb delivery inspect --project-dir /absolute/w11 \
  --trace-id TRACE_UUID --json
./sb delivery inspect --project-dir /absolute/w11 \
  --trace-request-id TRACE_REQUEST_UUID --json
./sb delivery inspect --project-dir /absolute/w11 \
  --trace-id TRACE_UUID --mutation-id MUTATION_UUID --json
```

The corresponding capability and bounded writer calls are:

```sh
./sb delivery trace-capabilities --json
./sb delivery trace-start --project-dir /absolute/w11 \
  --trace-request-id TRACE_REQUEST_UUID --input-json TRACE_START_JSON --json
./sb delivery trace-record --project-dir /absolute/w11 \
  --trace-id TRACE_UUID --mutation-id MUTATION_UUID \
  --expected-sequence N --input-json TRACE_RECORD_JSON --json
./sb delivery trace-owner-status --project-dir /absolute/w11 \
  --producer lenzora-hosted-v1 --parent-request-id PARENT_REQUEST_ID --json
./sb delivery trace-owner-record --project-dir /absolute/w11 \
  --producer lenzora-hosted-v1 --parent-request-id PARENT_REQUEST_ID \
  --publication-id PUBLICATION_UUID --expected-sequence N \
  --input-json OWNER_RECORD_JSON --json
```

These calls return closed acknowledgements with the original IDs, sequence,
and accepted document digest. JSON input is passed as an argument and is
bounded/validated; it is not a path, shell fragment, callable, or executable.

`--trace-request-id` is the lookup used before a trace ID is known or after a
lost start acknowledgement. `--mutation-id` is only valid with `--trace-id`
and returns that original receipt. Trace selectors form their own query mode;
do not combine them with the legacy `--remote`, environment/label,
operation/request, observe, limit, or cursor selectors. The legacy operation
query keeps its required remote and target scope. MCP
`delivery_inspect` uses the same selectors and meaning, and the trace writer
ports are `delivery_trace_capabilities`, `delivery_trace_start`,
`delivery_trace_record`, `delivery_trace_owner_status`, and
`delivery_trace_owner_record`.

The phases have separate meanings. `preflight` starts a trace before checks;
a passing preflight has `command_result=succeeded` and
`deployment_result=not_started`, and creates no workload job or activation.
The main command records producer stages and its own command result. Prepare
and activation workers retain their existing job/request IDs and publish
parent-keyed role evidence; they do not receive a trace ID in argv. A main
process that disappears after a child commits remains command-unknown while
the child owner result is shown separately. The joined deployment result is a
Sandbox query-time evaluation of retained producer projections plus bounded
read-only job, activation, delivery, and recovery owner evidence. It can be
incomplete or unknown even when the producer reported success.

Start and record are diagnostic writes with bounded JSON input. Each accepted
mutation has an immutable mutation/publication ID and monotonic sequence. If
an acknowledgement is lost, inspect the original request, trace, or mutation
first. Replay the identical payload and original ID only after a conclusive
missing lookup. An ambiguous lookup stops. Never mint a new trace, publication,
job, workload request, initializer, or deployment to recover an acknowledgement.
Owner-status is a receipt lookup, not `job-status`; owner-record cannot create
a parent without the validated first projection. Existing recovery fences and
owner terminal facts remain decisive.

A trace keeps its first parent identity. Later reports cannot switch its run
or contradict a known application revision. Revision checks work in both
orders: source selection before the run report, or source confirmation after it.

Hosting checkpoints record a short phase and effect-state event when either
changes. Repeating the same checkpoint adds no event. Retained event history
is bounded and reports omissions; terminal replay preserves the original facts.

Each phase job's `submission_digest` is a producer-recorded candidate. The
native job owner independently recomputes it from the retained role request,
control identity, and exact command digest; private argv and environment never
enter the trace. A matching candidate digest alone is not proof. The trace adds
no new `job-status` output: use the existing `job-status` owner for lifecycle
state and trace owner-status for publication receipts.

The bounds are finite: 10 seconds for a trace writer call; a cooperative
5-second aggregate query budget; 256 KiB query output; 32 KiB trace input and
producer projection; 128 KiB trace document; 64 events of 1 KiB each; 32
links; 11 stages; and 256 mutation receipts. The trace store allows at most
128 open/uncertainty-protected traces, 512 unprotected terminal traces
globally, 64 per project/producer/target scope, and 30-day terminal detail.
The SQLite busy bound is 2 seconds, the database cap is 128 MiB with up to
128 MiB rollback space, and permanent deny-only request/parent guards share a
4,096-entry controller cap. Producer parent records have their own 128
protected, 512 unprotected, 64-per-scope, 128 KiB, and 30-day maxima while
sharing the byte budget.

Trace queries open existing state read-only. They do not create stores,
migrate, reserve guards, prune history, reconcile owners, run project code,
start jobs, or perform network observations. The query may elide optional
projection, role, recovery, and event detail to stay within 256 KiB; it keeps
coverage and omission reasons. A missing, expired, unavailable, or timed-out
owner is partial evidence, never success.

Human output states early-history coverage and the counts of omitted events,
links, and recoveries, plus whether the producer projection was omitted.

Job evidence uses a private in-memory snapshot, with no SQLite connection to
owner files. Stable WAL commits are checked against the published index head
and frame checksums. Capture is limited to 32 MiB of database and WAL bytes,
a 32 MiB image, and 65,536 frames within the shared query budget. Changed,
unsafe, unsupported, or corrupt captures remain partial. Reads create no
sidecars and make no application writes to owner state; filesystem access-time
accounting can still occur.

Producer projections are claims with `producer_recorded` provenance. They
become useful proof only where the existing owner independently matches the
exact project, source/control identity, target, request, artifact, generation,
runtime, initializer, and route/edge evidence. A route write, healthy service,
accepted job, receipt-only result, or current health observation cannot prove a
historical deployment. W11 control source, application release source, and
installed controller runtime remain separate fields; a requested artifact or
source revision is never substituted for observed runtime evidence. A legacy
projection is an explicit read-only export from the existing run directory;
it starts at the earliest retained evidence and cannot reconstruct preflight
history or alter old run, job, or terminal bytes.

The capability and producer wiring passed isolated supported CLI/MCP/W11
exercises, including lost acknowledgements, an interrupted main with completed
workers, immutable parent/source binding, bounded events, and WAL read purity.
Focused regressions cover those observed cases. Installed-controller, remote,
native runtime, and public-route proof remain separate gates; synthetic native
fixtures cannot supply those proofs.

## Inspect recorded outcomes

The CLI command is:

```text
./sb delivery inspect --project-dir DIR --remote NAME \
  (--environment ENV | --label LABEL) \
  [--operation-id ID | --request-id ID] \
  [--observe] [--limit 1..50] [--cursor TOKEN] [--json]
```

Exactly one of `--environment` and `--label` is required. An operation or
request selector is exact and remains bound to that project and target. The
default limit is 10. The current MCP tool has the same meaning and shared
serializer:

```text
delivery_inspect(project_dir, remote, environment=None, label=None,
                 operation_id=None, request_id=None, observe=False,
                 limit=10, cursor=None)
```

With no operation/request selector, the default query is `recorded_only`. It
reads retained owner projections and history without network probes, migration,
pruning, reconciliation, or writes. `--observe` changes the mode to
`current_read_only`; it may perform bounded current runtime/route/edge probes,
but does not refresh terminal history or write an observation record. Observe
cannot be combined with a cursor.

Query success and delivery success are different. CLI exit zero and `ok=true`
mean that the bounded query was serviced, even when the delivery failed or its
history is unsupported. Invalid selectors or an unusable whole-query transport
return nonzero/`ok=false`; neither field is the delivery result.

The target view keeps these answers separate:

- `latest_attempt` is the newest retained attempt, whether it succeeded or
  failed.
- `latest_retained_complete_success` is the newest retained terminal success
  with complete retained evidence.
- `current_observation` is present only for `--observe` and describes the
  current read-only state. It cannot manufacture an older success.

For success A followed by failed attempt B, the target query reports B as
`latest_attempt` and A as `latest_retained_complete_success` while both remain
retained. Legacy owner facts without a journal row are shown as partial or
unsupported source evidence; reads do not backfill history. The ordinary
hosted owner also remains partial when it lacks an independently observed
aggregate configuration digest, even if another terminal owner fact says
success. A healthy current runtime does not prove a historical delivery.

The operation document is capped at 128 KiB and the full response at 256 KiB.
Terminal detail is retained for 30 days, at most 64 per target and 512
globally. Open or uncertainty-pinned records have a separate 128-record cap.
History pages use a stable bounded cursor (at most 512 bytes) and a first-page
high-water mark. Invalid, cross-target, expired, or retention-invalidated
cursors return an explicit error.

## Public exposure proof

The optional project configuration is a closed `delivery` object. A route write
or reload is only `configured`; it is not `verified`:

```json
{
  "delivery": {
    "schemaVersion": 1,
    "routes": {
      "deadlineSeconds": 120,
      "aliasPolicy": "serve_or_redirect_to_primary",
      "checks": [
        {
          "path": "/health",
          "statuses": [200],
          "markers": [
            {"kind": "header_equals", "field": "X-Application", "expected": "example-app"}
          ]
        }
      ],
      "releaseIdentity": {
        "required": true,
        "path": "/health",
        "kind": "header",
        "field": "X-Release",
        "expectedFrom": "application_commit"
      },
      "edgeProof": {"required": false}
    }
  }
}
```

The normalized route contract permits one to eight checks, one to four markers
per check, one to eight allowed statuses per check, and one to 20 unique
hostnames. Paths are absolute public paths without query, fragment, authority,
or userinfo. Unknown keys, secret-like values, sensitive headers, and
oversized contracts are rejected.

`deadlineSeconds` defaults to 120 and is limited to 10–300 seconds. The CLI
`deploy` and `preview create` commands accept `--verify-timeout` in the same
range; the value is frozen into the requested outcome. Verification gives every
requested primary and alias host its own DNS, HTTP-to-HTTPS redirect, TLS,
path/query-preservation, status, and application-marker checks under one finite
aggregate deadline. A generic 200, a successful route write, or a healthy
container is not enough. One failed alias keeps the exposed result non-success.

WordPress without an explicit contract uses the documented default: `GET
/wp-json/` must return 200, JSON `/url` must equal the primary public origin,
and `/namespaces` must contain `wp/v2`. This proves application availability;
it does not prove a release identity. A generic Compose application needs an
explicit marker contract or returns `delivery_route_contract_required` before
requested exposure effects.

Release identity is optional. With no mechanism, the result says
`release_identity_state=unsupported` and its scope is
`application_availability_only`. With `required=true`, the expected exact
application commit or artifact digest must be available and every applicable
host must pass it. Sandbox control revisions cannot stand in for application
release identity. Required edge proof remains a separate owner check and
cannot be disabled by this project contract. Current route evidence retains
the optional closed `route_contract` alongside the current observation, and
revalidates the registered remote identity before probing it.

`--observe` route evidence is finite: at most two attempts per host/check, five
redirects per request, five seconds per blocking network attempt, and 64 KiB of
response body per check. A deadline, unavailable required proof, worker reap
uncertainty, redirect/query mismatch, TLS failure, wrong backend/release, or
missing marker is failed or incomplete, never exposed-site success.

## Exact creation and URL ownership

Covered instance apply requires a succeeded ensure receipt and exact incarnation, then reserves a separate permanent apply-phase guard. Repeated covered apply returns unknown without repeating effects. Creation completion alone does not prove apply completion. These phase guards share the creation store's 4,096-record limit.

Covered deploy/preview ensure calls carry a closed `CreationContext` with the
original operation/request, frozen nonsecret intent digest, project identity,
root digest, and exact label. The instance owner commits a `CreationReceipt`
with the exact `instance_id` and `instance_incarnation_id` before dependent
source, instance, or URL effects. The relation is `created`, `reused`, or
`unknown`; reuse never becomes cleanup ownership, and an inventory difference
never proves creation.

The current `ensure` CLI exposes pure owner modes:

```sh
./sb ensure --project-dir DIR --label LABEL --creation-capability \
  --creation-kind compose --creation-runtime-mode compose --json
./sb ensure --project-dir DIR --label LABEL \
  --creation-prepare-json '{"delivery_intent_digest":"...","target_scope_digest":"...","create_allowed":false}' --json
./sb ensure --project-dir DIR --label LABEL --creation-receipt \
  --creation-context-json CONTEXT --expected-incarnation INCARNATION --json
```

Capability, preparation, and receipt queries do not call `ensure`, initialize a
runtime, or mutate an owner. A matching retained request is lookup-only. A
lost response, failed startup, pending child, missing receipt, or incarnation
drift stays pending/unknown/expired and cannot replay creation.

The covered URL writer is an explicit owner mutation mode. It accepts only the
original context and expected incarnation and changes the selected instance's
`home` and `siteurl` values:

```sh
./sb ensure --project-dir DIR --label LABEL --creation-url-json \
  '{"instance_id":"INSTANCE","url":"https://public.example"}' \
  --creation-context-json CONTEXT --expected-incarnation INCARNATION --json
```

The owner rechecks the exact incarnation before each write and before readback.
It retains per-field results, so one successful and one failed write returns
`remote_instance_url_incomplete`. A changed incarnation returns
`instance_incarnation_changed` and stops further writes. A repeated original
request returns its retained URL result without repeating writes; changed
intent conflicts before effects. URL results never contain old/new secret-
bearing URLs and never add deletion authority.

Delivery request guards are permanent deny-only replay protection: at most 4,096
guards per executing controller, each at most 1 KiB, within the 96 MiB delivery
database cap. Creation request guards have the same 4,096-per-controller bound
in an 8 MiB instance-owner database with at most 8 MiB rollback space. Neither
guard store expires, evicts, resets, or reopens a used key after detail,
target, receipt, or instance deletion. A new key at capacity refuses before
effects (`delivery_request_capacity` or `creation_request_capacity`); a changed
intent conflicts, and an old key with evicted detail returns typed
expired/unknown evidence without replay.

These records explain attempts and preserve safe continuation boundaries. They
do not authorize deployment, image rebuilding, production changes, credential
access, or destructive cleanup.

Ordinary apply's terminal runtime and edge blocks retain the applied configuration
digest used by their owner transaction. The diagnostic join compares that binding
with the admitted outcome; a missing or different digest cannot be complete.
This binding does not add independent byte-for-byte runtime configuration proof.

New ordinary applies require `ordinary_source_artifact_v1`. They keep the original
submitted Git commit and a typed Git artifact mapping separate. A nested source
is split and its tree checked locally before admission; the derived environment
and configuration digest use that fixed subtree revision. Publication pushes the
literal prepared object. Runtime and edge evidence must match the deployed
revision, while admission and job evidence retain the original submitted commit.
Old records without a mapping keep their original meaning and digest; inspection
does not infer a historical subtree from today's source or mutate old receipts.

When the original child command selects a manifest directory or file outside the
selected source directory, the recovery receipt additionally retains its
`invocation_root_digest`. This hashes the resolved original command selector;
the job and source root still bind the selected source checkout. Recovery compares
the retained original argv with that optional digest. Its absence preserves the
legacy source-root comparison and is never filled from a later invocation.

Artifact preparation refuses with `source_artifact_unavailable`,
`source_artifact_invalid`, or `source_artifact_mismatch`. A mismatched publication
return stops before reset and configuration writes; retained effects still show
that publication may have started. Exact tree checks and subtree preparation
disable Git replacement objects so the checked objects match literal publication.

An original `ensure` replay reports success only when its retained creation receipt
succeeded. Failed receipts return `instance_ensure_failed`; pending or unknown
receipts return `creation_request_unknown`. Both retain the receipt and perform no
new runtime work. A read-only receipt lookup can still succeed as a query while
reporting a failed or pending creation outcome.

## Diagnosis guidance compatibility

`next_action_reason` explains failure at the retained stage or the absence of an
authorized continuation. Complete failures do not ask the user to repeat the same
diagnosis for missing proof. The only generated actions are the original active
job's `job-status` or an incomplete immutable attempt's `host image status`, with
exact selectors. Human output gives a template for the same controller and project.
Neither authorizes recovery. Historical dotted action names and messages remain
unchanged inside retained records; current query guidance is separate. This is an
additive schema-v1 query field, with legacy projections still readable. Candidate
source evidence does not establish an installed controller supporting this field.
