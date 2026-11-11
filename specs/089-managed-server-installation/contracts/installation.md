# Installation contract

## Python identity port

sandbox.installation.binding.read_installation_binding(*, home=None) -> dict
sandbox.installation.binding.validate_current(binding, *, home=None) -> dict
InstallationBindingError.code identifies uninitialized/malformed/drift/authority.
Both reads are side-effect-free, including missing-state failures. Production
composition passes the selected home; no new core facade consumer. Missing binding
must never fall back to OwnerLocal. Test seams explicitly inject host observations.

Binding fields are specified in data-model.md. Policy covers current admitted-owner
rights; changing revision or canonical policy changes binding and invalidates prior
bound consumers. The server owns actor admission, not these caller-supplied fields.

## HTTP

GET /hosting/installation, existing ControlGate, no query/body accepted. Success
{ok:true, installation_schema:1, binding:<current binding>, error:null}.
Failure {ok:false, installation_schema:1, binding:null,
error:{code:<stable refusal>,message:<nonsecret bounded text>}}. No initialization
from GET. No credential values, raw machine-id, filesystem source or listener FD.

## Explicit lifecycle

sb installation initialize --layout owner|managed --confirm --json
sb installation status --json
sb installation plan --json
Further install/update/migration actions are plan/request-bound with explicit
confirmation; unsupported adapters refuse before effects. Root retains live authority.
Owner lifecycle initializes identity without relocation; managed mode uses approved
paths/principal only. Initial broad policy admitted-owner-v1 preserves access.

Initialization returns durable identity only, not a live binding. Full status and
read_installation_binding require actual service-context ownership marker; missing
live marker refuses rather than reading one from the persisted identity. GET is
the authenticated observer interface. Standalone CLI status can therefore refuse.
Managed uninstall is independently guarded by mode/fixed managed path even with
missing identity state.

## Installed extension port

Trusted composition registers named versioned capability providers with verifiers.
Read discovery advertises only a provider whose installed handler/verifier proves
availability for the current binding. Descriptors are immutable release metadata;
085 owns image/source digest validation and supervisor-held socket FD handoff.
No client/template selected image, socket, uid or home is authority.086 owns actual
credential-consuming handler and actor/grant ledger. Empty registry means no claims.

read_extension(name,binding) returns {digest,descriptor} from root-owned fixed
/etc/sandbox/extensions/<name>.json. Source manifest names:
- hosting.baseline.worker.v1: schema_version1, installation_id, incarnation,
  runtime_revision, policy_digest, image_id sha256:64hex, source_digest64hex,
  platform linux/amd64, protocol integer1.
- hosting.baseline.listeners.v1: schema_version1, installation_id, incarnation,
  runtime_revision, policy_digest, worker_release_digest64hex, front_door
  caddy|nginx, ingress_uids list of1..4 distinct nonnegative integer UIDs.

Registration envelope {schema_version:1,digest,descriptor}; digest is SHA256 of
sorted compact JSON. Core authority is validated without recursively evaluating
capabilities, so installed capability verifiers may safely consume metadata.
Descriptor presence never advertises hosted_baseline_worker_v1;085 must separately
assemble and verify its real installed handler/worker/listener before registering.
