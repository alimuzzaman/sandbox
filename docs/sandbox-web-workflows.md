# Sandbox web workflows

Source-based UI/UX changes, 2026-11-12. Runtime verification is held. This is
not a visual audit, responsive verification or accessibility compliance claim.

## Workflow map

Home lists hosts. Selecting a host scopes the instance rail. Local hosts offer
creation and instance actions; remote inventory remains read-only where its
service has no lifecycle API. Hosted targets lead to Stacks and hosted sites,
then a stack detail page and its direct shared stacks.

The bounded change covers stack access and the navigation and confirmation
controls it uses:

| Step | Before | Source change |
| --- | --- | --- |
| Navigate between hosts and stacks | Modified clicks were intercepted | Native modified clicks, downloads and explicit link targets are preserved |
| Open stack detail | Stale records hid refresh errors; no refresh button | Refresh stack, stale-record warning and partial-record guidance |
| Share a stack | Preflight had no progress or duplicate guard | Visible preflight; one pending create flow per host/stack within the page |
| Confirm an action | Enter on Cancel also confirmed; a second caller replaced the promise | Native button activation; competing confirmations cancel; dangerous dialogs start on Cancel |
| Receive a share link | One-time dialog survives polling | Existing URL custody retained; pending guard survives card replacement |
| Revoke a link/session | No pending view; confirmation promised immediate removal | Host/stack/id confirmation, pending view, result restored if its dialog was closed |
| Open WordPress admin | Only the clicked DOM button was guarded | Target identity guards repeated clicks across polling/navigation; pending label follows the card |

Shared dialogs have Tab wrapping and restore focus when the opener still exists.
Live-link tables have column headings and a horizontal overflow container.
Neither change has been exercised with a keyboard, screen reader or narrow screen.

## Boundaries

Pending sets contain host/stack identities only and live in page memory. They
disable both Share and Live links while creation or revocation is pending,
including replacement cards rendered by polling. The create control identifies
whether sharing or revocation is pending.
These guards
are not backend locks, cross-tab deduplication, durable job state or recovery
authority. A reload loses them. An unanswered request stays pending; the UI does
not invent a timeout, retry or cancellation protocol.

Share and revoke use the existing endpoint and exact original ids. Closing a
share preflight prevents this UI from proceeding to create. Closing a dialog
after submission does not cancel the operation. Its result is shown again when
received. One-time links remain confined to their result dialog; WordPress URLs
go only to the opened tab. API validation and backend authorization are unchanged.

Revocation can require cleanup. Partial results and original-id retry guidance
remain visible; there is no new force or lease-recovery control. Remote creation,
hosted apply and unavailable sign-in routes do not gain new UI authority.

## Remaining work and verification

The follow-up also covers local creation and dashboard jobs:

- Create drafts survive action, poll and route replacement in page memory. Native
  form submission supports Enter. Folder picking and field listeners are rebound
  after each replacement; focused text fields retain selection on forced renders.
- Create uses a conservative page-wide local mutation guard. The backend resolves
  directory aliases; the browser does not use a label or project basename as root
  identity. Instance actions, server changes, operations and terminal commands use
  the same per-instance guard. Bulk actions also respect those guards.
- Local action activity shows submission, receipt, observation, unconfirmed,
  stopped observation and terminal outcomes across routes. It retains metadata
  only, not command arguments, response output, URLs or tokens. The drawer alone
  displays output. Switching back to a live job follows subsequent output; an
  explicit later observation reads that receipt from offset zero.
- Submission timeout or transport/malformed-response failure keeps the action
  guarded. A late valid response from that same request is accepted without a
  second POST. Without a receipt there is no lookup endpoint, so the UI offers no
  replay/reset control. Supported instance observation can inform manual
  reconciliation but cannot prove which unconfirmed request ran.
- Receipt observation uses GET /api/job/<id>?offset=N only. Each pass stops after
  150 nonterminal snapshots or five consecutive failed/30-second timed-out reads.
  Stopping releases the refresh pause, not the mutation guard. Explicitly reading
  the same receipt again starts another bounded pass. A completed receipt is
  guarded while being reread, so its callback cannot clear a newer mutation.
