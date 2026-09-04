# Research: Instance-Scoped Server Configuration Fragments

## Decision 1: Make `server` a feature-owned command

**Decision**: Move `server` from the legacy parser/`sandbox.commands.net` registration
to one `CommandSpec` owned by `sandbox.commands.server`. Preserve both existing switch
forms, `sb server <type>` and `sb server <instance> <type>`, while recognizing
`sb server config <operation>` as a separate grammar. The command spec supplies a
pre-dispatch predicate that skips legacy writers for read-only config inspection.

**Rationale**: The current positional parser cannot safely grow a `config` subtree, and
routine CLI pre-dispatch rewrites Compose/environment artifacts. Command ownership gives
one compatibility boundary and lets `list`/`show` remain truly read-only.

**Alternatives considered**: Add another top-level `server-config` command (rejects the
requested interface and splits ownership); extend the legacy parser in `sandbox.cli`
(continues architecture debt and cannot own read-only pre-dispatch behavior); remove old
switch syntax (violates parity).

## Decision 2: Bind state to an instance incarnation, not its display name

**Decision**: Mint an opaque random incarnation ID when a new WordPress instance record
is created. Preserve it across apply, stop/start, relocation, and ordinary reconciliation;
remove/disassociate its fragment state only through confirmed deletion. All fragment
paths, receipts, locks, runtime mounts, and transactions bind this ID plus server type.

**Rationale**: Existing project and runtime identities are stable across a delete and
recreate of the same project/label. They cannot by themselves prove that retained server
configuration belongs to the new incarnation.

**Alternatives considered**: Key by display name (unsafe reuse); key by project root and
label (same reuse problem); infer identity from container ID (changes on legitimate
recreate and is not the ownership authority).

## Decision 3: Use an owner-only generation repository and one durable transaction

**Decision**: Store exact bytes and rendered candidates under
`$SANDBOX_HOME/runtime/server-config/<incarnation>/`. Immutable generations are content
addressed by canonical fragment-set digest. An atomic active receipt names the one proven
known-good generation. A mode-0600 journal records one mutation from `prepared` through
terminal state under a per-incarnation `flock`.

**Rationale**: Runtime activation and committed state cannot be one filesystem write.
Retaining prior and candidate identities before activation allows exact rollback and
crash reconciliation without selecting by timestamp.

**Alternatives considered**: Put fragment bytes in `sandbox.local.yml` or the registry
(broadens secret/output and parser surfaces); edit generated runtime files in place (not
atomic or reversible); SQLite (unnecessary for one writer/one journal and complicates
mountable generation files).

## Decision 4: Deny by default with a versioned authority

**Decision**: `wordpress-cache-v1` is a semantic authority, not permission to paste any
native configuration. A common policy rejects invalid names, controls, non-UTF-8 text,
NUL, secret-like identifiers, protected routes, and host/global constructs. Each adapter
then parses its native subset and rejects every unknown directive or context. Accepted
bytes are retained unchanged and hashed; parsing does not rewrite user input.

**Rationale**: A native syntax test proves syntax, not isolation. An allowlisted grammar
is required before a privileged server accepts caller-controlled data.

**Alternatives considered**: Regex denylist (new directives bypass it); accept any config
that `nginx -t`/OLS accepts (allows listeners, proxying, includes, and host escape); invent
a new portable DSL (cannot consume plugin-emitted fragments).

## Decision 5: Render the complete ordered set

**Decision**: Sort fragments by normalized name, wrap each in adapter-owned provenance
markers, and derive `set_digest` from the server type, authority versions, names, content
digests, and policy revisions. Derive the rendered generation separately from that set,
the renderer revision, canonical manifest, and exact rendered files/modes. Apply/revert
validate and activate the complete set.
Byte-identical same-name apply and healthy missing-name revert compare committed evidence
and return before validation.

**Rationale**: Individually valid fragments can conflict. Deterministic ordering and one
set identity make validation, rollback, drift detection, and evidence repeatable.

**Alternatives considered**: Append in request order (duplicates and nondeterminism);
validate only changed content (misses combined failures); overwrite one shared snippet
(cannot support independent named revert).

## Decision 6: Use exact active image identity for both adapters

**Decision**: Observe the target web container and bind validation to its content-addressed
image ID, runtime/container identity, server type, mount identity, and incarnation. Recheck
all facts immediately before activation. Mutable tags and configured image names are
metadata only and cannot authorize validation.

**Rationale**: The active binary and parser behavior are what matter. A compatible host
binary or mutable tag can accept a candidate the running server rejects.

**Alternatives considered**: Host-installed validators (version drift); validate against
the configured tag (tag can move); execute validation inside the live container (exposes
live data/config and can have side effects).

## Decision 7: Validate nginx in its real bounded site context

**Decision**: The nginx adapter renders the existing Sandbox base server configuration
plus the complete instance fragment generation and uses the exact active image to run
`nginx -t` against a synthetic, data-free document-root fixture. It also verifies that
each expected fragment marker appears once in the parsed effective candidate. The live
nginx service mounts only its incarnation root and includes its active generation.

**Rationale**: xSpeed's snippet contains server-context `set`, `if`, `rewrite`, `location`,
`access_log`, and `add_header`; validating it as a standalone file or inside the wrong
context is false evidence.

**Alternatives considered**: Mount directly over the shared checked-in base config
(cross-instance coupling); concatenate into `config/nginx-sandbox.conf` (host-global
generated source and unsafe concurrency); syntax scan only (misses native parser/context).

