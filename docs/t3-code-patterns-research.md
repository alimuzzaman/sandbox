# T3 Code pattern research

Status: research complete; no Sandbox code change approved.

This note records the prior approved, read-only audit of the local T3 Code app.
The paths below are source references in that checkout; they are proposals for
future Sandbox design work, not claims that Sandbox should copy T3's runtime or
Effect architecture.

## Source-backed proposals

1. **Closed, secret-safe boundary projectors.** T3's
   `packages/shared/src/schemaJson.ts` bounds issue counts, paths, and messages
   and deliberately omits actual values at process/UI boundaries;
   `packages/shared/src/schemaYaml.ts` turns parse/stringify failures into
   generic structural errors; and
   `apps/server/src/sourceControl/SourceControlProvider.ts` strips credentials,
   query strings, fragments, and control characters from transport errors.
   Sandbox already has redaction helpers and closed CLI/MCP projectors, so the
   useful lesson is a consistency audit of public boundaries—not a second
   error schema. Keep raw causes internal and bounded.

2. **Explicit MCP safety metadata and scoped invocation.** T3's
   `apps/server/src/mcp/McpInvocationContext.ts` requires capabilities on a
   scoped invocation, while `apps/server/src/mcp/McpHttpServer.ts` projects
   generic unauthorized/failure responses and registers read-only,
   destructive, idempotent, and open-world annotations. The contract scopes are
   explicit in `packages/contracts/src/auth.ts`. Sandbox already has explicit
   tool-group ownership and dependencies in
   `mcp/wp-server/tools/manifest.py` and `mcp/wp-server/composition.py`; a
   future design could add safety metadata there without changing transport
   behavior. Guard against duplicated or stale metadata.

3. **One owner for lifecycle, retry, and status.** T3's
   `packages/client-runtime/src/connection/supervisor.ts` and
   `packages/client-runtime/src/connection/registry.ts`, with the
   `docs/architecture/connection-runtime.md` contract, give one supervisor
   ownership of phases, retries, wakeups, and scoped cleanup. This fits a
   future Sandbox remote/hibernation or Guardian reconciliation design because
   `sandbox/application/runtime_service.py` and the durable job services already
   provide service and receipt boundaries. Do not add a second runtime facade or
   distribute retry ownership among CLI, MCP, and adapters.

4. **Deterministic readiness and drain gates.** T3's
   `packages/shared/src/DrainableWorker.ts` exposes `enqueue`/`drain` so tests
   wait for queued work without sleeps; `apps/server/src/serverRuntimeStartup.ts`
   queues commands until readiness and fails them when startup fails. Sandbox's
   durable jobs remain the source of truth; this pattern is only a possible
   in-process probe/Guardian test seam, not a replacement job state machine.

5. **Separate remote access, launch, and environment concerns.** T3's
   `docs/architecture/remote.md` distinguishes the execution environment from
   access and advertised endpoints, and keeps launch helpers separate from the
   ordinary renderer WebSocket path. Sandbox already models resolved targets
   and transports around durable jobs. A future design should keep endpoint
   normalization separate from runtime adapters and provisioning; it must not
   add a new remote transport or expose endpoint credentials in projections.

6. **Ordered, testable migrations.** T3's
   `apps/server/src/persistence/Migrations.ts` statically orders migrations and
   runs pending work before service readiness; the
   `024_BackfillProjectionThreadShellSummary.test.ts` fixture proves a backfill
   against older state. Sandbox already has versioned registry/job migration
   behavior and future-version refusal tests. Use this as a design reference
   for future evidence/job schema evolution, without changing current
   migration semantics or readiness gates.

## Explicit non-adoption decision

No T3 package, dependency, protocol, server lifecycle, MCP transport, remote
operation, or persistence implementation is being adopted. The audit found
that Sandbox already owns the corresponding boundaries: redaction and closed
projectors, explicit manifests/composition, RuntimeService and durable jobs,
resolved targets/transports, and versioned migrations. These findings therefore
close the research item only; each proposal requires its own design, acceptance
criteria, and tests before implementation. No runtime or source files were
changed for this research.
