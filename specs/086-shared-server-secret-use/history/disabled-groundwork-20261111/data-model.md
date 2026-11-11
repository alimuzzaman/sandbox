# Data model

All current records are nonsecret. Parse untrusted declarations into immutable closed records. Free-form fields are not diagnostic text; refuse invalid fields without echoing their input. A syntactically valid identifier is not proof of server registration, admission or authenticity.

## Records needed by executable source groundwork

| Record | Fields | Rules |
| --- | --- | --- |
| PersonalLocalSelection | scope=`personal-local` | Default when flags omitted; server reference fields forbidden |
| ServerSelection | scope=`server`, selected registered-remote reference, project reference, source id/revision, profile id/revision | All explicit; bounded nonempty identifiers, no paths/URLs/shell/value payload; no selection from environment; actual registration/revisions remain unverified statically |
| CredentialMapping | source key name, destination name, intended consumer selector, kind=`provider` or `generated` | Exact mapping, deterministic order, duplicate/ambiguous destinations refused; provider/generated cannot substitute; consumer purpose is not operator membership |
| StaticValidationResult | code, declaration status, scope, availability=`unverified`, nonsecret selected references | No presence booleans, fingerprints or local secret lookups |
| UnsupportedResult | fixed code, scope, execution=`disabled`, availability=`unverified` or inventory=`unavailable` | No operation/grant/admission id fabricated; no claim about server key absence |

Selection is a union, not a dictionary whose optional fields can mix scopes. Mapping records are only declaration data. Static validation may not supply authenticated actor or installation proof. There is no current persistent transition, reservation, grant issuer or enablement boolean.

## Future integration records, proposed only

| Entity | Immutable fields and relationships |
| --- | --- |
| CanonicalInstallationBinding | Service-authenticated installation id; current runtime revision; owner context/policy binding. Stable across supported private-layout use, distinct on replacement. Machine/uid/home observation insufficient |
| AuthenticatedOperator | Existing ControlGate actor proof plus initiating actor id; server execution owner remains a separate identity. No public constructor from client actor strings |
| ReviewedIntent | Installation, authenticated actor, project, runtime revision, target/copy/incarnation, exact committed source identity and artifact digest, all source/profile ids/revisions, exact source-key/destination/consumer mappings, original request id, finite deadline and separately explicit generation intent |
| ScopedReservation | Atomic scoped request identity bound to immutable intent digest; no values in digest. Scope includes installation/actor/project. Identical intent lookup is replay; changed intent refuses |
| TerminalPreflightRefusal | Reservation, fixed code, bounded required-key names/status, no-effect evidence and retained outcome. Frozen even after source repair |
| CommittedAdmission | Genuine retained committed server receipt referring to reservation, intent and installation; successful preflight necessary but insufficient alone |
| AdmittedOperation | Server-only type created from committed receipt and checked installation. References sibling target/source authority, finite lifetime and broker grant. Context frames cannot construct it |
| BrokerGrant | Operation/actor/install/request/intent binding, source/profile revisions, exact mappings, consumer purpose, deadline/cancellation. Never interchangeable between equal-rights actors |
| GenerationAuthority | Separate postadmission authority for declared generated-runtime credentials and confidential intended delivery. Cannot produce or replace required provider keys |

Provider values are not fields on any public/retained record. The consumer receives ephemeral broker-owned material within its supported confidential in-process/consumer boundary. An integration requiring provider values in argv, transport or files is unsupported. Do not implement future record issuers or generic persistent state as part of this source slice.

## Proposed future state transitions

```text
untrusted request -> authenticated reference resolution -> atomic lookup/reservation
reservation -> terminal no-effect preflight refusal
reservation -> successful key preflight -> genuine committed admission -> admitted operation
admitted operation -> running -> retained success/failure
```

These transitions require accepted installed integration. They are not an executable state machine in this slice. Uncertain lookup/reservation/refusal/admission is `acceptance_unknown` until original-identity lookup determines the retained state. No new id or automatic redispatch. Known terminal refusal permits an explicit new id linked to that refusal and newly reviewed intent; the original stays frozen. Same scoped id plus changed intent refuses. Concurrent identical reservations yield one binding, and only genuine committed admission can authorize one effect chain. Terminal ids/tombstones must never reopen after pruning.

Expiry/cancellation preserves inspection of retained outcomes, but stops new consumption and every later effect. Before consumption recheck grant, source/profile revisions, mappings and intended consumer. Before each effect check deadline/cancel and required live target fence; source publication also requires the sibling source-writer authority. Presence preflight, old lease observations and retained receipts cannot replace current checks.
