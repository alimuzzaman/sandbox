# Spec Kit prerequisite feature selection

The active Spec Kit feature normally comes from `.specify/feature.json`.
When reviewing an amended or older feature, inspect another existing feature
without changing that pointer:

```bash
.specify/scripts/bash/check-prerequisites.sh \
  --feature-dir specs/009-runtime-user-dir \
  --json --paths-only
```

`--feature-dir` accepts an existing directory inside the current Spec Kit
project. It is a read-only selector for this prerequisite check: the command
does not rewrite `.specify/feature.json`, and it fails closed for a missing or
out-of-project path. The normal no-argument behavior is unchanged.

Use `--require-tasks --include-tasks` when the selected feature is being checked
for implementation readiness. The selector only chooses the feature paths; it
does not bypass the usual `plan.md` or `tasks.md` checks.
