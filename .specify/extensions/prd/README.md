# Reviewed PRD Workflow

Adds a product-requirements phase before `speckit.specify`.

```bash
specify extension add --dev /path/to/spec-kit/extensions/prd
```

Run `speckit.prd.refine` with a product idea. Follow the active repository/user
model policy for drafting, specification, planning, and implementation. A PRD can
reach `READY FOR SPECKIT` only after independent readiness validation passes.
Record the actual drafting and review configurations; never relabel a fallback.
The mandatory `before_specify` hook blocks unreviewed PRDs and hands the reviewed
feature directory to specification. It checks the verdict, not a retired model.

The normal lifecycle remains `refine → specify → clarify → plan → tasks → analyze
→ implement`. Existing user authorization to complete that workflow carries
through its phase boundaries.

The extension does not merge, publish, create branches, or modify application code.

The development install is intentionally project-local. Remove it with
`specify extension remove --force prd`; the command and generated skills are
removed, while existing feature artifacts such as `prd.md` remain intact.
