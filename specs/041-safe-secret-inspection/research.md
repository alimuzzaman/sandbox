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

**Decision**: Add a common `secrets.sources` descriptor to `sandbox.config.*`; every source declares an explicit reviewed format and uses a project-relative path with a compatible suffix or basename that resolves within the project root. Keep the personal assignment file as the built-in `personal` alias.

**Rationale**: Explicit aliases prevent the inspector from becoming a general file-read primitive. A common config provider keeps WordPress and Compose behavior identical and follows the repository's schema-manifest boundary.

**Alternatives considered**: Arbitrary `--file`, directory scanning, format auto-detection, and auto-exposing credential files were rejected. A fixed `.env.local` only was too narrow for the approved explicit-registration requirement.

## Decision 4: Descriptor-safe reads and inert parser

**Decision**: Open sources with no-follow semantics, inspect the open descriptor for type/owner/mode/link/size, read bounded bytes, and dispatch only to the explicitly configured inert parser. Dotenv preserves raw non-target lines for updates. Structured formats expose stable selectors such as JSON Pointer paths, never source snippets; YAML aliases/tags, XML DTDs/entities, duplicates, excessive depth, and oversized documents fail closed.

**Rationale**: The current personal parser safely rejects expansion but its path read follows links and is not syntax-preserving. The repository already uses descriptor-based credential reads and a strict assignment grammar that can be adapted.

**Alternatives considered**: `Path.read_text`, `source`, dotenv packages with interpolation, implicit format inference, unsafe YAML loaders, XML entity expansion, and generic parser exception rendering were rejected because they widen race, execution, dependency, and disclosure surfaces.

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

## Follow-up: Open-source backend evaluation

**Decision**: Keep the least-disclosure broker as the public CLI/MCP boundary.
Evaluate SOPS as an optional, pinned backend for files already encrypted with
SOPS; do not expose the SOPS CLI directly and do not replace the current inert
plaintext assignment parser with dotenvx. Secret scanners remain optional test
and CI controls rather than readers.

**Rationale**: SOPS supports encrypted YAML, JSON, ENV, INI, and binary files,
targeted `set --value-stdin`, and FIFO-backed `exec-file`. Those mechanisms are
useful below the broker. Its normal decrypt and extract operations write
plaintext to stdout, however, and its process commands accept shell-shaped
command strings. dotenvx likewise has plaintext `get`/decrypt output and a
caller-variable mask that exposes six leading characters by default. Gitleaks
and detect-secrets detect likely credentials but do not provide typed,
syntax-preserving inspection or updates.

**Wrapper requirements**: A backend adapter must use direct argv; never place a
secret in argv; accept candidate material only through a protected descriptor,
FIFO, or stdin; bound and discard backend stdout/stderr on failure; translate
all failures to a stable broker code; never return exception text, exception
attributes, source snippets, or tracebacks; and retain the existing audit,
registration, disclosure, and MCP authorization checks. A dependency must be
pinned and verified and must not be downloaded implicitly during an operation.

**Failure research**: Synthetic canary tests confirmed that Python JSON and
TOML decode exceptions retain the complete input in `.doc`, PyYAML retains its
source buffer through parser marks, and subprocess exceptions retain captured
stdout/stderr. Message-only redaction is therefore insufficient. The broker
creates a fresh bounded error and raises it only after the original handler has
returned, severing cause, context, traceback, and secret-bearing frame locals.

## Follow-up: Official-format synthetic corpus

**Decision**: Maintain a checked-in corpus of wholly synthetic credential-file
shapes linked to the provider documentation that defines each format. The
initial corpus covers Google Cloud ADC variants, AWS shared credentials and web
identity, Azure certificate credentials, Kubernetes kubeconfig, Docker, npm,
PyPI, Terraform, Composer, Cargo, Maven, NuGet, OCI, GitHub App PEM, and a
binary-container placeholder. Never download or commit credential examples
whose provenance or active status cannot be proven.

