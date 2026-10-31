# Safe secret inspection

Sandbox's secret broker lets agents answer common credential questions without
opening an entire secret file. Its default output is key names or safe
structured selectors only. Higher
disclosure is explicit and ordered: metadata, shape validation, a fixed mask,
bounded use, protected update, then a human-only single-secret reveal.

This is a least-disclosure guardrail, not an operating-system security boundary.
An agent running as the same OS identity may still have filesystem access to a
registered source; repository and host policy must continue to prohibit raw
reads.

## Register project sources

Project sources, formats, and MCP permissions are explicit in
`sandbox.config.json` (or the equivalent YAML/config override layer). No source
is discovered by scanning the project, and callers cannot supply an arbitrary
path.

```jsonc
{
  "secrets": {
    "sources": {
      "project-env": {
        "path": ".env.local",
        "mcpModes": ["source_info", "keys", "metadata", "validate", "masked", "use"]
      },
      "gcp-credentials": {
        "path": "config/gcp-credentials.json",
        "format": "json",
        "mcpModes": ["source_info", "keys", "metadata"]
      }
    },
    "useProfiles": {
      "provider-status": {
        "source": "project-env",
        "key": "API_TOKEN",
        "argv": ["trusted-provider-cli", "status"],
        "destination": "API_TOKEN",
        "timeoutSeconds": 30,
        "maxOutputBytes": 65536,
        "mcp": true
      }
    }
  }
}
```

The example contains identifiers only, not a credential. Source aliases and
profile names are lowercase safe slugs. A project source path must be relative,
contained by the project root, and have a suffix or protected basename matching
its explicit format. Omit `format` only for backward-compatible dotenv sources.
The file must be regular,
owner-controlled, and inaccessible to group and other users. The built-in
`personal` source cannot be overridden by project config and grants no MCP mode
by default.

### Infisical sources over Tailscale

An opt-in `type: "infisical"` source uses an operator-provided community
self-hosted Infisical service as the shared source of truth:

```jsonc
{
  "secrets": {
    "sources": {
      "deploy-api": {
        "type": "infisical",
        "endpoint": "https://infisical.private.example",
        "projectId": "project-id-placeholder",
        "environment": "dev",
        "path": "/",
        "key": "API_TOKEN",
        "clientId": "client-id-placeholder",
        "timeoutSeconds": 10,
        "mcpModes": ["source_info", "keys", "metadata", "validate", "use"]
      }
    }
  }
}
```

These are placeholders and non-secret metadata only. Registration has no field
for a client secret, access token, or item value; unknown fields fail closed.
Do not disguise secret material as an endpoint, project ID, environment, path,
key, client ID, label, or query attribute. A same-user operator can edit the
authoritative config or bypass Sandbox and remains inside that trust boundary.

Provision one Infisical identity with the built-in Viewer role per Sandbox
project. The provider role covers the whole provider project; the narrower
environment/path/key restriction is Sandbox selection policy, not provider
RBAC. Give Linux and macOS distinct Universal Auth client secrets so either
controller can be revoked independently. Store each through the protected local
credential action documented by `./sb secrets --help`: macOS uses the approved
Keychain backend, while Linux requires an already-running, unlocked user Secret
Service. Sandbox never discovers alternate keyring backends, starts a daemon,
unlocks a store, copies credentials, or administers Infisical identities/roles.

Place the controller-local bootstrap credential through a hidden controlling-TTY
prompt. Intent is mandatory so an existing credential is never silently replaced:

```bash
./sb secrets credential-set --source deploy-api --create-only \
  --project-dir PROJECT_DIR
```

Use `--replace-only` for an intentional replacement. A trusted producer may use
`--stdin`, subject to the same single-line 64 KiB bound; never pass the value in
argv or an environment variable. Consume the registered value only through its
fixed profile:

```bash
./sb secrets use-profile --profile PROFILE_NAME --project-dir PROJECT_DIR
```

Credential placement is also the operator's approval receipt. Its version-2
locator binds the canonical project root and endpoint, complete exact selector, `mcpModes`, and
all fixed profiles that consume the source. Any change to that authority makes
the prior item unreachable and returns `credential_missing` until the operator
reviews the new configuration and places the credential again. Sandbox never
silently reads or migrates the earlier version-1 item. After re-approval,
rotate the Infisical client secret and place the replacement under the current
authority. An operator should then remove obsolete Sandbox items through the
approved native credential-store UI; agents must not list, export, or delete
keyring items with raw commands.

The endpoint must be operator-provided over the Tailscale/private route with a
certificate trusted by the OS. Sandbox verifies HTTPS, refuses redirects,
ignores ambient proxy settings, and never tries another or public endpoint.
Tailscale reachability alone grants nothing: the local credential and Infisical
role are still required. Deployment, upgrades, backup, restore, migration,
rotation, and disaster recovery remain operator responsibilities.

For provider sources, `keys` returns only the configured singleton name without
network access. Source status may probe availability but never logs in or reads
a value. Because macOS Keychain has no value-free existence query, macOS reports
credential presence as `unknown` until an authorized operation uses it. Metadata,
validation, and fixed-profile use perform one bounded login
and one exact item read with reference expansion/imports disabled; the access
token is not cached across operations. Provider OS-store reads/status, DNS, and HTTPS
exchange share one monotonic deadline in a disposable helper process; timeout
ends its whole process group. Provider values containing display-changing
control or format characters are refused before they become redaction needles.
Provider sources refuse mask, reveal,
arbitrary run/session, set, organize, copy, generation, list, search, wildcard,
export, write, delete, rotate, migration, and identity administration.

