# Contract: family-scoped node-store Compose overlay

This contract defines the planned additions to
`sandbox/config/compose.py::ComposeSchemaProvider` and
`sandbox/runtimes/compose.py::ComposeAdapter._overlay`. It is normative for the
`compose.nodeStore` opt-in and deliberately leaves package-manager behaviour to the hosted
project. No current project is opted in by this planning artifact.

## Descriptor input

The project-owned `sandbox.config.json`, `sandbox.config.yml`, or label/ machine override may
contain:

```json
{
  "kind": "compose",
  "compose": {
    "file": "compose.yaml",
    "service": "web",
    "internal_port": 4321,
    "health_path": "/",
    "nodeStore": true
  }
}
```

`nodeStore` is a strict boolean:

| Raw value | Normalized descriptor | Result |
|---|---|---|
| missing | `node_store: false` | Current overlay, byte-for-byte. |
| `false` | `node_store: false` | Current overlay, byte-for-byte; explicit rollback/opt-out. |
| `true` | `node_store: true` | Shared node-store overlay below. |
| string/number/list/object/null | error `compose.nodeStore must be a boolean` | No overlay file write and no Docker invocation. |

The key is merged using the existing project → machine override → label override precedence
for the `compose` object. An override may explicitly set `false` to roll back an inherited
`true`; an absent override does not erase the project declaration. Sandbox never infers this
field from package files, lockfiles, image names, or a discovered install command, and never
executes a project package script while resolving it.

## Family derivation

`derive_node_store_family(runtime_id: str) -> str` is a pure helper in the Compose adapter.
It operates on the canonical runtime/root identifier selected by the registry, not on a
workspace label supplied by a job:

1. Validate the input with the existing safe runtime-id grammar and lowercase it.
2. If the identifier contains exactly one deterministic sibling marker
   `-workspace-<14 lowercase hexadecimal characters>`, remove that complete marker. The
   marker must be bounded by the identifier grammar; malformed, repeated, or ambiguous
   markers are not stripped.
3. Keep the source identity/collision suffix that disambiguates two canonical project roots.
   If a runtime id is already the source id (no marker), use it unchanged.
4. Return the resulting family id after the existing length-safe slug normalization. An
   implementation that would collapse two distinct canonical project identities must append a
   deterministic hash suffix before returning; this is tested with same-display-name projects.

The composition boundary must pass the canonical source family id (or embed the same
collision suffix in every sibling runtime id) when a workspace marker is present. If stripping
the marker would produce a family id that disagrees with the source identity's collision-safe
id, the adapter refuses the opt-in as an ambiguous family rather than silently joining the
wrong project. This is the collision decision: preserve the source identity, fail closed on a
disagreement, and never fall back to a global/shared-by-name volume.

The family id is stable across the source checkout and all sibling workspaces materialized
from it. A workspace label change never creates another family. Different canonical project
identities never intentionally share a family, even when their display names match.

The named volume is exactly:

```text
sandbox-nodestore-<family_id>
```

The Compose YAML must set that value as the volume's explicit `name`, preventing Compose from
prefixing it with `sandbox-<workspace-runtime>`.

## Overlay shape

For a normalized `node_store=true` descriptor, `_overlay` retains the existing generated port
and resource entries and adds exactly one service mount, three environment variables, and one
top-level named volume:

```yaml
services:
  <declared service>:
    ports:
      - "127.0.0.1:<allocated>:<internal>"
    cpus: "<existing>"
    mem_limit: "<existing>m"
    pids_limit: <existing>
    volumes:
      - "sandbox-nodestore-<family_id>:/sandbox-node"
    environment:
      SANDBOX_NODE_STORE: /sandbox-node/store
      SANDBOX_NODE_MODULES: /sandbox-node/node_modules
      npm_config_store_dir: /sandbox-node/store
volumes:
  sandbox-nodestore-<family_id>:
    name: sandbox-nodestore-<family_id>
```

Normative invariants:

- The volume is Docker-managed and read-write at `/sandbox-node`; it is not a host bind.
- `SANDBOX_NODE_STORE` and `npm_config_store_dir` are the same path.
- `SANDBOX_NODE_MODULES` is a sibling path inside the same mount.
- The service receives one shared node-store mount, not one mount per workspace. No generated
  overlay volume may target the host-visible project/workspace directory.
- The overlay does not add `node_modules` mounts, alter the project command, change the image
  user, run `corepack`, run npm/pnpm/yarn, or inspect package files. The project Compose file
  must remove its old per-workspace dependency volume and point its dependency tree at
  `$SANDBOX_NODE_MODULES`.
