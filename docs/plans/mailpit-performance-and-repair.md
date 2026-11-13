# Reduce Mailpit panel and repair overhead

Status: planned, implementation not started. Updated 2026-11-12.

This follow-up does not block the already-authorized Mailpit deployment.
The current feature source is Sandbox `667b599` and setup `1f65a30`.
Review findings describe source paths, not measured latency or promised speedups.

## Phase manifest

| Field | Plan |
| --- | --- |
| Outcome | Show the same trustworthy inbox link with less panel work, and make route repair bounded and recoverable after a proven no-effect refusal. |
| Scope | Sandbox panel discovery and the setup wrapper's `--repair-mail` mode. |
| Assigned PRs | Planned: Sandbox panel optimization; xspeed-dev-setup repair simplification. None opened. |
| Target release cycle | One coordinated follow-up release after the current Mailpit deployment. Versions and date unassigned. |
| Dependencies and order | Confirm the current live inbox first. Implement the two independent source changes, review the assembled changes, deploy when authorized, then run direct acceptance checks. |
| Acceptance criteria | Only recorded Mailpit upstreams receive certificate parsing; no unused certificate facts are computed; compatible admitted clients can repair without a forced migration; proven no-effect refusals permit an explicit new attempt; unknown outcomes remain fenced; every repair observation shares one deadline. |
| Readiness and blockers | Source review complete. No implementation or runtime performance evidence. The refusal classifier must distinguish no effect from partial publication before retry behavior changes. |

## 1. Narrow panel discovery

Change `sandbox/commands/ui_dash.py`, `sandbox/hosting/tailnet_names/panel.py`,
and the `mail_access` action in `sandbox/hosting/tailnet_names/_remote_main.py`.

1. Read the existing server records once before requesting route metadata.
2. Derive the distinct valid recorded Mailpit loopback upstreams from those records.
3. Skip the privileged helper when there are no relevant upstreams.
4. Pass those upstreams with the actual panel Tailnet bind address to the helper.
5. Inspect managed blocks to find every name matching each requested upstream.
6. Read activation and certificate evidence only for those matching names.

Preserve multiple-name detection. Filtering must not pick one alias and conceal
another name for the same upstream. Unavailable server records stay unavailable.
An unrelated route must not force expensive certificate parsing for the inbox.

Keep the existing scan/output/time bounds and safe file checks. Do not add a
cache first: filtering avoids unrelated work without a stale-ready policy,
cache invalidation, or shared mutable cache state.

## 2. Parse only the certificate facts the panel uses

The shared `_chain_facts` currently launches four OpenSSL processes when no
private key is supplied. The panel consumes only identity and expiry.

Use a small panel-specific public-certificate read for SAN names and validity.
Combine compatible OpenSSL queries where supported. Do not compute the DER
fingerprint or formatted expiry string unless the panel actually consumes them.
Keep the existing publisher's certificate/key validation unchanged.

Preserve exact hostname matching, certificate validity, and the loaded activation
generation comparison against current block and public-chain bytes. Never read
private-key contents. Retain the outer hard timeout rather than adding more
per-file timing guards without evidence they help.

## 3. Remove the redundant server-source equality guard

Change only the `--repair-mail` admission check in `stacks/full-stack.sh`.
Remove `installed_source_revision == SB_RELEASE_REVISION` from that check.

Keep the locally selected clean, exact client revision. Keep selected remote
identity, authentication, admitted Tailnet control, active service, and supported
compatibility checks. Let the supported command refuse unavailable capabilities.
Do not relax the full-stack apply or migration paths.

Record both client and installed server revisions in the receipt. Compatible
source differences must not force a controller migration for route repair.

## 4. Permit a new attempt only after proven no effect

The local route fence currently survives every failure, including a refusal
before any remote submission. Preserve the original receipt in every case.

Classify the original result before permitting another attempt:

| Original outcome | Next action |
| --- | --- |
| Verified published route | Reuse it and perform the existing readiness check. |
| Complete authoritative refusal proving no effect | Preserve the receipt, correct the cause, and permit an explicitly identified new attempt. |
| Accepted failure with possible partial publication | Keep the fence and reconcile the original operation through supported inspection. |
| Lost acceptance, incomplete output, timeout, or unavailable evidence | Keep the fence and original request/job. Do not submit another identity. |

