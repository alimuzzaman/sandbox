---
name: secret-inspection
description: Safely inspect, validate, mask, use, or update one registered Sandbox secret without reading the whole source. Use when an agent needs a secret name, shape, fixed identifier, or bounded credential-consuming command; reveal remains a human-only last resort.
---

# Secret inspection

Use the lowest-disclosure operation that can finish the task. Never open or
search a secret source directly, request that a secret be pasted into chat, put a
secret in an argument, or export a secret into the parent shell.

## Decision order

Stop as soon as the task is satisfied:

1. List eligible key names from a registered source alias.
2. Inspect metadata for exactly one key.
3. Validate that key against a reviewed shape profile.
4. Request the fixed mask only when identification still requires it.
5. Let one trusted, bounded child process use the key without displaying it.
6. Update one key through hidden or protected input without reading the file.
7. If none of those works, ask the human to reveal one key outside every
   agent-captured terminal, transcript, recording, or tool call.

All examples below use placeholders. Replace identifiers such as `SOURCE_ALIAS`,
`SECRET_KEY`, and `PROFILE_NAME`; never substitute a secret value into a command.

## 1. Discover names only

```bash
./sb secrets inspect --source SOURCE_ALIAS --project-dir PROJECT_DIR
```

This is the default `keys` mode. Do not pass an arbitrary file path. If the
source is not registered, ask the operator to register the project `.env*`
source rather than reading it directly.

## 2. Inspect one key without characters

```bash
./sb secrets inspect --source SOURCE_ALIAS --key SECRET_KEY \
  --mode metadata --project-dir PROJECT_DIR
```

Metadata reports a safe state and a length bucket. Exact length is additional
disclosure; request `--exact-length` only for one key when a documented format
requires it.

## 3. Validate shape, not live validity

```bash
./sb secrets validate --source SOURCE_ALIAS --key SECRET_KEY \
  --profile PROFILE_NAME --project-dir PROJECT_DIR
```

Use only reviewed profile names. A result reports `pass`, `fail`, or
`not_applicable` checks and `live_checked=false`. It does not prove that a
provider accepts the credential.

## 4. Request the fixed mask only when needed

```bash
./sb secrets inspect --source SOURCE_ALIAS --key SECRET_KEY \
  --mode masked --project-dir PROJECT_DIR
```

Masking is fixed and non-expandable:

- A recognized eligible opaque token exposes only its reviewed public
  provider/type prefix, the constant `<redacted>` marker, and its final four
  characters.
- An eligible unrecognized opaque token exposes only `<redacted>` and its final
  four characters.
- Passwords, short or low-variety values, credential-bearing URLs, connection
  strings, JWTs, structured or multiline values, PEM/private keys,
  certificates, and binary material expose no characters.

Never attempt repeated masks, caller-selected prefix/suffix lengths, offsets,
templates, regular expressions, or guesses. The interface intentionally offers
none of those controls.

## 5. Use one secret without seeing it

Prefer this when the task needs provider behavior rather than secret text:

```bash
./sb secrets run --source SOURCE_ALIAS --key SECRET_KEY \
  --destination DESTINATION_NAME --timeout-seconds 30 \
  --project-dir PROJECT_DIR -- trusted-program status
```

Before launch:

1. Confirm `trusted-program` is the intended secret recipient.
2. Use direct arguments after `--`; do not invoke a shell or interpolate the
   value into arguments.
3. Choose only the destination name the program documents. Loader,
   interpreter, shell, prompt, and credential-helper control variables are
   denied.
4. Disable shell tracing, debug dumps, verbose HTTP diagnostics, crash
   reporters, environment serialization, and command logging.
5. Do not run `env`, `printenv`, `set`, or an unreviewed upload command.

The child receives a minimal environment plus the selected secret. It is an
intentional recipient and can still print, transform, persist, pass to a child,
or exfiltrate the value. Exact-match redaction is defense in depth: transformed
or encoded output might not be redacted. Verify only non-secret evidence such as
exit status, a bounded status response, or a resource identifier already known
to be public.

MCP may inspect or validate only explicitly authorized source modes. MCP use is
limited to a registered reviewed use profile; it never accepts arbitrary
commands or a candidate secret. There is no MCP reveal tool.

## 6. Update one key without reading the source

Prefer the hidden controlling-TTY prompt:

```bash
./sb secrets set --source SOURCE_ALIAS SECRET_KEY --replace-only \
  --profile PROFILE_NAME --project-dir PROJECT_DIR
```

For a new key, use `--create-only`. Use `--if-revision OPAQUE_REVISION` when
working from a prior non-secret inspection result. Other approved channels are:

```bash
approved-secret-producer | ./sb secrets set --source SOURCE_ALIAS SECRET_KEY \
  --stdin --replace-only --project-dir PROJECT_DIR

./sb secrets set --source SOURCE_ALIAS SECRET_KEY \
  --from-ref OTHER_SOURCE_ALIAS:OTHER_SECRET_KEY \
  --replace-only --project-dir PROJECT_DIR

./sb secrets set --source SOURCE_ALIAS SECRET_KEY \
  --generate PROFILE_NAME --create-only --project-dir PROJECT_DIR
```

The reviewed V1 generator is `random-base64url-32-v1`; do not invent profile
names or generate candidate material in the agent transcript.

The producer used with `--stdin` must be trusted and must not log its output.
Never use a plaintext flag, `KEY=value`, an ordinary environment variable, an
arbitrary input file, or a shell command string. Confirm only non-secret status,
validation state, and the opaque revision. Do not inspect the file afterward.

## 7. Human-only reveal exception

Do not run `secrets reveal` as an agent. Tell the human:

1. Open a local terminal that is not captured by the agent, a transcript,
   screen sharing, recording, accessibility capture, or durable job output.
2. Run the following themselves:

   ```bash
   ./sb secrets reveal --source SOURCE_ALIAS --key SECRET_KEY \
     --project-dir PROJECT_DIR
   ```

3. Read the prominent warning, verify the source alias and key, and retype the
   exact key name for this one request.
4. Use the displayed value only in the intended secure destination. Do not paste
   it into chat, tool arguments, issues, logs, comments, screenshots, or files.
5. Close or clear the terminal exposure when finished and rotate the credential
   if any unintended capture may have occurred.

Reveal has no JSON, pipe, redirection, wildcard, batch, `--yes`, cached
confirmation, or MCP form. The value goes directly to the controlling TTY while
stdout remains empty.

## Refusals and incidents

Treat a refusal as a security result. Do not bypass an unknown/unsafe source,
unsupported syntax, duplicate key, failed audit, dangerous destination, stale
revision, failed profile, missing TTY, or failed confirmation by reading the
source manually.

Broker failures return only a stable code and bounded public message. Never ask
for a traceback, dependency stderr, parser context, source excerpt, or debug
dump; those may retain the complete secret-bearing input. Report identifiers,
the correlation ID when present, and the stable code only.

If plaintext appears anywhere unintended:

1. Stop using the credential and stop any command still handling it.
2. Do not quote, copy, summarize, or conceal the exposed value.
3. Report the exposure privately, identifying the source alias and key only.
4. Rotate or revoke through the supported provider/operator workflow.
5. Remove or restrict captured artifacts through an authorized process, while
   preserving required incident evidence without the value.
6. Verify the replacement using metadata, validation, or bounded use—never by
   revealing it again.

See `docs/secret-inspection.md` for source configuration, MCP authorization,
operator warnings, and the complete threat boundary.
