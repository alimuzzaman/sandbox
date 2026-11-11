# Original-request target preparation

Source contract, not installed acceptance. Creating a stack must not require a
lifetime/capacity/runtime claim before provider preflight and committed admission.

The existing jobs repository owns the independently versioned
`credential_target_preparations_schema_version=1` extension. Job schema remains 7.
`CredentialOperationRepository.prepare_target(intent, *, project_identity,
project)` atomically freezes one complete intended target under the original
installation/incarnation/principal/project/request scope. It retains server
allocated copy8 and `i-32hex` incarnation, registered target project, canonical
intent/digest and eventual operation ID. Repeated metadata preparation returns
the original IDs; changed source/profile/artifact/mappings/deadline conflicts.
Reads allocate nothing. Unknown persistence requires original-scope inspection.

The trusted service calls
`admission.prepare_target(ledger, actor, *, body:bytes, project_identity:str,
project:str) -> sandbox.jobs.credential_preparations.TargetPreparationEvidence`.
The actual gate/owner proof supplies the actor. Registered recipe resolution
supplies project identity/project. The closed operation body replaces target_ref
with name; copy/incarnation cannot be supplied by the client. Returned
`operation_request()` is nonsecret proposed request data, never authority.
No host, source, lifetime, capacity, generated credential or runtime claim occurs.

For creation, operation reservation requires the exact retained preparation and
binds its operation ID in the same transaction. Provider preflight then either
freezes a no-effect refusal or permits genuine credential admission. Preparation
does not issue a second grant. Existing-target apply uses its recorded identity.

The claim adapter calls
`admission.require_target_preparation(receipt, *, name, project, environment,
copy_id, incarnation, source_commit) -> TargetPreparationEvidence` immediately
before claim and again at its write boundary. Only the genuine private committed
receipt is accepted. This port revalidates current actor/resolver, retained full
intent/digest/admission and exact preparation/operation linkage. Evidence records
and target_ref dictionaries are not receipts. The claim adapter independently
requires the genuine original job, installed policy and held canonical writer.
Public hoststore JSON gains no reserved-field or trusted-boolean bypass.

Runtime migration/initialization, production recipe registration, transport,
original-job composition and installed acceptance remain separate. No command
or mutation was executed to implement this contract. Root runs short checks.
