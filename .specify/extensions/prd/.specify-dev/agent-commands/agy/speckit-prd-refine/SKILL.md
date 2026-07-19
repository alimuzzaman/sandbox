---
name: speckit-prd-refine
description: Create or refine a Terra-drafted PRD and require Sol High validation
  before specification
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: prd:commands/speckit.prd.refine.md
---

# Refine a Product Requirements Draft

Create or resume one `specs/<number>-<slug>/prd.md` and converge product intent
before formal specification. This command owns only `prd.md` and
`.specify/feature.json`; it must not create or modify specification, planning,
task, implementation, branch, commit, or remote artifacts.

## Preferred models

- Draft and iteratively refine with `gpt-5.6-terra` at Medium effort.
- After the draft first passes all normal readiness checks, require exactly one
  independent read-only review with `gpt-5.6-sol` at High effort.
- This command cannot switch the active root model. Report what actually ran.
  If exact Sol High review is unavailable, leave the PRD `NOT READY` and never
  claim validation occurred.

## Input

```text
$ARGUMENTS
```

Accept a product idea, an existing feature directory, or an existing `prd.md`.
Never ask the user to repeat non-empty input.

## Establish the PRD

1. Locate the project through `.specify/` and read its init options, constitution,
   nearest agent instructions, and resolved `prd-template`.
2. Resume an explicitly supplied PRD first, then the active feature recorded in
   `.specify/feature.json` when it contains `prd.md`.
3. If no PRD exists, derive a concise 2–4 word kebab-case short name and run
   `.specify/extensions/prd/scripts/bash/create-prd.sh --json --short-name "<short-name>" -- "$ARGUMENTS"` from the project root.
   Parse `PRD_FILE` and fill the new template. Never overwrite an existing PRD.
4. If the directory already contains `spec.md`, `plan.md`, or `tasks.md`, stop:
   the feature has entered a downstream phase and this command must not backfill it.

## Terra refinement

Ground discoverable facts before asking about product choices. Run at most five
focused passes and stop early after two consecutive passes find no material issue:

1. Problem, actors, outcomes, goals, and non-goals.
2. Primary, negative, and boundary scenarios.
3. Constraints, dependencies, compatibility, risks, and contradictions.
4. Consequential decisions, assumptions, and open questions.
5. Measurable, implementation-independent acceptance outcomes.

For each pass, make compact evidence-backed updates to the PRD. Ask only about
choices that materially change scope, user outcomes, policy, acceptance, or
compatibility. Replace obsolete alternatives instead of appending duplicates.

## Final Sol validation

After every normal readiness item passes:

1. Give one Sol High reviewer the complete PRD, relevant evidence, confirmed user
   decisions, and the artifact boundary.
2. Require omissions, contradictions, ambiguous decisions, negative-scenario gaps,
   weak acceptance outcomes, invalid assumptions, implementation leakage, proposed
   edits, and a final `PASS` or `REOPEN` verdict.
3. Apply supported non-consequential improvements to the same `prd.md`.
4. If the verdict requires a consequential product decision, set `NOT READY`, ask
   the user, update the PRD, repeat the necessary Terra checks, then run one new
   final Sol High review.
5. Record only the actual drafting model, validation model/date, and verdict in
   metadata. Do not store reasoning transcripts or raw reviewer output.

Set `**Final Validation**` to `PASS — gpt-5.6-sol High` and `**Readiness**` to
`READY FOR SPECKIT` only after the latest exact Sol High verdict passes and every
readiness checkbox is complete. Recommend `speckit.specify`; do not invoke it.