# Product Requirements Draft: Native Runtime Adoption

**Status**: Discovery

**Created**: 2026-08-01

**Last Refined**: 2026-08-01

**Input**: "Native runtime adoption: allow Sandbox projects to run through an already-installed native WordPress/PHP development runtime instead of Docker, beginning from the existing Herd driver and defining safe capability-based lifecycle, isolation, parity, fallback, and reversibility across supported host runtimes."

**Drafting Model**: `gpt-5.6-sol` High (fallback; active root model cannot be switched to preferred `gpt-5.6-terra` Medium)

**Final Validation**: PASS — gpt-5.6-sol High

**Validated On**: 2026-08-01

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox's canonical WordPress runtime is an isolated Compose stack. It is predictable
and portable, but it duplicates PHP, a database, and a web server on machines that already
have a trusted local development runtime. The repository has one native exception:
`server: "herd"` provisions WordPress in Sandbox's normal instance directory, links it
through Herd, pins PHP, uses a host MySQL service, and routes WP-CLI/tests through the host
(`docs/sandbox-config-reference.md` §Host driver).

That exception proves native execution is viable, but it is expressed as special cases
across provisioning, lifecycle, WP-CLI, tests, debugging, deletion, domains, and remote
deployment. Its limitations are real: no container stop/log lifecycle, no Mailpit,
snapshot/restore gap, host-managed Xdebug, no hot Docker↔Herd switch, and no subdomain
multisite. Adding another native product by duplicating those branches would make behavior
less predictable and violate the repository's explicit runtime-adapter boundaries.

Native environments also have a larger blast radius than containers. PHP and databases
are shared host services; global configuration changes can affect unrelated projects;
pre-existing sites, databases, certificates, and web-server entries must never be mistaken
for Sandbox-owned state. Several popular products expose very different control surfaces:
Herd and Valet provide documented CLIs, Local exposes an in-process add-on API, and
Windows-only stacks cannot be controlled by Sandbox's POSIX entry point.

The product need is a capability-based native runtime contract with two native modes:
conservative adoption of an already-installed runtime, and an optional Sandbox-managed
native stack whose configuration, processes, ports, logs, sockets, database directory,
and site data are isolated under Sandbox ownership. Both require explicit selection and a
safe core operation set; Docker remains the canonical fallback.

## Users and Desired Outcomes

- **Developer already using Herd or Valet**: creates a Sandbox-owned WordPress site using
  the runtime and PHP versions already installed on the host, with its shared-host,
  trusted-project isolation level reported explicitly.
- **Developer operating a supported POSIX PHP/database/web stack**: explicitly declares
  its connection/control profile and receives a Sandbox-owned site without global stack
  takeover.
- **Developer who wants no Docker and has no incumbent stack**: previews and confirms
  installation of the required native packages, then runs a Sandbox-managed nginx or
  Apache, PHP-FPM, and MySQL/MariaDB stack isolated from system service configuration.
- **Developer relying on Docker reproducibility**: sees no change; Compose remains the
  default and CI/remote path.
- **Developer using Local, XAMPP, Laragon, or WAMP**: sees accurate detection and support
  status rather than an unsafe attempt to edit private state or cross platform boundaries.
- **Host owner**: can remove a native Sandbox instance without deleting pre-existing sites,
  databases, services, or shared configuration.
- **Tooling caller**: uses the same high-level ensure/status/open/WP/test/destroy flow and
  can inspect which optional capabilities the selected runtime lacks.

## Goals

- Establish one explicit native-runtime capability and ownership model.
- Bring the existing Herd path under that model without removing proven behavior before
  parity is demonstrated.
- Support official Valet as a first-class native runtime on macOS.
- Provide a `managed-native` option that can install missing supported binaries with
  explicit consent and run Sandbox-owned nginx or Apache, PHP-FPM, and MySQL/MariaDB
  service instances beside unrelated system services.
- Make isolation the primary managed-native acceptance gate: plugin code, web PHP,
  WP-CLI, exec, and tests cannot read, write, enumerate, signal, or connect to resources
  outside the instance's declared boundary unless a specific capability is granted.
- Support explicitly declared POSIX native stacks only when they meet the core isolation,
  execution, database, routing, health, and cleanup contract.
- Keep PHP version, WordPress version, project source, CLI execution, web execution, and
  test execution consistent for each instance.
