# Contract: Credential Broker Controller Authority v2

## Status and review gate

This contract replaces the v1 controller/lease protocol for every future
production Credential Vault path. The v1 document remains historical evidence
for the local T034/T035 seams; a runtime MUST NOT negotiate, accept, translate,
or fall back to v1 after any v2 component is present.

This is a local design artifact only. It does not enable the service, prove a
Linux transport, close T022 or T029, or satisfy the independent security review
in T031. The capability remains `implemented_unproven`, `adoptable=false`, with
`evidence_id=null`. The v2 protocol and every implementation of it require
explicit independent human security review under T031 before release or use
with real credential material.

Normative words `MUST`, `MUST NOT`, `SHOULD`, and `MAY` are requirements. A
missing, malformed, stale, ambiguous, unverifiable, or extra value is a refusal.

## Authority and trust boundary

Exactly one persistent controller service exists for each managed-native
`machine_id`. It is the sole authority for:

- loading bindings through the binding repository and enforcing CAS/version;
- registered-source ownership and credential resolution;
- effective native proof and support/evidence admission;
- exact egress-policy intersection and digest identity;
- choosing the fixed `auth_form` for one operation;
- issuing one-use descriptor leases; and
- durably committing the secret-free PRE/POST audit record.

The controller is not a broker library callback and is not reconstructed per
request. It owns one machine until quiesce/stop, keeps one authenticated
controller connection to the broker, and rotates `controller_epoch` on every
controller process start. No broker, guest, helper, supervisor, command, or
adapter may independently read the binding repository, policy registry, source
registry, or controller audit repository on this path.

The broker remains a separate unprivileged enforcement boundary. It does not
trust a message merely because its fields look valid. Before parsing any
controller or lease frame, it independently authenticates the configured
controller UID/GID, PID, Linux process start identity, executable identity,
unit/cgroup identity, and current connection using kernel-observed state. It
then independently matches the authorization to the exact broker-created
operation, canonical guest request, binding/version, machine, epochs, digests,
expiry, and fixed authentication form. A controller authorization is necessary
but never sufficient without those broker checks.

The trusted-controller threat boundary is the documented single owner on one
host. Root and a compromised process with the exact controller identity can
subvert local process state and are outside the hostile-workload claim. The
guest, sibling instances, upstream, broker input, status consumers, and all
repository or page content are untrusted. The root helper and service
supervisor are trusted only for fixed lifecycle/ownership observations; they
are not binding, proof, egress, resolution, or audit authorities and never
receive credential bytes.

## Strict protocol and encoding

The controller channel is a broker-owned abstract Linux `AF_UNIX`
`SOCK_SEQPACKET` endpoint. It is persistent for the authenticated controller
connection, carries control, authorization, and audit frames, and is separate
from the guest listener and one-use lease endpoint. Each packet has exactly one
`SCM_CREDENTIALS`, no `SCM_RIGHTS`, and the same kernel identity as the accepted
connection. Unexpected rights are closed before refusal. EOF, identity drift,
unit drift, sequence failure, or parse failure closes admission and terminally
invalidates all connection-owned claims and authorizations.

Every controller frame is canonical UTF-8 JSON with sorted keys, no whitespace,
no duplicate keys, no floats, and a fixed 16 KiB packet ceiling. Each schema
below is exact: unknown or missing keys, wrong types, invalid identifiers,
unbounded strings, and non-canonical bytes are refused. `protocol` is always
`credential-broker-controller-v2`; `machine_id` and `broker_epoch` are required
on every controller JSON frame. `controller_epoch` is required on every frame
except `HELLO_V2`, where it is forbidden because HELLO_ACK distributes it.
`sequence` is a positive
integer. The first frame in each direction has sequence 1; every later frame is
exactly the preceding value plus one, without gaps, through
9,007,199,254,740,991. Duplicate, skipped, decreasing, exhausted, or non-integer
values close the connection. The sole exception is the bounded single transport
retry of `AUDIT_PRE_V2`, `AUDIT_POST_V2`, or `AUDIT_ACK_V2`: after exact schema
decoding, the receiver may accept exactly one missing immediately preceding
transport sequence. A larger gap, second gap, or any non-audit gap remains
terminal, and durable semantic same-commit validation still applies. `reply_to`
names the exact opposite-direction
sequence where shown. Sequence state is transport replay protection only; it is
never caller-supplied, restored after reconnect, or used as audit identity.

### Normative scalar and schema registry

This canonical JSON table is normative. Prose names below are explanatory; if a
field set, bound, enum, or timeout differs from this table, implementation and
review MUST stop until the contract is corrected. `required` is the exact key
set for that variant. No optional keys exist.

