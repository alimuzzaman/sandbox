# Data model: Shared node store and hardlinked Git workspaces

These are the logical values that the planned checkout and Compose helpers exchange. They
are not a new registry schema or a claim that implementation exists. Paths and volume names
are private implementation inputs unless a contract explicitly permits them in a bounded
diagnostic.

## `MaterializationRequest`

Input to the shared checkout materializer in `sandbox/workspaces/checkout.py` and its remote
shell renderer.

| Field | Type | Rules |
|---|---|---|
| `source_path` | absolute path string / `Path` | Existing, non-symlink directory; the deployed source checkout. |
| `workspace_path` | absolute path string / `Path` | Sibling or otherwise caller-authorized target; must not resolve to `source_path`. |
| `source_identity` | string or null | Optional deployment identity (`sha256:<64 hex>` when supplied); never used to derive a path. |
| `workspace_label` | string | Existing workspace label grammar; used only by the caller to derive the deterministic sibling path. |
| `lock_key` | string | Internally derived canonical source identity for the per-source materialization lock; callers cannot supply or override it. |

Preconditions are checked before any cleanup. A rejected request leaves both source and
workspace unchanged. The materializer does not discover a project from package files and does
not run package scripts.

## `CheckoutMaterializationReceipt`

The bounded result returned by the Python helper and represented in remote diagnostics. It is
safe to persist with the existing deployment/workspace receipt mechanism; it is not a source
of truth for project identity.

| Field | Type | Meaning / invariant |
|---|---|---|
| `schema` | integer | `1` for this contract. |
| `workspace_path` | string | The target path, only inside the owning deployment boundary. |
| `source_identity` | string or null | Echo of the validated deployment identity, never a mutable path. |
| `history_mode` | enum | `hardlinked`, `copied`, or `none` when no usable Git history exists. |
| `hardlinked_files` | non-negative integer | Count of regular immutable object/pack files linked to source; zero for copied/none. |
| `copied_git_entries` | non-negative integer | Count of Git entries copied by value. |
| `fallback_reason` | enum or null | `null` for hardlinked/none; bounded reason such as `git_marker_file`, `cross_device`, `unsupported`, or `permission`. Malformed/unreadable Git markers are refusals. |
| `source_mutation_check` | enum | `not_run` until a caller performs the integrity seam; never `passed` without observed evidence. |
| `lock` | object | `{key, acquired, released}`; a busy lock is an error, not a successful receipt. |

The receipt MUST NOT claim a byte saving, a host measurement, or a successful integrity check
that the caller has not actually observed.

## `GitStorageLayout`

The materialized workspace has two explicitly separated regions:

```text
workspace/
├── <worktree files copied by value>
└── .git/
    ├── HEAD, index, refs/, logs/, config, hooks/, ...  # private copies
    └── objects/
        ├── <loose object files>                        # hard links or private copies
        ├── pack/<pack/index/bitmap/rev files>          # hard links or private copies
        └── info/                                       # private copies
```

Invariants:

1. No source and workspace inode is shared for `HEAD`, `index`, refs, reflogs, config,
   hooks, `packed-refs`, `COMMIT_EDITMSG`, `objects/info`, the marker file, or any worktree
   administrative file.
2. A regular file is hard-linked only when it is a loose object with the expected Git object
   path shape or a regular pack/index/bitmap/rev file below `objects/pack/`. Symlinks and
   special files are never hard-linked.
3. A workspace Git command may create or unlink objects in its own `.git/objects`, but no
   operation may rewrite or chmod an inode still shared with the source. The regression
   seam checks source bytes, inode-sensitive metadata, tracked status, and `git fsck` after
   each deploy/reset/discard/unpack/test/build operation.
4. No alternate, `gitdir:` marker, or config entry may point back into the source checkout
   after successful materialization. A valid marker-file checkout is copied by value and its
   marker is rewritten to the private administrative directory.
5. If no `.git` directory exists, the worktree is copied as today with `history_mode=none`;
   this is success, not a hard-link failure.

## `ProjectFamily`

An in-memory identity used only while generating a generic Compose overlay.