Enabling the MCP secrets tool group does not grant source access. Each source
must separately grant the required `mcpModes`; a use profile must also set
`mcp: true`. Keep the grant list minimal. The secrets group is absent from all
default MCP catalogs.

Supported registered formats are:

| Format | Typical documented sources | Inspection behavior |
|---|---|---|
| `dotenv` | `.env*`, Sandbox personal assignments | Names, scalar metadata, eligible fixed mask, validation, bounded use, targeted update |
| `json` | GCP credentials, Docker config, Terraform credentials, Composer auth | RFC 6901-style leaf selectors; no mask, exact length, or update |
| `ini` | AWS credentials, `.pypirc`, OCI config | `/section/key` selectors; no interpolation |
| `properties` | `.npmrc`-style flat configuration | Escaped `/key` selectors; continuations rejected |
| `toml` | Cargo credentials | Structured leaf selectors |
| `yaml` | kubeconfig and similar credentials | Safe scalar leaves; aliases, anchors, explicit tags, and duplicates rejected |
| `xml` | Maven settings and NuGet config | Element, attribute, and `#text` selectors; DTDs and entities rejected |
| `pem` | GitHub App, Azure service-principal, TLS/private-key bundles | Block labels only; material remains protected |
| `opaque` | OIDC/JWT/token files | One `/value` selector; scalar policy still applies |
| `binary` | PKCS#12/PFX, JKS/keystore containers | One `/file` selector and bucketed file metadata only |

Structured selectors use JSON Pointer escaping: `/` inside a source key becomes
`~1`, and `~` becomes `~0`. XML uses `@name` for attributes and `#text` for text
nodes; repeated sibling names receive a numeric segment. Selectors identify a
field without returning its value.

All parsers are bounded and inert. They never run interpolation, command
substitution, includes, executable tags, XML entities, shell evaluation, or
credential-source commands. Duplicate keys, unsupported active syntax, unsafe
links or permissions, terminal controls, oversized sources, excessive depth,
and changes during an operation fail closed without returning a source line.

Failures expose only a stable broker code and bounded public message. Unknown
parser, filesystem, subprocess, or future backend failures become
`operation_failed`; their message, stdout, stderr, source buffer, exception
attributes, cause, context, and traceback are neither returned to CLI/MCP nor
written to the secret audit. Debug mode must not weaken this boundary. Report
the operation, source alias, key name, correlation ID when available, and stable
code—never copy a raw exception or retry by opening the source.

## Lowest-disclosure workflow

Every example uses placeholders. Replace only aliases, key names, profile names,
paths, destinations, and trusted program arguments. Never place a secret value
in an example or command.

### 0. Check whether the registered source is usable

```bash
./sb secrets source-info --source SOURCE_ALIAS --project-dir PROJECT_DIR
```

This operation uses filesystem metadata and an open-without-read safety check.
It does not parse the source, issue a read syscall, follow symlinks, return the
registered path, or expose file bytes. The result contains:

- `exists`: `true`, `false`, or `null` when the operating system will not even
  disclose existence;
- `file_type`: `regular_file`, `missing`, `directory`, `symlink`, another
  special-file class, or `unknown`;
- `content_state`: `empty`, `nonempty`, `not_applicable`, or `unknown`;
- `size_bucket` for regular files, configured `format`, and source `scope`;
- `broker_readable` and `safety` (`safe`, `missing`, `unsafe`, `inaccessible`,
  `changed`, or `too_large`).

The default omits exact byte length because it can fingerprint a short or
provider-specific credential file. A local CLI operator may request it only
when necessary:

```bash
./sb secrets source-info --source SOURCE_ALIAS --exact-size \
  --project-dir PROJECT_DIR --json
```

MCP exposes `secret_source_info` only when that source explicitly grants
`source_info`. MCP never accepts an exact-size option. A missing or unsafe file
is a useful result; do not bypass it by opening the path directly. Fix the
registered source, owner-only permissions, or file type and repeat this probe.

### 1. List key names

```bash
./sb secrets inspect --source SOURCE_ALIAS --project-dir PROJECT_DIR
```

Default inspection returns bounded eligible key names and safe source identity.
It does not return source paths or values. Select one key before continuing.

### 2. Inspect one key's safe state

```bash
./sb secrets inspect --source SOURCE_ALIAS --key SECRET_KEY \
  --mode metadata --project-dir PROJECT_DIR
```

Metadata distinguishes states such as missing, empty, present, multiline,
structured, binary, or unsupported. Length is bucketed by default.
`--exact-length` is available only for eligible dotenv or opaque scalar values
with one selected key. Structured, PEM, and binary formats deny it even when a
caller requests it.

### 3. Validate a reviewed shape

```bash
./sb secrets validate --source SOURCE_ALIAS --key SECRET_KEY \
  --profile PROFILE_NAME --project-dir PROJECT_DIR
```

