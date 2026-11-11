# Source groundwork contract

Status: implementation proposal for the disabled source deliverable. No installed capability or new server wire is advertised.

## CLI selection

Add these options to the existing registered `host` family:

| Option | Meaning |
| --- | --- |
| `--credential-scope personal-local|server` | Omission preserves personal-local behavior |
| `--credential-project ID` | Explicit server registered project reference |
| `--credential-source ID` | Server registered source reference |
| `--credential-source-revision REV` | Exact nonsecret pinned source revision |
| `--credential-profile ID` | Approved intended consumer/profile reference |
| `--credential-profile-revision REV` | Exact nonsecret pinned profile revision |

Server scope also requires the existing explicit `--remote NAME` selector. No implicit current-host selection. References are nonsecret opaque identifiers, never a path, URL, command or provider value. The declaration decoder must impose bounded closed syntax consistent with existing identifier contracts and avoid echoing arbitrary invalid input. No server-ref flags with personal-local scope; no automatic scope inference. This slice adds no client actor, uid/home, installation-id override, raw command or grant flag.

## Observable branches

| Request | Result | Forbidden calls |
| --- | --- | --- |
| Server scope, malformed/mixed selection | `server_credentials_invalid_selection`, fixed bounded diagnostic | Secret readers, remote registry resolution, network/process/write/effect boundaries |
| `host validate` with valid server declarations | `server_credentials_static_validated`, declarations valid, availability `unverified`, execution `disabled` | All key presence/value inspection, source staging, provider/lifecycle/claim/generation calls |
| `host secrets` with valid server selection | `server_credentials_inventory_unavailable`, inventory `unavailable`, availability `unverified` | Local/server credential or metadata access, remote transport, provisioning |
| Any other host action with server scope, including `host stack up --plan` and `--confirm`, direct apply/stage/image/sync paths | `server_credentials_unsupported`, execution `disabled`, availability `unverified` | Existing handlers that set up remote entries/stores, source export/worktree/staging, claims/leases, jobs, provider/key resolution/generation or effects |
| Personal-local scope, default or explicit | Existing command result and behavior | No new server reference/config requirement |

Use existing JSON envelope conventions and nonzero exit for refusal/unavailable; successful static validation exits zero. Keep the fixed code available in JSON and text. Text diagnostics explain unavailable dispatch and unverified key availability without printing untrusted field contents. Do not add raw child output to an error. CLI help and docs must say these new options are disabled groundwork. They are not present on the planning baseline.

Server static validation checks required/generated declarations and exact mapping shape through the smallest reusable pure parser. It does not use `host stack up --plan`, `_secret_status`, Cloudflare clients, durable contexts or remote plan methods. Reject inline provider value declarations without rendering their contents. A missing local provider key is irrelevant. Static success proves neither remote registration, actor admission, source availability nor provider validity.

The capability decision is unconditional for this slice. No user flag, environment variable, copied manifest, parsed context frame or matching revision can enable it. A future installed capability adapter replaces refusal only in a separately reviewed complete integration. Existing remote commands using personal-local credential scope continue unchanged. This guards the newly selected server path without disabling working behavior.

## Minimal binding contract checks

Declaration tests must prove changes to a source key, pinned source/profile revision, destination, consumer or selected server change the parsed immutable declaration, even if destination-key sets stay equal. Do not claim this as authenticated intent binding. Closed selection types reject invented actor/grant/owner-context fields. Only future server-resolved binding can authenticate those facts.