<!-- CONTROLLER_V2_SCHEMA_TABLE_BEGIN -->
```json
{
  "auth_forms": ["authorization_bearer", "x_api_key"],
  "bounds": {
    "activation_ttl_ms": 30000,
    "audit_ack_timeout_ms": 1000,
    "audit_transport_retries": 1,
    "authorization_ttl_ms": 5000,
    "clock_skew_ms": 250,
    "controller_frame_bytes": 16384,
    "drain_timeout_ms": 5000,
    "handshake_timeout_ms": 1000,
    "lease_ack_bytes": 444,
    "lease_ack_timeout_ms": 1000,
    "lease_bytes": 16384,
    "lease_frame_bytes": 732,
    "lease_ttl_ms": 5000,
    "max_active_operations": 16,
    "max_sequence": 9007199254740991,
    "min_sequence": 1,
    "no_pending_retry_max_ms": 1000,
    "no_pending_retry_min_ms": 50,
    "timestamp_max_unix_ms": 4102444800000,
    "timestamp_min_unix_ms": 1700000000000
  },
  "digest_documents": {
    "activation_digest": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "request_sequence", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id", "activation_expires_at_unix_ms"],
    "audit_post_fingerprint": ["machine_id", "operation_id", "binding_id", "binding_version", "decision_id", "audit_root_id", "phase_id", "pre_commit_id", "outcome_class", "effect_certainty", "reason_code"],
    "audit_pre_fingerprint": ["machine_id", "operation_id", "binding_id", "binding_version", "decision_id", "audit_root_id", "phase_id", "event_code"],
    "authorization_digest": ["protocol", "machine_id", "broker_epoch", "controller_epoch", "operation_id", "request_digest", "binding_id", "binding_version", "auth_form", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id", "binding_expires_at_unix_ms", "authorization_expires_at_unix_ms", "decision_id"],
    "handshake_digest": ["protocol", "machine_id", "broker_epoch", "controller_epoch", "broker_pid", "broker_start_ticks", "broker_executable_digest", "broker_unit_digest", "broker_config_digest", "controller_pid", "controller_start_ticks", "controller_executable_digest", "controller_unit_digest", "controller_config_digest", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id"],
    "quiesce_digest": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "request_sequence", "reason_code", "drain_deadline_unix_ms"]
  },
  "enums": {
    "activate_decision": ["activated", "refused"],
    "admission_state": ["closed", "open"],
    "audit_disposition": ["committed"],
    "audit_phase": ["pre", "post"],
    "claim_state": ["claimed", "no_pending"],
    "drain_status": ["drained", "timeout", "refused"],
    "effect_certainty": ["none", "possible", "completed"],
    "event_code": ["credential_effect_pre"],
    "outcome_class": ["completed", "refused", "indeterminate"],
    "post_pairs": [
      ["completed", "completed"],
      ["refused", "none"],
      ["indeterminate", "possible"],
      ["indeterminate", "completed"]
    ]
  },
  "field_exceptions": {
    "HELLO_V2": {"forbidden": ["controller_epoch"], "reason": "distributed_by_HELLO_ACK_V2"}
  },
  "field_types": {
    "accepted": "boolean_true",
    "acknowledged_at_unix_ms": "timestamp",
    "activation_digest": "digest",
    "activation_expires_at_unix_ms": "timestamp",
    "active_operation_count": "uint_0_16",
    "activate_decision": "activate_decision",
    "admission_state": "admission_state",
    "audit_fingerprint": "digest",
    "audit_root_id": "audit_id",
    "auth_form": "auth_form",
    "authorization_digest": "digest",
    "authorization_expires_at_unix_ms": "timestamp",
    "binding_expires_at_unix_ms": "timestamp",
    "binding_id": "binding_id",
    "binding_version": "positive_sequence",
    "body_bytes": "uint_0_1048576",
    "broker_config_digest": "digest",
    "broker_digest": "digest",
    "broker_epoch": "epoch",
    "broker_executable_digest": "digest",
    "broker_pid": "pid",
    "broker_start_ticks": "positive_sequence",
    "broker_unit_digest": "digest",
    "claim_state": "claim_state",
    "commit_id": "commit_id",
    "content_type": "content_type",
    "controller_config_digest": "digest",
    "controller_epoch": "epoch",
    "controller_executable_digest": "digest",
    "controller_pid": "pid",
    "controller_start_ticks": "positive_sequence",
    "controller_unit_digest": "digest",
    "correlation_id": "correlation_id",
    "decision_id": "decision_id",
    "drain_deadline_unix_ms": "timestamp",
    "drain_status": "drain_status",
    "disposition": "audit_disposition",
    "effect_certainty": "effect_certainty",
    "effective_isolation_digest": "digest",
    "egress_digest": "digest",
    "event_code": "event_code",
    "evidence_id": "evidence_id_or_null",
    "handshake_digest": "digest",
    "header_bytes": "uint_0_65536",
    "host": "dns_name",
    "lease_id": "lease_id",
    "lease_sequence": "positive_sequence",
    "machine_id": "machine_id",
    "method": "http_method",
    "operation_id": "operation_id",
    "outcome_class": "outcome_class",
    "path": "request_path",
    "phase": "audit_phase",
    "phase_id": "audit_id",
    "policy_digest": "digest",
    "port": "https_port_443",
    "post_commit_id": "commit_id",
    "post_phase_id": "audit_id",
    "pre_commit_id": "commit_id",
    "proof_digest": "digest",
    "protocol": "protocol_literal",
    "quiesce_digest": "digest",
    "reason_code": "reason_code",
    "reply_to": "positive_sequence",
    "request_deadline_unix_ms": "timestamp",
    "request_digest": "digest",
    "request_sequence": "positive_sequence",
    "retry_after_ms": "uint_50_1000",
    "scheme": "https_literal",
    "sequence": "positive_sequence",
    "type": "message_literal",
    "wait_deadline_unix_ms": "timestamp"
  },
  "identifier_rules": {
    "audit_id": {"max": 63, "min": 16, "pattern": "^audit-[a-z0-9]{10,57}$"},
    "binding_id": {"max": 63, "min": 16, "pattern": "^binding-[a-z0-9]{8,55}$"},
    "commit_id": {"max": 63, "min": 16, "pattern": "^commit-[a-z0-9]{9,56}$"},
    "correlation_id": {"max": 64, "min": 1, "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"},
    "decision_id": {"max": 63, "min": 16, "pattern": "^decision-[a-z0-9]{7,54}$"},
    "dns_name": {"max": 253, "min": 1, "pattern": "^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"},
    "epoch": {"max": 32, "min": 32, "pattern": "^[0-9a-f]{32}$"},
    "evidence_id": {"max": 63, "min": 16, "pattern": "^evidence-[a-z0-9]{7,54}$"},
    "lease_id": {"max": 63, "min": 16, "pattern": "^lease-[a-z0-9]{10,57}$"},
    "machine_id": {"max": 63, "min": 8, "pattern": "^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$"},
    "operation_id": {"max": 63, "min": 16, "pattern": "^operation-[a-z0-9]{6,53}$"}
  },
  "messages": {
    "ACTIVATE_ACK_V2": {"direction": "broker_to_controller", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "activation_digest", "admission_state", "activate_decision", "active_operation_count", "acknowledged_at_unix_ms", "activation_expires_at_unix_ms", "reason_code"]},
    "ACTIVATE_V2": {"direction": "controller_to_broker", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id", "activation_digest", "activation_expires_at_unix_ms"]},
    "AUDIT_ACK_V2": {"direction": "controller_to_broker", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "audit_root_id", "phase", "phase_id", "audit_fingerprint", "commit_id", "disposition"]},
    "AUDIT_POST_V2": {"direction": "broker_to_controller", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "operation_id", "binding_id", "binding_version", "decision_id", "audit_root_id", "phase_id", "audit_fingerprint", "pre_commit_id", "outcome_class", "effect_certainty", "reason_code"]},
    "AUDIT_PRE_V2": {"direction": "broker_to_controller", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "operation_id", "binding_id", "binding_version", "decision_id", "audit_root_id", "phase_id", "audit_fingerprint", "event_code"]},
    "AUTHORIZE_V2": {"direction": "controller_to_broker", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "operation_id", "request_digest", "binding_id", "binding_version", "auth_form", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id", "binding_expires_at_unix_ms", "authorization_expires_at_unix_ms", "decision_id", "authorization_digest"]},
    "AUTHORIZED_V2": {"direction": "broker_to_controller", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "operation_id", "request_digest", "binding_id", "binding_version", "decision_id", "authorization_digest", "authorization_expires_at_unix_ms"]},
    "CLAIMED_V2_CLAIMED": {"direction": "broker_to_controller", "wire_type": "CLAIMED_V2", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "claim_state", "operation_id", "request_digest", "binding_id", "binding_version", "scheme", "host", "port", "method", "path", "content_type", "header_bytes", "body_bytes", "request_deadline_unix_ms", "correlation_id"]},
    "CLAIMED_V2_NO_PENDING": {"direction": "broker_to_controller", "wire_type": "CLAIMED_V2", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "claim_state", "retry_after_ms"]},
    "CLAIM_NEXT_V2": {"direction": "controller_to_broker", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "wait_deadline_unix_ms"]},
    "HELLO_ACK_V2": {"direction": "controller_to_broker", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "accepted", "controller_pid", "controller_start_ticks", "controller_executable_digest", "controller_unit_digest", "controller_config_digest", "handshake_digest"]},
    "HELLO_V2": {"direction": "broker_to_controller", "required": ["protocol", "type", "machine_id", "broker_epoch", "sequence", "broker_pid", "broker_start_ticks", "broker_executable_digest", "broker_unit_digest", "broker_config_digest", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id"]},
    "LEASE_ACK_V2": {"direction": "broker_to_controller_same_lease_socket", "encoding": "fixed_binary_444", "required": ["type", "machine_id", "broker_epoch", "controller_epoch", "lease_id", "lease_sequence", "authorization_digest", "audit_root_id", "post_phase_id", "post_commit_id", "outcome_class", "effect_certainty", "reason_code"]},
    "QUIESCE_ACK_V2": {"direction": "broker_to_controller", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "quiesce_digest", "admission_state", "drain_status", "active_operation_count", "acknowledged_at_unix_ms", "drain_deadline_unix_ms", "reason_code"]},
    "QUIESCE_V2": {"direction": "controller_to_broker", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reason_code", "drain_deadline_unix_ms", "quiesce_digest"]},
    "REFUSE_V2": {"direction": "controller_to_broker", "required": ["protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "operation_id", "request_digest", "binding_id", "binding_version", "decision_id", "reason_code"]}
  },
  "reason_codes": {
    "activate": ["activated", "admission_closed", "identity_mismatch", "proof_unproven", "proof_mismatch", "digest_mismatch", "evidence_missing", "expired", "quiescing"],
    "post": ["upstream_completed", "upstream_refused", "guest_disconnected", "deadline_exceeded", "revoked", "lease_invalid", "audit_unavailable", "internal_indeterminate"],
    "quiesce": ["operator_stop", "restart", "revoke", "expiry", "proof_drift", "egress_drift", "identity_drift", "cleanup", "drained", "drain_timeout", "identity_mismatch"],
    "refuse": ["admission_closed", "request_invalid", "binding_missing", "binding_mismatch", "binding_stale", "binding_expired", "source_unavailable", "proof_unproven", "proof_mismatch", "egress_denied", "authorization_expired", "lease_invalid", "revoked", "deadline_exceeded", "capacity_exceeded", "audit_unavailable", "internal_refusal"]
  },
  "temporal_rules": {
    "acknowledged_at_unix_ms": {"messages": {"ACTIVATE_ACK_V2": "request_receipt_lte_value_lte_request_receipt_plus_1000", "QUIESCE_ACK_V2": "request_receipt_lte_value_lte_min_drain_deadline_or_request_receipt_plus_5000"}},
    "activation_expires_at_unix_ms": {"max_future_ms": 30000, "messages": {"ACTIVATE_V2": "request_receipt_lt_value_lte_request_receipt_plus_30000", "ACTIVATE_ACK_V2": "value_equals_ACTIVATE_V2_value_and_acknowledged_at_lt_value"}},
    "authorization_expires_at_unix_ms": {"max_future_ms": 5000, "messages": {"AUTHORIZE_V2": "request_receipt_lt_value_lte_request_receipt_plus_5000_and_value_lte_binding_activation_request_expiries", "AUTHORIZED_V2": "value_equals_AUTHORIZE_V2_value_and_acknowledgement_precedes_value"}},
    "binding_expires_at_unix_ms": {"max_future_ms": null, "messages": {"AUTHORIZE_V2": "durable_absolute_value_in_global_range_and_value_gt_authorization_expiry_no_relative_ttl_cap"}},
    "drain_deadline_unix_ms": {"max_future_ms": 5000, "messages": {"QUIESCE_V2": "request_receipt_lt_value_lte_request_receipt_plus_5000", "QUIESCE_ACK_V2": "value_equals_QUIESCE_V2_value_and_acknowledged_at_lte_value"}},
    "request_deadline_unix_ms": {"max_future_ms": 30000, "messages": {"CLAIMED_V2_CLAIMED": "claim_time_lt_value_lte_original_guest_request_receipt_plus_30000"}},
    "wait_deadline_unix_ms": {"max_future_ms": 1000, "messages": {"CLAIM_NEXT_V2": "request_receipt_lt_value_lte_request_receipt_plus_1000"}}
  }
}
```
<!-- CONTROLLER_V2_SCHEMA_TABLE_END -->