The broker evaluates reviewed checks internally and returns only `pass`, `fail`,
or `not_applicable`, with `live_checked=false`. Shape validation can answer
questions about documented public prefix class, length, character set, segment
structure, parseability, or embedded expiry. It does not contact the provider or
prove that the credential is active, authorized, or accepted.

### 4. Request the fixed mask only when identification requires it

```bash
./sb secrets inspect --source SOURCE_ALIAS --key SECRET_KEY \
  --mode masked --project-dir PROJECT_DIR
```

Mask output has a fixed disclosure budget:

- Recognized eligible opaque tokens: reviewed public provider/type prefix,
  `<redacted>`, and the final four characters.
- Eligible unrecognized opaque tokens: `<redacted>` and the final four
  characters.
- Protected or ineligible values: no character preview.

Structured-source values never expose a mask. For dotenv and opaque sources,
protected values include passwords, values shorter than 24 characters,
low-variety values, credential-bearing URLs and connection strings, JWTs,
structured documents, multiline values, PEM/private keys, certificates, and
binary material. Callers cannot choose prefix or suffix lengths, offsets,
templates, regular expressions, or guesses. Repeating a request does not expand
the result. A mask is an identifier, not proof that a provider will accept the
secret.

### 5. Prefer bounded use over reveal

When a trusted program needs the credential, let Sandbox deliver exactly one
selected value to one child process:

```bash
./sb secrets run --source SOURCE_ALIAS --key SECRET_KEY \
  --destination DESTINATION_NAME --timeout-seconds 30 \
  --project-dir PROJECT_DIR -- trusted-program status
```

`secrets run` executes direct argv after `--`; it does not start an implicit
shell, substitute the secret into argv, export into the parent, or inherit all
registered secrets. The child starts from a reviewed minimal environment plus
the selected destination. The default timeout is five minutes; callers may
choose from one second to 30 minutes (only operator session mode, section 5b,
runs longer). Combined redacted output is bounded to
1 MiB and reports truncation. A child that exits unsuccessfully makes
`sb secrets run` exit nonzero after the bounded result is printed; the child
exit code is preserved when it is safe for the shell to represent it.

A command that is `sudo`, `sudo-rs`, `sudoedit`, `doas`, `su`, `pkexec`,
`run0`, `systemd-run`, or `machinectl`—directly, through a supported exec wrapper, by a renamed symlink, or as
a set-id executable—is refused with `escalation_unsupported` before any secret
is read. Those tools may drop the environment, and `sb` could not reliably stop
a privileged child at its timeout. Shell/interpreter command strings are not
parsed; do not hide an escalation inside `sh -c`, a script, or similar code.

For paired credentials, repeat `--secret KEY=DEST` so both brokered values enter
one child process, for example:

```bash
./sb secrets run --source SOURCE_ALIAS \
  --secret ACCESS_KEY=AWS_ACCESS_KEY_ID \
  --secret ACCESS_SECRET=AWS_SECRET_ACCESS_KEY \
  --project-dir PROJECT_DIR -- trusted-program status
```

Do not nest `secrets run`; each destination must be unique and pass the same
safe-environment policy.

Before using it:

1. Confirm the executable and every fixed argument are trusted and necessary.
2. Confirm the destination is the documented credential variable for that
   program. Loader, interpreter, runtime, shell, prompt, and credential-helper
   control variables are denied.
3. Turn off shell tracing, debug/diagnostic dumps, verbose HTTP output, crash
   reporting, request-body logging, and environment serialization.
4. Do not invoke `env`, `printenv`, `set`, a shell interpreter, or an unreviewed
   command capable of uploading files or environment state.
5. Verify the operation through non-secret evidence: exit status, a bounded
   health/status response, or an already-public resource identity.

On success, failure, and timeout, Sandbox ends the command's entire process
group. Profiles that intentionally detach a background daemon are unsupported;
start such software through a separately reviewed supervisor that does not
retain the brokered environment.

The trusted child is an intentional secret recipient. It can print, transform,
persist, pass to descendants, or exfiltrate the value. Sandbox registers
exact-match output redaction before launch, but transformed, encoded, fragmented,
or remotely logged forms may escape redaction. Redaction is defense in depth,
not permission to run an untrusted child.

For MCP, arbitrary commands are prohibited. `secret_source_info` performs only
the metadata-only file probe above. `secret_use_profile` accepts only a
registered reviewed profile whose source, key, direct argv, destination, output
budget, and timeout are fixed in configuration. `secret_inspect` and
`secret_validate` also require explicit source-mode authorization. No MCP tool
returns plaintext or accepts a candidate secret.

### 5b. Operator session for a long-running child

Some children have to keep running with their secret, for example a dev server
for a working day. Ordinary `secrets run` stops them after 30 minutes at most.
Session mode (spec 059) is the one exception, and it is operator-only:

```text
sb secrets run --session [--lifetime-seconds N]
               --source ALIAS (--key KEY [--destination NAME] | --secret KEY=DEST ...)
               [--project-dir DIR] -- ARGV...
```

- The lifetime is whole seconds from 1 to 43200 (12 hours). The default is
  28800 (8 hours). There is no unbounded value. `--timeout-seconds` is refused
  with `--session` and `--lifetime-seconds` is refused without it
  (`option_conflict`); a malformed or out-of-range lifetime is
  `lifetime_invalid`.
