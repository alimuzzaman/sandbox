# Contract: Delivery Diagnosis v1

## CLI and MCP

New command:

~~~text
./sb delivery inspect --project-dir DIR --remote NAME
  (--environment ENV | --label LABEL)
  [--operation-id ID | --request-id ID]
  [--observe] [--limit 1..50] [--cursor TOKEN] [--json]
~~~

Exactly one target selector is required. Omitting operation/request returns the target view. An operation/request selector must match that exact target; no cross-project lookup. Default limit is ten. --observe adds current read-only evidence and is incompatible with a continuation cursor; it never refreshes historical terminal proof.

MCP tool delivery_inspect accepts the same structured fields, with project_dir required, observe=false and limit=10. Both use the same DeliveryService and serializer, injected through the explicit tools manifest and server composition. The tool does not use app helpers, a shell-built command or a second independent interpretation of success.

The executing controller owns the local journal. --remote is the deployed target selector. Inspect an operation launched by another controller through that controller's existing supported execution transport; never silently search another machine's state.

## Envelope and exit meaning

The schema is QueryProjection in data-model.md. CLI exit zero and ok=true mean the bounded query was serviced, including a clearly described unsuccessful delivery or unsupported history. Invalid selector/contract or an unusable whole-query transport returns nonzero/ok=false. Neither is the delivery success field.

Example shape, with illustrative nonsecret identifiers:

~~~json
{
  "schema_version": 1,
  "ok": true,
  "observation_mode": "recorded_only",
  "latest_attempt": {
    "operation_id": "delivery-b",
    "delivery_succeeded": false,
    "delivery_state": "failed",
    "failure_stage": "initializer"
  },
  "latest_retained_complete_success": {
    "operation_id": "delivery-a",
    "delivery_succeeded": true
  },
  "current_observation": null,
  "history": {
    "completeness": "bounded",
    "returned_count": 2,
    "next_cursor": null
  }
}
~~~

This is a shape illustration; production identifiers and full required fields follow the closed model.

## Joining authoritative evidence

DeliveryService depends on explicit owner interfaces: recovery read-only projection, activation status projection, job evidence reader, instance receipt reader, runtime observer and edge proof reader. RecoveryRepository.load is read-only; add its bounded delivery projection there. Do not consume hosts.json directly. Do not call ActivationRepository.snapshot if it acquires mutation locks; expose an owner-provided pure read-only status projection. Do not call JobService.get or durable_job_services to inspect.

Join only when every applicable target, request/job, application source/artifact/config, incarnation/generation and proof identity matches. For extended nested-source operations, the original application/job/admission commit `C` joins the retained job and admission blocks, while runtime/edge/application-authority blocks require the independently observed deployed artifact commit `T` from the frozen `source_artifact`; `C` is never accepted as an alternative `T`. Missing linkage or source observation yields missing/partial; disagreement in either commit or artifact mapping yields conflicting. Preserve each observation time. Never combine an older runtime with a newer route as proof of an unstated common release.

The recovery owner must return the identical typed artifact object and `source_schema=2`; the diagnostic service validates it against `application.source_artifact` and the final configuration before joining. A legacy `source_schema=1` identity projection keeps its existing joins. A missing artifact does not infer a subtree from unequal revisions, and current health cannot manufacture historical source evidence.

Existing immutable activation results retain their schema-specific initializer, signed artifact, generation, runtime and edge decision. The diagnostic adapter may explain stronger proof but cannot reinterpret initializer refusal or use public-route observation to settle activation.

Legacy authority without a journal record is shown in optional recorded_source_evidence with partial/unsupported history. This field uses the same closed evidence-block map, validators and bounds as current_observation, but contains stored owner projections only and may appear in recorded_only mode. Preserve each source timestamp and exact identity joins. current_observation is reserved for --observe and stays null or absent in recorded_only mode. Owner-only reads never backfill journal/history or manufacture latest_retained_complete_success. Reads never migrate legacy state, create a journal, persist observations or add terminal results.

## Writer boundary and partial persistence

Covered command writers create one pre-effect diagnostic record after their required authority/capability checks. Snapshot existing authoritative terminal facts; append bounded progress as known. Identical terminal writes are no-ops; different terminal facts for the same operation are a conflict.

If effects commit but terminal journaling fails, report delivery_record_incomplete with retained authority/operation references and known effects. The query can show current authoritative completion while history remains partial; latest_retained_complete_success refers only to retained complete terminal snapshots. Before another covered mutation overwrites the previous authority record, persist its terminal snapshot or refuse. No cross-store atomicity is assumed.

Recovery uses a distinct operation/request link. The original outcome remains immutable. The query provides an action descriptor containing a supported command and safe selectors, or none plus a reason. It cannot supply missing authorization or synthesize fresh request identities.

