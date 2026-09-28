# Image activation incident settlement

Settlement records an operator-reviewed incident as `abandoned_with_effects`.
It retains the original uncertain transaction and result, preserves current and
previous generations, and leaves the generation counter unchanged. It performs
no container, volume, database, provider, or deletion operation. It releases the
incident's stage-proof custody only after its terminal record is durable.

Use ordinary `host image recover` first when retained observations can prove the
outcome. An empty runtime after effect entry does not prove absence of data effects.
Settlement requires reviewed backup receipts, a data compatibility assessment,
and an exact signed operator approval. It is never an automatic retry option.

## Observe and prepare a plan

Check local and installed Sandbox revisions first. Observation uses only the
registered remote and retained Compose project; it does not regenerate candidate
configuration. The fixed Linux helper requires metadata access through noninteractive
sudo. It checks the daemon and boot epoch, exact container identities, preserved
layers, volume and bind identities, and relevant host processes. Containers must
be stopped with restart disabled. Running consumers of shared data refuse the
observation. Private paths, configuration and environment remain in the helper;
only keyed inventory digests and closed identity fields are returned.
Legacy graph drivers and the containerd `overlayfs` snapshotter use explicit
storage identity projections. Container state, volume metadata, preserved paths,
running consumers and daemon identity are rechecked before returning. An empty
inventory can be reviewed, but still means unknown data effects and requires
the same backup assessment and exact approval; it cannot establish no-effect.

```sh
./sb host image settle --settlement-phase observe \
  --project-dir /path/to/project --environment production --remote REMOTE \
  --request-id SETTLEMENT_ID --expected-generation GENERATION \
  --activation-transaction sha256:TRANSACTION --json
```

If observation reports `not_quiescent`, settlement cannot stop containers or
disable restart policies. Any necessary containment is a separate, explicitly
authorized operation. `observation_unavailable`, `evidence_changed`, and
`remote_runtime_revision_mismatch` also leave the incident intact.

`containment-plan` can bind Docker's exact restart-wait state: running and
restarting, not paused, status `restarting`, and PID 0. That state has no current
process, but is not quiescent. Positive PIDs still require the existing cgroup
and process-start proof. A process disappearing during that read returns
`evidence_changed` without substituting a process identity. All container, image, mount, label, restart-policy,
daemon, transaction and generation bindings remain checked. The separately
approved `containment-apply` disables restart and stops only those exact IDs,
preserving their volumes. A changed state before effects invalidates the plan;
the helper does not retry or replace an uncertain request. Final stopped and
no-restart evidence, then independent settlement observation, are still required.

Prepare a closed `SettlementDataAssessment` JSON artifact containing the exact
target, incident transaction, intended application revision, one to eight sorted
backup receipt digests, and the reviewed `forward_initialization_reviewed`
decision. Its digest uses the domain
`sandbox.hosting.images.settlement-data-assessment.v1`. Receipt digests identify
real reviewed evidence; a syntactically valid digest is not backup or restore proof.

Run the same command with `--settlement-phase plan` and
`--settlement-data-assessment /path/to/assessment.json`. Save only its nested
`plan` object as `/path/to/plan.json`. The plan binds the original request, current
generation, assessment and fresh observation. Review the complete plan before
approval. A new observation or changed data requires a newly reviewed plan.

## Install approval and apply

The operator signs `SettlementApproval.signature_payload()` with an Ed25519 key
under SSH namespace `sandbox-feature-051-settlement`. The closed approval binds
the plan digest, authority identity/revision and an admission lifetime of at most
one hour. Store the armored SSH signature as base64 in `signature` and compute
the approval digest using the model's canonical body. Sandbox accepts only the
public key and signed approval file; it neither reads nor creates the private key.

```sh
./sb host image settle --settlement-phase install-approval \
  --project-dir /path/to/project --environment production --remote REMOTE \
  --request-id SETTLEMENT_ID --expected-generation GENERATION \
  --settlement-plan /path/to/plan.json \
  --approval-file /path/to/approval.json \
  --approval-public-key /path/to/operator.pub --confirm --json

./sb host image settle --settlement-phase apply \
  --project-dir /path/to/project --environment production --remote REMOTE \
  --request-id SETTLEMENT_ID --expected-generation GENERATION \
  --settlement-plan /path/to/plan.json \
  --settlement-approval sha256:APPROVAL --confirm --json
```

