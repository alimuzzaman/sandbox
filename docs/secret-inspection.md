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
choose from one second to 30 minutes. Combined redacted output is bounded to
1 MiB and reports truncation. A child that exits unsuccessfully makes
`sb secrets run` exit nonzero after the bounded result is printed; the child
exit code is preserved when it is safe for the shell to represent it.

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
  dotenv, JSON, INI, properties, TOML, YAML, XML, PEM, opaque-token, and binary
  project sources. It never scans for files or auto-detects a format from
  content, does not enumerate the process environment, and does not connect to
  external secret managers.
- Targeted updates currently remain dotenv-only. Structured round-trip writers
  require separate syntax-preservation and atomicity evidence.
- Shape validation is offline and always reports `live_checked=false`.
- Exact length and fixed masks remain deliberate metadata disclosure.
- The same OS identity may retain direct filesystem access.
- A trusted child and its descendants can disclose the delivered value.
- Exact-match redaction cannot guarantee transformed-output removal.
- Atomic update may leave owner-only plaintext temporary remnants after a host
  crash; cleanup is best effort.
- V1 does not promise perfect in-memory zeroization.
- SOPS, dotenvx, Gitleaks, and detect-secrets are not currently broker
  dependencies. SOPS is the preferred candidate for a separately reviewed
  encrypted-file backend; its plaintext-output commands must never be exposed
  directly to agents.

For the concise agent decision tree, load the `secret-inspection` skill with
`./sb skill show secret-inspection`.
