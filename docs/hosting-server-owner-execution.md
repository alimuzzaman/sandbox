# Hosting through the installed server CLI

For the shipped protocol-25 client pin, normal-entry setup, remote receipt
handling and verified coverage limits, read [Tailnet-only operations](tailnet-only-operations.md).
The release's exact-byte exec proof does not establish safe secret reconciliation
or atomic recipient-file publication; those remain separate P1 work.

Confirmed joined Tailscale stack-up and apply requests select the fixed installed server CLI durable
job before controller configuration and provider-key preflight. Every admitted
operator has the existing Linux owner's Sandbox rights. No controller allowlist,
owner SSH private key, managed installation or copied laptop provider key is a
prerequisite. Joined remotes refuse an explicit `--credential-scope personal-local`: joined
hosting requires server-held credentials. The separate explicit registered-profile contract
remains separate and cannot silently become this execution path.

Stack creation resolves `--ref` on the invoking controller before submission and
publishes that exact committed source, even when the requested branch differs
from the current checkout's HEAD. It retains the original ref and branch name in
the original command so generated names and dev-branch lifetime rules survive
the detached server checkout. Uncommitted edits are excluded, as in the existing
committed stack-template workflow. The receiver checks this binding against the
genuine retained job, current owner and actual staged HEAD without creating Git
branches. This requires the observed `job.server-cli-stack-ref.v1` capability;
an older receiver refuses before staging or acceptance. Ordinary controller
execution and generic retained commands without this binding keep their existing
ref resolution.

Ordinary joined jobs also recognize this shipped stack-reference contract in
installed capability discovery. It is retained or removed by the same identity-
checked registration update as other known contracts. Unknown or malformed
capability entries still refuse; recognizing this contract grants no new role.

Observation, validation and planning keep their existing paths. Joined
`host exec --remote NAME --name STACK --service SERVICE -- COMMAND` uses
metadata-only server selection and one no-source retained job. It needs no
local checkout, stack registry or hosting history. The client waits for the
original job and returns its validated capture and underlying exit code.
`--target PROJECT/ENVIRONMENT` is also supported. A non-stack target is
selectable only once it has a lifetime record: run
`sb host classify --remote NAME --target PROJECT/ENVIRONMENT --lifetime hosted --confirm`
(or `permanent`) once after its first apply. `host apply` never creates that
record. After a succeeded apply it only moves an existing classified record's
commit to the applied one, without changing its lifetime or TTL. Classify takes
the commit from the server's own `summary.json` when that shows a succeeded
apply, so classifying from a controller with older local history still matches.
Without a record, exec refuses with `host_server_target_evidence_unavailable`
and a hint: classify an unclassified target, or re-apply a classified one whose commit is stale. A busy target or one whose last apply did not
succeed refuses with `host_target_busy`. Non-joined targets keep their existing
controller execution paths.

The default batch contract transfers original non-TTY stdin once through the
SDK's hash-bound anonymous input facility, bounded to 1 MiB and a 30-second
intake deadline. TTY input remains empty as in existing host exec. No named
input file or source upload is created. Running-job observations return no
child output. Terminal stdout and stderr are retained as private, bounded
artifacts. These intended output bytes are not lexically scrubbed. Plain output
writes the verified exact bytes; JSON uses UTF-8 replacement and identifies the
original request and job. Ordinary job logs contain capture metadata, not the
artifact bytes. This is bounded batch output, without live streaming or an
interactive TTY.

The SDK's explicit `output_mode='utf8'` selects the older redacted log-capture
profile. It is not the normal joined host-exec caller's output mode, and the
caller never silently downgrades to it when artifact capture is unavailable.

Preacceptance unavailability, lost acceptance and unavailable observation of an
accepted job are distinct errors. The latter retains the known job ID. None
resubmits, falls back to controller state or automatically cancels an original
operation. An interrupted wait preserves that original handle.

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

Owner apply diagnostics use `hosting.apply_output` before log retention. The
existing `_apply_host` scope binds the exact values already returned by hosting
secret resolution to the genuine original owner job. Every logged owner phase
uses a fixed helper with those values delivered only by anonymous stdin. The
helper's child receives the explicit nonsecret owner environment and empty
stdin; it never inherits the filter input. Both child streams share one pipe
through the existing streaming exact-value and lexical scrubber. Only scrubbed
bytes reach `apply.log` and the bounded public tail used for return, progress
and errors. Phase timestamps, useful diagnostics and the actual exit status
remain visible. Legacy controller transport keeps its existing path.

The streaming scrubber retains complete exact values across whitespace emission
boundaries as well as partial prefixes across pipe reads. Overlapping exact
values cannot be split when an earlier match moves that boundary. Values with
spaces, tabs, newlines or UTF-8 characters keep the same exact-match protection.

The helper and child stay in the original durable process group and use the
SDK's original deadline/cancellation guard. A missing/malformed completion
receipt or post-child log failure fences that same job as unknown, withholds
rollback and lease retirement, and never replays the initializer. Log files
must be private, owner-held regular files without symlink or hardlink aliases.
Directory traversal uses Linux `O_PATH` or POSIX `O_SEARCH` handles where
available, so search-only ancestors such as `/home` mode `0711` need no read
permission or permission change. Each component still rejects symlinks, and
the final log must remain an owner-held `0600` regular file with one link.
Missing exact-value scope refuses before logged command effects. The output
scope is an internal negative filter, not a new operator role or grant.
Generic downstream log redaction cannot repair a previously retained raw value;
this change prevents new raw writes and does not claim historical log cleanup.
Transformed or deliberately encoded child output remains outside the shared
scrubber's exact-match guarantee.