- Bindings, the destination deny list, direct argv with no shell, the minimal
  child environment and closed standard input are the same as ordinary `run`.
- Standard output must be a terminal, `/dev/tty` must open as a terminal, and
  `sb` must be that terminal's foreground job. Otherwise the start is refused
  with `tty_required`. This keeps CI, MCP, durable jobs and captured remote
  paths out. The wrapper-aware privileged/set-id command refusal is the same as
  ordinary `run`. It happens before any secret is read.
- A start line shows the source, key names, the lifetime and the local end
  time. Redacted output is shown live; an end line shows the end reason, the
  child's exit status, an elapsed-time class and the number of dropped chunks.

The session ends on the first of these:

| End reason | Cause | `sb` exit status |
|------------|-------|------------------|
| `child_exited` | the child exited by itself | the child's status, mapped as for `run` (0 stays 0, 1 to 125 pass through, anything else is 1 with `child_failed`) |
| `lifetime_expired` | the lifetime ran out | 0 |
| `interrupted` | Ctrl-C, or any other catchable termination signal to `sb` (`SIGTERM`, `SIGQUIT`, `SIGUSR1`, `SIGUSR2`, `SIGALRM`, `SIGVTALRM`, `SIGPROF`, `SIGXCPU`) | 130 |
| `hangup` | the terminal closed (`SIGHUP`) or a write to it failed | 129 |

On every end, `sb` sends `SIGTERM` to the child's whole process group, then
`SIGKILL` to anything left after 2 seconds; the group is gone within 4
seconds. A crashed child is never restarted.

Behavior to know:

- Ctrl-Z is ignored for the whole session, so `sb` never stops enforcing the
  lifetime. Run the session in its own terminal tab.
- The lifetime is wall-clock time. Time the machine spends asleep counts; a
  session whose end time passed during sleep ends with `lifetime_expired` on
  wake.
- Text after the last whitespace may be held until the line completes or the
  session ends, because it could be the start of a secret. There is no
  time-based flush.
- Complete SGR colour and style sequences (`ESC [ ... m`) are kept. Every other
  control character and escape sequence (cursor movement, screen clearing,
  window titles, malformed or incomplete SGR) is removed whole. Colour is
  dropped, and a reset added, on any span where redaction changed text, so
  colour cannot split a value away from redaction. Ordinary `run` output is
  unchanged.
- Nothing from the child is retained. The live stream has no 1 MiB bound; only
  the end line and the audit outcome remain.

Risks that remain:

- The child is an intentional secret recipient, exactly as in ordinary `run`.
  It can print, transform, persist or send the value elsewhere, and redaction
  is defense in depth.
- `SIGKILL` or `SIGSTOP` sent to `sb` cannot be handled. A broker killed that
  way leaves its child running with the secret until something else ends it.
- A child that escalates privileges inside an opaque shell/interpreter string
  or script puts the privileged part out of `sb`'s reach: the polite request
  is relayed by `sudo`, but nothing can force it. When that happens the end
  line is followed by a warning and the audited result records
  `group_ended=false`; check for the leftover process yourself. Direct,
  supported-wrapper, symlinked, and set-id forms are refused up front.
- An agent harness that allocates a pseudo-terminal passes the terminal check.
  The skill tells agents never to start a session; the check is not an
  exclusive security boundary.

### 6. Update one assignment without reading the source

The safest local input is a hidden controlling-TTY prompt:

```bash
./sb secrets set --source SOURCE_ALIAS SECRET_KEY --replace-only \
  --profile PROFILE_NAME --project-dir PROJECT_DIR
```

The prompt opens the controlling terminal through a descriptor-backed stream,
so it works with nonseekable terminals while `getpass` suppresses input echo.
The terminal must still be available to the command.

Use `--create-only` when absence is required, or omit both create/replace flags
only when either intent is acceptable. When an inspection supplied an opaque
revision, add `--if-revision OPAQUE_REVISION` to refuse a concurrent change.

Protected standard input is available when a trusted producer can supply the
value without logging it:

```bash
approved-secret-producer | ./sb secrets set --source SOURCE_ALIAS SECRET_KEY \
  --stdin --replace-only --project-dir PROJECT_DIR
```

The broker accepts at most 64 KiB, removes one final line ending, and rejects an
empty, multiline, NUL-containing, or otherwise unsupported value without
echoing it. The producer must not expose its output in history, logs, or its own
diagnostics.

Broker-side copy and reviewed generation avoid a caller-visible value entirely:

```bash
./sb secrets set --source SOURCE_ALIAS SECRET_KEY \
  --from-ref OTHER_SOURCE_ALIAS:OTHER_SECRET_KEY \
  --replace-only --project-dir PROJECT_DIR

./sb secrets set --source SOURCE_ALIAS SECRET_KEY \
  --generate PROFILE_NAME --create-only --project-dir PROJECT_DIR
```

V1 ships the reviewed generator profile `random-base64url-32-v1`; unknown
generation profiles fail closed.

V1 writes one single-line dotenv assignment. It refuses structured, multiline,
binary, private-key, certificate, and PEM updates. A successful replacement
preserves unrelated assignments, comments, ordering, newline style, ownership,
and restrictive permissions and returns only the target identifiers, status,
validation result, and opaque revision.

