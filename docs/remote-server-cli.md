# Fixed server CLI jobs

This source contract is not installed acceptance. A server lacking the assembled
`job-capabilities` / `job-start --server-cli` contract is unavailable for this
adapter. It must not be described as denying an admitted owner. The managed
installation feature remains separate and paused.

Deploy-based remote submission requires the installed `job.acceptance-receipt.v1` contract
before source staging. The retained ACK carries `acceptance_contract`, the
original `request_id`, the repository's canonical `request_digest`, and exact
source identity/commit/dirty metadata. The client compares the receipt with its
original attempt; it never replaces contrary or omitted server source evidence
with local values. Missing or mismatched receipt evidence is acceptance unknown,
with the same original request retained and no automatic second submission.
Older services must use the supported selected-service update first.

An empty overlay still has its canonical fingerprint. Receipt reconciliation
keeps that fingerprint and the original request digest unchanged, while reporting
the source as clean. Fingerprint presence alone does not imply modified files.

Measured deployments also carry `source.content_digest`. Acceptance returns
that measurement from the original retained submission, and the client checks
it against the independently measured deployment. Missing or contrary evidence
stays acceptance unknown. Legacy submissions omit the optional field, preserving
their original canonical request digests and replay receipts. New materialized
submissions require the installed `job.source-content.v1` contract.

Control protocol 24 records these wire changes. The runtime continues serving
the common older control protocols, while new materialized jobs must provide
the measured-source binding. Older job writers must update to the reviewed
client revision; missing binding refuses before new admission. Existing receipt
replay remains valid. Updating client code does not authorize rewriting saved
registrations, strict pins, old jobs or plans.

Joined execution observes current installed contracts before deployment, even
when its local metadata already lists them. Discovery replaces known component
claims while preserving unrelated capabilities, so a stale registration cannot
lend an absent receipt contract to a changed service. This is capability
observation over the existing Tailnet owner transport, not a new permission.
Matrix receipts bind their parent and each original child source/request, matched
by unique workspace rather than assuming returned child order. Synchronized
projections retain their separate authoritative generation binding.

Ordinary joined `job-start` selection also discovers the installed contract
automatically. The application target resolver calls the shared execution remote
adapter when `job.exec` is missing, reads `job-capabilities` over the selected
keyless Tailnet transport, validates its machine/marker/revision against the
joined registration, and publishes only observed contracts by compare-and-swap.
This happens before source staging or job acceptance. `job-list` success alone
does not establish execution support. An older installed CLI without discovery
reports `remote_execution_capability_unavailable`; it does not become an owner
denial or authorize invented registration flags. A supported runtime update and
separate installed acceptance remain necessary for that older receiver.

`sandbox.application.server_cli.dispatch_server_cli(remote_name, argv, *,
project_dir, request_id, timeout_seconds=900)` submits before controller secret
preflight. `argv` starts with the CLI command, without an executable. The adapter
validates authenticated machine and service pins, reads actual installed CLI
capabilities, stages exact source through the existing transfer, and uses the
existing durable job repository, scheduler, supervisor and retained output. It
returns the original server acceptance receipt. Unknown acceptance is not retried.
The fixed installed executable is selected on the server, with no project PATH,
project script, self remote alias, instance ensure or SSH loop.

The optional SDK `source_ref` is a full, locally resolved commit and selects the
existing immutable measured-source deploy path instead of a working-tree overlay.
Host stack creation uses it together with its retained original ref/branch binding.
The installed real host parser and receiver advertise
`job.server-cli-stack-ref.v1`; the SDK requires that observation before publishing
or accepting this command. This adds no operator permission or provider grant.

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

Cancelling a process-owning server CLI parent still cancels its original
process when a listed child finishes concurrently. Only a fresh observation
of that exact child's terminal state permits continuing after `already_terminal`;
live-process identity failures and unknown outcomes remain errors.
Cancellation first commits an idempotent `server_cli_cancel_requested` event
in the existing job ledger. It then follows active descendants through completed
intermediate jobs without signalling those completed processes. New acceptance
checks the actual full parent chain and cancellation events in the same SQLite
write transaction as insertion. A child validated earlier cannot be admitted
after the original fence. Existing receipts remain readable; refusal of this
attempt does not establish that no earlier receipt exists.