Integers are JSON integers, not booleans. PID is 1 through 4,194,304. Port is
exactly 443; scheme is exactly `https`; method is `GET`, `POST`, `PUT`,
`PATCH`, or `DELETE`; content type is one canonical lower-case registered value
of at most 127 ASCII bytes; path is canonical absolute ASCII with length 1
through 2,048 and no control, fragment, userinfo, dot-segment, or percent-
encoded separator. Timestamps MUST be within the table range and within the
message-specific TTL from the receiver's current wall clock, allowing at most
250 ms forward skew. Clock rollback or uncertainty beyond that bound refuses.
The `temporal_rules` table covers all seven JSON timestamp fields. Binding
expiry is deliberately different: it is durable absolute binding state and has
no relative TTL cap, but must still be in the registry's global timestamp range,
future at authorization, and later than authorization expiry. Request deadline
is capped at 30,000 ms from the original guest request receipt, not from claim.
Lease expiry is a binary-frame field governed separately by the 5,000 ms lease
TTL and the stricter authorization/binding/activation/request minimum.

Only these reviewed v2 message types exist. All except `LEASE_ACK_V2` use the
controller JSON channel; that ACK uses the fixed same-socket binary encoding
defined below:

```text
HELLO_V2
HELLO_ACK_V2
CLAIM_NEXT_V2
CLAIMED_V2
AUTHORIZE_V2
AUTHORIZED_V2
REFUSE_V2
ACTIVATE_V2
ACTIVATE_ACK_V2
QUIESCE_V2
QUIESCE_ACK_V2
AUDIT_PRE_V2
AUDIT_POST_V2
AUDIT_ACK_V2
LEASE_ACK_V2
```

An unversioned name, `*_V1`, unknown version, mixed-version frame, protocol
probe, translation, compatibility shim, retry using another version, or v1
lease is terminally refused. There is no negotiation or downgrade response.

## Normative guest protocol v2 registry

The guest channel is distinct from the controller and lease channels. It is one
private-veth `AF_INET`/`SOCK_STREAM` listener for exactly one managed-native
machine. The derived tuple is exact: one Linux interface of at most 15 ASCII
characters, one canonical-string RFC1918 IPv4 `/30`, its two usable addresses assigned once as
broker and guest, and broker port `18443`. The listener MUST set
`SO_BINDTODEVICE`, read it back, and compare the exact interface before bind.
Before reading guest bytes it MUST use kernel-owned topology observation to
match machine, interface, `/30`, broker address/port, guest address, route
interface/source, isolated guest network namespace, and non-forwarded,
non-loopback arrival. Source address alone is never identity.
The managed policy MUST say `egress=deny` and `default_route=false`.
Integer address forms and loopback, link-local, reserved, unspecified,
multicast, non-RFC1918, non-canonical, or differently prefixed addresses are
refused before listener construction.

Each TCP connection carries exactly one request followed by exactly one
terminal result. The inactivity timeout is 5 seconds. The operation deadline is
1 through 30,000 ms from original request receipt and cannot be restarted by a
claim, authorization, lease, audit retry, reconnect, or guest retry. EOF before
one whole frame, a second request, trailing bytes, half-close ambiguity, or
unreadable topology is terminal. The broker closes the connection after the
one result send attempt; it never streams or pipelines.

Both envelopes use the exact nine-byte network-order header `!4sBI`: four-byte
magic, one-byte version `2`, and an unsigned four-byte JSON payload length.
Request magic is `SBG2`; result magic is `SBR2`. `SBGR`, `SBRS`, version 1,
unknown versions, other magic, a non-exact length, or trailing bytes are
refused without negotiation or translation.

Payloads are canonical ASCII JSON: sorted keys, no whitespace, duplicate keys,
floats, NaN, alternate base64, alternate header spelling/order, missing keys,
extra keys, or an integer outside 1 through 9,007,199,254,740,991 are refused.
Request payload keys are exactly:

```text
protocol="credential-broker-guest-v2"
machine_id, binding_id, binding_version,
scheme="https", host, port=443, method, path,
headers, body, content_type, deadline_ms, correlation_id
```

