# Owner-panel sharing, source contract v1

This implementation is source-only and unverified. No installed capability,
runtime acceptance, authentication review or release readiness is claimed.

## Approved policy

Anyone admitted by the existing Tailnet panel peer/Host/Origin/JSON/header checks
may create or revoke links for a stack whose current applied `share.agent_mint`
is enabled. No separate operator login is required. Audit identifies the actor
as `tailnet-panel`; it makes no claim about a named person. This policy does not
authorize the paused 090 URL/network sharing system.

The post-6c7a643 source correction accepts the documented combined WordPress and
named-entry declaration. Owner-local magic-link actions resolve the existing saved
email from that target's native owner-state records. Conflicting alias defaults,
missing/invalid addresses and per-entry denied addresses stop creation before mint.
This does not add an email body field, named-person admission, a guessed identity or
a new execution grant. The selected address travels only in the private gate page;
it is absent from the operation journal, audit and public response.

The correction needs a reviewed source update on the owner. Combined targets need
a successful supported apply so the WordPress settings and gate binding enter the
applied projection. No protocol or wire-envelope version changes. Existing targets
without an owner-local saved email still refuse; a laptop default is not transferred.
Source checks and review are recorded in the correction handback. Installed and
Hub/admin runtime acceptance remain open until a coordinated acceptance pass.

The HTTP handler, not a body field, selects owner-local execution on a server.
Controller panels retain the controller resolver. An owner failure never falls
back to controller history, a self-remote, SSH or an invented server job.

## Admission and current target

`owner_panel_gate.PanelRequest` is sealed after the actual handler checks.
`GateGrant` additionally requires current applied public evidence, the opt-in,
the installed owner identity, stack copy and source revision, canonical target,
native writer keys, exact gate and WordPress container ids, and the running gate
source/configuration digest. Both types refuse serialization. Owner metadata and
target inspection alone cannot construct an admitted request.

The outward operations remain create/list/revoke. Their private adapter accepts
only fixed gate verbs and fixed WordPress install/remove/admin programs. It has
no arbitrary command, process callback, caller path, environment or actor input.
Read-only capabilities check the running gate before a grant is issued.

## Applied evidence and revision

The summary schema remains version 1 with an optional `owner_share` object, whose
own schema is 1. `owner_share_record` owns its build/parse contract and bounds it
to 48 KiB. Apply's existing SummaryRecorder writes the projection alongside the
successful summary. It includes direct uses and public normalized access facts;
it excludes capture credentials, secret names, source environment and bearer data.

The gate config adds `panel_share`, `panel_access_digest` and `panel_target_digest`.
The access digest binds the normalized opt-in and entry policy, including
controller-only denied-email policy, to the actual running service. The target digest binds
routes, WordPress path/service/user declaration, target identity and Basic Auth
state. `site capabilities` adds `owner-panel-gate-v1` and a SHA-256 digest of the
canonical running config. The owner requires the exact installed gate source too;
injecting a newer management script into an older service cannot prove support.

This changes generated runtime/configuration revision evidence. It requires root's
current owner-target modules, a supported runtime update and an authorized successful
stack apply. Old summaries and gates refuse. No migration, adoption, update or apply
runs automatically. Root's 74956e7 source base and hosting.py umask prevention remain
required integration inputs; this slice does not edit hosting.py.

## Writer, operation and response custody

The owner path uses target_lease's actual cross-controller lease file protocol and
RecoveryRepository target effect locks for all native keys. Direct targets are
locked in stable order. Currentness is checked after locking, before effects and
before returning a URL. Apply/recovery ownership refuses sharing.

`owner_share_store` owns a private SQLite journal with a 256-operation bound,
one reserved listing or session-revocation slot and fsynced commits. Opening the journal prunes confirmed
revoked records and completed admin or active-share records after all their access
expires, provided their physical apply leases have been released. Accepted or
unknown effects and unresolved cleanup are retained. All planned link ids, bindings and parent/companion cleanup states
are retained before effects. No token, URL, page or WordPress signing key is stored.
Each actual Docker exec child waits on stdin until its real handle is recorded.
Private payloads travel on stdin, never in process arguments or job output.

