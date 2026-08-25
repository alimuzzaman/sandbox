# V8 isolates and managed sandbox runtime research

**Date**: 2026-08-25
**Status**: Research only; no runtime default or security claim changes
**Scope**: OpenSandbox/Credential Vault follow-up, open-source V8-adjacent
runtimes, Cloudflare's `workerd` model, Scaleway Serverless Containers Sandbox
v2, and possible Lenzora/Sandbox integration seams.

This note extends the credential-vault comparison in
[`specs/045-credential-vault-isolation/research.md`](../specs/045-credential-vault-isolation/research.md).
External documentation and repository content are evidence to verify against a
pinned release, not implementation authority.

## Executive decision

Do not replace Sandbox's Linux/browser boundary with a V8 isolate. V8
`Isolate`, Cloudflare `workerd`, Deno permissions, and QuickJS are in-process
language runtimes or capability systems; none is, by itself, an operating-system
boundary for hostile code. Keep Chromium capture in a browser-specific process,
managed container, or microVM. If Lenzora later accepts customer-authored pure
transforms, a small QuickJS-ng or Wasmtime worker can be evaluated behind an
outer process/container boundary.

Scaleway Serverless Containers Sandbox v2 is useful as a **managed remote
provider profile**, not as a local runtime to embed. It uses gVisor for faster
cold starts but implements only a selected Linux syscall set. Its deployment
secrets are environment variables available to the container, so they are not a
replacement for an opaque, per-request credential broker.

## What OpenSandbox contributes

The worthwhile Credential Vault ideas are narrow and already captured in the
credential-vault plan:

- Bind an opaque credential reference to an exact destination and request scope
  (scheme, host, port, method, path), with expiry and revocation.
- Apply the credential at the trusted egress boundary; never place its value in
  guest environment, argv, files, snapshots, logs, or retained output.
- Persist references and policy digests, then re-verify isolation and egress
  proof before reinjection after restart.
- Report declared capabilities separately from effective, live proof.

Do not copy transparent HTTPS MITM, default-allow compatibility modes, or
OpenSandbox's Kubernetes-specific pause/snapshot and multi-tenancy assumptions
into the Sandbox default. The v1 design remains an explicit application-layer
broker, not arbitrary `curl`/Git/SDK interception.

## Open-source runtime comparison

| Runtime | License/status | Actual boundary | Integration judgment |
| --- | --- | --- | --- |
| V8 `Isolate` | BSD-style/3-clause BSD; actively maintained | Separate managed heaps inside one process. V8's newer heap sandbox is an in-process memory-safety defense, not a process, VM, or network policy. | Excellent JS engine; not a hostile-workload boundary. Embedding C++ is a substantial project. |
| Cloudflare `workerd` | Apache-2.0; open source | Workers-style V8/Wasm host APIs. Its README says it is not sufficient defense-in-depth for malicious code and should run inside a secure VM. | Study its host/capability model; use only inside an outer gVisor/Kata/Firecracker-like boundary for hostile tenants. |
| `isolated-vm` | ISC; maintainer describes maintenance mode | Node addon over V8 isolates. `memoryLimit` is a guideline and catastrophic V8 errors can require process abort; its security guidance recommends a separate process plus OS/VM isolation. | Lower-risk JS jobs only; not a sole Lenzora boundary. |
| QuickJS / QuickJS-ng | MIT; QuickJS-ng is the actively maintained fork | Small in-process C engines. QuickJS-ng exposes memory, stack, and interrupt limits, but its security policy does not treat malformed/untrusted bytecode as safe. | Good narrow deterministic transform engine in a disposable worker with a tiny host API; not Chromium isolation. |
| Deno | MIT; active V8/Rust runtime | File, network, environment, and subprocess permissions are deny-by-default, but same-privilege code has no execution limit; Deno recommends an OS sandbox/VM/gVisor/Firecracker for untrusted code. | Best JS/TS ergonomics, still an outer-sandbox workload. |
| Wasmtime / WebAssembly | Apache-2.0; active Bytecode Alliance runtime | WebAssembly linear memory and WASI capabilities provide a strong language-neutral in-process model; resource limits and patching remain embedder duties. | Strong candidate for a future schema/plugin ABI when code can be compiled to Wasm; not a JS/TS runtime by itself. |
| gVisor | Apache-2.0 | User-space kernel interposes between workload and host kernel; stronger than an in-process isolate but not a hardware VM. | Candidate outer Linux worker profile; qualify syscall, browser, networking, and performance behavior before adoption. |
| Firecracker | Apache-2.0 | KVM microVM with a minimal device model; stronger boundary with higher operational cost and host/guest patching responsibilities. | Candidate for high-risk workloads, not a drop-in JS engine. |

