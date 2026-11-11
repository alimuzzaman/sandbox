# Hosting through the installed server CLI

Confirmed joined Tailscale stack-up and apply requests select the fixed installed server CLI durable
job before controller configuration and provider-key preflight. Every admitted
operator has the existing Linux owner's Sandbox rights. No controller allowlist,
owner SSH private key, managed installation or copied laptop provider key is a
prerequisite. An explicit `--credential-scope personal-local` retains the existing
controller credential workflow. The separate explicit registered-profile contract
remains separate and cannot silently become this execution path.

Observation, validation, planning and existing exec commands retain their
synchronous result and stdin contracts. They are not automatically staged from
the invoking cwd or converted to accepted-job responses. In particular the
durable supervisor has no controller stdin delivery port; automatic exec
handoff stays disabled rather than accepting a job with silent EOF. The owner
exec adapter below is for a genuine already-admitted server CLI invocation,
and does not itself supply controller stdin transport or a new read-only
server-target discovery endpoint.

The server job retains its original alias, machine/service pins, source,
request, process identities and deadline. A same-server hosting target is issued
only after that evidence validates, and only for the original alias. Another
alias uses its real registration. Missing or revoked selected-job evidence
refuses; it never falls back to controller secrets or self-SSH. Normal nested
stack applies use the installed fixed CLI child-job adapter, with their parent
and original deadline retained. Values stay in the server's existing registered
personal store and trusted hosting consumers.

Existing hosted exec targets can be selected without the invoking controller's
stack registry or hosting history. The server selector cross-checks the lifetime
copy and tuple, successful server apply summary, deployed Git revision and five
specific Compose labels. It never reads container environments or the runtime
environment file. It requires known committed Compose source files and retains
the actual runtime override. Manifest bytes must match their committed blob
identities using raw, unfiltered hashing. Inspection does not refresh Git's
index or run repository-configured fsmonitor, diff or content-filter helpers.
For a subtree publication, the successful summary
must bind the original stack/lifetime commit to the published Git revision;
those two revisions are retained separately. Incomplete historical evidence,
dirty source files, uncertain applies and the
preserved GS attempt remain unavailable, rather than being silently adopted.

Exec holds the server's existing native alias mutation keys and physical target
lease through execution and final validation. A different durable job cannot
take over a lease just because both run as the same Linux account. Unknown
execution or retirement retains the physical fence. Stdin remains a kernel
descriptor passed to the local process; it is not staged or logged. Captures
remain bounded. Lost job-start acceptance remains unknown under the original
request, and prevents blind stack replay.

An unknown owner process outcome also blocks later process calls in that same
retained job, including a reissued same-server target. Apply withholds provider
and runtime rollback while the original child may still write, and keeps its
physical lease. A timeout of the exec client does not prove container-side
descendants stopped, so owner exec keeps that lease as well.

Owner exec selects the requested service's unique first running container from
those verified labels. It executes that exact container ID without asking
Compose to render configuration or read the runtime environment file just to
discover service names. The original SSH/Compose path stays intact for existing
controller-local execution.

Owner runtime environment writes use the SDK's anonymous stdin channel. Neither
the original text nor a reversible base64 encoding enters the helper argv or
durable command record. The existing intended-recipient file stays at its
normal server runtime location with mode 0600. The writer rejects a symlink,
foreign-owned file or multiply linked file before truncation, sets its mode
before writing and synchronizes completion. This is the existing recipient
facility, not a new laptop credential copy or arbitrary staging facility.

`hosting.server_target_selection.inspect_server_exec_target` is a metadata-only
receiver composition port. It uses the SDK's current installed owner reader,
fixed bounded Git/Docker metadata commands and a complete current-source
snapshot. Its result is ordinary data, never a held writer or retained job
receipt. Missing installed owner-reader support refuses explicitly. The SDK
still owns authenticated transport registration, no-source job admission and
controller stdin delivery. A consuming job must reread the selector under its
actual process proof and compare the exact selector digest before effects.

This source increment requires the assembled `job.server-cli.v1` adapter from
the access transport feature. Old installations must report capability
unavailability before source submission. No capability is added by this hosting
module alone. Source checks, independent auth review and coordinator-run
installed acceptance are separate gates. This document does not claim a live
stack creation or provider validation. Authentication and production-path
changes require owner review before release. No install, provider action,
migration or retry of the old GS operation is performed by this increment.

## Minimal installed-e467 composition

The access transport SDK is a separate source dependency. This hosting adapter
does not require `credential_selection`, `shared_credentials`, installation
binding, Miu, owner-local baseline factories or their capability registries.
For an e467-based candidate, register the public callable
`sandbox.hosting.server_cli_dispatch.dispatch_host_if_selected` directly as the
host command's preconfiguration handler. The broader 086 handler in this feature
branch additionally owns its explicit-profile refusal and static parsing; none
of that is needed to dispatch existing Linux owner commands.

The minimal shared command hook is one optional `CommandSpec` field,
`preconfiguration_dispatch: Callable | None = None`, and the CLI call immediately
after parsing, before configuration/migration/credential access. The call checks
the selected command's handler and returns from CLI main only when it returns
true. The access SDK already retains `args._server_cli_argv = tuple(raw_argv)`
at that boundary. Exposing `--credential-scope personal-local` preserves an
explicit controller execution choice; it requires no registered-profile fields.

Owned runtime files are `commands/hosting.py`, `commands/host_stack.py`,
`commands/host_exec.py`, `hosting/host_exec.py`, `hosting/server_cli_dispatch.py`,
`hosting/server_cli_target.py`, `hosting/server_target_selection.py`, and the
two exact owner-target branches in `delivery/context.py`. Apply only this access
delta, rather than copying the whole feature's hosting or CLI files. The SDK
owns `application/server_cli.py`, fixed job admission/supervision and core
process/home delegates. Its actual installed parser, contract discovery and
retained job authority must accompany these adapters.
