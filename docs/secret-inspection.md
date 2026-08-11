# Safe secret inspection

Sandbox's secret broker lets agents answer common credential questions without
opening an entire `.env` file. Its default output is key names only. Higher
disclosure is explicit and ordered: metadata, shape validation, a fixed mask,
bounded use, protected update, then a human-only single-secret reveal.

This is a least-disclosure guardrail, not an operating-system security boundary.
An agent running as the same OS identity may still have filesystem access to a
registered source; repository and host policy must continue to prohibit raw
reads.

## Register project sources

Project `.env*` sources and MCP permissions are explicit in
`sandbox.config.json` (or the equivalent YAML/config override layer). No source
is discovered by scanning the project, and callers cannot supply an arbitrary
path.

```jsonc
{
  "secrets": {
    "sources": {
      "project-env": {
        "path": ".env.local",
        "mcpModes": ["keys", "metadata", "validate", "masked", "use"]
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
profile names are lowercase safe slugs. A project source path must be a relative
`.env*` path contained by the project root. The file must be regular,
owner-controlled, and inaccessible to group and other users. The built-in
`personal` source cannot be overridden by project config and grants no MCP mode
by default.

Enabling the MCP secrets tool group does not grant source access. Each source
must separately grant the required `mcpModes`; a use profile must also set
`mcp: true`. Keep the grant list minimal. The secrets group is absent from all
default MCP catalogs.

V1 parses only inert literal assignments. It never runs interpolation, command
substitution, includes, executable tags, or shell evaluation. Duplicate keys,
unsupported syntax, unsafe links or permissions, terminal controls, oversized
sources, and changes during an operation fail closed without returning a source
line.

## Lowest-disclosure workflow

Every example uses placeholders. Replace only aliases, key names, profile names,
paths, destinations, and trusted program arguments. Never place a secret value
in an example or command.

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
structured, or unsupported. Length is bucketed by default. `--exact-length` is
allowed only with metadata mode and one selected key; because exact length is
additional value-derived information, use it only when a documented format
requires it.

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

Protected values include passwords, values shorter than 24 characters,
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
choose from one second to 30 minutes. Combined redacted output is bounded to
1 MiB and reports truncation.

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

The trusted child is an intentional secret recipient. It can print, transform,
persist, pass to descendants, or exfiltrate the value. Sandbox registers
exact-match output redaction before launch, but transformed, encoded, fragmented,
or remotely logged forms may escape redaction. Redaction is defense in depth,
not permission to run an untrusted child.

For MCP, arbitrary commands are prohibited. `secret_use_profile` accepts only a
registered reviewed profile whose source, key, direct argv, destination, output
budget, and timeout are fixed in configuration. `secret_inspect` and
`secret_validate` also require explicit source-mode authorization. No MCP tool
returns plaintext or accepts a candidate secret.

### 6. Update one assignment without reading the source

The safest local input is a hidden controlling-TTY prompt:

```bash
./sb secrets set --source SOURCE_ALIAS SECRET_KEY --replace-only \
  --profile PROFILE_NAME --project-dir PROJECT_DIR
```

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

V1 writes one single-line literal assignment. It refuses structured, multiline,
binary, private-key, certificate, and PEM updates. A successful replacement
preserves unrelated assignments, comments, ordering, newline style, ownership,
and restrictive permissions and returns only the target identifiers, status,
validation result, and opaque revision.

Never use a plaintext command argument, `KEY=value`, a parent environment
variable, an arbitrary input file, a shell command string, or an ordinary MCP
field for the candidate. Do not verify by opening the source afterward; use
metadata, validation, or a bounded trusted child.

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

- V1 covers the built-in personal assignment source and explicitly registered
  project `.env*` files. It does not enumerate the process environment, infer
  secrets from mixed config documents, or connect to external secret managers.
- Shape validation is offline and always reports `live_checked=false`.
- Exact length and fixed masks remain deliberate metadata disclosure.
- The same OS identity may retain direct filesystem access.
- A trusted child and its descendants can disclose the delivered value.
- Exact-match redaction cannot guarantee transformed-output removal.
- Atomic update may leave owner-only plaintext temporary remnants after a host
  crash; cleanup is best effort.
- V1 does not promise perfect in-memory zeroization.

For the concise agent decision tree, load the `secret-inspection` skill with
`./sb skill show secret-inspection`.