Node's `node:vm` contexts are deliberately excluded: Node documents them as
not a security mechanism for untrusted code. See the primary sources in the
source list below for licenses, limits, and security caveats.

## Scaleway Serverless Containers Sandbox v2

Scaleway documents two sandbox modes. v1 is legacy, has slower cold starts, and
supports the full Linux syscall interface (but has a documented long-running
clock-drift issue). v2 is recommended, relies on gVisor, improves cold starts,
and supports only a selected Linux syscall set. It is a managed container
profile, not a published V8 isolate or Firecracker VM.

### Runtime and lifecycle facts

- Containers are stateless HTTP services that scale to zero. The documented
  scale-to-zero idle period is 15 minutes; scale-down begins after 30 seconds.
  Redeployments are rolling. Local storage is ephemeral and disappears with the
  instance; Block Storage and a running-container snapshot/restore API are not
  documented.
- In v2, `/tmp` and `/dev` are memory-backed `tmpfs` with no separate size cap;
  writes count directly against the container RAM limit and can cause OOM and a
  restart.
- v2 currently has no copy-on-write for forked subprocesses, so memory grows
  with fork count. This is a material risk for Chromium or other multi-process
  images; v1 may be more compatible when this is proven necessary.
- The service requires `linux/amd64` images. Use an external object store or
  database for durable artifacts/results.

### Network and secret facts

- One Private Network may be attached. Private egress uses the private
  interface, Internet egress uses the public endpoint, and private inbound
  traffic is not supported. The public endpoint cannot currently be disabled;
  use private mode and IAM authentication where appropriate. Outbound SMTP
  ports 25 and 465 are blocked.
- Secrets are deployment/container environment variables: hidden in the
  console after initial validation, inherited by containers in a namespace, and
  not yet linked to Scaleway Secret Manager. This is deployment-scoped
  injection, not exact per-request least privilege. The reviewed docs do not
  describe a transparent credential broker or per-destination egress allowlist.
- Treat responses, logs, and artifacts as untrusted. Keep Lenzora capture
  credentials outside the container when possible and use an external broker
  if a request must be authenticated.

### Published limits to model in any adapter

The current limitations page lists 70–6000 mvCPU, 128–12,228 MB memory,
24,000 MiB temporary disk, 80 concurrent requests per instance, 50 simultaneous
instances per container, 5000 invocations/second, 10 seconds–60 minutes HTTP
request duration, 200 environment variables plus 200 secret variables (each up
to 65,536 bytes/chars), 600 GiB total container memory per organization, and
one Private Network per container. Exceeding limits can restart a container;
lower quotas may apply before account verification. These are provider limits,
not Sandbox guarantees.

### Developer/API surface

The developer portal exposes a versioned HTTP API, CLI, Terraform, and Go,
JavaScript, and Python SDKs. The current Serverless Containers API is the
regional `containers/v1` surface at `api.scaleway.com`; it supports namespace
and container list/create/get/update/delete operations, explicit redeploy, and
trigger resources. The container create/update schema exposes image, region,
`min_scale`/`max_scale`, memory/CPU, timeout, privacy, HTTPS-only access,
Private Network ID, startup/liveness probes, command/args, local-storage
limit, and a `sandbox` enum (`v1`/`v2`). This makes a provider adapter feasible,
but it also means Sandbox must pin API version, image digest, region, and the
selected sandbox mode rather than relying on console defaults.

Scaleway API calls use an `X-Auth-Token` secret key whose permissions inherit
the associated IAM user/application. A Sandbox integration therefore needs a
dedicated least-privilege IAM application/key, never an owner key, and must
keep the key in the existing registered-source secret broker. API responses
include public endpoint and lifecycle status; treat those as provider metadata,
not proof that the workload's isolation or credential policy is effective.

