# Research: Hermes Authorization Controls

## Decisions

### Immutable, server-side authorization records

**Decision**: Store a fingerprinted request in the existing remote Hermes state and approve only its pending, unexpired state.

**Rationale**: OWASP recommends default-deny, least privilege, server-side authorization, and sufficient audit logging. The state file already provides an atomic, locked control-plane boundary.

**Alternatives considered**: Free-form prompt acknowledgements were rejected because they are not structured, durable, or auditable.

### In-place prompt delivery

**Decision**: Deliver approved context through Hermes `cron edit --prompt`, reconstructing the trusted catalog prompt and appending a sanitized block.

**Rationale**: Hermes documents in-place cron editing; no job deletion/recreation is required. This preserves the scheduler identity and cadence.

**Alternatives considered**: Creating a separate one-shot job was rejected because it could bypass the intended task and leave duplicate work.

### Origin and scope validation

**Decision**: Accept only canonical HTTPS origins with no credentials, path, query, or fragment, and simple slug scopes.

**Rationale**: This keeps the exact reviewed target visible while preventing credential persistence or prompt-structure injection.

**Sources**: [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html), [OWASP Transaction Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html), [Hermes cron documentation](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/features/cron.md).
