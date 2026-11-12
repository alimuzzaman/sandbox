# Remote transport status

The control-plane migration is staged. Normal host diagnostics, resource probes,
resource cleanup/revalidation, and dashboard inventory use the authenticated remote
service. They fail closed when the service is unavailable; they do not fall back to
SSH.

Control HTTP requests go directly to the selected endpoint. Ambient HTTP proxies
and redirects are disabled, including for the primary request and legacy bearer
HTTPS requests. Redirect responses remain HTTP errors; neither a credential nor
a mutation body follows them. Explicitly proxy-routed requests refuse before a
connection. Joined/Tailscale registrations never retry a recorded public origin
after DNS failure. Legacy owner HTTPS registrations retain the existing recorded
origin fallback with TLS hostname verification and no proxy or redirect.

| Surface | Current transport | Boundary |
| --- | --- | --- |
| `remote service diagnostics`, process/container view | authenticated service | migrated |
| `remote resources` observe/reclaim/lease/revalidate/remove | authenticated service | migrated |
| `./sb web` remote inventory and bounded deep refresh | local loopback BFF → authenticated service | migrated |
| `remote ssh` | explicit operator CLI command | escape hatch only; never internal/MCP |
| `remote join`, `remote service refresh` | authenticated service identity discovery | local registration only; no service changes |
| provision, service install/migrate/stop/status, recovery | SSH lifecycle | retained recovery/bootstrap exception |
| source upload, plugin mirror, bulk staging | tar/rsync/SSH | retained staging exception |
| jobs, workspace control, ensure/status/logs | SSH controller today | next migration slice |
| domains, Docker-pool control, deploy/hosting/preview | mixed SSH and local control | next migration slice |
| Hermes and recovery inventory | SSH-backed adapters | next migration slice; recovery remains an exception |

“Migrated” means the normal code path is typed, authenticated, bounded, and service
backed. It does not mean that SSH has been removed from lifecycle or source-transfer
recovery paths. The operator command is deliberately explicit:

Joining records the admitted service's ownership marker, runtime revision and
canonical machine identity from the existing schema-1 host-memory status contract.
Ordinary access automatically repairs missing metadata on an existing joined
controller through the authenticated service, within the caller's budget. No
per-controller permission or credential approval is required. An optional explicit
metadata refresh remains available:

```sh
./sb remote service refresh NAME --json
```

This writes only the local registration. It uses tailnet admission without an
owner bearer token or SSH, validates the complete response, preserves existing
pins and unrelated registration fields, and refuses identity/revision drift or a
concurrent registration change. It does not upgrade the remote, repair SSH, change
stack reservations, or bypass runtime compatibility admission. If the service
revision changed, use the separately reviewed lifecycle path; refresh cannot
replace a pin. A successful join does not imply every SSH operation is available.
After an authorized service update, a controller containing the compatible-cache
adoption option can reconnect the existing registration with:

```sh
./sb remote service refresh NAME --adopt-compatible-revision --json
```

This explicit version transition validates the full authenticated response, the
same saved machine/ownership marker and the actual selected unit's compatible
protocol/revision verdict before CAS-changing only the observed revision cache.
It never changes PinStore bindings, strict-mode pins, old operation adapters,
plans, stack reservations or delivery evidence. Strict or unproven compatibility
refuses. Older controllers must receive the reviewed client update to gain this
option; removing/rejoining is unnecessary. Normal refresh and operation-bound
revision checks remain strict, so old plans are not revived by this command.

Installer rollback keeps its private source/unit/environment recovery material
when restoration is incomplete. Source exchange reports its mode before the
post-exchange durability check so a failed fsync can still restore the exact
prior source. Missing activation outcome or failed restoration returns
`remote_service_rollback_indeterminate`, stops the selected Sandbox service and
withholds restart. Inspect that original attempt before another migration.
Credential preservation requires a current unit revision and a private,
nonsymlink credential file. Unsafe credential metadata or symlinked source paths
refuse before exchange, without requesting a local token or input.
Ordinary host-memory status and authenticated target-identity lookup also enforce
the saved machine pin; a changed machine or malformed pin refuses without refresh.
Legacy owner registrations with no machine pin keep their existing behavior.

An existing owner's registration that still selects public SSH can explicitly
move the same named record to keyless Tailnet transport:

```sh
./sb remote service refresh NAME --tailnet-only --adopt-compatible-revision --json
```

This requires the saved service marker/revision and a saved machine identity,
either the control pin or a prior confirmed host-identity pin. It first verifies
tokenless HTTP control, keyless SSH to the saved numeric Tailnet address, matching
machine identities, the observed existing front door and the selected compatible
owner unit. Only then does one local registration CAS change the connection.
It preserves the Linux account, unrelated metadata, credentials and operation
pins, and never changes the server, SSH policy, ingress or another controller.
The transient proof record contains no bearer. Joined Tailnet HTTP explicitly
uses no Authorization header even if a stored legacy bearer remains, and its
SSH unit probe skips credential-file authentication. Admission is proved by the
separate tokenless control request. Legacy nonjoined bearer paths are unchanged.
This transition supports the existing nginx profile or an unassigned profile
whose front door can be observed; explicit Caddy pins remain unsupported here.
Malformed/conflicting pins, strict revision drift or unproven observations refuse.
The option does not apply to `remote add` or other remote actions.