`headers` is a lexicographically name-sorted array of unique `[name,value]`
pairs. Names are canonical lower-case HTTP token names. Authorization,
proxy-authorization, x-api-key, Host, Content-Length, and every hop-by-hop
header are forbidden. Values contain no controls. Total name/value framing is
at most 65,536 bytes. `body` is strict canonical base64 of at most 1,048,576
decoded bytes. `content_type` is null or one canonical lower-case registered
value. Host/path/method and identifier rules are the same strict rules used by
`CLAIMED_V2`; the guest cannot supply an auth form, source reference,
credential, operation/decision/audit/lease identity, epoch, authorization,
policy/proof/egress/broker digest, evidence identity, callback, import, or
transport authority.

`request_digest` is SHA-256 over the exact canonical request JSON payload,
excluding the nine-byte envelope. The broker derives it only after the kernel
transport and canonical request pass. Equivalent alternate encodings do not
exist.

A success result has exactly `protocol`, `ok=true`, `status`, `headers`, `body`,
and `correlation_id`. Status is 200 through 299; 3xx is never delivered.
Response headers use only `cache-control`, `content-language`, `content-type`,
`etag`, `expires`, `last-modified`, `retry-after`, and `vary`, with the same
64-KiB bound. Body is strict canonical base64 of at most 4,194,304 decoded
bytes. A failure has exactly `protocol`, `ok=false`, `state`, `code`,
`retryable=false`, and `correlation_id`; state is `refused` or `indeterminate`
and code is one fixed controller-contract refusal/post reason projected without
arbitrary text. Operation, request, binding, decision, audit, lease,
authorization, source, credential, descriptor, digest, exception, path, PID,
upstream body, and diagnostic fields are forbidden from failure results.
The registry owns the exact state/code partition consumed by validation:
`indeterminate` permits only `audit_unavailable`, `deadline_exceeded`,
`guest_disconnected`, and `internal_indeterminate`; `refused` permits the
remaining fixed failure codes plus `deadline_exceeded`. Deadline is the sole
code valid in both states because its state depends on whether effect entry
already occurred.

`sandbox.isolation.credential_guest_protocol_v2` is the sole registry/codec for
this channel. Its immutable machine-readable registry includes every envelope,
canonical-JSON rule, exact schema/type/enum, scalar and packet bound, POST
phase combination, topology observation, egress-decision invariant, and
effect-entry rule. Its reviewed digest is pinned by tests, so changing a
security rule changes the registry digest. The historical v1 `SBGR`/`SBRS` helpers remain tests/history only
and MUST NOT be called by a v2 executable.

## Authorized effect execution v2

The broker may enter an effect only with one immutable
`AuthorizedEffectContextV2`. It binds the exact canonical `GuestRequestV2`,
one immutable `AuthorizedEgressDecisionV2` and its exact matching egress
digest, machine and both epochs, operation and request digest, binding/version,
decision, authorization digest and fixed auth form, lease identity/sequence,
sealed descriptor size, and request, binding, authorization, lease, and
activation deadlines. It contains no credential bytes, source reference,
resolver, repository, arbitrary callback, socket, filesystem path, or public
proxy choice.
Lease sequence is an exact integer from 1 through 9,007,199,254,740,991; boolean,
zero, overflow, and arbitrarily large integer forms are refused.

The egress decision records the canonical non-numeric request hostname as the
exact TLS SNI, port 443, the complete unique numerically sorted tuple of
canonical public IPv4 strings returned by the one authorized resolution, and
the canonical full egress-projection digest. Both hostname and every resolved
address MUST intersect current unrevoked grants. The exact tuple is also the
nft destination set. The executor cannot replace it, re-resolve it, use numeric
SNI, or accept a later DNS answer.

`EffectExecutionV2` records the full machine/epoch/operation/lease identity as
entered before calling its typed implementation. The identity remains
tombstoned for the process epoch and a second call is `effect_replayed`; an
exception of any kind, including a typed protocol exception, or an invalid
typed result after entry is `effect_indeterminate`, never
a retry. The only result is `EffectExecutionResultV2`, pairing one exact
`GuestResultV2` with an allowed POST `outcome_class`, `effect_certainty`, and
`reason_code` plus an exact `pre_effect` or `effect_entered` phase. `pre_effect`
permits only refused/none with upstream-refused, deadline, revoke, or invalid
lease. `effect_entered` permits completed/completed/upstream-completed or the
accepted indeterminate combinations; in particular a deadline after entry is
indeterminate/possible and never refused/none. Failure result code equals POST
reason, and every result correlation ID equals the exact request correlation
ID. This is an execution contract, not an upstream implementation or a public
credential/proxy API.

## Mandatory mutual handshake

No lifecycle, claim, authorization, lease, or audit traffic is accepted before
one successful handshake on the persistent connection. The broker creates a
fresh random 128-bit `broker_epoch` at process start and sends the first packet:

```text
HELLO_V2 = {
  protocol, type, machine_id, broker_epoch, sequence=1,
  broker_pid, broker_start_ticks, broker_executable_digest,
  broker_unit_digest, broker_config_digest,
  policy_digest, egress_digest, broker_digest, proof_digest,
  effective_isolation_digest, evidence_id
}
```

Before parsing it, the controller requires connection `SO_PEERCRED` and exactly
one matching per-packet `SCM_CREDENTIALS`, then independently observes and
matches PID, `/proc/<pid>/stat` start ticks, executable digest, sealed helper-
reported unit/cgroup digest, canonical config digest, machine, and configured
digest/evidence expectations. It performs start/observe/start sampling and
refuses PID reuse or an unreadable observation. The frame is not evidence; it
must equal the independently observed and sealed expected values.

The controller creates a fresh random 128-bit `controller_epoch` at its own
process start and replies within 1,000 ms:

```text
HELLO_ACK_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch,
  sequence=1, reply_to=1, accepted=true,
  controller_pid, controller_start_ticks, controller_executable_digest,
  controller_unit_digest, controller_config_digest, handshake_digest
}
```

The broker performs the symmetric `SO_PEERCRED`, per-packet
`SCM_CREDENTIALS`, PID/start/executable, unit/cgroup, config, machine, and
start/observe/start checks against its sealed configured controller
expectations. `handshake_digest` is SHA-256 of canonical JSON containing exactly
`protocol`, `machine_id`, `broker_epoch`, `controller_epoch`, the ten PID/start/
executable/unit/config identity fields from both frames, and the five configured
policy/egress/broker/proof/effective-isolation digests plus `evidence_id`.

The peer verifies the digest before marking the connection authenticated. The
next sequence is 2 independently in each direction. A negative handshake has no
wire response: either side closes. Timeout, extra/missing credentials, rights,
identity drift, wrong digest/evidence, malformed frame, unexpected sequence, or
any pre-handshake message closes the connection and broker admission. Restart
rotates that process's epoch; reconnect never adopts the former epoch or
sequence and never migrates an operation. At most one authenticated controller
connection exists per broker epoch.

## Exact controller schemas

The notation below gives exact key sets. Identifiers are bounded opaque
non-secret IDs; digests are lowercase 64-character SHA-256 hex; timestamps are
bounded UTC Unix milliseconds. Reason, outcome, and state values come only from
fixed code allowlists.

### Lifecycle and claim

Controller to broker:

```text
ACTIVATE_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  policy_digest, egress_digest, broker_digest, proof_digest,
  effective_isolation_digest, evidence_id,
  activation_digest, activation_expires_at_unix_ms
}

QUIESCE_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  reason_code, drain_deadline_unix_ms, quiesce_digest
}

CLAIM_NEXT_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  wait_deadline_unix_ms
}
```

`activation_digest` is SHA-256 of the canonical JSON document containing
exactly:

```text
{
  protocol="credential-broker-controller-v2", type="ACTIVATE_V2",
  machine_id, broker_epoch, controller_epoch, request_sequence=sequence,
  policy_digest, egress_digest, broker_digest, proof_digest,
  effective_isolation_digest, evidence_id, activation_expires_at_unix_ms
}
```

`quiesce_digest` is SHA-256 of the same canonical construction containing
exactly protocol, `type="QUIESCE_V2"`, machine, both epochs,
`request_sequence=sequence`, `reason_code`, and
`drain_deadline_unix_ms`. The differently named `request_sequence` key prevents
the digest document from being mistaken for the wire frame.

The broker's proof role is exact comparison, not fresh proof evaluation. It
matches policy, egress, broker, proof, effective-isolation, and evidence values
against immutable helper-produced sealed configured expectations loaded at
start. The controller alone evaluates current proof/effective isolation. A null
or empty evidence identity, `implemented_unproven`, `adoptable=false`, stale
expectation, or mismatch refuses activation. `ACTIVATE_V2` opens admission only
when those comparisons and the current authenticated process identity match,
the 30-second maximum activation expiry is current, and no quiesce/stop is in
progress. It never authorizes an operation.

The broker sends one terminal lifecycle acknowledgement within 1,000 ms:

```text
ACTIVATE_ACK_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  reply_to, activation_digest, admission_state, activate_decision,
  active_operation_count, acknowledged_at_unix_ms,
  activation_expires_at_unix_ms, reason_code
}
```

`reply_to` is the exact ACTIVATE sequence and `activation_digest` is copied only
after recomputation. `activate_decision=activated` requires
`admission_state=open`, `active_operation_count=0`, and
`reason_code=activated`. Every refusal requires `activate_decision=refused`,
`admission_state=closed`, and an `activate` reason from the registry. Missing,
late, malformed, or contradictory acknowledgement leaves the controller and
public path closed.

`QUIESCE_V2` synchronously closes guest and lease admission before any drain.
No later frame on that connection reopens an existing operation. The broker
sends exactly one terminal acknowledgement no later than the lesser of the
requested drain deadline and 5,000 ms:

```text
QUIESCE_ACK_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  reply_to, quiesce_digest, admission_state=closed, drain_status,
  active_operation_count, acknowledged_at_unix_ms,
  drain_deadline_unix_ms, reason_code
}
```

`reply_to`, digest, and deadline bind the exact request. `drain_status=drained`
requires count zero and reason `drained`; `timeout` requires a positive count
and reason `drain_timeout`; `refused` uses count zero and the fixed
`identity_mismatch` reason. A timeout or missing acknowledgement never reopens
admission and makes cleanup incomplete. `CLAIM_NEXT_V2` accepts no operation,
binding, request, or authorization value and its deadline is at most 1,000 ms.

Broker to controller:

```text
CLAIMED_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  reply_to, claim_state=claimed,
  operation_id, request_digest, binding_id, binding_version,
  scheme, host, port, method, path, content_type,
  header_bytes, body_bytes, request_deadline_unix_ms, correlation_id
}

CLAIMED_V2 (no-pending variant) = {
  protocol, type=CLAIMED_V2, machine_id, broker_epoch, controller_epoch,
  sequence, reply_to, claim_state=no_pending, retry_after_ms
}
```

The broker creates `operation_id` after complete transport and canonical guest
request validation. `request_digest` commits to the entire canonical
`broker-request-v1` bytes, including header values and body bytes that are not
copied onto the controller channel. The projection contains no guest header
values or body. If the bounded claim wait expires, the broker returns only the
exact no-pending `CLAIMED_V2` variant with a fixed bounded retry interval. It
contains no operation, binding, request, decision, authorization, or arbitrary
diagnostic. A controller cannot authorize or refuse that variant.

### Authorization decision

Controller to broker:

```text
AUTHORIZE_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  operation_id, request_digest, binding_id, binding_version,
  auth_form, policy_digest, egress_digest, broker_digest, proof_digest,
  effective_isolation_digest, evidence_id,
  binding_expires_at_unix_ms, authorization_expires_at_unix_ms,
  decision_id, authorization_digest
}

REFUSE_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  operation_id, request_digest, binding_id, binding_version,
  decision_id, reason_code
}
```

`auth_form` is exactly `authorization_bearer` or `x_api_key`. It is selected by
the controller from the authorized binding. The guest, broker request, lease
caller, configuration caller, adapter, or source record cannot supply, rename,
or override it. Unsupported or differently spelled values are refused.

`authorization_digest` is SHA-256 over canonical JSON containing exactly:

```text
{
  protocol, machine_id, broker_epoch, controller_epoch,
  operation_id, request_digest, binding_id, binding_version,
  auth_form, policy_digest, egress_digest, broker_digest, proof_digest,
  effective_isolation_digest, evidence_id,
  binding_expires_at_unix_ms, authorization_expires_at_unix_ms, decision_id
}
```

The controller calculates it only after it has loaded the exact binding,
validated source ownership, independently accepted current support/proof,
confirmed exact request-scope and egress intersection, matched the helper-sealed
proof/effective-isolation/evidence expectations, and checked both expiry values.
The authorization TTL is at most 5,000 ms. It resolves no credential before the broker acknowledges the exact
authorization. The digest is an integrity binding, not a bearer secret and not
evidence that the controller performed those checks.

Broker to controller:

```text
AUTHORIZED_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  reply_to, operation_id, request_digest, binding_id, binding_version,
  decision_id, authorization_digest, authorization_expires_at_unix_ms
}
```

Before `AUTHORIZED_V2`, the broker recomputes the authorization digest and
matches every field to its canonical request, local operation, current
activation digests, authenticated connection owner, current time, and fixed
`auth_form` allowlist. It performs only exact comparison of the proof,
effective-isolation, and evidence identity against sealed configured
expectations; it does not evaluate host proof. It stores the accepted record only in a bounded
connection-owned in-memory map. A mismatch terminally refuses the operation.
`AUTHORIZED_V2` does not indicate credential receipt or an upstream effect.

The broker constructs exactly one authorization registry for one authenticated
controller connection, pinned `machine_id`, `controller_epoch`, `broker_epoch`,
and authenticated connection owner. Every insert, match, consume, revoke, and
disconnect action must match that complete tuple. A foreign machine, either
foreign epoch, or another owner refuses without changing valid unrelated state.
The registry has no reset or epoch-replacement operation and MUST NOT be
reconstructed while the pinned epoch pair lives, including after capacity is
reached. T040 owns and enforces this one-registry lifecycle invariant.

The broker retains every admitted `operation_id` as an in-memory tombstone for
the full authenticated `(controller_epoch, broker_epoch)` pair, including
refused, mismatched, consumed, revoked, and expired authorizations. Tombstones
are never pruned by authorization expiry. Active authorizations plus tombstones
are bounded to 16 total distinct operation IDs. Once that epoch pair has seen
16 distinct IDs, every new authorization fails closed with `capacity_exceeded`
until either process restarts, establishes a genuinely changed authenticated
epoch pair, and constructs its one new empty registry. Reconstructing a registry
with the same epoch pair is forbidden. An existing ID remains a replay refusal even far after expiry;
neither time nor terminal state enables reuse. This intentionally limits one
epoch pair to 16 total operations, not merely 16 concurrent operations.

## Operation state machine

The broker owns one bounded state machine per guest operation:

```text
pending
  -> claimed
  -> authorized
  -> lease_bound
  -> pre_audited
  -> effect_possible
  -> post_audited
  -> completed | refused | indeterminate
```