Installation is immutable and owner-only, scoped to the exact target and approval
digest. Apply holds target ownership, verifies installed authority, takes two
fresh observations matching the plan, and rechecks authority before commit.
`persistence_uncertain` never releases custody. `custody_pending` means the
terminal record exists but custody release needs replay. Repeat the exact request,
plan and approval selectors: durable replay precedes credentials, observation and
admission expiry, and only completes any pending custody release.

## Approve the next forward activation

The first activation at the unchanged generation must be a new graph activation
with separate `ForwardSettlementApproval` authority. It binds the settlement
terminal digest, original transaction, new request ID, target/generation,
application revision, plan/proof/snapshot/policy/rollback-grant digests and reviewed
data assessment. Its SSH namespace is `sandbox-feature-051-settlement-forward`.
A settlement signature cannot authorize this role.

Install the signed forward artifact with the same explicit target selectors and
`--settlement-phase install-forward-approval --request-id NEW_ACTIVATION_ID`,
`--approval-file`, `--approval-public-key`, and `--confirm`. Then add
`--settlement-forward-approval sha256:FORWARD_APPROVAL` and
`--settlement-predecessor sha256:SETTLEMENT_TERMINAL` to the normal image activation
command. All normal image, private-input, initialization, runtime and edge checks
still apply. Adoption, rollback and reuse of the uncertain request do not consume
this authority. The signed subject remains in transaction, generation and recovery
evidence. Missing, expired or mismatched authority refuses before new custody or
runtime effects; an exact durable successful activation replay remains readable.

## Read-only refusal diagnostics

Failed `observe` and `containment-plan` commands may include an advisory
`diagnostic` object. The top-level failure code, nonzero exit, and every recovery
gate are unchanged. For example:

```json
{"schema_version":1,"ok":false,"code":"not_quiescent","operation":"settle","phase":"observe","diagnostic":{"schema_version":1,"reason":"container_restart_enabled","subject":"owned_container","sample":"first","container_id":"1111111111111111111111111111111111111111111111111111111111111111"}}
```

The nested version is the capability signal. An older reply, malformed detail,
or failure before a supported predicate has no diagnostic; this means diagnostic
unavailable. Never infer that the target is safe from absent detail. Diagnostics
are not plans, approvals, receipts, or evidence of a completed settlement. They
are not persisted in incident state or emitted for mutating settlement phases.

| Reason | Meaning |
| --- | --- |
| `target_identity_changed`, `daemon_identity_changed` | An existing identity comparison failed. |
| `container_set_changed` | An existing container membership comparison failed. |
| `container_process_disappeared` | The positively owned container's inspected process disappeared before its existing identity read completed. |
| `container_process_owner_unavailable` | The existing process ownership check failed. |
| `container_binding_changed` | Two existing snapshots differ for the same positively owned container. |
| `container_paused`, `container_state_invalid` | The existing containment state check refused. |
| `container_not_stopped`, `container_restart_enabled` | The existing settlement quiescence check refused. |
| `helper_activity_present` | The existing bounded process scan found matching activity. |
| `retained_data_consumer_running` | The existing inventory found a running consumer of retained data. |

Each detail contains only a closed reason, subject and sample label. A full
64-hex container ID appears only after positive project ownership; external
consumers and helpers have categories only. No process IDs, names, command
arguments, environment, credentials, mount names or private paths are exposed.
`first` and `second` refer to existing inventory samples; `comparison` compares
those samples. `identity_before` and `identity_after` refer to the adapter's
existing identity checks. A first-sample failure does not imply a second sample
ran. Unknown or mismatched combinations are discarded while retaining refusal.
Malformed or contradictory container state retains the original refusal without
claiming that the container is running or has an enabled restart policy.

Use the exact retained request, transaction and generation with the read-only
commands above. A refusal still requires stable ownership and a separately
approved containment plan before any stop, then fresh settlement observation.
Do not repeat an unstable plan, invent a durable job ID, change the activation
identity, or treat a diagnostic as permission to run initializers.

This additive helper output changes the Sandbox runtime digest. Verify matching
clean client and installed runtime revisions before relying on it. A controller
update and any production action require separate release authorization.

This source contract alone does not establish that any deployment, backup,
operator approval, containment, or production recovery has occurred.
