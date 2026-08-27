# Contract: Standalone Credential Broker Service v1

## Status

This is a pre-implementation security contract. It defines the minimum service,
transport, lifecycle, and cleanup invariants needed to prepare T022 locally. It
does not select or enable a credential-bearing runtime path, close T003, prove
T022 or T029, or authorize an evidence/support-tier promotion under T031.

The capability remains `implemented_unproven` with `adoptable=false` and no
evidence ID. A separate security design review must accept the concrete trusted
lease channel before service code may be treated as implementation-ready.

## Components and ownership

The v1 runtime has four distinct boundaries:

1. The trusted control plane owns binding metadata, the registered-source
   resolver, proof evaluation, audit-safe lifecycle state, and one-use lease
   issuance.
2. One unprivileged credential-broker service exists for exactly one
   managed-native `machine_id`. It validates the explicit request contract and
   performs the approved upstream HTTPS operation.
3. The untrusted guest client uses one instance-bound request endpoint. It never
   connects to a root-helper, resolver, or broker-supervision socket.
4. The fixed root helper performs only digest-bound service lifecycle and
   cleanup-observation verbs. It never receives, resolves, reads, forwards, or
   logs credential bytes.

The service identity MUST bind the `machine_id`, policy digest, egress digest,
broker protocol/config digest, executable digest, endpoint identity, and fixed
resource limits. No unit name, filesystem path, command, user, port, or service
property may be supplied directly by the guest or an unvalidated caller.

## Guest request transport

The guest transport MUST be dedicated to one managed-native instance. A shared
host listener, generic forward proxy, root-helper control socket, or endpoint
reachable from another instance is forbidden.

The concrete transport must prove all of the following before request parsing:

- the listener is bound only to the intended instance boundary;
- the peer belongs to the exact `machine_id` and cannot arrive through another
  host, sibling instance, loopback alias, or forwarded route;
- a short-lived opaque transport capability matches the supervised broker
  process and is rotated on broker replacement or restart;
- the request binding ID/version, instance ID, and transport identity agree;
- network reconciliation adds only the exact guest-to-broker reachability and
  does not widen the existing upstream default-deny grant; and
- transport material is not a credential, is never an authorization header for
  the upstream, and is excluded from status, audit, retained output, and durable
  binding state.

Source-address filtering alone is not sufficient transport authentication. The
implementation may use a dedicated guest-visible Unix socket or an exact private
veth listener only after its ownership, peer authentication, namespace exposure,
and cleanup behavior are contract-tested and independently reviewed.

## Trusted one-use lease channel

The guest request transport and the trusted lease channel are separate. The
trusted channel connects only the control-plane resolver to the exact supervised
broker process after request scope, proof, egress, binding version, expiry, and
transport checks pass.

FR-008 and SC-002 prohibit credential bytes in workload/process-control
channels, while `data-model.md` describes a trusted one-use broker launch
channel. This note does not silently reinterpret that boundary. T033 must decide
whether the selected anonymous descriptor or private IPC mechanism is wholly
inside the trusted control-plane-to-broker boundary. If it is not, the feature
spec must be clarified before implementation; T022 remains blocked.

Before implementation, T033 must select and document one concrete mechanism,
such as an inherited one-use descriptor or an owner-authenticated private Unix
socket with peer-credential checks. The accepted mechanism MUST satisfy all of
these rules:

- credential bytes never enter root-helper argv, stdin, environment, protocol,
  unit text, service properties, configuration files, staging paths, durable
  sockets, journals, status, audit, exceptions, or retained output;
- the helper cannot connect to, impersonate, or replay the trusted lease;
- the guest and sibling instances cannot discover or connect to the channel;
- the lease is process-bound, binding-version-bound, expires before or with the
  binding, and can be consumed at most once;
- the broker acknowledges only non-secret lease identity and outcome metadata;
- failure or indeterminate acknowledgement closes the lease and is not retried
  as a credential-bearing request; and
- buffers and descriptors are closed promptly with best-effort cleanup, without
  claiming universal memory zeroization.

Until this mechanism passes T033, an in-process callback remains a local model
only and no standalone service path may be enabled.

## Fixed helper protocol

The helper-side service protocol is limited to these intended fixed verbs:

```text
credential-broker-start  machine_id policy_digest egress_digest broker_digest
credential-broker-status machine_id policy_digest egress_digest broker_digest
credential-broker-stop   machine_id policy_digest egress_digest broker_digest
```

All arguments are validated non-secret identities. The helper derives the unit,
runtime directories, executable, and service properties from fixed code and a
helper-owned secret-free record. It discards child output except for bounded,
schema-checked non-secret status. It refuses absent ownership proof, changed
descriptions/digests, foreign units, unexpected files, symlinks, or caller-
selected service properties.

`start` creates a broker in closed/`credential_pending` state. It does not open
credential admission. `status` proves the exact process and digest identity but
does not make the capability adoptable. `stop` closes admission before bounded
drain and stops only the exact owned service.

## Lifecycle and cleanup order

The required lifecycle order is:

```text
predecessor/effective proof
  -> start broker closed
  -> establish reviewed trusted lease channel
  -> recheck policy/egress/broker/source/binding digests
  -> open admission
  -> close admission on revoke, expiry, drift, restart, or cleanup
  -> invalidate leases and drain within the documented bound
  -> stop and observe broker absence
  -> remove guest transport reachability
  -> continue machine/network/image/policy cleanup
```

Cleanup observation MUST distinguish `present`, `absent`, `drifted`, and
`unavailable`. A failed read is never absence. A foreign or drifted unit is never
stopped. Broker cleanup must precede network or machine removal, be idempotent,
and retain a recovery item when ownership or absence cannot be proved.

After a broker or machine restart, durable bindings enter
`credential_pending`; no old transport capability, lease, descriptor, or process
identity can reopen admission.

## Local verification boundary

Local unit and contract tests may prove validation, fixed argv, serialization,
no-secret surfaces, fail-closed state transitions, ownership checks, and cleanup
ordering. They do not prove Linux process isolation, systemd ownership, guest
reachability, hostile no-leak behavior, drain timing, or cleanup on Ubuntu 24.04.

T022 remains open until its local seams and authorized helper/service lifecycle
proof pass. T029 remains open until the public `./sb` acceptance path exercises
the full live matrix. T031 remains open until an independent reviewer accepts
the exact source revision, contracts, live evidence, cleanup evidence, and
support decision.

## Explicit refusals

- No Compose, Herd, macOS, generic host-job, Kubernetes, or remote-workspace
  fallback.
- No plaintext credential CLI, MCP, API, fixture, or helper operation.
- No transparent HTTPS interception, generic proxying, shared broker pool, or
  cross-instance transport.
- No support-tier or evidence-ID promotion from code presence or local tests.