Supervisors check the complete chain before launch and while running, including
the original minimum deadline. The owner process/context ports do the same
before effects. Actual parent links, not environment hints, caller JSON root IDs
or a cached immediate-parent phase, define this authority. A succeeded parent
still permits its legitimate detached child while the unfenced ancestor chain
and original deadline remain valid. Cancelling a terminal root also fences late
admission. The fence makes no claim that signals succeeded or work stopped;
original process-identity refusals still propagate. Ordinary jobs without a
server-CLI ancestor retain their existing lifecycle rules. No schema version or
controller authorization list is introduced.

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

The internal `JobService.submit(..., anonymous_input=...)` handoff accepts only
an issued `AnonymousJobInput` for an independent fixed server CLI job. Intake
reads original stdin once into an anonymous file, bounded to 1 MiB and a finite
deadline. The retained original request binds its size and SHA256; the private
descriptor records only those fields plus device/inode. The detached supervisor
receives the actual file as descriptor0 and verifies owner, private mode, zero
links, device/inode, size and bytes before giving it to the fixed child. A
descriptor number or serialized binding cannot substitute for the held file.
An occupied scheduler slot produces a retained terminal
`anonymous_stdin_capacity_unavailable` receipt and no dispatch, rather than
queuing a job whose anonymous input would disappear. Ordinary jobs retain
DEVNULL. The dedicated no-source adapter composes this handoff; it does not
enable generic project argv forwarding or an interactive TTY.

`ServerCLIContext.target_selector_digest` is optional retained selection context.
It is read only after the actual original job and current kernel child have been
proved, with a closed SHA256 encoding. Ordinary creation jobs leave it None.
Historical exec consumers re-resolve under their held native locks and pass
this digest into their sealed selection on every before/after check. Client
snapshot dictionaries cannot create a `ServerCLIContext` or replace its job
provenance.

`sandbox.application.server_cli_exec.submit_server_cli_exec(remote_name, *,
snapshot, service, argv, stdin_fd, request_id, timeout_seconds=300,
output_mode='artifact')` implements
the source contract for a fixed historical host-exec job. It needs no controller
checkout. The client captures original stdin once into a bounded anonymous file
and sends only its size/hash in the nonsecret selector envelope, with the actual
bytes through the keyless SSH stdin FD. It makes exactly one submission. An
incomplete, timed-out or malformed acceptance is
`ServerCLIExecAcceptanceUnknown` with the original request ID, never a new
request or an implicit retry.

The installed `job-start --local --server-cli --server-cli-exec-selector JSON
-- COMMAND...` receiver resolves existing target metadata itself. Client paths
are absent from this closed envelope. It verifies current machine/marker/revision,
selector digest and unique first service, captures and verifies original stdin,
then re-resolves before durable acceptance. It derives the exact source root,
canonical project/source identities and published commit on the receiver. Its
retained provenance includes the original target snapshot, selector, input
size/hash and absolute deadline. The supervisor and current-job context enforce
that original deadline; admission time cannot reset it. The fixed installed
child runs `host exec --json` under genuine owner-job context. Artifact mode adds
the private `--exec-capture-artifacts` receiver flag. The original job binds the
chosen output contract; it cannot silently fall back to UTF8 after acceptance.
Explicit `output_mode='utf8'` preserves the earlier capture projection. Existing
retained job status/output/artifact ports provide bounded result observation.
This contract does not grant a role or use a self remote alias.
The server ledger retains the original request ID even if the controller loses
its receipt. `job-list --remote NAME --kind server-cli --workspace server-exec
--json` returns those request IDs; follow its normal cursor pagination. Inspect
the matching retained job with `job-status` and bounded `job-output` before any
replay. Unknown acceptance does not authorize a second request identity.

The dedicated intake is bounded to 1 MiB stdin, 256 argv entries / 64 KiB argv,
30 seconds stdin intake and an original request deadline of at most 3600 seconds.
It captures batch stdin before admission; it does not provide an interactive
TTY or claim live streaming. The generic v1 forwarding adapter still refuses
`host exec`; normal controller integration must explicitly use this dedicated
port and preserve its original receipt, status and captured result. No public
exec readiness is inferred from metadata alone. Discovery's separate
`server_exec_capabilities` contains `job.server-cli.exec-stdin.v1` only when
the actual receiver parser, owner target, native execution scope and digest-aware
selector are assembled. The client observes that contract before reading stdin
or submitting a job. Older receivers refuse as capability unavailable at this
read-only stage. Source checks, independent review and installed end-to-end
acceptance are still required. Binary discovery is separate:
`server_exec_capture_capabilities` advertises
`job.server-cli.capture-artifact.v1` only when the actual host handler exports
`SERVER_CLI_EXEC_CAPTURE_VERSION=1` with the committed custody implementation.
Absent binary support refuses before stdin intake/submission; it is not an
operator permission error.

