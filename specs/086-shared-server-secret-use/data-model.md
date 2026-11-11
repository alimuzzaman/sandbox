# Data model: Credential operations v1

| Entity | Fields and invariant |
| --- | --- |
| InstallationBinding | Full accepted schema in contracts/authenticated-consumption-v1.md; immutable snapshot validated against read-only current port; no lazy initialization |
| AuthenticatedControlReceipt | Private issued frozen in-process record, via and verified principal, policy epoch; tailnet login/node or token service provenance; no serialized constructor |
| CredentialIntent | Installation, actor, project, target/copy/incarnation, committed source/artifact/manifest digests, exact provider/generated mappings, source/profile revisions, effective deadline, request identity and explicit generation intent |
| Reservation | Unique scoped replay key and canonical intent, reserved with no value/effect authority |
| PreflightRefusal | Frozen safe names/status, no_effects and terminal outcome; source repair cannot reopen it |
| CommittedCredentialReceipt | Server-issued in-process receipt backed by committed admission row and fresh successful preflight; not a sibling lifecycle job record |
| CredentialOperation | Receipt plus current full installation binding, operation ID, cancellation/deadline and required lifecycle/source/target fence references |
| BrokerGrant | Private ephemeral actor/operation/intent/consumer/purpose/revision binding; no wire encoding or reusable token |
| EffectCheckpoint | Operation+step unique fencing token, started/completed/unknown; unknown cannot be blindly redispatched |
| ApplicationDelivery | Reviewed intended recipient/artifact/profile, minimal explicit environment or approved protected runtime-secret facility, finite retention/cleanup and backup/export boundaries |

State transitions and recovery are normative in contracts/authenticated-consumption-v1.md. Persist only nonsecret records. Restart requires server revalidation and fresh in-process proof, never deserializing a public projection into authority. Lookup of retained terminal outcomes remains possible after revocation while current authentication authorizes inspection; it grants no fresh effects. Operation ownership remains actor-specific even though policy grants equal full operator rights.
