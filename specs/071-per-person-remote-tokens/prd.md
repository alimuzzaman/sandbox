# Product Requirements Draft: Per-Person Tokens for the Remote Control Service

**Status**: Refined

**Created**: 2026-11-09

**Last Refined**: 2026-11-09

**Input**: "Per-person bearer tokens for the Sandbox remote control service. Anyone with SSH access can create one; token holders have no SSH or passthrough; a token may do everything except SSH."

**Drafting Configuration**: Drafted in the main working session with read-only repository fact-finding helpers.

**Final Validation**: `REOPEN` — independent readiness review; findings not yet applied. See `docs/research/per-person-tokens-handoff.md`.

**Validated On**: 2026-11-09

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

The Sandbox remote control service, published at `https://sandbox.xspeedcdn.online`
on remote `xspeed-dev`, accepts one shared bearer token. Everyone who needs the
service holds the same secret, so nobody can tell who did what, and removing one
person means rotating the secret for every controller. The owner wants to give
individual people access to the service without giving them SSH access to the
server.

## Users and Desired Outcomes

- **Operator** (anyone with SSH access to the server): gives a named person access, sees who holds access, and removes one person's access without disturbing anyone else.
- **Token holder** (a person with a token and no SSH access): connects an MCP client to the service and uses it.
- **Owner**: can attribute activity to a person and knows exactly what a token holder can and cannot do.
- **Existing controllers and live remotes** (`xspeed-dev`, `xcloud-london`): carry on working with no change.

## Goals

- Operators can create, list, and revoke per-person tokens, both on the server and from a controller over SSH.
- Only a hash of each token is kept on the server; the raw token is shown exactly once.
- Every route the shared token protects today accepts a valid per-person token, and a change to the token set takes effect with no service restart.
- A token never yields an SSH session, tunnel, host passthrough, or SSH credentials through a purpose-built feature, and the remaining indirect paths are found and reported.
- Requests, and where cheap jobs and actions, can be attributed to a person's label.
- The shared token and services with no per-person tokens behave exactly as they do today.
- The flows that still need SSH are listed so the owner knows what token holders can really do.

## Non-Goals

- HTTPS source upload for `deploy` and job flows. The owner did not choose it.
- Scoped or role-limited tokens. The owner chose everything except SSH.
- Sandboxing `job_start`, `instance_exec`, or any other tool to stop a token holder reaching the host.
- Email lists, identity providers, OAuth, or self-service sign-up. A person's name is a free-text label.
- Token creation over the HTTP service. Creation requires SSH access.
- Giving token holders a working `sb` CLI on their own machine; they use an MCP client.
- Rotating, replacing, or retiring the shared bearer token.
- Updating any server as part of this work.

## Product Scenarios

### Scenario 1 — Operator creates a token on the server

- **Starting state**: An operator has an SSH session on the server.
- **User action**: Runs the create command with a person's name and, optionally, an expiry.
- **Expected outcome**: The raw token is displayed once, the server stores only its hash with the label and created time, and the token works immediately with no restart.

### Scenario 2 — Operator creates a token from a controller

- **Starting state**: An operator's machine has the remote registered with SSH access.
- **User action**: Runs the create command naming that remote.
- **Expected outcome**: Same result as Scenario 1. The raw token appears once on the operator's terminal and is written to no file on either machine.

### Scenario 3 — Token holder connects

- **Starting state**: A person has a token and no SSH access.
- **User action**: Configures an MCP client with the service URL and token, following the documentation.
- **Expected outcome**: The client connects and can use the tools the service exposes.

### Scenario 4 — Revocation

- **Starting state**: A token is in use.
- **User action**: An operator revokes it.
- **Expected outcome**: The next request with that token gets the same `401` as an unknown token, with no restart. Other tokens and the shared token still work.

### Scenario 5 — Expiry

- **Starting state**: A token was created with an expiry that has passed.
- **User action**: The holder makes a request.
- **Expected outcome**: The same `401` as an unknown token.

### Scenario 6 — Listing

- **Starting state**: Tokens exist in several states.
- **User action**: An operator lists them.
- **Expected outcome**: Each token's identifier, label, created time, expiry, and state appear. No raw token or hash value is shown.

### Scenario 7 — Existing shared token

- **Starting state**: A controller holds the shared token and the server may or may not have any per-person tokens.
- **User action**: The controller uses the service.
- **Expected outcome**: Unchanged behaviour.

### Scenario 8 — Attribution

- **Starting state**: Two people hold tokens.
- **User action**: Each makes requests and starts a job.
- **Expected outcome**: The service log shows each request's label with no token value, and a started job can be tied to the label that started it.

### Scenario 9 — Damaged or unsafe token store (negative)

- **Starting state**: The token store is corrupt, has loose permissions, is a symlink, or belongs to another user.
- **User action**: A holder presents a once-valid token; a controller presents the shared token.
- **Expected outcome**: The per-person token is refused with the same `401`, the shared token still works, and the operator can see why from the service log with no secret in it.