**Rationale**: Provider documentation is the authority for field names and
container structure, but copying realistic example keys creates unnecessary
scanner noise and disclosure risk. Synthetic fixtures let tests exercise exact
selectors and parser behavior without creating usable credentials. A manifest
records the source URL, configured format, fixture, and expected selectors.

**Disclosure policy**: Structured values support inventory, metadata,
registered validation, bounded child-process use, and the separately confirmed
local TTY reveal path. They do not support masked previews or exact lengths:
arbitrary prefixes/suffixes of passwords, PEM bodies, JSON fields, or config
values are not generally public identifiers. Only the existing reviewed opaque
token profiles may return a fixed public prefix plus `last4`.

**Mutation policy**: The initial structured adapters are read-only. Updating a
nested JSON/YAML/XML/INI credential safely also requires syntax-preserving
round trips, duplicate-key handling, revision checks, and provider-specific
validation; until that is implemented and reviewed, targeted update remains
limited to dotenv assignments.

## Primary external evidence

- [Doppler Secrets Access Guide](https://docs.doppler.com/docs/accessing-secrets)
- [1Password: Load secrets into scripts](https://www.1password.dev/cli/secrets-scripts)
- [Vault KV v2 API and patch behavior](https://developer.hashicorp.com/vault/api-docs/secret/kv/kv-v2)
- [GitHub CLI secret setting](https://cli.github.com/manual/gh_secret_set)
- [MCP elicitation security requirements](https://modelcontextprotocol.io/specification/draft/client/elicitation)
- [Stripe API-key types, prefixes, and reveal behavior](https://docs.stripe.com/keys)
- [Kubernetes Secret good practices](https://kubernetes.io/docs/concepts/security/secrets-good-practices/)
- [SOPS supported formats and key providers](https://getsops.io/docs/)
- [SOPS targeted updates and stdin input](https://getsops.io/docs/usage/common-operations/)
- [SOPS FIFO and child-process delivery](https://getsops.io/docs/usage/advanced/)
- [dotenvx get, masking, and decrypt behavior](https://github.com/dotenvx/dotenvx)
- [Gitleaks redacted scanner output](https://github.com/gitleaks/gitleaks)
- [detect-secrets baseline scanner](https://github.com/Yelp/detect-secrets)
- [Google Cloud Application Default Credentials](https://docs.cloud.google.com/docs/authentication/application-default-credentials)
- [Google external account credential configuration](https://google.aip.dev/auth/4117)
- [AWS shared credentials and config files](https://docs.aws.amazon.com/sdkref/latest/guide/file-format.html)
- [AWS web identity token files](https://docs.aws.amazon.com/sdkref/latest/guide/access-assume-role-web.html)
- [Azure service-principal authentication](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli-service-principal)
- [Kubernetes kubeconfig v1 schema](https://kubernetes.io/docs/reference/config-api/kubeconfig.v1/)
- [Docker registry authentication storage](https://docs.docker.com/reference/cli/docker/login/)
- [npmrc authentication configuration](https://docs.npmjs.com/cli/v8/configuring-npm/npmrc/)
- [Python package index configuration](https://packaging.python.org/en/latest/specifications/pypirc/)
- [Terraform CLI credentials](https://developer.hashicorp.com/terraform/cli/commands/login)
- [Composer private-package authentication](https://getcomposer.org/doc/articles/authentication-for-private-packages.md)
- [Cargo credentials configuration](https://doc.rust-lang.org/cargo/reference/config.html#credentials)
- [Maven settings reference](https://maven.apache.org/settings.html)
- [NuGet authenticated feeds](https://learn.microsoft.com/en-us/nuget/consume-packages/consuming-packages-authenticated-feeds)
- [OCI SDK configuration](https://docs.oracle.com/en-us/iaas/Content/API/Concepts/sdkconfig.htm)
- [GitHub App private-key management](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps)