## Decision 8: Validate OpenLiteSpeed with an isolated exact-image boot and probe

**Decision**: The OLS adapter renders a complete instance vhost generation at a fixed
adapter-owned inclusion point. Validation creates a disposable exact-image container with
network mode `none`, read-only root, only adapter-owned synthetic fixtures/config mounts,
bounded tmpfs, no live volumes, no inherited environment, and no secrets. It starts OLS,
probes an adapter canary over the container's loopback, and requires evidence that every
fragment is active rather than silently ignored. If the exact image lacks the required
isolated probe/inclusion capability, support is refused before mutation.

**Rationale**: OpenLiteSpeed can accept or ignore directives without a useful standalone
syntax result. A real isolated boot plus behavior canary is the minimum honest validation.

**Alternatives considered**: Treat `.htaccess` presence as proof (OLS can ignore modules
or directives); `lswsctrl` status only (does not prove fragment inclusion); boot with live
WordPress volumes or network (violates isolation and can mutate data).

## Decision 9: Keep activation target-only and readiness distinct from reload

**Decision**: Adapters expose typed `activate`, `reload`, `observe_readiness`, and
`observe_runtime` operations with one shared absolute deadline. nginx reloads only the
selected Compose `nginx` service. OLS restarts/reloads only the selected `wp` web service.
Success requires a post-activation HTTP readiness observation plus unchanged incarnation,
server, exact image, and active generation. Unknown is not ready.

**Rationale**: A successful control command can be followed by a failed process. Separate
phase evidence identifies the actual failure and prevents acknowledgement from becoming
readiness proof.

**Alternatives considered**: Full `compose up --force-recreate` (unnecessary service
churn and weak isolation); reload acknowledgement as success (can mask later failure);
unbounded retry loops (violates operation deadlines).

## Decision 10: Roll back once and fail closed on ambiguity

**Decision**: Before live activation, publish a transaction that binds exact prior and
candidate sets and runtime facts. Any possible post-activation failure restores the prior
generation pointer, performs at most one recovery activation, and proves readiness. A
failure or timeout leaves `recovery_needed`; later mutation can only reconcile the exact
journal-bound prior state or refuse. Read-only operations never reconcile.

**Rationale**: Retrying a failed candidate or selecting the newest generation can compound
damage. One known-good recovery attempt is bounded and auditable.

**Alternatives considered**: Repeated reload/restart until healthy (unbounded and may hide
root cause); declare rollback after pointer restoration (server may still run candidate);
auto-repair from `list` (violates read-only contract).

## Decision 11: Require lifecycle gates and an attached instance mount

**Decision**: New/reconciled instances receive the incarnation-specific read-only mount.
If an old running instance lacks it, config mutation refuses before state write and points
to supported `sb apply --instance NAME`; it does not silently recreate the web tier.
Server switch refuses any active or unhealthy fragment state. Deletion needs ordinary
destructive confirmation plus explicit inclusion of the exact fragment state.

**Rationale**: Mount attachment changes runtime identity and cannot be hidden inside
candidate activation. Explicit lifecycle gates preserve FR-022 and make deletion/name
reuse safe.

**Alternatives considered**: Attach the mount during apply (invalidates validated runtime
identity); write into an already mounted WordPress `.htaccess` (conflicts with plugin/WP
ownership and cannot guarantee exact restoration); carry fragments across server types
(unsafe translation).

## Decision 12: Defer Apache and MCP parity

**Decision**: V1 reports nginx and `litespeed` only. Apache remains unsupported until its
`.htaccess` coexistence, full-candidate validation, header behavior, rollback, and live
two-instance proof satisfy the same contracts. MCP may later adapt the application service
but no second policy implementation is planned.

**Rationale**: Apache looks cheap because xSpeed emits `.htaccess`, but that file is
jointly mutated by WordPress and plugins. Safe named replacement and exact rollback are
not cheap. Deferring it protects the required nginx/OLS path.

**Alternatives considered**: Marker-edit live `.htaccess` in v1 (races independent
writers and cannot restore exact combined state); claim list-only Apache support
(misleading); add MCP simultaneously (larger public surface before CLI proof).

## Decision 13: Prove behavior through Sandbox-owned operations

**Decision**: Live acceptance uses disposable target and control instances, plugin-owned
warm/purge actions through `sb wp`, supported Sandbox HTTP/browser probes for response
markers, and an independent request-scoped PHP sentinel. A controlled adapter fault that
passes validation then fails activation/readiness proves rollback; invalid syntax proves
pre-activation refusal and zero reload separately.

**Rationale**: A response header alone could be emitted by PHP, and invalid syntax should
never reach activation. The evidence must distinguish server ownership, validation
refusal, and rollback as separate claims.

**Alternatives considered**: Infer from files or source (not live proof); use raw Docker,
SSH, or direct runtime edits (unsupported); call a syntax-invalid refusal a rollback
(contradicts safe validation order).

## Resolved Clarifications

- Canonical public server type remains `litespeed` for compatibility; human docs describe
  it as OpenLiteSpeed.
- Fragment bytes must be strict UTF-8 server text, non-empty, at most 262,144 bytes, with
  no NUL or disallowed control characters. Bytes are not normalized before hashing/storage.
- Routine output uses SHA-256-derived content/set identities only. Exact image/container
  identifiers are bounded evidence fields; raw inspect payloads are never returned.
- No time-based choice participates in recovery. Timestamps are display/audit fields only.
- No open product or architecture question remains for task generation.
