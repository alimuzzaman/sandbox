# Phase 1 Data Model: Per-Project-First & Modular `sb`

Entities the feature operates on. No new on-disk schema — the change removes the synthesized
`main` and reorganizes code; storage shapes are existing.

## Instance

A per-project WordPress stack.

- **name**: unique instance name (derived from the project dir basename; `main` is no longer
  reserved or special after this feature).
- **project_root**: canonical path of the owning project (1:1).
- **server**: `apache` | `nginx` | `litespeed` | `herd`.
- **ports**: wordpress_port, db_port, mailpit_port.
- **wp_dir**: `runtime/wp-<name>/`.
- **state**: `pending` | `ready` (registry `status`).
- Invariant: every Instance is reachable ONLY via its project (no implicit/global instance).

## Registry entry

The authoritative project→instance mapping (`runtime/registry.json`).

- **key**: canonical project_root.
- **fields**: instance, status, ports, server, url/login_url/admin_url, wp_version, source.
- Role: the single source of truth; `resolve_instances` and `_cwd_instance` read it.

## Instance config

Per-instance settings under `sandbox.local.yml` `instances.<name>`.

- **fields**: ports, server, domain, wp_config, multisite, extra_mounts, **app_password**
  (unified per-instance key — Stage A), autologin_token.
- Written by `ensure_instance`/`apply_config` (`_build_instance_block`); must be complete so
  nothing depends on the removed `main` runtime defaults.

## Resolution outcome (behavioral)

The result of instance resolution for a CLI invocation.

- **inputs**: `--instance`, `$SANDBOX_INSTANCE`, cwd→project (registry), command name.
- **outcome**: a resolved instance name OR an **error** (no project + non-project-routed).
- **states**: explicit → env → cwd-registry → error. No `main` state exists post-Stage-B.
- **validation**: a resolved name must be a known instance (registry ∪ sandbox.yml
  `instances:`); unknown → die listing valid instances.

## Feature module (Stage C)

A self-contained CLI feature unit.

- **name**: feature group (lifecycle, instances, data, wp, net, debug, integ, ui_dash,
  uninstall, config_setup).
- **interface**: `register(subparsers)` (declare subcommand + flags) and `run(cfg, args)`
  (handler). Self-registers into the `COMMANDS` registry consumed by `cli.py`.
- Replaces the hand-maintained `handlers = {...}` dict + scattered `cmd_*` functions.

## Relationships

- Project_root 1—1 Instance (via Registry entry) 1—1 Instance config.
- `cli.py` 1—* Feature module (registry); each Feature module owns ≥1 command name.
