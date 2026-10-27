# RexEnv as a preferred Sandbox provider

Checked 2026-10-27 against public RexEnv source at
`a4c039b01715e63caca3a3df3480509a52ad3604`, the published 0.9.4 release, and
Sandbox's current runtime, ingress and resolver contracts. No app, installation,
private state, DNS/CA setting or live service was inspected or changed. This is
source-based feasibility, not runtime qualification.

## Recommendation

RexEnv is a plausible preferred provider for explicitly selected, trusted
WordPress/PHP development and headless QA. It is not ready to replace Sandbox's
universal default. There is currently no registered RexEnv integration in
Sandbox. A provider must satisfy the selected project's required capabilities
before becoming eligible, and persisted projects must keep their chosen runtime
when a command fails. Do not silently start another backend as error recovery.

Keep Compose for generic multi-service applications and workflows requiring
container boundaries. Keep Sandbox's job, workspace, secret, recovery, hosted
delivery and evidence mechanisms; reuse RexEnv's native site/service mechanisms
where their contracts fit. Native project code executes with host-user authority,
so convenience does not establish hostile-code containment.

The current [clean-URL policy](../../clean-url-default.md) makes Sandbox-owned
Docker/Caddy and DNS the default. Runtime/adoption evidence and an explicitly
approved policy change are required before promotion. This check performs no
default change and does not authorize disabling the existing path.

## Feature fit

“Available upstream” means source/doc support, not a working Sandbox integration.

| Sandbox feature | Fit and required work |
|---|---|
| Native WordPress/PHP sites, PHP/server selection, linked repositories | Strong upstream fit. Add exact project-root/label/site-ID mapping, compatible version checks and ownership receipts. A plugin repository is not automatically a complete WordPress document root. |
| Local TLD/DNS and trusted HTTPS | Available upstream. Register separate resolver and ingress adapters; let RexEnv remain the owner of its DNS route, edge and CA. Prove existing-site preservation and removal behavior. |
| WordPress management, multisite, repo scripts and watchers | Named upstream operations exist. Map only qualified capabilities; shared services and route enable/disable do not provide independent per-project process suspension. |
| Browser visits/screenshots and HTTP diagnostics | Reusable against a correctly exposed URL, but trust, hostname, login, redirects and self-requests need acceptance with the selected backend. |
| Arbitrary `sb exec`, WP-CLI/eval and PHPUnit/integration suites | Execution bridge required. Offered repository scripts or named WP operations do not themselves establish Sandbox's argv/cwd/toolchain/deadline/stream/cancellation contract. No silent host-binary fallback. |
| Snapshots, `@install`, DB-only/full restore and reset | Recovery integration required. Upstream database export/import is not proof of Sandbox's database-plus-files baseline and rollback semantics. |
| Query Monitor, mail, Xdebug, PHP extensions and server variants | Qualify individually. Site-scoped data and execution planes matter; the documented Linux catalog lacks some desktop components, including Apache and Xdebug. |
| Durable jobs, workspace leases, source sync, resource accounting and retention | Sandbox remains the control-plane owner. An upstream job ID or CLI timeout does not settle our acceptance, owned-child cessation or cleanup guarantees. |
| Secrets, supervised consumption and credential mediation | Preserve Sandbox's broker/authority boundary. Do not convert native shared-host execution into a managed credential/isolation claim. |
| Generic Compose applications and multiple arbitrary services | Keep the existing Compose provider; PHP site management is not a generic container-runtime replacement. |
| Hosted production delivery, Cloudflare DNS/Access and server recovery | Separate Sandbox capabilities. Local TLD/CA features do not replace public DNS, public TLS, deployment or production recovery. |
| Wake-on-request and idle suspension | Needs an explicit integration contract. RexEnv's site stop keeps a stopped route; global service stop affects sibling sites. Neither should be advertised as existing Sandbox activation parity. |