Before `effect_possible`, any known failure is `refused` and performs no
credential-bearing effect. Once descriptor bytes can reach the typed request
adapter, missing/invalid terminal result, controller disconnect, audit failure,
acknowledgement failure, timeout, cancellation, or cleanup uncertainty is
`indeterminate`; it is never automatically replayed. `completed` is possible
only after the upstream terminal outcome and POST audit acknowledgement are
both known. Terminal state is delivered at most once to the exact retained
guest and lease connection, then removed within a fixed bound.

Only the authenticated connection that received `CLAIMED_V2` can authorize,
refuse, audit, or lease that operation. Duplicate claims, a second controller
connection, cross-operation fields, cross-guest state, expired authorization,
or an out-of-order transition are terminal refusals. Quiesce, controller EOF,
broker close, binding revoke/expiry, digest drift, or process identity drift
clears all not-yet-effectful state and makes effect-possible state
`indeterminate`.

## Lease protocol v2

After `AUTHORIZED_V2`, the controller may resolve the registered source and
make exactly one send attempt to the broker-owned lease endpoint. It creates
one close-on-exec sealed anonymous `memfd`, writes only the bounded credential
bytes, applies write/grow/shrink/further-seal prohibitions, and transfers
exactly one descriptor using `SCM_RIGHTS`. The dispatcher independently
verifies the exact broker PID/start/executable/unit/config identities before
`sendmsg`; the broker authenticates the exact controller process before reading
ancillary data.

The v2 lease envelope is exactly 732 bytes. Every integer is unsigned
big-endian. Digest fields contain raw 32-byte SHA-256 output. Epochs contain raw
16-byte values represented as 32 lower-case hex characters only in JSON/status.
Fixed text fields are ASCII, contain one value matching the scalar registry,
then zero padding; embedded NUL, nonzero padding, truncation, or a value that
fills its field without room for one NUL is invalid.

| Offset | Width | Field |
|---:|---:|---|
| 0 | 8 | `magic` |
| 8 | 2 | `version` |
| 10 | 2 | `header_len` |
| 12 | 4 | `total_len` |
| 16 | 64 | `machine_id` |
| 80 | 16 | `broker_epoch` |
| 96 | 16 | `controller_epoch` |
| 112 | 64 | `operation_id` |
| 176 | 32 | `request_digest` |
| 208 | 64 | `binding_id` |
| 272 | 8 | `binding_version` |
| 280 | 1 | `auth_form` |
| 281 | 7 | `reserved` |
| 288 | 32 | `policy_digest` |
| 320 | 32 | `egress_digest` |
| 352 | 32 | `broker_digest` |
| 384 | 32 | `proof_digest` |
| 416 | 32 | `effective_isolation_digest` |
| 448 | 64 | `evidence_id` |
| 512 | 64 | `decision_id` |
| 576 | 32 | `authorization_digest` |
| 608 | 8 | `authorization_expires_at_unix_ms` |
| 616 | 64 | `lease_id` |
| 680 | 8 | `lease_sequence` |
| 688 | 8 | `lease_expires_at_unix_ms` |
| 696 | 4 | `descriptor_size` |
| 700 | 32 | `frame_digest` |

`magic` is the eight bytes `53 42 43 4c 56 32 00 00` (`SBCLV2` plus two NULs).
`version=2`, `header_len=732`, `total_len=732`, and every reserved byte is zero.
`auth_form=1` means `authorization_bearer`; `auth_form=2` means `x_api_key`;
all other tags refuse. `evidence_id`, `decision_id`, and `lease_id` match their
respective identifier rules; `lease_id` uses
`^lease-[a-z0-9]{10,57}$`, length 16 through 63. Descriptor size is 1 through
16,384. Binding version and lease sequence are 1 through
9,007,199,254,740,991. `frame_digest` is SHA-256 over bytes 0 through 699.

The codec accepts exactly one whole frame in one packet. Alternate order,
truncation, trailing bytes, missing/extra ancillary data, invalid padding, or
any version other than v2 is terminal. The broker requires a prior unconsumed
`authorized` operation owned by the same authenticated persistent controller
connection and matches every lease field before descriptor inspection.

`lease_id` is fresh and unpredictable but not a bearer authorization.
`lease_sequence` is scoped to the exact `(controller_epoch, broker_epoch)` pair,
starts at 1, and increases by exactly one without gaps. A broker restart rotates
`broker_epoch`, discards the old pair, and safely resets the new pair to 1 even
when the controller process survives. The broker
atomically marks `(controller_epoch, lease_id)` and the authorization as
consumed before reading bytes or invoking the adapter. A failed transfer,
ambiguous/truncated frame, expired lease, stale authorization, descriptor/seal/
size error, audit failure, or missing acknowledgement never restores either.
There is no binding-only lookup and no recovery from a frame prefix.

The lease expiry is at most 5,000 ms after receipt and MUST be no later than authorization expiry, binding expiry,
activation expiry, operation deadline, or the fixed maximum lease TTL. The
broker rechecks all deadlines immediately before PRE audit and immediately
before adapter entry. Revoke, quiesce, controller restart, broker restart, or
connection loss invalidates every outstanding lease.

## Persistent audit protocol

The persistent authenticated controller connection is the only audit channel.
The broker cannot append directly to a file or repository and cannot accept an
injected `already_audited` flag. Audit messages carry only bounded non-secret
metadata. Raw headers/body, source reference, credential bytes or lengths,
descriptor data, upstream body, exception text, filesystem paths, environment,
argv, socket names, PID values, and arbitrary diagnostics are forbidden.

Broker to controller, before adapter entry:

```text
AUDIT_PRE_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  operation_id, binding_id, binding_version, decision_id,
  audit_root_id, phase_id, audit_fingerprint, event_code
}
```

`event_code` is exactly `credential_effect_pre`. `audit_root_id` identifies the
operation audit semantics and `phase_id` is a distinct PRE identifier. The PRE
fingerprint is SHA-256 of canonical JSON containing exactly machine, operation,
binding/version, decision, audit root, phase ID, and event code. It excludes
transport sequence and connection epochs so a transport retry can preserve the
same semantic event. Controller to broker:

```text
AUDIT_ACK_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  reply_to, audit_root_id, phase, phase_id, audit_fingerprint,
  commit_id, disposition
}
```

For PRE, `phase=pre` and `disposition=committed`. The controller durably appends
the reviewed secret-free projection before sending the acknowledgement. No PRE
acknowledgement means refusal and no descriptor read or upstream effect.

Broker to controller after the adapter returns or effect certainty is lost:

```text
AUDIT_POST_V2 = {
  protocol, type, machine_id, broker_epoch, controller_epoch, sequence,
  operation_id, binding_id, binding_version, decision_id,
  audit_root_id, phase_id, audit_fingerprint, pre_commit_id,
  outcome_class, effect_certainty, reason_code
}
```

POST uses a new phase ID under the same audit root. Its fingerprint is SHA-256
of canonical JSON containing exactly machine, operation, binding/version,
decision, audit root, phase ID, PRE commit ID, outcome, effect certainty, and
reason. Exact valid combinations are:

| Outcome | Effect certainty | Allowed reason codes |
|---|---|---|
| `completed` | `completed` | `upstream_completed` |
| `refused` | `none` | `upstream_refused`, `deadline_exceeded`, `revoked`, `lease_invalid` |
| `indeterminate` | `possible` | `guest_disconnected`, `deadline_exceeded`, `audit_unavailable`, `internal_indeterminate` |
| `indeterminate` | `completed` | `audit_unavailable`, `internal_indeterminate` |