### Scenario 10 — Remote not yet updated (negative)

- **Starting state**: A controller has this feature and the remote's installed Sandbox does not.
- **User action**: The operator runs the create command against that remote.
- **Expected outcome**: A clear refusal that names the need to update the remote; no token is reported as created.

### Scenario 11 — Malformed credentials (negative)

- **Starting state**: A client sends a wrong scheme, an empty value, or non-ASCII bytes in the header.
- **User action**: Any covered route is requested.
- **Expected outcome**: The same `401`, never a server error.

## Proposed Product Behavior

- Anyone with SSH access to the server can create, list, and revoke tokens. No other way of minting a token exists on the service.
- A token is valid when it is known, not revoked, and not expired. Every other outcome is one indistinguishable `401`.
- Token checks compare in constant time and do not stop at the first match.
- A person's name is free text within a bounded length and may not contain control characters. Two tokens may share a label; each has its own identifier, and revocation targets the identifier.
- Revoked tokens remain listed as revoked rather than disappearing, so the record of who had access survives.
- Attribution distinguishes per-person tokens from the shared token in a way a chosen label cannot forge.
- Expiry is optional; a token with no expiry lasts until revoked.

## Constraints and Dependencies

- This is an authentication change on a service reachable from the public internet. It stops for the owner's security review before any merge, pull request, or server update.
- No server is changed by this work. No live proof can be run, and the handback must say so.
- The token store lives under Sandbox's own home, is readable and writable only by its owner, and is separate from the `personal` secrets file, which must never be touched.
- Raw tokens never reach disk, state, or logs.
- Controllers and the two live remotes must not break, and a service with no token store behaves as today.
- The server must carry a Sandbox revision with this feature before tokens work there.
- Areas owned by other active work stay untouched: `sandbox/hosting/destroy.py`, `zone_coverage/`, `sandbox/jobs/registry.py`, `sandbox/application/job_service.py`, and autologin code in `sandbox/commands/hosting.py` and `sandbox/core/_hosting.py`. Also `hosting/local_images.py:164`, `core/_hosting.py:815-820`, and `commands/ui_dash.py`, which await separate human security review.
- Source upload for `deploy` and some job flows is SSH-only today, which limits what a token holder can do.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Who can create tokens | Anyone with SSH access to the server | Owner's statement | User |
| Token holders' SSH | None: no SSH session, tunnel, passthrough, or credentials | Owner's statement | User |
| What a token may do | Everything except SSH, including `job_start` and `instance_exec`, after being told holders could likely reach root on the server | Owner chose this knowingly; recorded plainly in docs and the security-review list | User |
| Identity | Free-text label; no email list | Owner's statement | User |
| HTTPS source upload | Not built | Owner did not choose it | User |
| Expiry | Optional, per token | Requested behaviour | User |
| Shared token | Keeps working unchanged | Requested behaviour | User |
| Revoked records | Kept and shown as revoked | Requested stored state includes revoked state | User |
| Unsafe store | Per-person tokens refused; shared token unaffected | Fails closed without locking operators out of a working control plane | Assumption, listed for security review |

## Open Questions

- None.

## Acceptance Outcomes

- A newly created token authenticates on all covered routes within one request of creation, with no restart.
- A revoked or expired token and an unknown token produce responses that are identical in status and body on every covered route.
- After any sequence of create, list, and revoke, no file written by the feature contains a raw token, and the store is mode `0600`.
- The `personal` secrets file is unchanged by every token operation.
- The raw token appears in exactly one command output: the create that made it.
- With no token store, the service accepts the shared token, rejects everything else, and needs no configuration change.
- A malformed credential produces a `401` on every covered route, never a `5xx`.
- Each authenticated request produces one log record naming its label or the shared token, with no token value.
- The owner receives a ranked list of every path from a token to host access, and a list of every `sb` and MCP flow that does not work without SSH.
- Documentation tells a token holder how to connect an MCP client and states plainly what a token may do.

## Risks and Assumptions

- **Risk**: The owner's chosen scope leaves `job_start` and `instance_exec` open, so a token holder can likely reach the host as the service user and possibly root, and from there the SSH material and the token store. "No SSH" holds for purpose-built features, not against a determined holder. This is accepted by the owner and goes on the security-review list.
- **Risk**: With no live proof, behaviour on real servers is unverified until the owner reviews and updates a server.
- **Risk**: A holder who can write to the token store through host access can mint their own tokens.
- **Risk**: The job ledger is owned by concurrent work, so job attribution may sit beside the ledger rather than in it.
- **Assumption**: The SSH user who creates tokens is the same user the service runs as, so both see the same Sandbox home.
- **Assumption**: Tokens are long random values, so an unsalted fast hash resists offline guessing.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent readiness review verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
