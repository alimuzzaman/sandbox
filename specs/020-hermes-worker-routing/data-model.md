# Data Model: Reproducible Hermes Worker Routing

## Routing Policy

| Field | Meaning | Validation |
|---|---|---|
| coordinator model | Root conversation coordinator | Spark model identifier |
| direct worker | Static subagent target | Terra provider/model pair |
| default assignee | Fallback named task worker | `terra` |
| task decomposer | Work classification model | Spark provider/model pair |
| task specifier | High-judgment task specification model | Sol provider/model pair |

## Worker Profile

| Profile | Model | Scope | Tool boundary |
|---|---|---|---|
| `luna` | Luna | Evidence, read/search, summaries | `safe` + `file`; policy-only no-write guard |
| `terra` | Terra | Bounded implementation and tests | Standard Hermes CLI tools |
| `sol` | Sol | Architecture and sensitive decisions | Standard Hermes CLI tools; human checkpoint policy |

## Ownership

Sandbox owns the three named routing profiles and its delimited root coordinator-policy block. Provider credentials, sessions, memories, and non-owned root policy content remain operator-owned.
