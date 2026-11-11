# Fixed server CLI jobs

This source contract is not installed acceptance. A server lacking the assembled
`job-capabilities` / `job-start --server-cli` contract is unavailable for this
adapter. It must not be described as denying an admitted owner. The managed
installation feature remains separate and paused.

`sandbox.application.server_cli.dispatch_server_cli(remote_name, argv, *,
project_dir, request_id, timeout_seconds=900)` submits before controller secret
preflight. `argv` starts with the CLI command, without an executable. The adapter
validates authenticated machine and service pins, reads actual installed CLI
capabilities, stages exact source through the existing transfer, and uses the
existing durable job repository, scheduler, supervisor and retained output. It
returns the original server acceptance receipt. Unknown acceptance is not retried.
The fixed installed executable is selected on the server, with no project PATH,
project script, self remote alias, instance ensure or SSH loop.

The server binds the original request, source identity, machine, ownership marker,
CLI revision and deadline in the retained job. `current_server_cli_context()`
returns a `ServerCLIContext` only for the actual retained running child: PID,
parent PID, boot/start identities, fixed argv and target provenance must agree.
An environment job ID is a lookup key, never authority. The bounded wait covers
the supervisor committing its child identity. Cancellation, expiry and replacement
refuse the context.

`server_cli_context_if_selected()` returns None for ordinary execution only. A
selected invalid, missing or revoked server-CLI context raises; consumers cannot
catch it as permission to fall back to another host. `context.origin_remote_name`
retains the originating alias. A host adapter selects this local target only for
that exact alias; aliases are routing context, not authorization. The immutable
`context.target_metadata` carries only validated origin IPv4/IPv6, front door and
control port. Bearer, SSH and credential configuration are excluded.

`context.owner` is a frozen `ServerCliOwner` containing the actual Linux UID,
account home, Sandbox home, fixed executable, machine identity, marker and runtime
revision. These derive from the current account and selected nonsecret service
unit; the separate EnvironmentFile is never opened. This establishes current
owner-layout execution, not immutable root-controlled release authority.

`read_current_server_owner()` exposes only that installed receiver metadata for a
fixed read-only target inspector. It does not authorize local commands: the
execution ports still require the actual retained current job. The unit is the
same `REMOTE_MCP_SERVICE` selected by lifecycle installation, not a parallel unit
name. Joined lifecycle staging observes the real unit, machine, endpoint and
private credential-file metadata before upload, and rechecks the observed unit
revision under the install lock without reading or exporting credential values.

Host adapters must consume the typed owner target instead of resolving a self
alias. `validate_owner_current(owner)` rechecks the actual retained child before
effects. `owner_process(owner, command, *, timeout=30, input_data=None)` runs an
existing trusted host command locally with that proof and original deadline. It
does not skip target/native/lifetime locks or turn a dictionary flag into local
authority. Host/CI selection and credential-consuming recipe integration must be
assembled and tested before claiming a complete stack workflow.

`owner_command_args(owner, command)` and `owner_environment(owner)` support a
bounded host-exec caller retaining its original stdin FD. The caller revalidates
the issued host target before and after effects and enforces the retained
deadline. The captured process channel uses bounded output, anonymous input,
explicit environment and the original durable process group. Timeout or incomplete
output is `server_cli_effect_unknown`, never proof that every effect stopped or
permission to release a reservation or retry.

`local_server_cli_submission_argv(owner, argv, *, project_dir, request_id,
source_commit, timeout_seconds, parent_job_id)` derives a fixed nested job from
the current original job. It retains the parent handle and target metadata, caps
the original deadline and uses a deterministic child request key. The installed
server verifies actual ancestry and the retained parent before acceptance.
The supported job parser computes source/project identities for the exact child
root; parent lineage and the exact child source commit remain separate bindings.
Child supervisors watch the original parent deadline/cancellation; a CLI parent
retains its own process lifecycle and exposes child receipts instead of being
silently reclassified as a processless matrix. An uncertain nested receipt remains
unknown; the deterministic identity must not be replaced to replay the operation.

The fixed CLI job receives explicit HOME, SANDBOX_HOME, account, PATH, locale and
user-runtime/DBus coordinates, plus fixed supervisor audit keys. No parent
environment is copied, no SSH agent/key is passed, and no provider value is sent
from the controller. Normal server commands resolve the Linux owner's existing
secret store. Secret value-output commands are unavailable through this retained
adapter. No per-controller role, credential approval or new broker grant is
introduced.

Retained-command restrictions use the actual installed CLI parser before
dispatch. Global selector abbreviations and valid options before a host action
cannot hide secret-output or missing-stdin commands. Parser diagnostics are
suppressed on this validation path. An embedding without the installed parser
refuses ambiguous sensitive syntax. Original argv, including explicit strict
runtime intent, remains intact for the fixed child; command selection does not
copy environment values or dispatch a command.

After a supported service update, a new authenticated identity read or ordinary
service status can refresh a joined registration's observed revision cache.
The same machine and ownership marker, complete authenticated response and
actual compatible unit protocol must agree within the original deadline. The
local write uses compare-and-swap. Strict runtime mode refuses drift. Existing
operation adapters, plans, reservations and server runtime pins are not rebound.
Compatibility observation alone makes no cache write. Older controllers lacking
this behavior need a supported controller update; this is not a global
registration migration. Explicit `remote service refresh NAME
--adopt-compatible-revision --json` remains available for the same bounded proof.

Installed discovery can fill a missing origin from a unique actual public network
interface address. It never queries an Internet address service, guesses a
hostname, or overwrites explicit routing metadata. Ambiguous or private-only
interfaces leave origin evidence unavailable.

Version1 retained jobs have no interactive stdin handoff. `host exec` and explicit
stdin/interactive options refuse before lookup or acceptance. These decisions
use the installed parser's resolved input modes, including legal abbreviations
and Hermes token/passphrase stdin, rather than matching original option text.
Embeddings without that parser refuse ambiguous input-option prefixes. Normal synchronous
CLI contracts remain unchanged. A local host-exec adapter may retain the original
stdin FD with the validated local argv/environment ports, but that alone is not
proof of an end-to-end remote stdin transport.

`job-capabilities --inspect-host-target --origin-remote-name NAME --name SLUG`
or `--target PROJECT/ENVIRONMENT` composes the installed owner's fixed read-only
target inspector. Optional `--environment` narrows name selection. The inspector
must actually be installed; its absence reports capability unavailable. This
mode observes existing server lifetime, successful summary, published and
original source commits, native mutation keys and five nonsecret container
labels. It creates no job, source checkout, target claim or execution grant.
The schema1 response includes service name/index/container ID and a selector
digest. It excludes container environments and provider values.

`sandbox.application.server_cli_targets.observe_server_cli_target(remote_name,
*, target=None, name=None, environment=None, budget_seconds=45)` performs that
fixed read through the joined keyless Tailnet transport, after authenticated
machine identity and current owner/revision validation. It needs no controller
project directory or local stack history. The returned metadata cannot select a
local process channel or authorize a later job. Execution must independently
resolve and revalidate the retained selector under genuine current-job proof.
Discovery lists the optional contract in `inspection_capabilities`, separately
from executable job contracts so older strict execution consumers remain valid.
No no-source job admission or remote original-stdin handoff is enabled by this
metadata port.