- Make optional capability gaps discoverable before provisioning.
- Ensure native runtime selection, provisioning, reconciliation, and cleanup are
  idempotent and never mutate unrelated host state.

## Non-Goals

- Replacing Docker as the default, CI, or remote runtime.
- Automatically switching an existing instance between Docker and native runtimes or
  migrating its data. Runtime changes require an explicit export/recreate/import workflow.
- Installing or repairing a third-party incumbent runtime or Local add-on. Package
  installation for the explicitly selected Sandbox-managed native stack is in scope;
  taking over or globally reconfiguring system service instances is not.
- Adopting an arbitrary pre-existing WordPress site as a Sandbox-owned instance.
- Native Windows support; Laragon/WAMP and Windows-side Herd/XAMPP remain outside the
  current entry point. WSL2 can use supported Linux-side runtimes only.
- Pretending container-only facilities exist natively. Missing mail capture, snapshots,
  log aggregation, per-instance Xdebug, or server switching must be reported as such.
- Advertising managed-native on a platform where Sandbox cannot enforce and prove a
  Docker-like untrusted-code boundary. Package/process separation without security
  isolation is insufficient.
- HTTP ingress and local DNS integration; specs A and B own those shared host concerns.

## Product Scenarios

### Scenario 1 — New Herd instance

- **Starting state**: Herd and a compatible host database are installed; the project
  explicitly selects Herd in machine-local configuration.
- **User action**: The developer ensures the project.
- **Expected outcome**: Sandbox creates only its owned WordPress directory and databases,
  prepares the Herd backend/runtime requirements, and hands hostname-route ownership to
  specs B/A. After A registers the route, Sandbox applies the requested PHP version,
  verifies web and CLI versions agree, and reports the shared-host isolation level and
  optional capability gaps before completion.

### Scenario 2 — New Valet instance

- **Starting state**: Official Valet is installed on macOS and running with a reachable user-supplied host
  database; the project explicitly selects Valet.
- **User action**: The developer ensures the project.
- **Expected outcome**: Sandbox provisions owned databases and WordPress state and prepares
  the Valet backend/runtime requirements. Spec B selects/resolves the hostname and spec A
  owns link/route registration. Sandbox then applies the supported per-site PHP version,
  and the same WP-CLI/test flows target that instance without Docker.

### Scenario 3 — Explicit POSIX native profile

- **Starting state**: A host has PHP, a database, and a web server, and the operator has
  declared the supported commands/connections and owned configuration scope.
- **User action**: The developer previews and confirms native provisioning.
- **Expected outcome**: Sandbox validates every core capability before mutation, creates
  isolated owned state, and verifies a live request. If any capability is missing, it
  refuses native provisioning before partial state is created and recommends Docker.

### Scenario 4 — Runtime merely detected

- **Starting state**: Local or XAMPP is running, but no supported external lifecycle
  contract is available.
- **User action**: The developer asks for runtime choices or selects that product.
- **Expected outcome**: Sandbox reports it as detect-only and explains why it cannot host
  a Sandbox-owned instance; it does not inspect or edit private product state.

### Scenario 4a — Install a Sandbox-managed native stack

- **Starting state**: Docker is unavailable or deliberately not selected, no adoptable
  incumbent exists, and the host has a supported package manager. Required binaries may
  be missing.
- **User action**: At an interactive terminal, the developer selects `managed-native`,
  reviews the exact packages, paths, ports, processes, and privilege changes, and confirms.
- **Expected outcome**: Sandbox installs only the confirmed missing packages, initializes
  Sandbox-owned service state, starts isolated native services, provisions the instance,
  and proves web/PHP/database/tool health. It does not enable, stop, or rewrite the host's
  default nginx, Apache, PHP-FPM, MySQL, or MariaDB service.

### Scenario 4b — Managed stack coexists with system services

- **Starting state**: System nginx and MySQL already serve unrelated applications on their
  default ports and data directories.
- **User action**: The developer provisions a Sandbox-managed native instance.
- **Expected outcome**: Sandbox uses different owned configuration roots, PID files,
  sockets, ports, logs, and database directory. The pre-existing applications remain
  healthy before, during, and after provisioning and teardown.

### Scenario 4c — Required native version is unavailable safely