Upstream command evidence: [CLI contracts](https://github.com/rexenv/rexenv/blob/a4c039b01715e63caca3a3df3480509a52ad3604/docs/CLI-ROADMAP.md).
Sandbox capability handling is in `sandbox/runtimes/wordpress.py`; native trust
classes and owner separation are in [native runtime isolation](../../native-runtime-isolation.md).

## Reuse TLD and SSL through one owner

Sandbox can expose its usual commands while delegating site operations to a
RexEnv runtime adapter, routing/certificates to a RexEnv ingress adapter, and
name resolution to a RexEnv resolver adapter. This follows Sandbox's existing
separation; runtime code must not independently edit ingress or resolver state.
RexEnv generates and reloads its edge configuration from site records. Directly
patching that generated Caddyfile would create competing owners and would not be
a stable integration contract. Keep its CA private key under its owner and
consume the resulting HTTPS endpoint, rather than copying key material.

RexEnv accepts configurable development TLDs. `.test` is in its reserved safe
set; `.tst` is syntactically accepted but in the warning tier. Its local resolver
maps the chosen namespace to loopback. An existing foreign route is refused by
ordinary setup; takeover is described as deliberate, backed up and reversible.
It is not a side effect to hide in site creation. See the
[TLD policy](https://github.com/rexenv/rexenv/blob/a4c039b01715e63caca3a3df3480509a52ad3604/src-tauri/src/core/tld.rs)
and [resolver ownership code](https://github.com/rexenv/rexenv/blob/a4c039b01715e63caca3a3df3480509a52ad3604/src-tauri/src/core/dns.rs).

Taking over `.tst` would affect every existing hostname using that suffix, not
only a newly selected PHP project. A mixed fleet therefore needs either separate
namespaces with qualified listener coexistence, or one explicit front door able
to route both native and Compose backends. A supported external-backend route
contract for RexEnv was not established here. Changing the suffix or reusing the
same Caddy executable alone does not solve that ownership problem.

RexEnv issues local leaves with its own CA and terminates them at Caddy. This can
replace duplicate local-development TLS provisioning for RexEnv-owned sites.
Server-side CA trust does not automatically trust it on a developer's separate
computer, and local CA certificates do not replace public/Cloudflare TLS. See
[certificate ownership](https://github.com/rexenv/rexenv/blob/a4c039b01715e63caca3a3df3480509a52ad3604/src-tauri/src/core/ssl.rs)
and [edge generation](https://github.com/rexenv/rexenv/blob/a4c039b01715e63caca3a3df3480509a52ad3604/src-tauri/src/core/proxy.rs).

## Port and migration constraints

Both defaults want HTTP/HTTPS endpoints. RexEnv supports alternate ports and a
bind address, but a port change is refused once any site exists. Its port
availability check considers listener owners across local addresses, so two
different loopback addresses are not yet a qualified coexistence solution.

More importantly, its off-443 guard refuses URL-rewriting flows, including
multisite conversion and domain changes; its policy also names imports/sync and
worktree-copy scenarios. Thus “put RexEnv on 8443” is not a full-feature parity
solution. Preserve canonical URLs and decide one edge ownership/migration model
before promoting it. See [edge-port policy](https://github.com/rexenv/rexenv/blob/a4c039b01715e63caca3a3df3480509a52ad3604/src-tauri/src/core/edge_ports.rs)
and [port ownership checks](https://github.com/rexenv/rexenv/blob/a4c039b01715e63caca3a3df3480509a52ad3604/src-tauri/src/core/ports.rs).

## Small qualification sequence

1. Register an observation-only integration with exact compatible CLI/app identity,
   fixed argv, bounded output and safe status/site projection.
2. On one approved disposable Linux VM, qualify headless startup, one fresh site,
   exact DNS/HTTPS route and two-site noninterference. Keep MCP disabled unless
   its broader authority is explicitly selected. Record the app/service ownership.
3. Add runtime, ingress and resolver adapters with explicit per-site ownership and
   capability refusals. Lost mutation replies remain unresolved until read-only
   reconciliation proves the result; killing `rex` is not app-side cancellation.
4. Qualify the real plugin workflow: link/install/activate, browser verification,
   requested WP execution and test suite, reset/snapshot restoration, and cleanup.
   Preserve the neighboring site and unrelated resolvers/listeners/CA trust.
5. Decide same-TLD migration or separate namespaces, prove route/trust/rollback
   parity, and only then propose a per-machine/per-project preference for eligible
   trusted WordPress/PHP work. A universal default replacement is a separate,
   larger decision requiring remaining generic/runtime/platform parity.

This sequence belongs to W21 discovery and any later material feature's Spec Kit
workflow. The current request authorizes the assessment, not installation,
provisioning, DNS/CA takeover, MCP enablement or a default-policy change.