| Field | Type | Rules |
|---|---|---|
| `runtime_id` | safe runtime id | The id selected by the registry/Compose adapter for this instance. |
| `family_id` | safe lower-case id | Derived by removing one exact `-workspace-<14 lowercase hex>` segment from the canonical runtime/root id. If the segment is absent or malformed, use the canonical id unchanged. Never derive from the workspace label alone. |
| `source_runtime_id` | safe runtime id | The source checkout's canonical id, used to prove family equality in tests. |
| `workspace_runtime_ids` | set of safe ids | Sibling workspace ids that resolve to the same `family_id`; informational only. |
| `volume_name` | string | Exactly `sandbox-nodestore-<family_id>`, with Compose `name:` set explicitly. |

The derivation is pure and collision-aware: source and workspace fixtures with the same
canonical family resolve to one id; two project identities that differ only by display name
must still resolve to different canonical runtime ids before this step. An ambiguous marker
is not stripped and therefore cannot accidentally join a family.

## `NodeStoreOptIn`

Normalized project-owned Compose declaration.

| Input | Normalized value | Behaviour |
|---|---|---|
| key absent | `False` | Legacy overlay is byte-identical to the current overlay. |
| `nodeStore: false` | `False` | Explicit legacy/rollback mode. |
| `nodeStore: true` | `True` | Emit the shared volume and environment contract. |
| any non-boolean | error `compose.nodeStore must be a boolean` | Refuse before writing an overlay or invoking Docker. |

The key is merged with the existing project/override Compose layers using the current
descriptor precedence. It is not inferred from `package.json`, lockfiles, image names, or
container inspection.

## `NodeStoreOverlay`

The normalized output for an opted-in service is:

```yaml
services:
  <declared service>:
    volumes:
      - sandbox-nodestore-<family_id>:/sandbox-node
    environment:
      SANDBOX_NODE_STORE: /sandbox-node/store
      SANDBOX_NODE_MODULES: /sandbox-node/node_modules/<canonical-runtime-id>
      npm_config_store_dir: /sandbox-node/store
volumes:
  sandbox-nodestore-<family_id>:
    name: sandbox-nodestore-<family_id>
```

The existing generated port/resource entries remain. The service receives one and only one
new shared node-store mount. The dependency tree and package store are both under that mount;
neither is written to the host-visible workspace directory. The project Compose file remains
responsible for removing any old per-workspace dependency volume and using
`$SANDBOX_NODE_MODULES`; Sandbox does not rewrite it.

For `False`, the normalized output contains no `SANDBOX_*` variables, no `npm_config_store_dir`,
no node-store volume, and no top-level `volumes` addition. The bytes of the existing overlay
must remain unchanged for the same descriptor, runtime id, and port.

## `LegacyWorkspaceState`

Compatibility classification for an existing workspace:

| State | Detection | Allowed behaviour |
|---|---|---|
| `legacy-copy` | Workspace predates Spec 044 or has a private full `.git/objects` copy | Start, reset, status, and build with the existing path; no migration required. |
| `shared-history` | Receipt/layout proves private metadata plus hardlinked objects | New materializer may refresh or reset under the source lock. |
| `fallback-copy` | Shared history was unavailable or marker-file/cross-device fallback was selected | Operate as a full copy; record the bounded reason; never retry links in place. |
| `indeterminate` | Receipt/path/integrity evidence is incomplete | Refuse destructive reset/destroy until the existing workspace recovery/index process resolves it. |

Migration is an explicit, reversible operator step. It never deletes a legacy workspace or
volume implicitly. Rollback is `compose.nodeStore=false` plus the old project Compose layout;
the old workspace remains intact until the operator confirms a named reclaim plan.

## State transitions

```text
validate request
   ├─ invalid/busy ───────────────> refused (no workspace mutation)
   └─ acquire source lock
         ├─ no .git ──────────────> copy worktree, history_mode=none
         ├─ directory + links work -> stage private metadata + links,
         │                             atomic publish, history_mode=hardlinked
         ├─ marker file/unsupported -> stage and publish full private copy,
         │                             history_mode=copied + fallback_reason
         └─ copy error ────────────> failed (source and previous workspace preserved)

compose.nodeStore=false/absent ─────────> legacy overlay, no new volume
compose.nodeStore=true + family valid ──> shared overlay, named volume
named reclaim plan (read-only) ─────────> confirmation_required until explicit apply
```
