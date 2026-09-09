# Contract: Public Exposure Verification v1

Applies to requested deploy exposure and preview creation. It adds evidence to ordinary/immutable diagnosis without weakening or replacing their existing runtime, initializer, activation or edge authority.

## Project contract and CLI

Register optional top-level delivery in sandbox/config/manifest.py, implemented by sandbox/config/delivery.py. Closed shape:

~~~json
{
  "delivery": {
    "schemaVersion": 1,
    "routes": {
      "deadlineSeconds": 120,
      "aliasPolicy": "serve_or_redirect_to_primary",
      "checks": [
        {
          "path": "/health",
          "statuses": [200],
          "markers": [
            {"kind": "header_equals", "field": "X-Application", "expected": "example-app"}
          ]
        }
      ],
      "releaseIdentity": {
        "required": true,
        "path": "/health",
        "kind": "header",
        "field": "X-Release",
        "expectedFrom": "application_commit"
      },
      "edgeProof": {"required": false}
    }
  }
}
~~~

Bounds: one to eight checks; one to four markers/check; one to eight explicitly allowed status codes/check; paths at most 1,024 bytes, absolute with no authority/userinfo/fragment/query; marker fields/expected values at most 256 bytes; one to 20 unique requested hostnames; total normalized contract at most 32 KiB. Reject unknown keys and invalid/sensitive marker headers or secret-like URLs. Marker kinds are header_equals, json_pointer_equals and json_array_contains; use only scalar public expected values. Expected strings may use the closed tokens $primary_origin or $requested_origin; no environment interpolation or arbitrary expressions.

deadlineSeconds defaults to 120 and permits 10–300. CLI deploy and preview create gain --verify-timeout with that same range and optional --request-id; an override is frozen into the requested outcome digest. MCP remote_deploy receives equivalent optional fields through its existing owned wrapper; no new app-helper consumer. Preview's required --confirm and existing deployment authority remain.

aliasPolicy is serve_or_redirect_to_primary or serve_only. HTTP must upgrade to HTTPS. A HTTPS alias may serve directly, or under the first policy redirect to the requested primary. An arbitrary hostname is never an allowed destination.

WordPress without an explicit contract uses a documented runtime-provided contract: GET /wp-json/ must return 200, JSON /url must equal the primary public origin, and /namespaces must contain wp/v2. This is declared application availability proof, not release identity. Custom applications without a non-generic marker contract receive delivery_route_contract_required before requested exposure effects.

releaseIdentity may be omitted or required=false; expose state=unsupported when no identity mechanism exists. If required=true, kind is header or json_pointer, field/path are bounded as above, and expectedFrom is application_commit or artifact_digest. A missing expected exact value or unsupported provider refuses pre-effect. Never use the Sandbox control revision or a generic status response as release identity.

edgeProof is optional and defaults required=false for generic exposure. If the requested application/hosting contract already requires edge proof, it cannot be disabled here. Required evidence uses the existing edge owner and exact target/config/release join. If no supported provider can supply a required proof, refuse before exposure; pending/stale proof after effects leaves delivery incomplete.

## Finite observer

sandbox/delivery/routes.py owns the observer; route_worker.py isolates potentially blocking DNS/TLS/socket work. The parent uses a monotonic aggregate deadline including child startup, retries, redirects and body reads. It terminates/reaps its worker at expiry and returns incomplete. No leftover worker, background retry or later mutation.

At most two attempts per host/check, five redirects per request, five seconds per blocking network attempt bounded by remaining time, and 64 KiB response body per check. The entire worker input is at most 32 KiB and result at most 128 KiB. No captured application body is retained. The worker receives only the explicit public contract/target inputs and a minimal defined process environment; do not forward secret environments or use credentials for public probes.

Perform public DNS resolution, normal hostname-based requests and certificate validation through the system trust store. No certificate bypass, forced Host routing, direct-origin substitution or authenticated public request may count as public proof. Record a bounded digest/count of returned address evidence; DNS resolution alone does not prove backend ownership.

## Per-host proof

For every primary and requested alias, and every declared path:

1. Resolve DNS and retain time/result.
2. Request HTTP with a generated nonsecret query probe. Require a valid redirect to an allowed HTTPS origin with the same path and query.
3. Request HTTPS under normal hostname/certificate checks. Follow only allowed destinations: same requested host or the primary under alias policy, port 443, no userinfo/fragment, no downgrade.
4. Compare normalized path and complete query multimap after each redirect, preserving repeated keys and values. The generated probe uses two values including an encoded value; dropped/changed/duplicated values fail. Do not persist raw Location query values.
5. Validate allowed status plus every declared application marker against the final response. A 200/3xx alone cannot pass. Evaluate optional/required release identity separately using the application source/artifact expected value.
6. Join applicable existing edge proof separately. Origin/runtime health does not satisfy it.

Reject malformed/oversized Location, unauthorized origins, loops, excessive redirects, TLS validation failure, missing markers, wrong backend/release, body overflow or changed query. DNS/network timeout may use the one bounded retry; permanent contract/identity/redirect failures do not loop.

## Result and effects

Route configuration produces configured, never verified. verified requires all requested hosts and required checks to pass within the aggregate budget. failed means a decisive check failed. incomplete means deadline, unavailable required proof or uncertain observation; neither is a successful exposed-site outcome.

The command result includes operation/request/instance links and per-effect known states even when one alias fails after primary configuration. Retain existing explicitly authorized route/DNS cleanup results, including partial/failed cleanup. No automatic instance deletion, broad route pruning or new cleanup permission.

Public output retains hostname, safe origin/path, status, redirect/query match booleans, TLS result, public marker/revision digests, deadline/times and proof references. Drop body/header dumps, cookies, login tokens, raw query values and private exceptions.

## Required negative controls

Observe success on every alias, wrong DNS/no DNS, invalid certificate, redirect to another origin, dropped/changed/repeated query, redirect loop, application failure/401, generic 200 missing marker, wrong backend/release, one failing alias, stale required edge proof and aggregate deadline. Configuration/reload and a healthy container must never turn these controls into successful exposure. If release identity is unavailable and optional, a successful route result says exactly that its scope is application availability only.
