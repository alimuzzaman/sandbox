# Research: Safe Secret Inspection

## Decision 1: Separate disclosure capabilities

**Decision**: Expose inventory, metadata, profile validation, masking, use, update, and reveal as distinct operations with increasing privilege. Default to names only.

**Rationale**: Vault separates `list`, `read`, and `patch`; Doppler provides names-only access and child-process injection; 1Password uses references and command-scoped delivery. Separate capabilities make accidental plaintext return structurally harder.

**Alternatives considered**: One overloaded `read` command with output flags was rejected because parser mistakes or default changes could silently broaden disclosure.

## Decision 2: Fixed masking, not scrambling

**Decision**: Recognized opaque tokens may show a reviewed public provider/type prefix plus fixed `last4`; eligible unrecognized opaque tokens show only `last4`. Use a constant `<redacted>` marker and refuse protected classes.

**Rationale**: Reversible scrambling and Base64 provide no confidentiality. Datadog's fixed last-four identifier and documented public prefixes from providers such as Stripe are stronger precedents than caller-selected prefix/suffix widths.

**Alternatives considered**: Arbitrary first/last N, custom regex, hashes, per-character masks, and random substitution were rejected because repeated calls create reconstruction or guessing oracles and per-character masks reveal exact length.

## Decision 3: Registered project sources in common configuration

**Decision**: Add a common `secrets.sources` descriptor to `sandbox.config.*`; paths must be project-relative `.env*` files that resolve within the project root. Keep the personal assignment file as the built-in `personal` alias.

**Rationale**: Explicit aliases prevent the inspector from becoming a general file-read primitive. A common config provider keeps WordPress and Compose behavior identical and follows the repository's schema-manifest boundary.

**Alternatives considered**: Arbitrary `--file`, directory scanning, and auto-exposing every `.env*` were rejected. A fixed `.env.local` only was too narrow for the approved explicit-registration requirement.

## Decision 4: Descriptor-safe reads and inert parser

**Decision**: Open sources with no-follow semantics, inspect the open descriptor for type/owner/mode/link/size, read bounded bytes, and parse literal assignments without shell execution. Preserve raw non-target lines for updates.

**Rationale**: The current personal parser safely rejects expansion but its path read follows links and is not syntax-preserving. The repository already uses descriptor-based credential reads and a strict assignment grammar that can be adapted.

**Alternatives considered**: `Path.read_text`, `source`, dotenv packages with interpolation, and YAML inference were rejected because they widen race, execution, and dependency surfaces.

## Decision 5: Owner-only JSONL audit with intent first

**Decision**: Store bounded intent and outcome records in an owner-only append-only JSONL journal. Every operation fails before secret processing if intent cannot be durably recorded.

**Rationale**: A dedicated journal avoids misusing job or Hermes audit models and provides reviewable accountability without value-derived fields. Intent-first ordering is required for reveal and mutation evidence.

**Alternatives considered**: Ordinary application logs were rejected as too broad and weakly protected. Hashes or previews in audit were rejected because they create correlation and guessing surfaces.

## Decision 6: Child-scoped use with minimal environment

**Decision**: Local CLI use runs one direct-argv child in a new process group with a reviewed minimal environment, one approved secret destination, exact-match streaming redaction, bounded output, and timeout. MCP use is profile-only.

**Rationale**: Doppler and 1Password establish subprocess delivery as the preferred alternative to export. Doppler also documents environment-variable names that can alter loaders and interpreters, so dangerous destinations must fail before launch.

**Alternatives considered**: Parent-shell export, implicit shell commands, plaintext substitution into argv, arbitrary MCP commands, and claiming redaction makes an untrusted child safe were rejected.

## Decision 7: Protected input and atomic targeted updates

**Decision**: Accept hidden TTY or explicit stdin, registered-reference copy, or reviewed generation. Lock the target, verify an opaque revision, modify one parsed assignment, write a mode-0600 same-directory temporary file, sync, replace atomically, and sync the directory.

**Rationale**: Vault PATCH and SOPS targeted set establish partial update semantics; GitHub CLI demonstrates prompt/stdin input. Atomic replace plus revision checking preserves unrelated content without returning it.

**Alternatives considered**: Plaintext flags, environment input, blind append, in-place truncate/write, and plaintext backup files were rejected because they leak, create duplicates, or risk partial corruption.

## Decision 8: Local human-only reveal

**Decision**: Reveal writes one confirmed value only to the controlling TTY, never stdout, JSON, pipes, MCP, or agent tooling. The operator must retype the exact key after a risk warning.

**Rationale**: This satisfies the explicit one-secret-read requirement without creating a programmable exfiltration interface and preserves the repository's no-secret-stdout rule.

**Alternatives considered**: `--plain`, `--yes`, one-time bearer handles returned to MCP, clipboard writes, and stdout were rejected because they are readily captured or replayed.

## Decision 9: Opt-in MCP catalog

**Decision**: Register an explicit `secrets` group but exclude it from default and runtime-scoped catalogs. Enabling the group is necessary but not sufficient: each source lists allowed MCP modes and each use profile independently opts in.

**Rationale**: Existing MCP transports can be remotely reachable and bearer authentication alone must not grant machine secret reconnaissance. Explicit group plus descriptor policy creates two deliberate gates.

**Alternatives considered**: Adding secret tools to default project catalogs was rejected because existing remote clients would gain a sensitive capability after upgrade.

## Decision 10: Preserve compatibility and avoid composition roots

**Decision**: Modernize the existing `secrets` command to owned parser configuration while retaining `migrate-zshrc`; inject a service factory into the MCP group; do not import `sandbox_core.py` from new modules or MCP `app.py` helpers.

**Rationale**: This follows explicit command/MCP manifest contracts and the repository's rollback/parity policy.

**Alternatives considered**: Adding more centralized parser branches, shelling from MCP into `sb`, or reading config/state JSON directly were rejected as architectural regressions.

## Primary external evidence

- [Doppler Secrets Access Guide](https://docs.doppler.com/docs/accessing-secrets)
- [1Password: Load secrets into scripts](https://www.1password.dev/cli/secrets-scripts)
- [Vault KV v2 API and patch behavior](https://developer.hashicorp.com/vault/api-docs/secret/kv/kv-v2)
- [GitHub CLI secret setting](https://cli.github.com/manual/gh_secret_set)
- [MCP elicitation security requirements](https://modelcontextprotocol.io/specification/draft/client/elicitation)
- [Stripe API-key types, prefixes, and reveal behavior](https://docs.stripe.com/keys)
- [Kubernetes Secret good practices](https://kubernetes.io/docs/concepts/security/secrets-good-practices/)