No other pair or reason is valid. POST acknowledgement uses `AUDIT_ACK_V2` with
`phase=post` and `disposition=committed`. The controller commits POST before
acknowledging. A missing, malformed, negative, duplicate-conflicting, or late
POST acknowledgement yields `indeterminate` and no replay. Repeating an
identical semantic audit frame may only return the same durable `commit_id`.

Transport and semantic idempotency are separate. A PRE or POST sender may make
at most one transport retry within the 1,000 ms acknowledgement deadline. The
retry uses the next transport sequence; any acknowledgement `reply_to` targets
that retry sequence. It preserves the audit root, phase ID, fingerprint, and
every semantic field. The controller
stores a durable tombstone keyed by machine, audit root, phase, and phase ID,
containing the fingerprint and commit ID. An exact semantic replay returns the
same commit ID; a different fingerprint or semantic field for that key is a
conflict that closes the connection. A new transport sequence never creates a
second audit event.

After controller crash/restart, every durable PRE tombstone without a POST
tombstone is closed before activation by one internal durable recovery
tombstone. It contains exactly machine, audit root, PRE phase/commit IDs, a
fresh POST phase/commit ID, `outcome_class=indeterminate`,
`effect_certainty=possible`, `reason_code=audit_unavailable`, and controller
timestamp. It is not an `AUDIT_POST_V2` wire frame and needs no lost operation
ID. The old broker epoch is quiesced/replaced and no operation or credential
request is resumed. If recovery append cannot be proven durable, activation
remains closed.

Durable audit storage contains its own bounded controller event identity,
binding/machine/version, decision, lifecycle class, fixed reason/outcome, and
timestamps, phase fingerprints, and replay tombstones. It MUST NOT persist operation ID, request digest, lease ID,
authorization digest, auth header, credential/source data, descriptor facts,
guest content, or controller/broker protocol frames.

## Same-socket lease acknowledgement v2

For every canonical lease frame that identifies a current authorized operation,
the broker sends exactly one `LEASE_ACK_V2` back to the controller on the same
connected lease `SOCK_SEQPACKET` socket used for descriptor transfer. Direction
is broker to controller. No new connection, JSON encoding, ancillary descriptor,
or controller-channel transport sequence is involved. An unidentifiable,
truncated, wrong-magic/version/length, or non-canonical lease closes the socket
without inventing an acknowledgement identity.

The acknowledgement is one fixed 444-byte binary packet. Integers are unsigned
big-endian; epochs/digests are raw; fixed ASCII identifiers use the v2
identifier rules plus one NUL and zero padding.

| ACK offset | Width | ACK field |
|---:|---:|---|
| 0 | 8 | `ack_magic` |
| 8 | 2 | `ack_version` |
| 10 | 2 | `ack_total_len` |
| 12 | 64 | `machine_id` |
| 76 | 16 | `broker_epoch` |
| 92 | 16 | `controller_epoch` |
| 108 | 64 | `lease_id` |
| 172 | 8 | `lease_sequence` |
| 180 | 32 | `authorization_digest` |
| 212 | 64 | `audit_root_id` |
| 276 | 64 | `post_phase_id` |
| 340 | 64 | `post_commit_id` |
| 404 | 1 | `outcome_class` |
| 405 | 1 | `effect_certainty` |
| 406 | 1 | `reason_code` |
| 407 | 5 | `ack_reserved` |
| 412 | 32 | `ack_frame_digest` |

`ack_magic` is `53 42 41 43 4b 32 00 00` (`SBACK2` plus two NULs), version is
2, total length is 444, and reserved bytes are zero. Outcome tags are
1=`completed`, 2=`refused`, 3=`indeterminate`. Effect tags are 0=`none`,
1=`possible`, 2=`completed`. Reason tags are 1=`upstream_completed`,
2=`upstream_refused`, 3=`guest_disconnected`, 4=`deadline_exceeded`,
5=`revoked`, 6=`lease_invalid`, 7=`audit_unavailable`, and
8=`internal_indeterminate`. The outcome/effect/reason combination must match the
exact POST table above. `ack_frame_digest` is SHA-256 over bytes 0 through 411.

Both epochs, lease ID/sequence, and authorization digest must exactly match the
one sent lease and current process pair. `audit_root_id`, `post_phase_id`, and
`post_commit_id` must exactly match a committed POST `AUDIT_ACK_V2`; the broker
cannot send lease ACK before that durable controller acknowledgement. A
canonical lease rejected before descriptor-byte read still uses PRE then POST
`refused`/`none` with `lease_invalid` before its lease ACK. Once effect is
possible, the ACK carries only the audited completed or indeterminate outcome.

The broker makes one bounded send within 1,000 ms after receiving the POST audit
acknowledgement and before the operation request deadline plus 1,000 ms. The
controller sets that absolute same-socket receive deadline when dispatching the
lease. EOF, timeout, short/trailing ACK data, mismatch, or invalid tag/digest is
terminal: the controller closes the socket, does not retry the lease or ACK,
and never replays or creates a replacement identity for the credential-bearing
request. The ACK and its timeout do not change the already durable POST record.

## Replay, rotation, restart, and expiry

- `broker_epoch` is freshly generated on every broker process start;
  `controller_epoch` is freshly generated on every controller process start.
- Lease sequence is keyed by `(controller_epoch, broker_epoch)`. Either process
  restart creates a new pair beginning at 1; state from an old pair is never
  imported, compared, or accepted.
- Epoch values, sequence numbers, operation/decision/audit/lease IDs, and
  digests are scoped to the exact machine and authenticated process identities.
- Duplicate, decreasing, skipped-beyond-bound, stale, cross-epoch,
  cross-connection, cross-machine, or cross-operation values are refused.
- Controller reconnect after EOF requires a fresh authenticated connection,
  fresh activation, and new operations. Existing operations never migrate.
- Broker restart begins closed in `credential_pending`; controller restart
  quiesces or replaces the broker before any new activation.
- Durable binding metadata can survive restart; authorization, operation,
  audit-channel, descriptor, consumed-ID, and sequence state cannot.
- Wall-clock rollback, expiry ambiguity, or deadline evaluation failure closes
  admission. Callers may lower fixed deadlines but cannot raise them.
- No timeout, retry, recovery job, audit repair, helper restart, or guest retry
  may reuse a credential-bearing operation identity or automatically create a
  second request identity.

## Configuration and module ownership

Future implementation keeps these ownership boundaries:

- the controller module owns binding/repository access, registered-source
  resolution, proof/egress decisions, authorization construction, lease
  dispatch, and durable audit;
- one shared v2 codec module owns exact schemas, canonical encoding, bounds,
  digests, reason allowlists, and rejects all v1/unknown input;
- the broker executable owns guest canonicalization, kernel controller identity
  observation, operation state, authorization verification, descriptor checks,
  typed request execution, and bounded guest results;
- managed service planning owns only derived secret-free unit/config identity,
  limits, start/stop ordering, and cleanup observation;
- the fixed root helper owns only reviewed derived lifecycle verbs; and
- public CLI/application composition owns proof-gated intent and status, not
  binding, authorization, protocol, audit, or credential mechanisms.