`read_server_cli_exec_result(remote_name, receipt, *, budget_seconds=15)` observes
one original retained job. It checks the request/source/provenance and selected
machine/owner against the receipt. A running result returns status without child
output. After termination it reads bounded retained stdout pages, verifies the
fixed host-exec result's target/service/exit code and returns the original capture
envelope for the caller to render. It makes no submission, retries or stdin
replay. Malformed, missing, mismatched or truncated observations raise
`ServerCLIExecObservationUnavailable` with the known job and request IDs; known
acceptance is not relabeled as a new unknown submission. Compatible service
updates may permit reading an older job's ledger without rewriting that job's
original revision or granting it a new execution context.

The shared `job-artifact-get` service also gates private capture bytes on a
succeeded or failed original job, before and after reading. Running, uncertain,
cancelled, timed-out or interrupted jobs refuse, including a durable cancellation
fence on the original job or an ancestor. The private files remain retained;
refusal does not replay execution or delete evidence. Ordinary artifact reads
keep their existing behavior. A later cancellation cannot retract bytes already
returned by a completed read.

`sandbox.application.server_cli_capture.prepare_server_cli_capture()` opens only
the current original job's private artifact custody, before effects. The
returned context manager's `publish(actual_ExecOutcome)` is one use and closes
its handles; `close()` is idempotent for setup, cancellation and no-publish
paths. The trusted host handler calls publish only after successful native-scope
exit, final target validation and physical-fence retirement, followed by fresh
current owner/selection proof. Unknown completion and cancellation cannot
publish positive custody. Output persistence grants no new target authority.

The publisher accepts actual bounded stdout/stderr bytes, not paths, environment
values or provider-store references. It creates two immutable owner-only byte
artifacts plus a closed binding manifest. The original job/request, machine,
owner marker/revision, project/source identities, original and published source
commits, selected target/service, selector digest and deadline bind the manifest.
Files use fixed ledger IDs, no links, private directories and mode0600; bytes and
directories are fsynced before one registry publication transaction. Only the
small manifest descriptor and outcome metadata enter ordinary logs. Their
existing redaction remains enabled. Byte artifacts intentionally preserve the
authorized container output; the capture contract does not read host broker
files, inherit provider values or provide an arbitrary host-file reader.
Publication rechecks the original row and full ancestor fence on its held SQLite
connection. Full kernel/unit proof runs before and after commit; opening a second
migrating repository while holding the write lock is avoided. Cancellation or
post-commit proof uncertainty withholds the acknowledgement and preserves private
custody for observation/cleanup, without replay.

Each stream is bounded to 1 MiB, with existing truncation flags retained. The
manifest is at most16KiB; all three artifacts expire for reads within seven days
and remain subject to supported job cleanup/retention. Expiration is not a new
background deletion service. Missing/partial publication or a crash retains
private uncertain files for existing cleanup and never authorizes replay or
deleting prior data. Repeating custody for the same original result reuses its
immutable registered artifacts; different bytes refuse overwrite.

The artifact reader validates owner/private mode, no links, exact opened file
identity, complete bounded content hash and retention before returning a chunk.
The result adapter verifies the manifest against the original receipt and
retained terminal ledger, then fetches bounded artifact chunks through the
existing `job-artifacts` / `job-artifact-get` transport. 512KiB chunks remain below
the 1 MiB JSON envelope limit after base64 expansion. This encoding is the
existing byte transport, not a log encoding. Paired `stdout_bytes` /
`stderr_bytes` are returned only after both streams verify. Plain CLI rendering
writes those exact bytes; user JSON may keep its legacy UTF8 replacement
projection. No bytes pass through the lexical log redactor, including canaries
that resemble credentials. This is bounded completed-output parity, not
unbounded output, interactive TTY or live streaming.
