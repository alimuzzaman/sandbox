# Research: Local Images for Hosted Deployments

## Separate local-image authority

**Decision**: Keep local build/transfer receipts separate from signed `hostingImages` staging and activation.

**Rationale**: The existing channel selects machine-owned registry policy and permits registry-specific identity aliases. Local transfer is operator-confirmed content movement with stricter config/platform/layer equality.

**Alternatives**: Reusing signed helpers would conflate authorities; unverified hard-no-build tags provide no source/image proof.

## Hermetic value-safe build projection

**Decision**: Bind exact source, project/files/profiles/services/build definitions, literal non-secret inputs, derived revision, and symbolic approved secret destinations. Run Compose with a constructed environment.

**Rationale**: Existing Compose execution can inherit ambient variables. Existing host config and secret binding digests use controller-local HMAC keys, so Linux and macOS cannot compare them.

**Alternatives**: Raw rendered config leaks secrets; secret-value hashes create an oracle; controller-local HMAC is not portable.

## BuildKit-only secret boundary

**Decision**: Permit secret values only through declared BuildKit secret inputs; refuse their use in image refs, args, contexts, Dockerfiles, platforms, or other public build fields.

**Rationale**: Current provider reads expose no shared value-version identity. Apply independently resolves approved current values and retains existing opaque recovery bindings.

**Alternatives**: Copying secrets between controllers violates scope; adding a shared secret-binding authority is a separate feature.

## Exact image comparator

**Decision**: Match exact Docker configuration ID, `linux/amd64`, and ordered RootFS layer digest.

**Rationale**: Archive/load preserves the portable configuration and layers. Docker 29's containerd store can expose a distinct builder-local `.Id`, so the receipt retains that as separate evidence while defining the saved archive configuration digest as the exact destination image ID. Tags, manifest aliases, builder-store IDs, and archive hashes do not by themselves prove destination runtime configuration equality.

## Remote engine serialization

**Decision**: Transfer and receipt-bound apply share one engine-wide lock held by a dedicated registered-SSH connection. Apply acquires it before delivery admission or source staging. This does not enable the separately gated hosting coordination store.

**Rationale**: Docker tags are daemon-global, so per-environment ownership cannot prevent two environments that declare the same tag from racing between verification and Compose. Connection lifetime gives bounded crash release without a separately migrated coordination capability.

## Dedicated fixed remote transport

**Decision**: Reuse registered remote lookup/SSH construction, but add a focused bounded stream transport. Stage an owner-only temporary archive, verify declared size/hash before load, then freshly inspect.

**Rationale**: Existing generic SSH helpers either buffer whole input/output or lack streamed stdin. Direct `docker save | docker load` cannot refuse a corrupted archive before loading.

**Alternatives**: Registry publication and raw operator SSH/Docker are out of scope.

## Immutable remote handoff

**Decision**: Publish a closed remote receipt beneath the registered Sandbox home under its content digest, bound to registration, authenticated machine, Docker daemon, platform, and observations.

**Rationale**: Linux can transfer while macOS later applies without local archives. Controller-local hosted state is not suitable cross-controller evidence.

## CLI and durable admission

**Decision**: Add `host image build-local`, `host image push-local`, and `host apply --local-images DIGEST`. Include the selector in closed durable apply arguments/evidence.

**Rationale**: The current two-level parser supports additive image actions. Explicit apply selection prevents transferred content from silently changing deployment behavior.

## Compatibility and protocol

**Decision**: Preserve receipt-free apply and existing image actions. Register new helper/receipt shapes and bump the remote spoken protocol while preserving supported compatibility.

**Rationale**: Remote service migration is required before new controller/runtime shapes can be relied upon.
