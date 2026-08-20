# Quickstart Validation: Safe Secret Inspection

Use only synthetic fixture credentials. Never point these checks at an existing personal or project secret source.

## Prerequisites

- Work on a non-`main` branch.
- Use a temporary project and temporary `SANDBOX_HOME`.
- Ensure the fixture source is owner-only and contains test-only values.

## Fixture

Create an isolated project descriptor that registers `.env.fixture` as `fixture-env`, grants only the intended MCP inspection modes, and defines one fixed test use profile. The fixture source should include:

- one recognized-format synthetic token;
- one eligible unrecognized opaque value;
- one password-like value;
- one unrelated assignment and comments.

All fixture values must be unmistakably non-production test data.

## Focused automated checks

```bash
python3 -m unittest \
  tests.test_secret_config \
  tests.test_secret_parser \
  tests.test_secret_policy \
  tests.test_secret_service \
  tests.test_secret_commands \
  tests.test_secret_mcp
```

Run the architecture/composition regressions separately:

```bash
python3 -m unittest \
  tests.test_command_composition \
  tests.test_mcp_composition \
  tests.test_architecture_boundaries \
  tests.test_modularity
```

## Live CLI proof

Against the isolated fixture project:

1. Run default inspection and confirm only key names appear.
2. Run one-key metadata and shape validation and confirm no fixture value appears.
3. Run fixed masking and confirm only the permitted public prefix/final four appear.
4. Run a trusted fixture child that reports presence only; confirm exit status, parent isolation, and redaction.
5. Update one key through stdin; confirm non-secret status and that unrelated bytes remain unchanged.
6. Attempt reveal without correct confirmation and confirm refusal with empty stdout. A successful reveal is covered with an in-memory fake TTY test so automated evidence never prints even synthetic secret content.
7. Confirm none of these commands regenerate Compose or `.env` runtime files.

## MCP proof

Compose a fake MCP server with the explicit `secrets` group and service factory:

- verify exact tool inventory;
- verify the group is absent from every default catalog;
- verify unauthorized modes and arbitrary commands refuse before source access;
- verify authorized inspection matches CLI fields;
- verify registered profile use returns only redacted bounded output.

## Leak check

Search captured stdout, stderr, JSON, errors, audit JSONL, and test failure text for every complete fixture value. The expected count is zero. Also verify that repeated masks do not expand disclosure.

## Final repository checks

```bash
git diff --check
python3 -m unittest tests.test_secret_config tests.test_secret_parser tests.test_secret_policy tests.test_secret_service tests.test_secret_commands tests.test_secret_mcp
```

Record live CLI/MCP evidence separately from unit/contract results. Do not claim production secret-manager enforcement: this v1 remains a least-disclosure workflow while the same OS identity retains direct file access.