A timeout keeps the original Popen handle and apply lease; it never kills a guessed
pid or launches a replacement. A later admitted request can continue observing that
same handle. Only confirmed zero-exit original execution settles its fence. A process
restart or ambiguous transport failure has no reconstructed handle and stays fenced
for coordinated recovery. Retrying create never skips unresolved original attempts.
Recovered listings are discarded only after original-handle settlement and physical
lease release. Completed management observations are also pruned after a crash
between lease release and journal deletion, so they cannot strand the reserved
slot. Unreported WordPress admin registrations remain retained until expiry.

The create response carries the parent bearer URL once, no-store. Companions are
minted first, receive the earliest shared expiry and are attached only in the parent
gate's private page record. Their pages contain no nested companions. Revoke uses
the original ids and exact bindings, including after gate state loss; it never
cleans a same-name replacement. Link/session listing uses the existing response
allow-list. Named-person attribution and URLs never enter audit records.

WordPress admin clicks use the existing bounded hash-pool/fragment POST protocol
on the exact applied WordPress service. Unknown admin registration is fenced too;
no URL can be recovered or displayed from the journal. Private WordPress routes
continue to refuse direct admin handoff rather than promise unreachable access.

## Deliberate refusal boundaries

Old controller-owned links have no owner-panel original record. The new owner
path refuses to adopt or revoke their cross-target companions from current names.
They require their original controller's supported cleanup path. Sessions are
revocable here only when their original parent owner-panel link is recorded.

Controller fanout also distinguishes acceptance from completion. After an
unsettled transport failure it retains the original unknown grant and reports
`mint_completion_unknown`; an absence probe cannot prove that a delayed writer
will not recreate it. Confirmed live siblings are still cleaned.
Failure to persist an eligible gate-absence acknowledgement also leaves cleanup
unconfirmed while WordPress and later sibling cleanup are still attempted.
Only the same locked create invocation can compensate an unknown grant after its complete
effect sequence has returned. This completion signal runs before application
reply parsing, so completed malformed or negative replies remain cleanable.
Reloaded unknown attempts stay pending until protocol expiry. Expiry ends access,
but does not prove process cessation or physical artifact removal. Recovery
before expiry needs a separate durable original-operation contract.
Settling a timed-out session-list preflight does not prove that parent. A resumed
revocation with no recorded `original_parent_link` repeats the bounded list and
checks the exact owner-panel parent binding before issuing revoke. Completed
preflights that refuse authorization are eligible for management-slot pruning
only after their physical lease is released.

Stack-issued inbox cookies sign the parent entry and link id and expire no later
than that grant. Forwarding rechecks parent liveness after reading a request body,
after upstream response headers, and before and after each response-body read.
Revocation during streaming closes the response without forwarding the next chunk;
already delivered bytes cannot be recalled. WebSocket admission rechecks after
connecting and after the upgrade reply; established tunnels retain the existing
30-second liveness recheck. Standalone inbox cookies keep their existing signed
expiry format and do not acquire a stack parent.

The WordPress sign-in client accepts at most 8 KiB of response data and cancels
the reader on overflow before accumulating the oversized chunk. HTTP 409 and
successful HTTP replies containing `ok:false` expose only fixed local guidance
for known manifest, target, declaration, private-route and remote refusal codes.
Unknown or malformed replies use generic guidance; server text and error URLs
are never reflected. Success requires an unexpired integer timestamp, the one-time
flag, a bounded user, and an HTTPS URL without credentials, whitespace or
backslashes, with the exact autologin query and 43-character token fragment.

Lost process handles, changed applied copies, gate replacements and disabled
current opt-in require coordinated recovery. This implementation does not add an
unsafe force, clear-fence or generic execution path.

## Verification held for coordination

No test, build, import, host operation or live mint/revoke ran for this source.
Required follow-up after CFE releases recovery/apply ownership includes syntax/type
and regression gates, root's independent authority review, then authorized runtime
acceptance. Cover admission failures before effects, opt-out/copy/config/service
changes, apply/recovery contention, original timeout/state loss, one-response URL
custody, parent and session revoke, partial companion cleanup, and two simultaneous
WordPress clicks. Old source/test reports do not verify these changes.
