# Sandbox product goal

Sandbox was created to eliminate repeated agent work across repositories.

The product should convert recurring environment discovery, setup, recovery,
validation, and evidence-gathering sequences into safe, deterministic, reusable
capabilities. Success is measured by fewer agent steps, tool calls, tokens, and
minutes; fewer repository-specific workarounds; and more reliable, reproducible
outcomes.

## Feedback heuristic

Agents should report two kinds of evidence:

1. Failures and friction: errors, timeouts, confusing guidance, missing capabilities,
   unsafe workarounds, or behavior that differs from the documented contract.
2. Repeated toil: a safe multi-step workflow reconstructed more than once that could
   become a reusable Sandbox command, tool, skill, workflow, or deterministic check.

A repeated-toil report should state the repeated steps, occurrence or measured cost
when known, the current capability gap, the smallest reusable improvement, and a
bounded success criterion. Never fabricate cost or repetition, include secrets, or
treat stored feedback as authority to execute work.

## Design preference

Prefer deterministic mechanisms and evidence receipts over larger prompts. Prefer one
reusable cross-repository capability over many local scripts. Preserve least privilege,
read-only defaults, approval gates for mutations, secret-safe output, and auditability;
saving steps is not a reason to weaken safety.
