---
description: "Validate an active reviewed PRD before specification"
---

# Validate PRD Handoff

Read `.specify/feature.json` from the project root. If it does not identify a
feature directory, or that directory has no `prd.md`, report `NO_ACTIVE_PRD` and
allow the normal specification flow to continue from its user input.

When an active `prd.md` exists:

1. Refuse handoff unless all three conditions hold:
   - `**Readiness**: READY FOR SPECKIT` is present;
   - `**Final Validation**` records the latest independent `PASS` verdict and the actual reviewer model/effort under the active repository/user policy;
   - every readiness checkbox is checked.
2. On refusal, report `PRD_NOT_READY`, identify the smallest failed condition, and
   direct the user to `speckit.prd.refine`. Do not create or modify any artifact.
3. On success, report `PRD_READY` and
   `SPECIFY_FEATURE_DIRECTORY=<active-feature-directory>`. The invoking
   `speckit.specify` command must treat this as the explicit feature directory,
   use the complete PRD as its authoritative feature description, create only
   `spec.md` and its specification checklist there, and never modify `prd.md`.
   Follow the active repository/user model policy for specification and report
   the actual configuration used. Inline backticks around metadata values do not
   change their meaning; do not require a retired model name as a pass token.
