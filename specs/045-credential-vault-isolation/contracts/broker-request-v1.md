# Contract: Explicit Broker Request v1

## Purpose

Define the reviewed guest-to-broker request that lets an untrusted workload
request one approved upstream operation without receiving the credential.

## Request shape

```text
binding_id       opaque binding identifier
binding_version  positive version from the desired binding
transport        instance-bound local broker endpoint/capability; never shared
                 across instances and never a host control-plane credential
scheme           HTTPS for MVP
host             exact canonical host
port             exact canonical port
method           approved method
path             exact canonical path
headers          bounded application headers; no Authorization header from guest
body             bounded bytes/content type, if allowed by binding
deadline_ms      bounded caller deadline
correlation_id   non-secret diagnostic identifier
```

The guest request contains no credential value. The broker strips or rejects
hop-by-hop and security-sensitive duplicate headers, refuses redirects unless a
separate exact binding is selected, validates the destination certificate, and
uses pinned DNS/IP and the instance egress grant before opening upstream use.
The transport must prove the instance/workload binding without relying on a
shared host socket; the concrete local transport is a Phase 1 design choice,
not a public plaintext-secret interface.

## Validation order

1. Authenticate the local broker request and identify the instance/workload.
2. Load the exact binding version and verify `ready` state, expiry, policy,
   egress, broker, and effective-isolation digests.
3. Canonicalize and compare scheme, host, port, method, path, headers, body
   limits, and deadline. Any ambiguity fails closed.
4. Resolve the opaque reference through the trusted resolver and create a
   process-bound lease.
5. Open a new validated TLS connection to the exact upstream and apply only the
   binding’s bearer/API-key header.
6. Enforce response size/time/concurrency bounds, apply best-effort redaction,
   and return a bounded result or stable error code.

No credential resolution or upstream connection occurs for a request that fails
steps 1–3.

## Response and error contract

Successful responses contain status, bounded headers selected by the contract,
bounded body data, and correlation ID. Errors contain a stable non-secret code,
human-readable safe summary, retryability classification, and correlation ID.
They never contain an authorization header, resolved source value, lease, or
raw upstream diagnostic that could contain one.

The contract must define maximum request/response bytes, maximum concurrent
requests, connection and total deadlines, cancellation, and whether streaming
is supported. MVP may reject unsupported streaming rather than buffering
without a bound.

The v1 limits are 64 KiB total request headers, 1 MiB request body, 4 MiB
response body, 16 concurrent requests per broker, 5 seconds to connect, 30
seconds total request time, and 5 seconds of inactivity. A caller may lower but
not raise these limits.

Authentication profiles are registered, not guest-selected header names:
`authorization_bearer` and `x_api_key` are the only v1 profiles. The broker
constructs the corresponding header and rejects any guest `Authorization` or
API-key header.

## Explicit non-goals

This contract does not promise transparent interception for arbitrary `curl`,
Git, package managers, SDKs, HTTP/2, or a generic forward proxy. Those require a
separate design and compatibility/evidence gate.