- **Starting state**: The project pins a PHP/database/web-server version unavailable from
  the configured trusted package sources or installed binaries.
- **User action**: The developer previews managed-native installation.
- **Expected outcome**: Sandbox reports the unavailable version before mutation and
  recommends Docker or an incumbent that supplies it. It does not add an unapproved
  third-party repository, build arbitrary source, or silently substitute a version.

### Scenario 4d — Plugin or CLI attempts to escape its instance

- **Starting state**: A managed-native WordPress instance contains code that attempts to
  read the user's home directory or a sibling instance, write outside the WordPress data
  boundary, enumerate/signal host processes, access host devices or control sockets, or
  connect to host/sibling services.
- **User action**: The code runs through a web request, cron, WP-CLI, exec, or test.
- **Expected outcome**: Every attempt is denied by the operating-system sandbox. The same
  code can access only the WordPress root, explicitly declared project mounts with their
  declared read/write mode, instance temporary storage, and explicitly granted service or
  network capabilities.

### Scenario 4e — Isolation unavailable or weakened

- **Starting state**: The kernel disables unprivileged user namespaces, a required
  sandboxing feature is missing, or the effective policy exposes a host path/socket.
- **User action**: The developer previews, ensures, or re-starts managed-native.
- **Expected outcome**: Sandbox fails closed before executing WordPress/plugin code and
  recommends Docker. It never silently downgrades to ordinary host processes or treats a
  previous isolation check as permanently valid.

### Scenario 5 — Non-interactive caller without prior selection/consent

- **Starting state**: A native runtime is present but the project has not explicitly
  selected it or consented to shared-host changes.
- **User action**: MCP or CI ensures the project.
- **Expected outcome**: Sandbox never prompts and never auto-adopts the runtime. It uses
  the declared Docker default, or returns pending-consent if the project explicitly pinned
  native execution but machine consent is missing.

### Scenario 6 — PHP version mismatch

- **Starting state**: The requested PHP version is unavailable or the web and CLI tiers
  resolve to different versions.
- **User action**: Sandbox validates or ensures the runtime.
- **Expected outcome**: Provisioning fails before the instance is declared ready and
  reports the exact mismatch. It does not silently fall back to a global PHP version.

### Scenario 7 — Foreign site/database collision

- **Starting state**: The candidate native runtime identity, WordPress directory, or database
  already exists and is not proven Sandbox-owned.
- **User action**: The developer ensures a same-named instance.
- **Expected outcome**: Sandbox refuses the collision and leaves every foreign object
  unchanged; it does not infer ownership from naming alone.

### Scenario 8 — Re-ensure and source changes

- **Starting state**: A healthy native instance exists and project plugin/theme mappings
  or WordPress configuration have changed.
- **User action**: The developer applies or re-ensures.
- **Expected outcome**: Sandbox reconciles only its owned files, mappings, database state,
  and runtime registration; repeated application converges to the same state.

### Scenario 9 — Optional feature requested

- **Starting state**: The selected runtime lacks a capability such as Mailpit capture,
  per-instance Xdebug toggling, or snapshots.
- **User action**: The developer requests that operation.
- **Expected outcome**: Sandbox returns a structured unsupported-capability result with a
  safe runtime-specific alternative where one exists; no shared global setting is changed.

### Scenario 10 — Destroy owned native instance

- **Starting state**: Sandbox owns the site registration, databases, WordPress directory,
  test database, and runtime metadata.
- **User action**: The developer confirms destroy.
- **Expected outcome**: Only unchanged owned objects are removed. Foreign or drifted state
  is left in place with a residual/recovery report, and repeated destroy is safe.

### Scenario 11 — Native runtime disappears

- **Starting state**: A previously healthy native runtime is stopped, uninstalled, or its
  database credentials change.
- **User action**: Status, ensure, or a tool call runs.
- **Expected outcome**: Sandbox reports the failed capability and does not claim the clean
  URL or tooling path healthy. It preserves owned data and gives an explicit recovery or
  Docker re-provision path.

### Scenario 12 — Runtime switch requested

- **Starting state**: A Docker or native instance already contains data.
- **User action**: The developer changes the runtime selection and re-runs ensure.
- **Expected outcome**: Sandbox refuses an implicit switch, identifies the existing data,
  and requires the separate export/recreate/import flow. No data or registration is
  deleted by ordinary ensure.

