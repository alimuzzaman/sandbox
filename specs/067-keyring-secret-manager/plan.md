# Implementation Plan: Cross-Platform Open-Source Secret Manager

**Branch**: `latest` | **Date**: 2026-10-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/067-keyring-secret-manager/spec.md`

## Summary

Add an opt-in Infisical source adapter behind the existing Sandbox secret broker. The
adapter authenticates once per operation with a controller-specific bootstrap credential
held in an explicitly approved OS credential store, reads one exact registered item over
bounded verified HTTPS, and passes the value only through the broker's existing fixed-child
boundary. Existing file sources and their behavior remain unchanged.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: Python standard-library HTTP/SSL; existing PyYAML; pinned
`keyring`, `SecretStorage`, and `jeepney` for approved OS credential-store adapters. The
Infisical SDK and CLI are excluded.

**Storage**: Non-secret registration in existing Sandbox configuration; bootstrap secret
in macOS Keychain or an already-running, unlocked Linux Secret Service; access tokens only
in process memory for one operation; existing append-only secret audit for secret-free
intent and outcome.

**Testing**: Python `unittest`, injected provider/store fakes, and
`tests.subprocess_support.synthetic_environment` / `run_test_process`; separately
authorized live acceptance against a synthetic Infisical project. Every focused test must
complete in under 5 seconds.

**Target Platform**: macOS 11+ and Linux desktop sessions with an existing unlocked Secret
Service; operator-provided community self-hosted Infisical reachable at a Tailscale-private
origin over verified HTTPS.

**Project Type**: Existing Python CLI and MCP service.

**Performance Goals**: Local capability inspection completes within 2 seconds; network
operations obey their declared finite timeout; status performs no value fetch; responses
are capped at 1 MiB and secret values at the existing 64 KiB limit.

**Constraints**: No provider deployment or administration; no secret listing, search,
reveal, write, delete, migration, or automatic retry; no redirects, ambient proxy or
credential discovery, public fallback, daemon startup, or keyring unlock; durable audit
intent precedes authentication and retrieval.

**Scale/Scope**: First release supports one Infisical project identity per Sandbox project,
one exact environment/path/key per source, one separately revocable bootstrap credential
per controller, and fixed registered child profiles only.

## Constitution Check

*GATE: Passed before Phase 0 research and re-checked after Phase 1 design.*

- **I. Per-project model — PASS**: registrations resolve from the current project's
  authoritative configuration; there is no global fallback source.
- **II. Registry authority — PASS**: the feature does not add instance resolution or read
  runtime registry JSON directly.
- **III. Modular package — PASS**: provider and credential-store adapters live under
  `sandbox/secrets/`, register through explicit manifests, and do not add logic to `sb`.
- **IV. Live-stack proof — PASS WITH DEFERRED EVIDENCE**: synthetic local verification is
  planned; Linux/macOS plus Infisical live proof remains mandatory before completion and
  requires separately authorized infrastructure unavailable during planning.
- **V. Idempotency and docs — PASS**: local credential placement has explicit create versus
  replace intent; source registration has no provider side effect; docs change with code.
- **VI. Parity before removal — PASS**: existing file adapters remain the default and no
  compatibility path is removed or disabled.
- **Additional secret/process constraints — PASS**: no secret is written to repository
  artifacts, argv, logs, audit, or retained evidence; test subprocesses receive only
  synthetic environments.

## Project Structure

### Documentation (this feature)

```text
specs/067-keyring-secret-manager/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli-mcp.md
│   ├── configuration.md
│   └── provider-adapter.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── commands/secrets.py
├── config/
│   ├── manifest.py
│   └── secrets.py
└── secrets/
    ├── adapter_manifest.py
    ├── context.py
    ├── service.py
    ├── providers/
    │   ├── base.py
    │   ├── http.py
    │   ├── infisical.py
    │   └── manifest.py
    └── credentials/
        ├── base.py
        ├── linux.py
        ├── macos.py
        └── manifest.py

mcp/wp-server/
├── requirements.txt
└── server/

tests/
├── test_secret_commands.py
├── test_secret_config.py
├── test_secret_credentials.py
├── test_secret_infisical.py
├── test_secret_mcp.py
└── test_secret_service.py

docs/
├── sandbox-config-reference.md
└── secret-inspection.md
```

**Structure Decision**: Extend the existing broker and configuration packages with two
narrow internal adapter families. A source-adapter manifest discriminates the unchanged
file adapter from the Infisical adapter. Provider HTTP and OS-store mechanics remain below
`SecretService`; CLI and MCP remain policy surfaces rather than direct provider clients.

## Design Decisions

1. A missing source `type` continues to mean the existing file source; `type: infisical`
   selects a strict provider descriptor.
2. The provider descriptor contains only non-secret endpoint, project, environment, path,
   key, client identity, modes, and timeout metadata. A secret is never accepted there.
3. Provider `keys` reports the single configured key locally. `source-info` may use only
   the public status endpoint. Neither operation authenticates or reads a value.
4. `metadata`, `validate`, and fixed-profile `use` may fetch only the exact registered item
   when their source mode is enabled. Provider sources do not support reveal, arbitrary
   run, operator session, list, search, wildcard, write, delete, organize, or migration.
5. Every value-reading operation performs one Universal Auth login and one exact secret
   read, then discards the access token. Cross-operation token caching is excluded.
6. macOS uses an explicitly instantiated Keychain backend. Linux uses SecretStorage
   directly to detect a locked collection without unlocking or prompting. Automatic
   keyring backend discovery is forbidden.
7. HTTP uses verified TLS and system trust, disables redirects and environment proxies,
   applies connection/read and response-size bounds, and performs no automatic retry.
8. Infisical reads set `type=shared`, `expandSecretReferences=false`, and
   `includeImports=false` to prevent wider or transitive retrieval.
9. Static compatibility is pinned to a reviewed community release/API contract. A
   versioned, secret-free live acceptance record—not local registration or `/api/status`—
   proves provider capacity, Viewer scope, private routing, two-controller credentials,
   and negative cross-project behavior. Provider administration remains out of scope.
10. CLI and MCP environments consume one pinned dependency manifest with a revision digest,
    while imports remain lazy so file-only workflows do not require keyring packages.

## Implementation and Verification Order

1. Complete all configuration, adapter, service, CLI/MCP, dependency, and documentation
   changes using read-only evidence and injected seams.
2. Exercise supported local CLI workflows with synthetic configuration and fakes; do not
   contact a live provider or host keyring without separate authorization.
3. Write focused regressions from the observed behavior, then run focused gates. No focused
   test may take 5 seconds or longer.
4. Run the repository's required local final gate only after implementation and focused
   verification are complete.
5. With explicit authorization and suitable infrastructure, run the Linux/macOS live
   acceptance in `quickstart.md`; empty or malformed evidence is not success, and the
   feature remains live-unverified until that separate completion gate passes.

## Complexity Tracking

No constitution violations require justification.
