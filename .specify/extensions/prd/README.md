# Reviewed PRD Workflow

Adds a product-requirements phase before `speckit.specify`.

```bash
specify extension add --dev /path/to/spec-kit/extensions/prd
```

Run `speckit.prd.refine` with a product idea. Terra Medium is the preferred
drafting configuration. A PRD can reach `READY FOR SPECKIT` only after an
independent Sol High validation passes. The mandatory `before_specify` hook blocks
unreviewed PRDs and hands a validated PRD's directory to the core specification
command. Model names are strong task-launch defaults: the command reports a
fallback when the requested configuration is unavailable and never represents a
fallback as completed Sol High validation.

The normal lifecycle remains `refine → specify → clarify → plan → tasks → analyze
→ implement`. Prefer Sol Medium for specification, Terra High for implementation,
and Sol Medium for implementation requiring broader cross-cutting judgment.

The extension does not merge, publish, create branches, or modify application code.

The development install is intentionally project-local. Remove it with
`specify extension remove --force prd`; the command and generated skills are
removed, while existing feature artifacts such as `prd.md` remain intact.