After adoption, doctor uses the same tokenless header selection. The shared
HTTP opener requires the saved numeric Tailnet address and refuses bearer
headers, proxies, redirects and public endpoints. Joined SSH refuses a changed
transport rather than returning to ordinary SSH. `remote up` and
`remote service migrate` preserve the remote owner credential file even when a
legacy bearer remains stored locally. The shared installer enforces that rule
for low-level callers too. `remote provision --force` cannot replace a joined
Tailnet registration; use the supported lifecycle commands instead.

Control protocol 25 adds optional `tailscale_host` to receiver-owned server-CLI
routing metadata and its retained provenance. It is emitted only for a unique
Tailnet IPv4 address observed on the local `tailscale0` interface. Public
`origin_ipv4` and `origin_ipv6` still require unique global interface addresses;
they are never inferred from the Tailnet address. Missing or ambiguous Tailnet
evidence omits that field. Historical retained metadata without it stays valid.
Deploy matching source before relying on the new routing field. Older clients
with strict routing-field validation may refuse responses containing the new
field and must update before using server-CLI submission on this runtime.

A connection change does not rewrite historical delivery bindings. If older
target operations require a rebind, first inspect the supported
`remote rebind-host NAME --json` plan; that command must prove the existing target
records and current service identify the same machine before confirmation.
The confirmed write also compares the registration observed before the machine
probe, so concurrent registration changes refuse. Rebinding changes only the
connection acceptance metadata, preserving the original registered digest and
historical delivery receipts.
Do not remove/rejoin the record or edit its state to bypass a refusal.
Pinned history and apply first validate the live machine with the existing status
contract, using the same total operation budget. A foreign apply-plan target
refuses before any request. Exhausted or unavailable identity evidence never
dispatches the requested history or mutation. Refresh also refuses a malformed
existing pin instead of silently normalizing it.

Service status reports live HTTP admission as `control_admission` and uses that
evidence for `authenticated`: true when admitted, false for an observed HTTP
401/403, and null when unavailable. `ssh_access` separately reports the selected
Linux account's SSH path. Missing unit metadata can leave ownership, bind, port,
and service name unknown; it does not establish denied Tailnet access.
Runtime compatibility is independently derived from the
actual selected SSH user's service-unit revision and protocol declaration.
Compatible protocol evidence can admit a differing controller hash; missing,
malformed or incompatible unit evidence cannot. A separate Linux user's empty
home is still a different SSH execution context. This does not repair that home
or establish that every Linux user sees the owner's state.

Joined registrations historically omitted the host front-door profile and therefore
defaulted to Caddy. Ordinary access now adopts a missing profile only after
read-only SSH proves the same machine and uniquely observed nginx or Sandbox
Caddy listener ownership. Unavailable evidence leaves the profile unavailable;
routing refuses instead of assuming Caddy. Explicit nginx adoption is also
available with `remote join NAME USER@HOST --front-door nginx` or
`remote service refresh NAME --front-door nginx --json` for an existing join.
This optional profile step uses bounded read-only SSH probes, checks the same
canonical machine identity as the authenticated control service, and requires
active valid nginx with its conf.d include and no active Caddy. It changes no
host configuration and cannot replace an explicit different profile. Plain
refresh continues to use HTTP only. Existing failed/reserved stack manifests and
delivery requests are not rewritten or retried by adoption. Do not use `remote add`
to repair a join: it changes provisioning metadata.

For joined Tailnet registrations, SSH, SCP and Git use the recorded private Tailnet
address and selected Linux account (for xSpeed, `alim`). They exclude local SSH
configuration, keys, agent, proxies and multiplexed sessions, so switching Tailnet
identity cannot reuse an earlier login or fall back to public/key-based SSH.
HTTP control admission and Tailscale SSH policy remain separate prerequisites.
An actual SSH policy denial is reported separately; timeout or missing profile
facts are unavailable evidence. Owner registrations with Tailnet HTTP control
keep their separate SSH transport; HTTP configuration alone does not enable
Tailscale SSH on another server. Their multiplexing is disabled before launching a command when the
UNIX socket path, including OpenSSH's temporary suffix, cannot fit; no command is
retried and no socket is moved to a shared directory.

Hermes repository authentication and interactive chat use the same joined SSH
policy, including TTY handling and credential input through stdin. Runtime source
upload, provisioning, service start/migration and broker cleanup retain the full
registration through the shared transport. Pro-plugin rsync derives its destination
operand and SSH command from the same resolved endpoint. These changes preserve
existing nonjoined owner SSH behavior; they do not remove the separate lifecycle
credential or normal host/CI secret-preflight prerequisites.

Every direct `remote ssh` command requires `--confirm`, including read-only commands,
and a short `--reason`.

```sh
./sb remote ssh <remote> --confirm --reason "diagnose service" --command 'systemctl --user status sandbox-remote-mcp'
```

Do not use that command as an automatic retry or to bypass a service revision mismatch.