## Proposed Product Behavior

- **Explicit opt-in only.** Runtime presence is discoverable, but native execution is
  selected in machine-local project configuration. Detection never changes the runtime.
- **Docker remains canonical.** Unpinned projects, CI, and remote deployments use Compose.
  A native selection that cannot pass preflight does not silently create a different
  runtime under the same instance identity.
- **Three explicit modes.** Local WordPress runs through Compose, an adopted incumbent, or
  `managed-native`. A project never slides between modes because a binary happens to be
  present or missing.
- **Core contract before mutation.** A native runtime must prove isolated instance files,
  owned database creation/removal, web registration, PHP version agreement, WP-CLI/exec,
  test execution, health/status, and conservative destroy before it is adoptable.
- **Capabilities, not product branches.** Each runtime declares supported operations and
  limitations. Callers check capability before side effects and receive the same operation
  result shape regardless of runtime.
- **Owned state only.** Sandbox owns a dedicated WordPress directory, uniquely attributable
  production and test databases, its site registration, and its runtime metadata. Names
  alone never establish ownership; observed state must match the last applied record.
- **Shared services stay shared.** Sandbox does not stop or globally reconfigure PHP,
  databases, web servers, or runtime applications. Instance `down` means unregistering or
  disabling only when the runtime exposes a per-site operation; otherwise it reports that
  the shared service continues to serve.
- **Managed services stay isolated.** Managed-native may install binaries, but runs them
  with Sandbox-owned configuration roots, loopback ports or sockets, PID files, logs, and
  database data. Package-manager system services and default configuration remain outside
  Sandbox ownership.
- **Untrusted execution stays inside.** Web PHP, cron, WP-CLI, arbitrary exec, Composer
  scripts, and tests share the same instance sandbox policy. The trusted Sandbox control
  plane may manage host-side lifecycle, but it never loads plugin or project PHP outside
  that boundary.
- **Deny by default.** The managed-native boundary exposes only runtime libraries, the
  instance WordPress root, read-only declared source mounts by default, instance-scoped
  writable data/temp paths, and instance service sockets. Host home/state, sibling
  instances, process/IPC namespaces, devices, privilege escalation, host control sockets,
  and network access are denied unless a narrow capability is explicitly declared.
- **No silent downgrade.** Isolation is checked before every start and untrusted command.
  Missing kernel features, policy application errors, or unexpected visible resources
  make the operation fail closed and recommend Docker.
- **Per-instance containment.** Managed-native may share immutable installed binaries, but
  each instance has separate process, filesystem, IPC, network, resource, secret, and
  database boundaries. One instance never receives a sibling's database socket or
  credentials.
- **Complete resource boundary.** Each instance has explicit ceilings for CPU, memory,
  process count, execution time, disk bytes, inode count, file descriptors, sockets, and
  I/O. The sandbox closes or rejects inherited descriptors before untrusted execution so a
  host file or control socket cannot enter through an already-open handle.
- **Explicit egress capabilities.** External HTTP or other network access needed by a
  project is opt-in, scoped, observable, and revocable. Enabling egress does not grant
  access to host loopback, private control sockets, or sibling instance networks.
- **Preview before privilege.** Installation reports the package manager, packages,
  requested versions, transaction actions, declared Sandbox path roots, privilege changes,
  and known maintainer-script/system-service effects. It runs only after current
  interactive confirmation; MCP and CI never initiate a package installation or password
  prompt.
- **Trusted acquisition only.** Managed-native uses already configured operating-system
  package sources or an explicitly approved official binary distribution with integrity
  verification. It does not add repositories, execute remote install scripts, or compile
  arbitrary source implicitly.
- **Version integrity.** The requested PHP version must be used by web, WP-CLI, exec, and
  tests. Unsupported versions are a preflight failure, not a silent fallback. WordPress
  test versions remain pinned consistently with the project.
- **Optional capability honesty.** Snapshots, mail capture, Xdebug, aggregated logs,
  multisite modes, and hot server switching are individually declared. Product output and
  MCP results never imply parity that the runtime cannot deliver.
- **No implicit migration.** Changing an existing instance's runtime is blocked until the
  user performs an explicit data-preserving export/recreate/import workflow.
- **Drift-aware cleanup.** Destroy removes only unchanged owned state. Missing runtime
  control, changed registrations, or changed databases leave a visible residual record and
  recovery guidance.