A terminal failure or `ok:false` alone never proves no effect. Audit the current
CLI and journal result shapes before implementing the classifier. If they cannot
prove no effect, retain the fence for that case. Do not invent a generic recovery
API or clear server leases, journals, DNS records, or foreign artifacts.

## 5. Give route repair one overall deadline

Apply one finite deadline to `--repair-mail`, including admission, inventory,
submission observation, paged output, readback, and the final HTTPS HEAD.
Use the remaining budget for each wait and command timeout. Never restart an
80-minute budget for each sequential job.

Use supported timeout controls or the existing bounded runner. Do not assume
that every CLI command accepts `--timeout`. Preserve the ordinary stack workflow's
existing timing behavior. The exact repair budget is an implementation policy
choice, not a measured operation duration.

When the deadline expires, retain the original request/job and receipts. Stop
observing without cancelling accepted work or automatically resubmitting it.
Keep terminal output pagination, post-publication readback, and the final TLS
HEAD. These establish different facts and are not redundant checks.

## Source review and direct acceptance

Follow the user's requested order: finish the surgical source changes, review
changed behavior, deploy when authorized, then run direct checks. No predeploy
test suite, benchmark framework, extra daemon, or generic abstraction is planned.

Use a small helper for postdeployment checks and report exact commands, outcomes,
and retained logs. Check the following behavior:

- The real CFE inbox still returns success at `/mail/` over validated Tailnet HTTPS.
- The panel offers the matching link and keeps missing or ambiguous access visible.
- Supported public-access verification still excludes public access.
- Panel observation performs no OpenSSL work for unrelated upstreams and skips
  the helper when no Mailpit ports are recorded.
- A compatible admitted client is not rejected solely for a source difference.
- Proven no-effect refusal recovery preserves the first receipt and uses a new
  explicit attempt. Unknown or partially applied outcomes remain fenced.
- Sequential observations consume one shared deadline and preserve accepted work
  when observation stops.

Do not read mailbox contents or secret values. Do not create extra production
routes or intentionally interrupt publication merely to manufacture evidence.
Label unexercised failure branches as source-reviewed, not runtime-proved.
If reporting latency, state the exact operation, route count, cache condition,
and whether the measurement includes privileged startup and certificate parsing.
Do not infer a speedup from fewer subprocesses alone.

## Safeguards that stay

Keep exact recorded stack/remote/port selection, managed ownership, safe file
modes and link counts, current activation generation, certificate identity and
validity, conflicting-upstream refusal, server-side lease/ingress protections,
Tailnet/public verification, complete original receipts, and unknown-result fences.

## Evidence

- Panel review: `/Users/alim/Sites/git/.scratch/sandbox/mailpit-performance-review-20261112.txt`.
- Repair review: `/Users/alim/Sites/git/.scratch/xspeed-dev-setup/mailpit-guardrail-review-20261112.txt`.
- Current deployment handoff: `/Users/alim/Sites/git/.scratch/sandbox/mailpit-source-handoff-20261112.txt`.

Planning creates no additional merge, publish, or deployment authority. Record
human review of changes to admission and retry behavior before their release.

## Current deployment finding

The original route-add job `37aadaa1966197cf108808ed2ace3c4a` failed with
`tailnet_name_config_missing` before configuration loading completed.
Source tracing and retained contract establish that the dispatcher incorrectly
moved this controller-owned operation into a server-owner job. The server does
not need a copy of the controller configuration, broker, records, or ACME account.

The immediate repair removes `tailnet-name` from server-owner dispatch, retaining
the existing registered remote runner. Inspect the controller name inventory
before a new add. Keep the original refused job and its attempt fence. Do not
copy credentials or change controller/server configuration as a workaround.

The wrapper also stops requiring server-source equality when service compatibility
and authenticated Tailnet admission hold. This allows a fixed controller client
without another server migration. An explicit public IPv4 may be supplied for the
exposure probe when the registered SSH endpoint has a Tailnet-only address.

These deployment corrections are being handled now. The panel performance and
retry/deadline follow-up remains planned. Feedback `b1a032d8646c8c9793d48794b928322f`
recorded the original symptom; its missing-server-setup diagnosis is superseded
by this execution-site cause. Prefer an existing readiness result over a new probe.