- Snapshot validation requires a boolean done, terminal boolean ok or pending
  null, string status/chunk, and safe nondecreasing integer offset. Malformed
  data cannot produce a success or release a mutation guard.
- Partial remote inventory and failed local refresh are distinguished from empty
  inventory. The console overlays at narrow widths and has a viewport width cap,
  wrapping output, a named close button, Escape and connected-opener focus return.

These are page-local guards, not cross-tab locks. Reload discards drafts, receipts
and guards. Remote creation remains unavailable. Terminal output now uses the
same activity flow; reopen the terminal to enter the next command.

New dashboard receipts use opaque random 128-bit job ids. Both ordinary jobs and
bulk actions use the same id format; snapshots echo the requested id and the
client checks it. Job responses are no-store, and invalid observation ids or
offsets refuse. Legacy numeric receipts can still identify an existing in-memory
job, but new jobs no longer reuse the numeric sequence after a service restart.
The compatibility sequence remains available to existing tooling but is not used
to allocate new receipts.

Jobs remain process-local. A restart loses the old job record, so observing its
receipt returns unavailable and the action stays unconfirmed. There is no durable
request lookup or resolved instance identity in the response, and no durable
recovery claim. Successful create reports server completion and links back to
local instances; it never selects a result by label/basename.

Deferred browser scenarios for this follow-up:

- Type a create draft, trigger passive/forced renders, navigate away/back, use
  Back/Forward, choose a folder, and submit with Enter and rapid repeated clicks.
- Change labels/path aliases while a create is pending; attempt bulk, per-instance,
  server and terminal mutations through replacement DOM. Verify one POST.
- Exercise refusal, malformed receipt, missing receipt, timeout and late receipt;
  ensure unknown outcomes remain guarded and no automatic replay occurs.
- Close/reopen activity, switch among concurrent jobs, exhaust a bounded pass,
  lose contact, receive 404, and observe the same receipt again. Verify GET-only
  observation and guard retention. Test completed receipt reread against a new
  action. After a service restart, the old receipt must remain unavailable rather
  than selecting a newly allocated job; missing/mismatched snapshot ids refuse.
- Confirm success/failure output, empty output, long output, stale/empty/partial
  inventory, narrow widths/zoom, keyboard focus and screen-reader status updates.

After explicit user approval of the exact CAS lease recovery and the requested
deployment-first sequence, verify these browser scenarios before acceptance:

- Modified click, middle click, Back/Forward and deep links across host/stack views.
- Failed and partial hosting refresh with existing and missing stack records.
- Double-click share during preflight, confirmation and mint; close during each
  phase; navigate away/back during polling; retain and copy the once-shown link.
- Cancel via Enter and Escape; Tab/Shift+Tab in confirmations and share dialogs;
  nested confirmations, detached openers and multiple retained link dialogs.
- Revoke success, refusal and partial cleanup; close while pending; repeat only
  the original id; confirm no create/revoke overlap for the same stack in one page.
- Double WordPress click across a poll and a route change; blocked popup, closed
  popup, unavailable status, malformed/oversized refusal and success responses.
- Narrow widths, zoom, long ids, light/dark themes and assistive technology.

No tests, builds, imports, syntax checks, browser sessions, host operations or
deployment were run for this change. Deferred regression coverage must follow
the repository's execution order after the hold is lifted.

## Lifetime inventory from joined hosts

Joined `host stack list` may return a durable-job acceptance receipt instead of
the inventory itself. The panel retains that original request and job while it
is running. Later cache refreshes observe the same job; they do not submit a
replacement. Only successful terminal output with matching job/request identity,
complete bounded stdout, byte count and digest becomes a lifetime listing.
Output pages use base64 so a page boundary cannot corrupt a UTF-8 record.

Unavailable observation keeps lifetimes unknown and protected. Submission
timeout or an unusable acceptance receipt prevents automatic resubmission in
that panel process. This in-memory tracking does not reconstruct an original job
after a panel restart or establish why an older fetch failed. Local server rows
and remote-only lifetime rows remain part of the combined inventory.