`hosting.server_target_selection.inspect_server_exec_target` is a metadata-only
receiver composition port. It uses the SDK's current installed owner reader,
fixed bounded Git/Docker metadata commands and a complete current-source
snapshot. Its result is ordinary data, never a held writer or retained job
receipt. Missing installed owner-reader support refuses explicitly. The SDK
owns authenticated transport registration, no-source job admission and
controller stdin delivery. A consuming job must reread the selector under its
actual process proof and compare the exact retained selector digest before
effects. Missing digest refuses before runtime metadata queries. The
`commands.host_exec.SERVER_CLI_EXEC_BINDING_VERSION = 1` source contract means
that check is assembled with native-lock and physical-lease scope. SDK exec
capability discovery requires this contract; it grants no authority by itself.

This source increment requires the assembled `job.server-cli.v1` adapter from
the access transport feature. Old installations must report capability
unavailability before source submission. No capability is added by this hosting
module alone. Source checks, independent auth review and coordinator-run
installed acceptance are separate gates. This document does not claim a live
stack creation or provider validation. Authentication and production-path
changes require owner review before release. No install, provider action,
migration or retry of the old GS operation is performed by this increment.

No-source exec additionally requires the actual SDK
`job.server-cli.exec-stdin.v1` admission/result ports and
`host.target-inspect.v1` metadata port, together with this digest-aware handler.
The generic v1 source-job adapter continues to refuse exec/stdin. Installing a
descriptor or this version constant without the composition does not enable it.

Exact binary output needs a separate privacy-reviewed retained capture contract.
`--output-encoding base64` in inner JSON would not be sufficient: the generic
job output redactor can recognize and rewrite arbitrary base64 text. This
increment does not bypass redaction. The separate artifact capture composition
uses a hidden receiver-only `--exec-capture-artifacts` option. It prepares SDK
custody before effects under the genuine original owner/selector, captures up
to 1 MiB per stream and requires actual reader EOF. After the whole held target
scope exits successfully, native locks exit, physical retirement succeeds and
the current selector/owner validate again, it publishes the actual `ExecOutcome`
through that custody. Ordinary job stdout contains only the returned metadata.
Cleanup failure or missing custody refuses capture publication.

The client renders verified SDK `stdout_bytes` and `stderr_bytes` directly to
the corresponding plain-output byte streams. User JSON keeps the default UTF-8
replacement projection. It never puts raw/base64 artifact bytes back into job
logs. The SDK owns byte artifact storage, immutable registry/manifest bindings,
retention, authenticated chunk retrieval and a separate capture capability.
The actual committed custody/result API is SDK
`f09bc57b464a1fb58c5ed2b1fbcdf4313d9ca555`. This composition exports
`SERVER_CLI_EXEC_CAPTURE_VERSION = 1` with the emitter, EOF checks and byte-aware
renderer. The normal client explicitly selects `output_mode='artifact'`; a
server lacking the separate capture capability refuses before stdin intake or
submission. There is no automatic downgrade to the older lossy log capture.
The SDK's explicit `output_mode='utf8'` remains the old profile. An export and
source agreement are not test, review, installed or binary-runtime acceptance.

Artifact capture is bounded to 1 MiB per stream and seven days of exposure,
subject to the existing job cleanup lifecycle. It holds intended command-output
bytes in private server job artifacts rather than in ordinary lexical logs.
The publisher and reader bind the original job/request, source, installed owner,
target selector, service, deadline and exact hashes. Missing, changed, expired
or cancelled custody cannot become success or authorize a replay. Live TTY,
unbounded input/output and streaming parity remain outside this contract.

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
at that boundary. The parser exposes `--credential-scope personal-local`, but joined hosting
refuses that override before submission; it does not select controller execution.

Owned runtime files are `commands/hosting.py`, `commands/host_stack.py`,
`commands/host_exec.py`, `hosting/host_exec.py`, `hosting/server_cli_dispatch.py`,
`hosting/server_cli_exec_client.py`,
`hosting/server_cli_target.py`, `hosting/server_target_selection.py`, and the
two exact owner-target branches in `delivery/context.py`. Apply only this access
delta, rather than copying the whole feature's hosting or CLI files. The SDK
owns `application/server_cli.py`, fixed job admission/supervision and core
process/home delegates. Its actual installed parser, contract discovery and
retained job authority must accompany these adapters.

## Project-dir delivery children

A source-bound project-dir apply requires both `job.server-cli-source-branch.v1`
and `job.server-cli-project-dir-apply.v1` in `server_cli_apply_capabilities`,
outside the strict job capability list. Older servers refuse before staging.
The outer job submits one local durable child with the same staged root, clean
HEAD, validated branch token and live parent. Retained parentage distinguishes
the child even though its root equals its parent's root. The child must pass
ordinary durable admission before effects; the outer never applies directly.
The existing cleanup guard protects parent materialization while children run,
and resource inventory protects live job roots. The child's original deadline
and cancellation lineage remain enforced.

`host recover` does not use ordinary apply admission or submit another apply;
it keeps the recovery service's source/authority guards and bound-branch check.
Rollback applies use the same nested apply path. This source fix has not been
installed or exercised against a live host.
