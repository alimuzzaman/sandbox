# Research: Sandbox Modular Boundaries

## Decision 1 — Use structural contracts and plain dependency bundles

**Decision**: Define small Python protocols for registry, runtime, and side-effect contracts, with immutable dependency bundles constructed at CLI/MCP composition roots.

**Rationale**: Python's typing model supports structural subtyping, allowing existing implementations and focused fakes to satisfy contracts without inheritance or a framework. This keeps migration incremental and makes dependency direction inspectable. See the [Python typing documentation](https://docs.python.org/3/library/typing.html#nominal-vs-structural-subtyping).

**Alternatives considered**:

- Abstract base classes: valid, but would force nominal inheritance during compatibility migration.
- Dependency-injection framework: rejected as unnecessary machinery for a small set of explicit composition roots.
- Continue monkey-patching global/back-filled symbols: rejected because it preserves hidden dependencies.

## Decision 2 — Keep argparse and move parser ownership into command specs

**Decision**: Preserve argparse and use feature-owned parser-builder callbacks plus handlers, scopes, and capability metadata in a deterministic manifest.

**Rationale**: The standard library explicitly supports subparsers, aliases, per-subparser arguments, and binding handler functions through parser defaults. The coupling is caused by ownership location, not argparse itself. See the [argparse subcommand documentation](https://docs.python.org/3/library/argparse.html#sub-commands).

**Alternatives considered**:

- Replace argparse: rejected as unrelated compatibility risk.
- Declarative schema for every argument immediately: rejected because complex existing parsers can migrate safely through builder callbacks.
- Leave parser blocks centralized: rejected because future runtime/recovery features would continue editing central routing.

## Decision 3 — Use explicit package-owned manifests

**Decision**: Built-in schemas, adapters, CLI commands, and MCP groups are listed by package manifests, sorted deterministically, and validated for duplicate names/aliases/tools.

**Rationale**: Explicit manifests make ownership, ordering, and duplicate behavior testable and avoid importing untrusted project code. The MCP SDK supports explicit tool registration and in-memory server testing; the project can compose groups before registration while preserving current schemas. The official SDK currently identifies v1.x as the stable production line, so this feature preserves the installed v1 contract rather than adopting the v2 prerelease. See the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

**Alternatives considered**:

- Filesystem module discovery: rejected because ordering and trust are implicit.
- Python entry points/dynamic third-party loading: deferred until packaging, compatibility, and trust policies exist.
- Manual imports in central bootstrap: rejected because each new group expands the composition root.

## Decision 4 — Preserve state formats with atomic same-filesystem replacement

**Decision**: Registry and migrated Hermes state repositories write to a sibling temporary file, flush data, and replace the destination on the same filesystem while retaining current locks and format compatibility.

**Rationale**: Python documents `os.replace()` as atomic on POSIX when successful and warns that cross-filesystem replacement can fail, which supports sibling temporary files under the existing state directory. See [`os.replace`](https://docs.python.org/3/library/os.html#os.replace).

**Alternatives considered**:

- Eager format migration: rejected because it expands the data-loss surface and weakens rollback.
- New database for the registry: rejected because the current JSON scale and interoperability do not justify it.
- Direct overwrite: rejected because interruption can corrupt the only source of truth.

## Decision 5 — Capability checks live in one runtime service

**Decision**: CLI and MCP resolve project identity and request operations through the same runtime service. The service selects an adapter and checks code-declared capabilities before invoking dependencies.

**Rationale**: One enforcement point prevents transport drift and makes zero-side-effect rejection testable. Persisted capability lists are diagnostic only, avoiding stale authorization-like decisions.

**Alternatives considered**:

- Branch by kind in each handler/tool: rejected because it spreads policy.
- Persist capabilities as authoritative registry fields: rejected because adapter upgrades can make them stale.
- Adapter methods return generic “not implemented” errors after invocation: rejected because setup work may already have caused side effects.

## Decision 6 — Extract mechanism, retain adapter policy

**Decision**: Process, HTTP, port, path, and proxy modules provide bounded mechanisms; WordPress orchestration remains behind the WordPress adapter and legacy modules until parity supports later migration.

**Rationale**: Generic projects need safe primitives but do not need to inherit WordPress provisioning, credential, URL mutation, database, or Mailpit assumptions.

**Alternatives considered**:

- Reuse existing WordPress lifecycle wholesale: rejected because it synthesizes WordPress state.
- Rewrite all WordPress orchestration: rejected as too broad for a boundary-first feature.

## Decision 7 — Decompose Hermes by persistent state and side-effect ownership

**Decision**: Extract state, routing, jobs, gateway/public access, backup planning, and service composition sequentially, preserving current public functions through a facade.

**Rationale**: These concerns have different data-loss, process, network exposure, and authorization risks. The next scoped-recovery feature needs an isolated backup boundary but must not pull gateway or job behavior into recovery policy.

**Alternatives considered**:

- Split `_hermes.py` by line count: rejected because it does not define dependency direction.
- Add recovery policy directly to `_hermes.py`: rejected because it would deepen coupling.
- Rewrite all Hermes behavior at once: rejected because rollback and parity would be weak.

## Decision 8 — Structural enforcement over global metric targets

**Decision**: Guard prohibited dependencies, direct registry access, duplicate registration, kind-branch locations, and no-new-back-fill. Treat global wildcard/file-length counts as audit information.

**Rationale**: A smaller file can remain tightly coupled; a larger facade can be safe when it delegates through explicit contracts. Guards should test the architecture's failure modes.

**Alternatives considered**:

- Universal line limits: rejected as cosmetic.
- Remove every wildcard import now: deferred for untouched features to keep review and regression scope bounded.

## Resolved planning questions

- Existing `sandbox_core.py` surfaces used by production code or tests remain supported facades.
- Registry v1 and v2 remain readable; compatible unknown fields round-trip; unsupported future versions fail closed.
- CLI names/options/exit behavior and grouped help remain compatible; incidental ordering may become deterministic.
- MCP names and schemas are public; registration order is deterministic but not public.
- Current remote Scaleway/Hermes environment is the representative smoke target.
- Shared proxy scope is route plan/apply/remove and rollback; WordPress URL mutation stays adapter-owned.
- Unknown external core consumers are protected by the facade, while new use is prohibited.
- State-affecting migration tests use fixtures/disposable state and require the existing snapshot/human gates where destructive behavior is possible.