- **Specs A/B compose the URL.** C owns the backend runtime endpoint, files, PHP, database,
  and execution capabilities. B owns hostname/TLD selection and resolution. A exclusively
  owns hostname-route registration/removal—including Herd/Valet link/proxy/secure actions—
  rather than hiding those mutations inside C.

## Constraints and Dependencies

- Herd's documented CLI supports link/unlink, per-site PHP isolation, secure/unsecure,
  proxy lifecycle, and service inspection; database services may depend on Herd Pro
  ([official Herd CLI](https://herd.laravel.com/docs/macos/advanced-usage/herd-cli)).
- Valet documents link/unlink, wildcard `.test` sites, secure/unsecure, per-site PHP
  isolation, and runtime-specific CLI execution on macOS; it does not bundle a required
  database and points users to a separate provider
  ([official Valet documentation](https://laravel.com/docs/valet)).
- Local exposes an add-on API inside its Electron process and site metadata/actions, but
  this feature does not install or depend on an add-on
  ([official Local add-on documentation](https://localwp.com/help-docs/building-your-add-on/)).
- Laragon exposes Windows command-line reload behavior, but Sandbox has no native Windows
  entry point ([official Laragon CLI](https://laragon.org/docs/cli)).
- Existing Herd v1 behavior is the compatibility baseline described in
  `docs/sandbox-config-reference.md`; constitution VI forbids removing its old path before
  adapter parity is live-proven.
- Per-machine runtime selection and credentials remain in gitignored local configuration;
  shared project configuration must not leak database secrets or machine-specific paths.
- Native host commands are bounded, argv-based, non-interactive, and redact secrets.
- nginx supports an alternate configuration and runtime prefix; Apache supports an
  alternate server root/configuration and scoped lifecycle; PHP-FPM supports independent
  configuration and pools; MariaDB documents independent option files and data directories
  ([nginx command-line reference](https://nginx.org/en/docs/switches.html),
  [Apache httpd reference](https://httpd.apache.org/docs/2.4/en/programs/httpd.html),
  [PHP-FPM configuration](https://www.php.net/manual/en/install.fpm.configuration.php),
  [MariaDB initialization](https://mariadb.com/docs/server/clients-and-utilities/deployment-tools/mariadb-install-db)).
- Package installation can create or start system service artifacts on some platforms.
  Managed-native preflight must either prove those side effects are suppressed/preserved
  or decline installation; it may not stop an unrelated service after the fact to recover
  a port.
- Linux managed-native isolation requires an enforced policy built from user, mount, PID,
  IPC, UTS, and network namespaces, no-new-privileges/capability removal, syscall filtering,
  resource controls, and a minimal mount view. Bubblewrap can construct these namespaces
  but explicitly leaves the security policy to its caller; it is not sufficient merely to
  invoke the binary ([bubblewrap security documentation](https://github.com/containers/bubblewrap/blob/main/README.md)).
- Landlock may add unprivileged filesystem restrictions as defense in depth, but its
  documented limitations mean it cannot replace the namespace/mount boundary
  ([Linux Landlock documentation](https://www.kernel.org/doc/html/latest/userspace-api/landlock.html)).
- Managed-native is Linux-first. A macOS or other POSIX implementation remains unsupported
  until it has an equally enforceable, primary-source-supported boundary and passes the
  same adversarial live proof; ordinary Herd/Valet adoption remains explicitly shared-host
  and lower isolation.
- Incumbent Herd, Valet, and explicit POSIX profiles execute trusted project/plugin code
  with the developer account's shared-host reach unless their own verified controls say
  otherwise. They are lower-isolation modes and must never be presented as containment for
  hostile plugins or CLI input.
- C supplies backend requirements/endpoints only. B owns local hostname selection and
  resolution; A exclusively performs and records all hostname-route lifecycle mutations,
  including Herd/Valet site link, proxy, and TLS actions.
- Runtime adapters register through explicit manifests/contracts; no new consumer may
  branch through central compatibility facades or raw registry JSON.
- Constitution IV requires live WordPress, WP-CLI, database, and test evidence for each
  runtime advertised as adoptable.
- Specs A and B must be available for any runtime that delegates URL ingress/resolution.

## Runtime Coverage Policy

| Runtime environment | Initial tier | Boundary |
|---------------------|--------------|----------|
| Laravel Herd on macOS | Adoptable; existing lower-isolation baseline | Trusted-project shared-host execution; move behind the capability contract with parity evidence before retiring special cases. |
| Official Laravel Valet on macOS | Adoptable, lower isolation | Trusted-project shared-host execution; requires documented CLI, a user-supplied supported database connection, and full core-contract proof. |
| Explicit POSIX PHP + DB + web profile | Conditionally adoptable, lower isolation | Trusted-project execution only; no auto-discovery mutation, and the operator declares paths/connections/owned config scope. |
| Sandbox-managed native stack | Adoptable initially only on proven Linux/package-manager/kernel combinations | Optional package installation plus per-instance sandboxed nginx or Apache, PHP-FPM, and MySQL/MariaDB; no system-service takeover and no isolation downgrade. |
| Local | Detect-only | No external integration without an explicitly installed Local add-on, which is out of scope. |
| XAMPP on supported POSIX hosts | Detect-only initially | May advance only after a documented isolated control profile and live proof; no private state edits. |
| Herd/Laragon/WAMP/XAMPP on Windows | Outside native platform | Sandbox does not run natively on Windows or mutate Windows-side services from WSL2. |
| DDEV | Existing Compose/generic-runtime concern, not native | Do not relabel a container orchestrator as host-native adoption. |
| Arbitrary pre-existing WordPress site | Not adoptable | Import/adoption of foreign site data is a separate explicit workflow. |

### Initial Managed-Native Matrix

The first advertised and live-proven combination is Ubuntu 24.04 LTS using its configured
APT sources, PHP 8.3 FPM/CLI, bubblewrap 0.9, MariaDB 10.11, and either nginx 1.24 or
Apache HTTP Server 2.4. Other distributions, PHP/database version families, MySQL, and
macOS remain unadvertised until they receive an explicit matrix entry and the full package,
coexistence, isolation, hostile-code, lifecycle, and WordPress proof.

## Required and Optional Capability Policy

**Required for an adoptable native runtime**: preflight, ensure, status/health, open,
WordPress CLI, bounded host exec, filesystem/log access, production and test database
isolation, test execution, apply/reconcile, and conservative destroy.

**Additionally required for managed-native**: dry-run installation plan, explicit current
consent, trusted package provenance, isolated configuration/data/socket/PID/log paths,
bounded user-level lifecycle, coexistence proof against occupied default service ports,
removal of Sandbox-owned service state without uninstalling shared packages by default,
and adversarial proof that web/CLI/exec/test code cannot escape its instance boundary.

**Optional and truthfully declared**: per-instance start/stop, aggregated service logs,
snapshot/restore, mail capture, per-instance Xdebug control, subdomain multisite, alternate
web-server switching, and remote deployment.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Default runtime | Docker Compose | Reproducible, isolated, remote/CI-compatible, and current canonical behavior | Existing product policy |
| Native selection | Explicit machine-local opt-in | Presence alone does not authorize shared-host mutation | Best practice selected by user delegation |
| First-class products | Herd and Valet, plus an explicit POSIX profile when it proves the core contract | These expose documented automation or an operator-declared stable surface | Primary-source research |
| Sandbox-managed native stack | In scope beside Docker, with nginx or Apache + PHP-FPM + MySQL or MariaDB | Gives a first-party no-Docker option without trusting private third-party runtime state | User clarification; primary-source isolation support |
| Package acquisition | Installed binaries first; confirmed trusted system packages or verified official distributions for missing components; no implicit repository addition | Reduces supply-chain and host-configuration risk | Best practice selected by user delegation |
| Managed service ownership | Own configs/processes/ports/sockets/logs/data; never own default system service instances merely because their packages were installed | Binary installation and service ownership are different boundaries | Best practice |
| Package removal | Do not automatically uninstall shared packages on instance destroy; remove only Sandbox-owned runtime state, with a separate reviewed unused-package cleanup | Other applications may begin using the same binaries | Conservative cleanup policy |
| Managed-native isolation | Docker-like, fail-closed boundary for every untrusted PHP/CLI/exec/test path; no lower-isolation fallback | Plugin code is untrusted and must not inherit the developer account's host reach | User clarification; security best practice |
| Initial managed-native platform | Linux only where required namespace, privilege, syscall, mount, network, and resource controls pass preflight | macOS lacks an established equivalent contract in current grounding; claiming parity would be unsafe | Evidence-based scope |
| Network policy | Deny by default; explicit scoped egress grants never include host/sibling control surfaces | Prevents filesystem isolation from being bypassed through host services | Least privilege |
| Source mounts | Read-only by default; writable subpaths require explicit declaration | Runtime code normally needs to execute source, not rewrite the developer's checkout | Least privilege |
| Resource isolation | Limit CPU, memory, PIDs, time, disk bytes/inodes, file descriptors, sockets, and I/O; sanitize inherited descriptors | Host safety requires more than namespace visibility controls | User isolation priority; defense in depth |
| Incumbent isolation label | Herd, Valet, and explicit shared-host profiles are trusted-project/lower-isolation modes | They do not confine hostile PHP with Docker-like boundaries | Evidence-based capability honesty |
| A/B/C ownership | C owns backend runtime; B owns hostname/resolution; A owns every hostname route, including Herd/Valet lifecycle | Prevents duplicated mutations and circular cleanup state | Cross-feature review |
| Initial managed-native matrix | Ubuntu 24.04 APT, PHP 8.3, bubblewrap 0.9, MariaDB 10.11, nginx 1.24 or Apache 2.4 | Matches the first host/package combination available for full live proof | Repository host evidence; bounded initial support |
| Detect-only products | Local and initially XAMPP | Private/in-process or insufficiently isolated external control surfaces are not safe automation contracts | Primary-source research |
| Native Windows | Out of scope | Current POSIX entry point and host assumptions do not run natively on Windows | Repository platform policy |
| Required parity | Core operational parity; optional features are capability-reported rather than faked | Native runtimes inherently differ, but basic project/tool lifecycle must remain predictable | Best practice |
| PHP mismatch | Fail preflight; no global fallback | Silent version drift invalidates web/CLI/test results | Existing Herd finding corrected into policy |
| Database source | Incumbent modes use a user-supplied service; managed-native may install supported MySQL/MariaDB binaries and owns a separate server data directory | Keeps adoption conservative while making the first-party native mode complete | User clarification; ownership policy |
| Runtime switching | No implicit switch or migration | Avoids destructive data loss and ambiguous ownership | Best practice |
| Collision/drift | Refuse foreign state; preserve externally changed owned state for reconciliation | Names and markers alone are insufficient proof of ownership | Existing safety policy |
| URL concerns | Compose through specs A/B | Avoids hidden Herd/Valet-only branches and keeps host networking coherent | Three-feature architecture |

## Open Questions

- None.

## Acceptance Outcomes

- A new Herd and a new Valet instance each pass live web, PHP-version, WP-CLI, database,
  plugin mapping, and test-suite checks without starting a Sandbox WordPress container.
- On every advertised managed-native platform combination, a fresh host can preview and
  confirm missing-package installation, then serve WordPress through the selected
  nginx/Apache, PHP-FPM, and MySQL/MariaDB combination without Docker.
- With unrelated system web/database services already occupying default ports, managed-
  native provision, re-ensure, stop/start, and destroy leave their PIDs, configuration,
  data, ports, and health unchanged.
- No managed-native package installation starts from a non-interactive caller, adds an
  unapproved repository, executes an unverified remote installer, or silently changes a
  pinned version.
- Identical adversarial probes executed through web PHP, cron, WP-CLI, arbitrary exec, and
  tests cannot read a host-only sentinel, a sibling-instance sentinel or secret, list or
  signal host processes, access host devices/control sockets, write outside declared
  writable paths, or connect to host/sibling services.
- A managed-native instance with no egress grant cannot reach external, host-loopback,
  private, or sibling networks. A scoped egress grant enables only its declared target and
  remains visible in status.
- Disabling any required namespace/sandbox prerequisite or introducing an unexpected
  visible host mount makes preflight/start/exec fail closed; no WordPress/plugin code runs
  unsandboxed.
- Declared project source appears read-only inside the instance by default; an explicitly
  writable subpath is writable without exposing its parent or other checkout paths.
- CPU, memory, process-count, and execution-time limits applied to one instance prevent an
  intentional exhaustion probe from taking down or inspecting a sibling instance.
- Disk-byte, inode, file-descriptor, socket-count, and I/O exhaustion probes are contained
  within the declared instance limits, and an inherited-descriptor probe cannot read or
  control any host resource.
- Destroying the last managed-native instance removes Sandbox-owned configs, processes,
  sockets, logs eligible for cleanup, and data only after confirmation; it does not
  uninstall packages that may be shared with other applications.
- Re-ensuring either runtime produces the same C-owned state and does not duplicate
  databases, runtime records, files, or mappings; A separately proves route/link/TLS
  idempotency.
- The PHP major/minor observed by the web request, WP-CLI, bounded exec, and tests is the
  requested version; an unavailable version fails before ready state.
- A project with no native opt-in continues to use Docker with unchanged observable
  lifecycle and URL behavior.
- A non-interactive call cannot auto-adopt a detected runtime or wait for input.
- Foreign native runtime identity, directory, or database collisions remain byte-for-byte
  unchanged and are reported before partial provisioning; hostname/link collisions are
  verified under A.
- Every required capability is discoverable before ensure; requesting an optional missing
  capability returns a structured unsupported result and performs no global mutation.
- Destroy removes only unchanged C-owned backend registration/state, production/test
  databases, WordPress directory, and runtime metadata; A separately removes hostname
  routes, and a second destroy is safe.
- Runtime disappearance or credential drift changes status to unhealthy without deleting
  owned data or claiming the URL/tool path works.
- Changing the selected runtime for an existing populated instance is refused without
  deleting or overwriting it and directs the user to an explicit migration workflow.
- Docker regression checks, existing Herd behavior, remote-deploy refusal for native
  selections, and per-project registry ownership continue to pass.
- Every runtime advertised as adoptable has captured live-stack evidence for the complete
  required capability set.
- Herd, Valet, and explicit POSIX profiles are visibly labeled trusted-project/lower-
  isolation before ensure and in status; managed-native is never downgraded to one of them.
- For Herd and Valet, C produces the backend/runtime state without registering a hostname;
  B chooses/resolves it and A alone creates/removes the link/route/TLS state.

## Risks and Assumptions

- **Risk**: A native adapter can damage shared PHP/database/web-server state. Explicit
  selection, owned scopes, preflight, and conservative cleanup are the primary controls.
- **Risk**: Herd/Valet CLI output and features vary by version and paid tier.
- **Risk**: Host PHP extensions or ini settings can differ between web and CLI even when
  version numbers match; readiness must test effective behavior, not only binary names.
- **Risk**: Database credentials authorize more than one Sandbox database; secret handling
  and name/ownership checks are critical.
- **Risk**: Capability-based migration of the current Herd path touches many commands and
  can regress behavior that works today; parity must precede removal.
- **Risk**: Live verification requires real host installations and cannot be proven solely
  inside the canonical Docker stack.
- **Risk**: OS package transactions may enable or start default services as maintainer
  scripts, briefly creating conflicts or expanding system state. Each supported platform
  must prove a non-takeover installation path before it is advertised.
- **Risk**: Running a database under the developer account changes the security and
  availability model compared with a system daemon; loopback/socket scoping and secret
  permissions are mandatory.
- **Risk**: A single installed PHP binary may not satisfy the repository's version matrix;
  refusing unavailable versions will make managed-native narrower than Docker by design.
- **Risk**: Bubblewrap and namespaces are mechanisms, not a complete policy. A mistaken
  bind mount, inherited file descriptor, D-Bus/control socket, syscall allowance, or
  network bridge can defeat the intended boundary; adversarial tests are release gates.
- **Risk**: Unprivileged user namespaces are disabled on some hardened distributions, and
  seccomp/Landlock/cgroup capabilities vary by kernel. Those hosts must fall back to Docker
  rather than receive weaker isolation.
- **Risk**: Deny-by-default network access changes ordinary WordPress behaviors such as
  updates, license activation, and remote APIs until the project declares egress.
- **Assumption**: Users selecting native execution accept shared-service availability and
  resource characteristics while expecting Sandbox to isolate its own site/data.
- **Assumption**: A supported runtime can execute bounded non-interactive commands and
  expose enough observed state to prove ownership and health.
- **Assumption**: Docker remains available as the recommended fallback for unsupported
  native products and automation environments.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed or explicitly accepted as grounded product policy.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: READY FOR SPECKIT

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