Never use a plaintext command argument, `KEY=value`, a parent environment
variable, an arbitrary input file, a shell command string, or an ordinary MCP
field for the candidate. Do not verify by opening the source afterward; use
metadata, validation, or a bounded trusted child.

### 6b. Group one dotenv source into documented sections

```bash
./sb secrets organize --source SOURCE_ALIAS --project-dir PROJECT_DIR
./sb secrets organize --source SOURCE_ALIAS --apply --project-dir PROJECT_DIR
```

`--source` defaults to `personal`. Without `--apply` the command reports only;
with it, the source is rewritten under the same lock, signature check, ownership,
and permissions as a targeted update. Pass `--if-revision OPAQUE_REVISION` to
refuse a write when the source changed since a prior inspection.

Organization moves the raw assignment records produced by the parser and emits
generated banner comments around them. No value is decoded into the output, and
a rewrite is refused unless reparsing the rendering yields the identical set of
assignment lines. Comment lines directly above an assignment travel with it;
detached comment blocks are filed by the first known key name they mention, or
into `Notes`. Rerunning is stable: previously generated banners are recognized
and replaced rather than nested.

Groups are declared in `sandbox/secrets/organizer.py` by exact key name and key
prefix, ordered by owner. Exact matches win over prefixes and the longest prefix
wins among prefixes. A key matching no group keeps its content and is reported
under `Ungrouped`; add a group entry rather than renaming the key. The result
contains group titles, key names, and counts only.

Mixed newline styles, unsupported syntax, duplicate keys, and non-dotenv formats
fail closed (`mixed_newlines`, `syntax_unsupported`, `organize_unsupported`).
There is no MCP form: organization is local CLI only.

### 7. Reveal one key only as a human exception

Agents must not execute reveal. The human operator must leave every
agent-captured terminal and perform this locally:

```bash
./sb secrets reveal --source SOURCE_ALIAS --key SECRET_KEY \
  --project-dir PROJECT_DIR
```

Before doing so, ensure the terminal is not being captured by an agent tool,
durable job, transcript, screen recording/share, accessibility tool, or command
logger. The command shows a prominent warning with the source alias and key and
requires the operator to retype that exact key name. Confirmation is fresh for
one request and cannot be cached.

After confirmation, the command writes only the selected value directly to the
controlling TTY and keeps stdout empty. It has no JSON, pipe, redirection,
wildcard, batch, noninteractive, `--yes`, automatic clipboard, or MCP form.
Never paste the displayed value into chat, a tool argument, an issue, comment,
log, screenshot, or durable file. Close or clear the exposure when finished and
rotate if capture is possible.

## Unsafe actions

Do not work around the broker with any of these patterns:

```text
UNSAFE: opening, printing, searching, sourcing, or evaluating a secret file
UNSAFE: putting a candidate value in argv or a KEY=value command argument
UNSAFE: exporting a secret into the parent shell
UNSAFE: asking a human to paste a secret into chat or an MCP/tool field
UNSAFE: running env, printenv, set, shell tracing, or verbose HTTP diagnostics
UNSAFE: serializing the environment or sending it to an unreviewed destination
UNSAFE: repeating or varying masks to accumulate characters
UNSAFE: running reveal in an agent-controlled terminal or redirecting its output
UNSAFE: storing plaintext in scratch files, task notes, comments, or test output
```

A broker refusal is not permission to fall back to a raw file read. Fix the
registration, ownership/mode, source syntax, audit availability, key selection,
validation profile, destination, revision, or TTY condition without handling
the value.

## Audit behavior

Every attempt records owner-only audit intent before secret processing and a
non-secret outcome afterward. If intent cannot be durably recorded, the
operation fails before reading a value, launching a child, displaying, or
mutating. Audit records may contain source aliases and key names, so treat them
as security-sensitive.

Audit data never contains values, fixed masks, exact lengths, hashes of values,
source excerpts, candidate input, child output, provider bodies, or temporary
file content. If outcome recording fails after an operation, the result reports
that failure without embedding secret data.

Session mode records operation `use_session` instead of `use`. Its intent is
written before the source read, and its outcome is `decision=succeeded` with
`reason_code` set to the end reason (`lifetime_expired`, `interrupted`,
`hangup` or `child_exited`), or the refusal code. No audit field was added.

## Incident response

If plaintext is displayed, logged, captured, persisted, or sent somewhere
unintended:

1. Stop the consuming command and stop using the affected credential.
2. Do not repeat, quote, copy, summarize, or conceal the value.
3. Report privately through the approved security channel using only source
   alias, key name, time, surface, and affected destination.
4. Revoke or rotate through the provider or approved operator workflow. Assume
   terminal scrollback, transcripts, recordings, logs, screenshots, child
   processes, and remote diagnostics may retain it.
5. Remove or restrict captured artifacts only through an authorized retention
   process; preserve required incident evidence without reproducing the value.
6. Update the registered source through hidden input, protected stdin,
   registered reference, or reviewed generation.
7. Verify replacement with metadata, shape validation, or bounded use. Do not
   reveal again merely to compare values.

## Limitations

- V1 covers the built-in personal assignment source, opt-in exact-item
  Infisical sources, and explicitly registered
  dotenv, JSON, INI, properties, TOML, YAML, XML, PEM, opaque-token, and binary
  project sources. It never scans for files or auto-detects a format from
  content and does not enumerate the process environment. Infisical is the
  only external provider adapter; it does not expose general vault browsing.
