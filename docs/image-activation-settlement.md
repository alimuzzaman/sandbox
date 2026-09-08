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

This source contract alone does not establish that any deployment, backup,
operator approval, containment, or production recovery has occurred.