The API documentation says new containers default to public unless `privacy`
is set to private. An adapter must set private/IAM or HTTPS-only policy
explicitly and verify the resulting state after create/update/redeploy. [Scaleway
developer portal](https://www.scaleway.com/en/developers/)
[API overview](https://www.scaleway.com/en/developers/api/)
[Serverless Containers API](https://www.scaleway.com/en/developers/api/serverless-containers)
[container schema](https://www.scaleway.com/en/developers/api/serverless-containers/containers)

## Lenzora evidence and consequence

The current capture path resolves a credential in the application process,
converts it to `Authorization`/`Cookie`/custom headers, and passes it through
Playwright `extraHTTPHeaders`. The repository path currently persists the
`captureOptions` object to `snapshotRequest` and `snapshot` JSON, so a real
header value would be retained unless another caller-side redaction exists.
This is a concrete credential-custody gap independent of the choice of JS
runtime. See:

- [`execution-runtime.ts`](/Users/alim/Sites/git/lenzora/src/comparison-sets/registry/execution-runtime.ts#L45-L155)
- [`snapshot.service.ts`](/Users/alim/Sites/git/lenzora/src/services/projects/snapshot.service.ts#L558)
- [`project-repository.ts`](/Users/alim/Sites/git/lenzora/src/projects/providers/prisma/project-repository.ts#L601-L623)

A V8 isolate would not replace Chromium, DOM/fonts, browser binaries, or the
capture network guard. The safer direction is an opaque per-request reference
and exact-origin egress broker, with fail-closed behavior when the broker or
isolation proof is unavailable.

## What Sandbox could integrate later (all lower priority)

1. **Managed gVisor qualification profile.** Add a separately qualified
   `gvisor`/Sandbox-v2-like adapter or evidence lane for Linux workers. Measure
   syscall compatibility, browser startup, DNS/egress enforcement, `/tmp` RAM
   accounting, fork/subprocess memory, OOM/restart behavior, and cleanup. Do not
   promote it from evidence to default adoption based on the provider label.
2. **Optional Scaleway remote provider.** Implement a manifest-registered,
   explicitly selected provider for stateless HTTP jobs. Pin image digests and
   region, persist provider/container IDs and artifact references, model
   scale-to-zero/OOM/rolling replacement, and enforce provider limits. This is
   not a replacement for Sandbox's persistent interactive workspace or local
   Credential Vault.
3. **Opaque credential-reference contract.** Extend the managed-native plan with
   a broker-only resolver and exact-origin request binding. Store references and
   digests only; never make Scaleway namespace secrets or guest environment
   variables the vault API.
4. **Pure transform worker.** Benchmark QuickJS-ng first, then Wasmtime if a
   language-neutral ABI is valuable. Require a disposable process/outer sandbox,
   no network/filesystem imports, schema-only input/output, CPU/memory/wall-clock
   limits, bounded output, and hostile probes. Keep browser capture out of this
   path.
5. **Evidence and receipt schema.** Record runtime/provider, image digest,
   region, policy digest, resource limits, and bounded failure/restart reasons
   without secret values. Require a release-specific compatibility matrix before
   a provider is selectable.

No item above changes the current Compose default, promotes the unproven native
adapter, or authorizes a production deployment.

## Primary sources

- [OpenSandbox Credential Vault research/spec](../specs/045-credential-vault-isolation/research.md)
- [V8 API and license](https://v8.dev/docs/api), [V8 license](https://github.com/v8/v8/blob/main/LICENSE), [V8 Sandbox](https://v8.dev/blog/sandbox)
- [Cloudflare workerd](https://github.com/cloudflare/workerd)
- [isolated-vm security](https://github.com/laverdet/isolated-vm#security)
- [QuickJS manual/license](https://bellard.org/quickjs/quickjs.html), [QuickJS-ng](https://github.com/quickjs-ng/quickjs)
- [Deno security model](https://docs.deno.com/runtime/fundamentals/security/)
- [Wasmtime security](https://docs.wasmtime.dev/security.html)
- [gVisor](https://github.com/google/gvisor), [Firecracker](https://github.com/firecracker-microvm/firecracker)
- [Scaleway Sandbox v1/v2](https://www.scaleway.com/en/docs/serverless-containers/reference-content/containers-sandbox/)
- [Scaleway container concepts](https://www.scaleway.com/en/docs/serverless-containers/concepts/), [limits](https://www.scaleway.com/en/docs/serverless-containers/reference-content/containers-limitations/), [Private Networks](https://www.scaleway.com/en/docs/serverless-containers/reference-content/containers-private-networks/), [secure container](https://www.scaleway.com/en/docs/serverless-containers/how-to/secure-a-container/)
- [Scaleway developer portal](https://www.scaleway.com/en/developers/), [API overview](https://www.scaleway.com/en/developers/api/), [Serverless Containers API](https://www.scaleway.com/en/developers/api/serverless-containers), [container schema](https://www.scaleway.com/en/developers/api/serverless-containers/containers)
