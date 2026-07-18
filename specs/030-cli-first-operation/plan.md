# Implementation Plan: CLI-first Sandbox operation

**Branch**: `main` | **Date**: 2026-07-18 | **Spec**: [spec.md](spec.md)

## Summary

Make the `sb` command and a shipped skill a complete CLI-first interface for
generic Compose and WordPress projects. Add generic service execution through
the existing runtime service, retain MCP as optional scoped integration, and
synchronize automatic-delivery guidance across project documents.

## Technical Context

**Language/Version**: Python 3.10+

**Primary Dependencies**: argparse; existing runtime adapters and command registry

**Storage**: Existing project descriptor and instance registry; no schema change

**Testing**: Python `unittest`; live generic Compose instance check

**Target Platform**: POSIX local development hosts and provisioned remote hosts

**Project Type**: CLI tooling with optional MCP adapter

**Performance Goals**: Guide generation is local and immediate; execution uses
the established bounded runtime timeout.

**Constraints**: No raw Docker bypass, no inferred shell/service, capability
check before execution, docs/skills updated with code.

**Scale/Scope**: Two project kinds, one new runtime command, one guide command,
one shipped skill, and delivery-policy documentation.

## Constitution Check

- Per-project ownership: PASS. `exec` resolves the registered instance and
  project root; `guide` reads only a supplied/current descriptor.
- Registry authority: PASS. No direct registry file reads are introduced.
- Modular command composition: PASS. New commands use explicit `CommandSpec`
  registration in their own command module.
- Live-stack verification: PASS. Generic local execution is exercised against
  the fixture instance.
- Docs with code: PASS. README, agent guides, skill, and CLI documentation are
  included.
- Delivery-policy amendment: PASS. Constitution patch is versioned 1.0.2.

## Project Structure

```text
sandbox/
├── commands/runtime.py       # CLI-first guide and generic execution
├── application/context.py    # compose execution capability
├── commands/manifest.py      # command module ownership
└── cli.py                    # instance-resolution classification

skills/sandbox-cli/SKILL.md   # shipped CLI-first operating instructions
docs/cli-first-operation.md   # human reference
tests/test_cli.py             # public command and guide coverage
tests/test_command_composition.py
```

**Structure Decision**: New CLI behavior is feature-owned in a command module;
the existing CLI remains the composition root and only categorizes the
instance-scoped command.

## Research

See [research.md](research.md). The design relies on the existing Compose
adapter's `exec` operation and avoids a second process transport.

## Design Artifacts

- [data-model.md](data-model.md)
- [contracts/cli-first-operation.md](contracts/cli-first-operation.md)
- [quickstart.md](quickstart.md)