- Targeted updates currently remain dotenv-only. Structured round-trip writers
  require separate syntax-preservation and atomicity evidence.
- Shape validation is offline and always reports `live_checked=false`.
- Exact length and fixed masks remain deliberate metadata disclosure.
- The same OS identity may retain direct filesystem access.
- A trusted child and its descendants can disclose the delivered value.
- Exact-match redaction cannot guarantee transformed-output removal.
- Ordinary `secrets run` is limited to 30 minutes. Only operator session mode
  (section 5b) runs longer, up to 12 hours, and a session child outlives a
  broker killed with `SIGKILL`.
- Atomic update may leave owner-only plaintext temporary remnants after a host
  crash; cleanup is best effort.
- V1 does not promise perfect in-memory zeroization.
- SOPS, dotenvx, Gitleaks, and detect-secrets are not currently broker
  dependencies. SOPS is the preferred candidate for a separately reviewed
  encrypted-file backend; its plaintext-output commands must never be exposed
  directly to agents.

For the concise agent decision tree, load the `secret-inspection` skill with
`./sb skill show secret-inspection`.

## Ownership profiles and create-only migration

<a id="ownership-profiles-and-create-only-migration"></a>

Secret ownership profiles establish an explicit, metadata-only mapping from
local secret sources into remote provider vaults (such as Infisical) under strict
`office` or `personal` domain isolation.

There is no blind retry, overwrite, update, destination deletion, list, search,
export, or administration operation. A foreign controller must resume from
its own journal; controller-qualified approval and run IDs provide owner guidance
but grant no authority. Missing foreign journals are never reconstructed from
values or silently adopted.

### Core principles and trust boundary

1. **Explicit human cleanup**:
   Cleanup removes only source keys whose mapped names are journaled as
   `verified` or `reconciled`. Durable per-name cleanup progress (`cleanup.json`)
   tracks `prepared` intent before atomic replacement (recording the staged file's
   device and inode alongside expected remaining key names) and checkpoints
   `removed` state after replacement and directory fsync. On crash recovery under
   a fresh human approval with an unchanged map, matching staged inode metadata
   safely promotes prepared removals without re-executing or adopting untracked
   removals; unexpected missing keys remain refused. Every remaining key receives
   a fresh read-grant RAM comparison immediately before its continuity-guarded
   atomic removal. A changed, missing, pending, or unverifiable destination leaves
   that key in the source and stops cleanup. A stale approval or any initially
   ineligible name causes zero source mutation. The final comparison is an
   observation, not a transaction across systems; the operator must keep the
   destinations quiescent. Migration never deletes a source key by default.

2. **Writer atomicity and continuity semantics (R10, R11)**:
   - In `writer.remove_keys`, atomic replacement writes the staged file to a temporary
     descriptor (`temporary_fd`) and captures `staged_continuity` via `os.fstat(temporary_fd)`.
   - The `before_replace` callback verifies that current continuities across all
     approved sources match expected continuities across previous removals.
   - **Deadline check after final observation (R10)**: Fresh current-source nofollow
     metadata is recaptured after the callback and validated against expected continuity.
     Immediately after this final validation and immediately before `os.replace`, the
     effective deadline is rechecked. If authority has expired, `approval_expired` is
     raised with zero replacement and zero source modification.
   - **Post-rename baseline derivation (R11)**: Following `os.replace`, `renamed`
     continuity is captured from the open staged descriptor. Permitted continuity is
     derived strictly from `staged_continuity`, allowing only legitimate `ctime` changes
     while validating all other metadata against the staged write.
   - **Directory fsync and drift refusal (R11)**: The `renamed` observation establishes the
     verified baseline before `os.fsync(directory_fd)`. After directory fsync, current
     path metadata is captured via nofollow stat and must match `renamed` exactly
     (`same_as(renamed)`). If an external editor modified or replaced the file during
     directory fsync, `source_changed` is raised, and Sandbox never adopts an external
     editor's state as the continuation for subsequent removals. If replacement
     succeeded before drift was detected, prepared recovery evidence is safely retained
     in `cleanup.json`; resuming requires a fresh human approval. No claim of atomicity
     against the unavoidable last-instruction race is made.

3. **Clamped worker session deadlines**:
   Worker session deadlines in reconciliation and migration are clamped to
   persisted token expiry sidecar values before opening workers (`worker_deadline
   = min(deadline, persisted_exp)`). Active protected operations are bounded by
   `min(deadline, effective_exp, token_exp)`. Clock checks before `exists` and
   each `readback_equals` enforce this bound, ensuring expired credentials fail
   closed with `approval_expired` while preserving all prior journal receipts.

4. **Scoped runs**:
   Scoped runs repeat `--ownership-key NAME` for the required mapped destination
   names and inject only their declared `consumer` variables. Selection,
   consumer names, argv, escalation, and bounds are checked before value reads.
   Ownership selectors cannot be mixed with `--source`, `--key`, `--secret`,
   `--destination`, `--session`, or session lifetime options. Without ownership
   selectors, the existing run path is preserved. Output redaction has the same
   limits as existing bounded runs; the trusted child remains an intentional
   recipient and must not print or encode secrets.