The optional query-only `next_action_reason` explains the offered owner read or
why no authorized continuation is established. It uses frozen outcome facts,
including a known failure stage, without rewriting the selected operation or its
terminal digest. Complete failed outcomes are failures, not missing-proof loops. A nonterminal
owner without a usable job read remains `authority_pending`, even when its evidence
is complete. A failed outcome retains its stage explanation when an owner read is
offered; that read never authorizes recovery.
The optional `recovery_operations` field contains completeness (known/partial/missing/expired),
omitted count and at most ten existing operation summaries. Its reverse owner lookup
uses the original operation ID, including a retained expired-detail guard, and exact
project/root/remote/target scope. Missing or omitted recovery detail never changes the
original outcome. Recovery records label inherited application/control fields as
original deployment metadata. Existing required query fields remain unchanged.
Active jobs can offer `job-status` with the original job ID. Incomplete immutable
attempts can offer `host image status` with the original remote, environment and
request ID. Other outcomes offer no action unless an existing safe owner read is
established. No recovery request or authority is generated. Human output supplies
a command template using `--project-dir .` from the inspected project directory
on the same controller and Sandbox home. If `./sb` is elsewhere, use the absolute
path to the same Sandbox executable;
JSON/MCP retain structured selectors. Legacy dotted action names remain accepted
inside retained records for byte/digest compatibility, but new query guidance
never emits them.

The journal owner reserves request_bindings atomically with the initial operation. reserve_request(scope, request_key, operation_id, intent_digest) uses the stable project/target-kind/remote-name/environment-or-label scope and returns the original operation for an identical retained key, delivery_request_expired for identical evicted detail, or request_conflict for changed intent. These compact deny-only guards do not confer workload authority. They never expire or evict: 4,096 guards/controller, at most 1 KiB each, within the existing database cap. A new key at capacity refuses delivery_request_capacity before accepting the delivery record or starting protected effects. Existing-key lookup remains read-only and available. Detail/target-metadata eviction never removes a guard; no reset or namespace rotation may reopen a used key. Exact operation lookup after detail expiry can expose only its guard and explicit missing proof, never a new attempt.

## Bounds and retention

Maximum response is 256 KiB; maximum selected operation is 128 KiB, including 64 events of at most 1 KiB each. History summaries are at most 2 KiB each. Apply the output budget before serialization; replace omitted optional detail with explicit bounds and safe references. Never truncate a JSON byte string into invalid output or silently drop required evidence while retaining complete status.

Pagination is by a stable monotonic journal sequence bounded by the first page's snapshot high-water mark. Cursor carries version, scope/filter digest, high-water mark and last sequence, at most 512 bytes. New writes do not enter old pages. Retention-invalidated cursors yield delivery_cursor_expired with explicit history limits; cross-target or malformed cursors yield delivery_cursor_invalid.

Terminal detail retention is 30 days, 64 per target and 512 globally. Open or uncertainty-pinned records have a separate 128-record cap. Permanent request guards have their separate 4,096-record cap and survive all detail/target-history pruning. An expired request returns delivery_request_expired and cannot execute again. No query prunes. Missing/expired detail or lost target metadata is explicit, and historical completeness is never inferred from present health.

## Secret-safe output

Use closed allowlisted objects followed by existing redact_structure/redact_text as defense in depth. Drop bodies, login/admin token URLs, cookies, authorization headers, secret environment, private argv and private paths. Public hostnames and validated origin/path may be retained; raw query values become equality results/digests only when nonsecret. Errors carry typed codes and capped safe text, never an arbitrary upstream exception string.

## Observable parity

Repeated CLI/MCP calls against the same recorded fixture must agree on identifiers, delivery success, evidence completeness, next action and history limits; timestamps belonging to the query may differ. Nested-source output must preserve `C` versus `T`, the exact artifact mapping and null/missing source evidence when `T` is not independently observed. Hashes/counts of existing authority and journal rows remain unchanged. --observe may issue bounded read-only network probes, but cannot write routes, runtime state, generation, recovery, job transitions or diagnostic history.

## Full-command trace extension and remaining output precision

The additional trace selector grammar, producer write APIs, query envelope, bounded owner ports and exact role joins are defined in [Deployment trace v1](deployment-trace.md). Trace inspect uses the original control project and --trace-id or --trace-request-id; it rejects legacy target/operation/observe/pagination selectors. This is a discriminated service route, not a weakening of the legacy query scope. CLI/MCP share the same codecs and read-only behavior.

Existing command writers must actually append observed stage/effect events before terminal settlement; an available but unused append_event helper does not meet the timeline requirement. Replayed writes add no event, the ring remains 64, and old empty history is never invented. Correct new terminal reason mapping only; legacy terminal document bytes/digests remain unchanged. Query-only next_action_reason may explain a frozen failure without editing it. Render safe command templates with shlex.join on the same actual executable/controller/home and project cwd.

The delivery owner exposes bounded reverse recovery reads across at most 640 retained documents, matching original operation plus exact scope and returning at most ten summaries with omitted/expired/unknown coverage. A later recovered child never rewrites the original failure or latest retained original success. An empty retained list proves only that no retained matching child was found. Inherited requested/control fields are labelled original deployment metadata, not fresh recovery executor observations.

The new activation trace port must supply exact source/artifact/config/plan/proof/runtime/public dimensions through bounded owner-native decoding; the old result/generation projection alone is insufficient. Missing/legacy proof remains partial. Schema-v2 image references are parsed only after native validation; more than 32 manifests retain partial coverage and a generation/proof reference instead of truncation or increasing the delivery schema limit. Full trace queries use the contract's cooperative deadline and separate elision codec; they never execute producer code or current network observations.