The broker's canonical root-owned/group-readable configuration may contain only
machine identity, expected controller/service identities, both executable and
config digests, endpoint identities, policy/egress/broker digest expectations,
helper-produced sealed proof digest, effective-isolation digest, opaque evidence
identity (or null to force closed state), the exact secret-free full egress
projection, and fixed bounds. The projection contains exactly machine and base
policy identities, the `EgressGrantSet` digest, and the canonical full set of
grant IDs, kinds, destinations, ports, expiries, and revoked states. It contains
no DNS answer selected by a caller and is identical in reciprocal controller
and broker configs. Validation reconstructs `EgressGrantSet` and verifies its
canonical digest even when the grant set is empty; an empty caller-forged
digest is never accepted. The helper derives
and seals this secret-free expectation record from the accepted immutable plan;
the controller cannot rewrite it, and the broker never reads proof/evidence
repositories or evaluates live isolation. It contains no binding, source reference, auth form,
authorization, operation/lease/audit ID, request digest, PID/start value,
credential material, header/body, proof result, or evidence promotion. It is
loaded with no-follow, regular-file, owner/group/mode, size, canonical-byte, and
digest checks. PID/start identity is observed after process start and checked
start/digest/start to refuse PID reuse.

For an HTTPS effect, DNS authorization is a full-set intersection rather than
an any-address shortcut: one current non-revoked `hostname_https` grant must
exactly cover the canonical host and port 443, at least one current
`public_cidr_tcp` grant must exist, and every independently resolved public IPv4
address must fall within the union of the full current CIDR set. Empty, private,
loopback, link-local, multicast, mixed, partially covered, expired, revoked,
wrong-owner, wrong-policy, or wrong-digest sets refuse. DNS pinning and the
effective nft set must use that same complete address set; a subset cannot
authorize or preserve an effect. The resulting immutable authorized decision
pins that sorted canonical address tuple, exact non-numeric hostname/SNI,
port 443, and projection digest through effect execution; it cannot re-resolve.

The two derived service records use fixed service roles, UIDs, unit identities,
and endpoint identities. Each record pins its own executable/config digest and
the exact peer executable/config digest; lifecycle construction requires both
directions to match before any action. A stop consumes one opaque, one-use
receipt minted only after the controller session accepts and verifies the exact
`QUIESCE_ACK_V2`; caller-created mappings are never stop authority.

Guest application composition consumes a different opaque capability minted
only by the exact authenticated broker session. It is frozen to that broker
object's private nonce, concrete type, purpose `guest_bridge`, machine, epochs,
and config object, and is one-use. A controller receipt, copied-field object,
arbitrary `submit_guest_v2` object, replay, wrong broker object, purpose change,
or epoch/config drift is not guest bridge authority. The historical v1
application broker factory is permanently closed and never constructs or
returns a `handle` path. The resulting bridge has no writable instance
dictionary or raw dependency cells. The exact broker consumes the receipt,
binds the canonical validator and clock internally, and mints one guest-submit
capability whose only inputs are the canonical request and transport identity.
The bridge retains only that already-bound capability. The raw broker session
exposes no submission method accepting caller-selected validation or time. No
module-level dependency registry exists, and representations contain no
dependency or identity details.

The controller and application processes are trusted components. Arbitrary
same-process Python reflection, monkeypatching, closure inspection,
`object.__new__`, `object.__setattr__`, or mutation of module globals means that
trusted process is already compromised and is outside this threat model. Exact
Python types, private constructors, and one-use receipts prevent accidental
public-API laundering; they are not cryptographic or unforgeable authority
against code executing inside the trusted process. Untrusted plugins and guest
workloads cannot execute Python objects in either process and never receive the
bridge or submit capability. They cross only the authenticated guest socket,
whose canonical data schema cannot name imports, callbacks, Python objects,
controller paths, validators, clocks, sessions, or legacy handlers. The
security boundary is the cross-process socket plus kernel-observed peer and
packet identity described above.

The controller receives repository, registered-source, proof evaluator, egress
authority, and audit repository only through explicit application-context
interfaces. Its service config names fixed module/config digests and derived
owned endpoints, never arbitrary repository paths or source locations. The
controller compares its fresh proof result with the same helper-sealed
expectations before sending activation or authorization; the broker then
performs the independent exact-match enforcement described above.

## Start, quiesce, stop, and cleanup

Start order is: compile immutable secret-free plans; start the per-machine
controller closed; start the broker closed; independently authenticate both
processes; establish the persistent controller connection; re-evaluate current
binding/proof/egress/support state; then send `ACTIVATE_V2`. Code presence,
config presence, a running unit, or connection success never opens admission.

Stop order is: close public admission; send `QUIESCE_V2`; stop new guest and
lease acceptance; terminalize or drain operations within fixed bounds; require
POST audit acknowledgements where an effect may have occurred; close and stop
the broker; stop the controller; prove units/processes/sockets/cgroups/
descriptors are absent; then remove exact owned network and machine resources.
Unknown, foreign, or ambiguous resources are retained as cleanup-incomplete and
never deleted by inference.

Crash, signal, controller EOF, audit-channel failure, identity drift, unit
replacement, proof drift, digest drift, revoke, or expiry immediately closes
admission. Cleanup is idempotent but never replays an operation or treats an
unreadable observation as absence.

## No-secret and closed-by-default surfaces

Credential bytes and credential-derived reversible values MUST NOT enter guest
environment/argv/files/mounts/snapshots; helper or supervisor input/output;
unit text/properties; controller/broker config; filesystem paths; `/proc`
command/environment; registry/policy state; status/capability output;
audit/log/journal/telemetry/exception/test output; broker-generated errors;
cleanup/recovery records; or any durable controller/protocol state. The sole
permitted carrier is the authenticated one-use v2 `memfd` lease after exact
authorization acknowledgement.

The broker never deliberately emits, copies, or logs the applied authorization
header or credential. It exposes only the reviewed response-header allowlist,
fixed broker error shapes, and a bounded upstream body. That body is untrusted:
an upstream can reflect the credential verbatim or transform it into a form a
scanner does not recognize. Before guest delivery or any permitted retention,
the broker performs bounded best-effort redaction of the exact credential bytes
and fixed reviewed encodings while the request-scoped descriptor callback is
active. A redaction failure closes the response; a transformed reflection may
still evade detection. The contract therefore makes no universal response-
confinement claim. Tests and live hostile probes cover declared literal and
transformed cases, but success does not prove all possible upstream transforms.
Public status, audit, logs, and retained job evidence MUST omit upstream bodies
entirely rather than relying on redaction.

The service starts closed and remains closed for unsupported runtime, non-Linux
host, non-root or unowned lifecycle configuration, missing authorized proof,
`implemented_unproven`, `adoptable=false`, `evidence_id=null`, stale or mixed
digest, identity ambiguity, absent controller, v1-only peer, audit uncertainty,
cleanup uncertainty, or any unknown state. There is no Compose, Herd, macOS,
Kubernetes, generic proxy, transparent interception, shared broker, or
test-fake fallback.

## Local and live evidence boundary

Local contract tests may prove exact text/schema invariants, codec behavior once
implemented, state transitions using fakes, replay refusal, fixed allowlists,
no-secret serialization, and closed defaults. They do not prove real
`SO_PEERCRED`/`SCM_CREDENTIALS`, process start/executable identity, abstract
sockets, `SCM_RIGHTS`, memfd seals, systemd/cgroup ownership, veth isolation,
drain timing, hostile inspection, or cleanup on Ubuntu 24.04.

T022 and T029 require authorized live Ubuntu 24.04 evidence for those facts.
T031 requires an independent human review of the exact clean source revision,
this v2 contract, live evidence, audit behavior, and cleanup evidence before
any support/evidence/adoptability change.
