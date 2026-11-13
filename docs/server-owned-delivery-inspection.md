# Exact server-owned delivery inspection

Candidate source implementation, 2026-11-12. Not installed or runtime-verified.
The user's instruction to proceed without Spec Kit governs this work package.

## Supported selection

Use the explicit server-owned path with a joined authenticated Tailnet remote:

```text
sb delivery inspect --server-owned --remote REMOTE --name STACK --request-id REQUEST --json
sb delivery inspect --server-owned --remote REMOTE --target PROJECT/ENVIRONMENT --operation-id UUID --json
sb delivery inspect --server-owned --remote REMOTE --original-job-id JOB --request-id REQUEST --json
```

Require exactly one scope selector and one exact operation or request ID. The
ordinary controller-local `delivery inspect --remote` behavior is unchanged;
that remote flag scopes local history and does not label it as a server result.
No local project directory, environment/label override, history, cursor, trace,
observe, arbitrary argv, recovery purpose or source reference is accepted in the
server-owned mode. Missing binding is a refusal, never an inferred root.

The installed `delivery.inspect-exact.v1` receiver runs through a fixed
`job-capabilities --inspect-delivery` operation. Capability discovery and the
response must agree with the authenticated current target, marker and revision.
The receiver rechecks its owner after reading. No source staging, durable job,
startup reconciliation, workload probe, secret read or lease mutation occurs.
Existing capability discovery still reads installed service/routing metadata.
Plain capability discovery and fixed inspection dispatch before configuration,
instance resolution, automatic migration and generated Compose/environment writers.
The early receiver accepts only its closed capability/inspection arguments.
Explicit instance and configuration-file selectors cannot be silently ignored
by server-owned delivery inspection.

## Retained scope

A named selector reads only the exact retained hosting admission for that target.
The original request, project identity, root digest, runtime identity, machine and
job must agree with the exact delivery. Successful apply, a live container and a
valid rendered checkout are not prerequisites. A replaced admission cannot grant
access to an older attempt. An operation selector is compared with the operation
bound to the admission's original request, including when detail has expired.

New hosted and exposure producers retain a separate native owner binding in the
same transaction as the delivery reservation. The producer proves the current
installed server CLI child through the existing retained context validator;
an environment lookup key alone cannot assert that identity. The binding records
the original job, exact delivery target, operation/request and owner identity.
It grants read selection only. It does not grant execution, recovery or mutation.

An original-job lookup uses that binding, not a new checkout, job argv or supplied
scope. A job may admit several distinct deliveries; the exact selector must match
one. Duplicate request IDs across deliveries of the same job refuse as ambiguous.
The binding checks the current machine and ownership marker. Its recorded runtime
revision is historical; receiver/client revision checks use the current revision,
so upgrading the installed runtime does not erase retained diagnostic access.

The additive binding table is created only by an explicit new delivery writer.
Readers never migrate or backfill it. Existing databases keep schema version 1
and old readers remain compatible. Bindings are bounded by the existing 4096
request-guard ceiling and remain while detail expires. Legacy original jobs without
this binding return `binding_unavailable`. Named inspection can still use their
validated retained hosting admission. No historical row is rewritten to manufacture
an association.

## Closed wire and response

The request is an object with exactly these fields, nulls included:

```json
{"schema_version":1,"contract":"delivery.inspect-exact.v1","origin_remote_name":"remote","name":null,"target":null,"original_job_id":null,"operation_id":null,"request_id":null}
```

Existing remote, stack, target, job, UUID and request-ID codecs constrain selectors.
Unknown or duplicate keys, empty strings, booleans as versions and mixed modes
refuse. The request limit is 2048 encoded bytes. The response limit is 4096 bytes;
the transport drains stdout/stderr with caps before JSON decoding. The whole client
read has one 15-second budget. The receiver also has a 15-second budget, with
remaining time applied to SQLite reads. No unbounded raw error text is printed.

The exact response contains only `schema_version`, `contract`, `ok`, `owner`,
`selector`, `status`, `operation`. Owner contains exactly `target_identity`,
`ownership_marker`, `runtime_revision`. Selector must equal the validated request.
Statuses are `found`, `missing`, `expired`, `binding_unavailable`,
`binding_mismatch`, `unsupported`, `bounds`, `unavailable`. Only the first three
mean a serviced query. A failed delivery can therefore have query `ok=true`.

Only `found` carries an operation, containing exactly `operation_id`,
`delivery_state`, `execution_state`, `evidence_completeness`, validated using the
existing delivery vocabulary. No paths, source URLs, logs, argv, credentials,
free-text evidence or next-action commands cross this read interface. Failures
before a trustworthy envelope produce a fixed local `unavailable` error, never a
raw transport exception or a fallback controller result.

## Source ownership and deferred evidence

The implementation is in `application/server_cli_delivery.py`,
`delivery/server_binding.py`, delivery producer/repository seams, the owner
admission/stack record readers, command registration and capability composition.
The successful-target execution inspector keeps all of its existing guards.
Generic durable dispatch is not used for this read.

After independent source review and the root owner's deployment-first sequence,
exercise failed/interrupted records with no caller checkout or live container,
native original-job retention, duplicate and replaced scopes, expiration, wrong
owner/marker/revision, malformed and oversized wire data, timeout and forbidden
side effects. Verify legacy local inspection separately. None of these checks ran
in this source-only assignment. This slice does not establish full CLI-family
Tailnet acceptance or close provider-authority gaps.
