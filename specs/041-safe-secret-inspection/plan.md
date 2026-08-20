# Implementation Plan: Safe Secret Inspection

**Branch**: `latest` | **Date**: 2026-08-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/041-safe-secret-inspection/spec.md`

## Summary

Add a transport-neutral least-disclosure secret broker for registered personal
and project secret files. The initial literal-assignment boundary is extended by
an approved follow-up to explicitly configured JSON, INI, properties, TOML,
YAML, XML, PEM, opaque-token, and binary-container sources. A modular
`sandbox.secrets` package owns secure source opening, inert parsing, metadata and
profile validation, fixed masking, owner-only audit, bounded child use, targeted
atomic dotenv updates, and local TTY reveal. The existing `secrets` CLI is
migrated to an owned `CommandSpec`; an explicit opt-in MCP group delegates to
the same service; a built-in agent skill and operator guide enforce the safe
sequence. Existing migration and secret-resolution behavior remain intact.

## Technical Context

**Language/Version**: Python 3.10+ compatible production code; validation on the repository's current Python 3.14 runtime

**Primary Dependencies**: Python standard library plus the repository's existing
safe YAML dependency; existing Sandbox command registry, configuration provider
manifest, MCP composition registry, and streaming redactor; FastMCP through the
existing server environment

**Storage**: Registered plaintext literal-assignment sources; owner-only append-only JSONL security audit under `$SANDBOX_HOME/runtime/secrets/`; short-lived in-memory request/value state only

**Testing**: Existing `unittest` suite; focused unit, contract, CLI, MCP-composition, architecture-boundary, and live CLI/MCP acceptance checks

**Target Platform**: Supported macOS and Linux developer hosts; local CLI and explicitly opted-in project-scoped MCP servers

**Project Type**: Modular Python CLI plus MCP server and built-in Markdown agent skill

**Performance Goals**: Compliant 1 MiB sources inspect within two seconds locally; child use enforces a 1 MiB combined output budget and a 5-minute default timeout

**Constraints**: Never echo a password/token to stdout; never put plaintext in argv, MCP arguments, JSON, logs, audit, errors, commits, or chat; reveal only to the controlling TTY; no arbitrary paths; no source execution; no hidden CLI runtime reconciliation; Python-level memory zeroization is not claimed

**Scale/Scope**: Up to 1 MiB, 4,096 assignments or structured scalar leaves, 32
levels of nesting, 64 KiB per scalar, 100 inventory/metadata selectors per
request, exactly one selector for validation/mask/use/update/reveal; structured
sources are read-only in this follow-up

**Metadata-only follow-up**: Registered sources also expose a no-content-read
probe for existence, file type, empty/nonempty state, size bucket, configured
format, and broker-safe readability. Exact bytes are local-CLI opt-in; MCP has
no exact-size input and requires a distinct `source_info` grant. Paths, UIDs,
raw permission modes, timestamps, OS diagnostics, and source bytes remain
undisclosed.

## Constitution Check

*GATE: Passed before research and re-checked after design.*

- **I. Per-project model**: PASS. Project sources and MCP permissions resolve from the explicit project root; no implicit instance or global project fallback is introduced. The personal source is a deliberately named machine-local alias, not an instance.
- **II. Registry source of truth**: PASS. No registry JSON is read directly and no instance-resolution path is duplicated. This feature is runtime-independent and uses the normal project configuration loader.
- **III. Single entry and modular package**: PASS. `sb` stays unchanged as the entry file. New mechanisms live under `sandbox/secrets/`; CLI and MCP adapters register through existing manifests and do not consume `sandbox_core.py`, registry internals, `app.py` helpers, or the Hermes facade.
- **IV. Live-stack proof**: PASS by plan. Verification includes real `./sb` CLI calls against an isolated fixture project and an explicitly composed MCP group, in addition to focused tests. The feature itself does not require WordPress mutation.
- **V. Idempotency and docs-with-code**: PASS. Inspection is read-only, repeated fixed masks disclose nothing new, updates are lock/revision protected and atomic, and code lands with the operator guide and skill.
- **VI. Parity before removal**: PASS. Existing `secrets migrate-zshrc` and secret resolution are retained; centralized parser ownership changes only after parity tests.
- **Additional constraints**: PASS. No `runtime/wp/` or `vendor/` edits; no real secret content is read during development or tests; Spec Kit artifacts remain outside shipped package files.

**Post-design re-check**: PASS. Contracts preserve all gates; no constitutional exception or complexity waiver is required.

## Project Structure

### Documentation (this feature)

```text
specs/041-safe-secret-inspection/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   ├── config.md
│   └── mcp.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── commands/
│   ├── manifest.py                 # retain explicit command ownership
│   └── secrets.py                  # owned parser and thin CLI adapter
├── config/
│   ├── manifest.py                 # register common secrets config provider
│   ├── secrets.py                  # normalize source/use-profile descriptors
│   ├── wordpress.py                # carry raw secrets layer
│   └── compose.py                  # carry raw secrets layer
└── secrets/
    ├── __init__.py
    ├── audit.py                    # owner-only intent/outcome journal
    ├── context.py                  # CLI/MCP service composition
    ├── formats.py                  # explicit bounded structured/PEM/binary adapters
    ├── models.py                   # bounded requests/results and reason codes
    ├── parser.py                   # inert syntax-preserving assignments
    ├── policy.py                   # metadata, profiles, masking, destination policy
    ├── runner.py                   # direct-argv bounded child execution
    ├── service.py                  # transport-neutral orchestration
    ├── sources.py                  # registered aliases and descriptor-safe opens
    └── writer.py                   # lock/revision/atomic targeted replacement

mcp/wp-server/
├── server.py                       # inject scoped service factory
└── tools/
    ├── manifest.py                 # opt-in explicit secrets group and names
    └── secrets.py                  # thin MCP adapters

skills/secret-inspection/SKILL.md   # least-disclosure agent runbook
docs/secret-inspection.md           # operator guide and threat boundaries
README.md                           # command/catalog discovery link

tests/
├── fixtures/secret-formats/       # synthetic provider shapes plus provenance
├── test_secret_config.py
├── test_secret_formats.py
├── test_secret_parser.py
├── test_secret_policy.py
├── test_secret_service.py
├── test_secret_commands.py
├── test_secret_mcp.py
├── test_command_composition.py
├── test_mcp_composition.py
├── test_architecture_boundaries.py
└── test_modularity.py
```

**Structure Decision**: A transport-neutral feature package owns all secret mechanisms. CLI and MCP remain adapters registered by explicit manifests. Common project configuration normalizes named sources and use profiles once for WordPress and Compose descriptors. This avoids new consumers of compatibility roots and keeps higher-risk policy identical across transports.

## Complexity Tracking

No constitution violations require justification.