5. **Private runtime state and persistence failures**:
   Runtime state lives under `$SANDBOX_HOME/runtime/secret-ownership/`, with
   owner-only 0700 directories and 0600 files, no symlinks. Approvals, admitted
   evidence, JSONL journals, completion records, and the random controller ID
   contain only metadata. A torn final journal fragment is truncated only under
   the exclusive lock; surviving attempts remain unknown. Earlier corruption
   refuses. `status` reports journal names/states and the latest completion's qualified
   run ID, names/counts, and revocation state. Retention is dry-run by default, preserves corrupt or
   uncertain state, and removes only completed settled metadata on `--confirm`.
   Stable lock files remain to prevent concurrent runs from using different lock
   inodes. Retention never lists or deletes native-store entries.
   If writing a completion record fails upon operation finish, the payload reports
   `persistence="persistence_failed"` and `completion_record="persistence_failed"`
   while preserving all known operation state and revocation guidance without re-throwing.
   An otherwise successful operation then returns `provider_failed` with exit 4,
   retaining its original result in `operation_code`. Existing failures keep their
   primary code and exit status.

6. **Derived token lifecycle and revocation guidance**:
   Derived access-token revocation is attempted on session exit. Only the exact
   provider acknowledgment means `revoked`. Unsupported routes, malformed
   responses, killed workers, and uncertain shutdown report `cleanup_unknown`.
   Derived token follow-up is preserved and formatted across all operations
   (migration, cleanup, scoped runs, and destination-checked classification),
   aggregating uncertain sessions (`cleanup_unknown`) only, specifying the
   remaining token lifetime (`expires at <timestamp>` using the latest uncertain
   finite expiry, or `expiry unknown` if any uncertain session has an unknown
   expiry) and clarifying that native bootstrap entries and Cloudflare credentials
   remain operator-managed. Primary failure codes are preserved while including
   derived token follow-up guidance. An otherwise successful operation with
   uncertain revocation exits 6. A scoped destination read refuses to launch
   the consumer if derived-token revocation is uncertain.

7. **Exit codes**:
   Ownership exit codes are:
   - `0`: Success or safe plan.
   - `2`: `ownership_not_configured`, `profile_unknown`, `classification_blocked`, `source_unregistered`, `source_unsupported`, `other_domain`, `ownership_keys_required`, `selection_invalid`, `input_invalid`, `option_conflict`, `command_invalid`, `consumer_collision`, `source_key_missing`, `duplicate_key`, `syntax_unsupported`, `source_missing`, `source_too_large`, `escalation_unsupported`, `audit_unavailable`.
   - `3`: `approval_required`, `approval_expired`, `approval_mismatch`, `approval_human_only`, `authority_mismatch`, `evidence_required`, `evidence_inadmissible`, `evidence_expired`, `source_changed`, `continuity_unreliable`, `credential_missing`, `credential_scope_invalid`, `journal_locked`, `journal_foreign_controller`, `journal_missing_owner_elsewhere`, `journal_corrupt`, `cleanup_ineligible`, `tty_required`, `key_missing`, `source_scope_denied`, `source_unsafe`.
   - `4`: `provider_contract_unconfirmed`, `provider_policy_requires_approval`, `existence_unknown`, `pre_existing`, `provider_failed`, `response_invalid`, `value_unsupported`, `operation_timeout`.
   - `5`: `run_stopped`.
   - `6`: `cleanup_unknown`.
   Unmapped refusal codes fall back to exit 1. Primary operation failures keep their status when audit or revocation also fails.
   Scoped runs propagate the child command's exit code on success.

8. **Audit behavior and failure handling**:
   Ownership output never includes values, value fingerprints, lengths, masks, or
   legacy revisions. The audit schema remains version 1 and records ownership
   operation names (`ownership_classify`, `ownership_approve`, `ownership_migrate`,
   `ownership_cleanup`, `ownership_run`), key names, counts, and fixed outcomes only.
   If audit outcome logging fails in CLI ownership dispatch, the primary operation
   payload is preserved, `audit="audit_unavailable"` is attached, and an otherwise
   successful operation fails with `code="audit_unavailable"` and exit 2.

9. **Filesystem and test root portability**:
   Temporary testing and fixture paths use canonical `Path('/tmp').resolve(strict=True)`
   across Linux and macOS, avoiding host-dependent `/private/tmp` assumptions or
   parent-environment fallbacks.

### Configuration (metadata only)

Ownership is configured in `sandbox.config.json` (project layer) or
`$SANDBOX_HOME/sandbox.local.yml` (machine override) under the `secrets` section.
It contains non-secret metadata only: no secret values, passwords, tokens, or
bootstrap credentials.

`office` and `personal` domains must use distinct project identities: they must
not share provider `projectId` or client identities (`readClientId`, `createClientId`).
Endpoints must be canonical HTTPS origins (`https://...` without paths, trailing
slashes, or query components); unencrypted HTTP endpoints are refused.

