# Generic project instances

## Compose adapter

Generic PHP, JavaScript/Node, Docker-native, Laravel/Sail, Astro, and similar
projects share one explicit `kind=compose` runtime. The project declares the
Compose file, public service, internal port, and health path. Detection and the
Astro preset are read-only with respect to project execution: they do not run
package scripts or infer a command to execute beyond the declared preset
install/dev contract.

Sandbox owns only the loopback port overlay and runtime registry/artifact state.
Generic destroy runs Compose down without `-v`, so project-owned named volumes
remain intact. WordPress-only capabilities must be rejected before WP-CLI, REST,
database, Mailpit, or WordPress filesystem side effects.

## Remote SSH performance

Remote operations use OpenSSH `ControlMaster=auto` with an endpoint-hashed
owner-only control socket and a bounded 600-second `ControlPersist` lifetime.
Runtime uploads and dirty-file deployment should use one streamed archive/session
where possible; one SSH/SCP process per file is materially slower on higher-RTT
VPS links. HTTPS/Tailscale is the preferred transport for long-lived remote MCP,
while SSH multiplexing remains the command/control path.