- A project/image that ignores the variables is still allowed to start and install using its
  existing layout; Sandbox must not turn that compatibility case into an overlay-generation
  failure. The lower storage saving is then a project integration gap, not a Sandbox error.
- Build-time package caches (including BuildKit `--mount=type=cache,id=pnpm-store`) are not
  changed by this overlay.

## Legacy byte identity and rollback

For the same descriptor, runtime id, port, and resource values, `nodeStore=false` or an absent
key must produce byte-identical `sandbox.override.yaml` content to the pre-feature adapter.
It contains only the existing ports, CPU, memory, and PID limits. It has no `volumes:` block,
no `environment:` block, no `SANDBOX_*` key, no `npm_config_store_dir`, and no named-volume
reclaim instruction.

An existing workspace or project does not need migration to start, reset, status, or build.
Rollback is explicit: set `compose.nodeStore=false`, restore the project-owned dependency
mount/command, and leave the named family volume and old workspace untouched until a reviewed
reclaim plan is accepted. The adapter must not remove old per-workspace volumes as a side
effect of `ensure`, `apply`, `destroy`, or descriptor parsing.

## Named store lifecycle (no automatic cleanup)

The shared volume is intentionally outside broad workspace-volume cleanup. The current Sandbox
code has **no named-store reclaim interface**; raw Docker inspection, process listing, and
volume removal are unsupported and must not be used as a workaround. Before any apply is
possible, a future implementation task must define and register a supported Sandbox CLI/MCP
surface with the following plan/apply contract. This feature does not add automatic deletion.

The supported interface must preserve:

1. **Plan/read-only**: resolve the exact family id, inspect
   `sandbox-nodestore-<family_id>`, report its current existence/size and running-container
   mounts, and write a named plan id. No `rm`, `prune`, or workspace mutation is allowed.
2. **Confirm/apply**: require the plan id and an explicit `--confirm` (or equivalent API
   confirmation), re-check that no running container mounts the exact named volume, then remove
   only that exact volume. A missing volume is an idempotent `already_absent` outcome.
3. **Never** use `docker volume prune`, wildcard removal, an inferred family, or automatic
   reclaim from `ensure`, `status`, capacity pressure, job cleanup, or workspace destroy.

Removing a shared package store is recoverable only by a later install repopulating it; the
plan/apply record must say so. No command may claim that package data is backed up or that a
store removal is lossless.

## Required test seams (future implementation)

`tests/test_compose_node_store.py` must cover at least:

| Seam | Falsifiable assertion |
|---|---|
| Descriptor normalization | absent/false/true values normalize as above; every non-boolean is rejected before overlay/Docker I/O; override precedence is deterministic. |
| Family helper | source id and sibling `-workspace-<14hex>` ids resolve to one family; labels do not split it; malformed/repeated markers do not strip; distinct canonical roots (including same display name) remain distinct. |
| Overlay rendering | opt-in output has exactly one explicit named volume, one `/sandbox-node` mount, exact environment values, and no host bind for store/modules. |
| Legacy output | absent/false output bytes match the pre-feature fixture and contain no node-store additions. |
| Unsupported consumer | an image/Compose fixture that ignores the variables still passes adapter service validation; no package script is executed by Sandbox. |
| Concurrent family use | two runtime ids of one family request the same volume name; two different families request different names. |
| Permission boundary | a container writes store/modules in the named volume while ordinary host-side workspace preparation/removal sees no root-owned store/module entries in the bind. |
| Build-cache boundary | the generated overlay and any adapter command leave existing BuildKit cache declarations unchanged. |
| Reclaim safety | plan is read-only; apply without confirmation or with a mounted/raced volume refuses; only the exact planned name can be removed; broad prune/wildcards are rejected. |

## Required live-remote evidence (future release gate)

On a real configured remote, after the project itself opts in:

1. Materialize two workspace labels from one deployed source and one control project without
   opt-in. Capture `docker compose config` for each and show that the family pair names one
   exact `sandbox-nodestore-<family>` volume while the control project remains byte-identical
   to legacy output.
2. Start both family workspaces and inspect the declared service mounts/environment. Confirm
   no per-workspace dependency volume is created and that the project dependency tree and
   store resolve inside `/sandbox-node`.
3. Run concurrent installs of differing dependency versions. Capture exit status and bounded
   store/module paths; no claim of performance or byte savings is valid without measured
   host-space evidence.
4. Remove/recreate only a temporary test store through the named plan/confirmation path and
   verify the next install repopulates it. Do not invoke broad cleanup or touch unrelated
   volumes.

This planning package contains no remote output, volume measurements, deployment result,
permission proof, or release certification.