```jsonc
{
  "secrets": {
    "sources": {
      "personal-file": {
        "path": ".secrets/personal.env",
        "format": "dotenv",
        "mcpModes": ["keys"]
      },
      "office-file": {
        "path": ".secrets/office.env",
        "format": "dotenv",
        "mcpModes": ["keys"]
      }
    },
    "ownership": {
      "profiles": {
        "office": {
          "domain": "office",
          "destination": {
            "endpoint": "https://manager.example.org",
            "projectId": "<office-project-id>",
            "environment": "prod",
            "path": "/",
            "readClientId": "<office-read-client-id>",
            "createClientId": "<office-create-client-id>",
            "timeoutSeconds": 30,
            "gateway": "none"
          }
        },
        "personal": {
          "domain": "personal",
          "destination": {
            "endpoint": "https://manager.example.org",
            "projectId": "<personal-project-id>",
            "environment": "personal-dev",
            "path": "/",
            "readClientId": "<personal-read-client-id>",
            "createClientId": "<personal-create-client-id>",
            "timeoutSeconds": 30,
            "gateway": "none"
          }
        }
      },
      "entries": [
        {
          "source": "office-file",
          "key": "STRIPE_API_KEY",
          "profile": "office",
          "name": "STRIPE_API_KEY",
          "consumer": "STRIPE_API_KEY",
          "origin": "source"
        },
        {
          "source": "personal-file",
          "key": "OPENAI_API_KEY",
          "profile": "personal",
          "name": "OPENAI_API_KEY",
          "consumer": "OPENAI_API_KEY",
          "origin": "source"
        },
        {
          "source": "office-file",
          "key": "LEGACY_REPORT_TOKEN",
          "profile": "undecided",
          "name": "LEGACY_REPORT_TOKEN",
          "consumer": "LEGACY_REPORT_TOKEN",
          "origin": "source"
        }
      ]
    }
  }
}
```

### Operational CLI workflow

All ownership commands operate through `./sb secrets ownership <action>` or
scoped execution through `./sb secrets run --ownership-profile ...`.

#### 1. Classify secret mapping and destinations
Inspect the mapping state of a profile to verify that all source keys exist,
domain separation is valid, and entries are unambiguous:

```bash
# Offline classification of mapped keys
./sb secrets ownership classify --profile office

# Online classification verifying destination preflight status
./sb secrets ownership classify --profile office --check-destination
```

#### 2. Validate storage admission evidence
Verify compliance evidence for remote storage before migration approval:

```bash
./sb secrets ownership evidence-check --profile office --evidence /path/to/storage-evidence.json
```

#### 3. Place provider credentials into local secure store
Store client credentials into the local OS credential store (macOS Keychain, Linux Secret Service).
Credentials are provided interactively or piped via `--stdin` to keep secrets out of shell history
and command-line arguments:

```bash
# Read capability credential
./sb secrets ownership credential-set --profile office --capability read --stdin --create-only < /path/to/read-token.txt

# Create capability credential
./sb secrets ownership credential-set --profile office --capability create --stdin --create-only < /path/to/create-token.txt
```

#### 4. Request human operator approval
Approvals require an interactive foreground TTY and can never be granted by background processes or automated agents:

```bash
# Request migration approval (valid for 15 minutes, requires admitted evidence)
./sb secrets ownership approve --profile office --operation migrate --evidence /path/to/storage-evidence.json --minutes 15
```

The operator reviews the prompt and types `approve` to create the signed approval record (`ap_<controller_id>_<hex16>`).

#### 5. Execute create-only migration
Migration is create-only: it writes new entries to the destination but never overwrites existing secrets or mutates the source:

```bash
# Dry run: preview planned migration actions without contacting provider
./sb secrets ownership migrate --profile office --approval ap_<controller_id>_<hex16>

# Confirmed migration: creates destination secrets and journals verified state
./sb secrets ownership migrate --profile office --approval ap_<controller_id>_<hex16> --confirm
```

#### 6. Inspect ownership status
Review migration journals and completion records:

```bash
./sb secrets ownership status --profile office
```

#### 7. Perform source cleanup under separate approval
Source cleanup is a separate, destructive human operation. It requires a dedicated cleanup approval
and only removes keys that have been verified or reconciled at the destination:

```bash
# 1. Request dedicated cleanup approval
./sb secrets ownership approve --profile office --operation cleanup --minutes 15

# 2. Dry run cleanup to verify eligible keys and expected source state
./sb secrets ownership cleanup --profile office --approval ap_<controller_id>_<hex16>

# 3. Confirmed cleanup: safely removes keys using atomic staged replacement
./sb secrets ownership cleanup --profile office --approval ap_<controller_id>_<hex16> --confirm
```

#### 8. Bounded scoped runs
Inject mapped secrets into child commands using exact `--ownership-key` flags. The child receives
only the declared `consumer` environment variables:

```bash
./sb secrets run --ownership-profile office --ownership-key STRIPE_API_KEY -- npm run sync-payments
```

#### 9. Manage retention of settled metadata
Remove settled journal and run metadata while preserving active locks and uncompleted operations:

```bash
# Dry run retention
./sb secrets ownership retention --profile office

# Confirmed retention
./sb secrets ownership retention --profile office --confirm
```

### Credential placement and human security gate

- **Placeholder stdin / human workflow**: Credential placement uses interactive prompts or `--stdin`. Never configure real secret tokens, passwords, or client secrets in configuration files or command arguments.
- **Human security and disposable provider gate**: Real production migration remains strictly gated on explicit human security sign-off (covering authority, continuity, lifetime, revocation, gateway bundle, and security policy) and disposable provider verification. Do not perform operational migration without verified proof artifacts.
