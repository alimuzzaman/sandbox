# Luna Max feedback-convergence evaluation — 2026-08-13

This is the measured implementation log requested for the feedback-convergence work.
It records only observed evidence. The agent runtime did not expose per-agent token or
currency-cost telemetry, so cost is reported as unavailable rather than estimated.

## Routing

- Architecture and dependency planning: `gpt-5.6-sol`, extra-high reasoning.
- Bounded implementation and rework slices: `gpt-5.6-luna`, max reasoning.
- Integration, independent review, and final release decisions remain owned by the root
  agent; subagent self-scores are not acceptance evidence.

## Observed implementation performance

| Slice | Approximate elapsed / calls | Focused evidence | Quality observation |
| --- | --- | --- | --- |
| Existing-spec feedback amendments | 10m25s; calls unavailable | guide parse, scope and diff checks | Strong traceability; implementation tasks initially remained open. |
| Feedback CLI/MCP/service | ~45m / ~30 calls | 11 feedback + 19 MCP tests | Broad feature coverage; independent review later found unknown legacy fields could pass through reads. |
| Feedback hostile-record rework | ~25m / ~18 calls | 13 feedback + 24 targeted MCP/command tests | Narrow allowlist fix; original append-only record bytes preserved. |
| CLI/config convergence | calls ~30; elapsed unavailable | 176 focused tests | Good breadth; strict missing-label behavior and restore UI seams needed later integration. |
| Durable jobs | elapsed/calls unavailable | 35 focused tests | Durable acceptance and context propagation were strong; production remote enumeration was missed initially. |
| Configured-remote rework | ~20–25m / ~30 calls | 26 focused + 65 broader tests | Explicit catalog seam and real dependency composition fixed the missed production path. |
| Resources/network diagnostics | elapsed unavailable | 117 focused tests | Safety classification was useful; first pass introduced an embedded-program syntax defect, later fixed. |
| Hosting/source identity | initial elapsed unavailable | 194 focused tests | Security and source identity improved; nested repository source-root behavior was not exercised deeply enough initially. |
| Nested hosting rework | ~20–25m / ~24 calls | 180 hosting/remote + 17 contract + 3 targeted tests | Real nested-Git archive/Compose placement is covered; live VPS proof remains pending. |
| Restore approval gate | elapsed unavailable | 35 grouped tests | Mutation gate was correct; parser/bridge/dashboard propagation required a separate seam pass. |
| Shared seam integration | ~25m / ~20 calls | 43 bridge/resource + 16 MCP + 3 CLI tests | Fixed several cross-owner gaps and the embedded parser syntax defect. |
| PHP config model | ~8m / ~30 calls | 32 focused tests | Model and compatibility behavior were solid; runtime propagation required later integration. |
| PHP catalog/probe | ~35m; calls unavailable | 24 PHP-extension tests | Good deterministic probe boundary; runtime lifecycle remained unproven. |
| PHP Compose planner | elapsed/calls unavailable | 112 PHP/config/catalog/Compose tests | Initially violated the no-PECL policy and lacked model interoperability; both were corrected before review. |
| PHP managed-native | ~75m; calls unavailable | 69 focused tests | Broad allowlist/provenance coverage; profile-only image capability still needed consistency rework. |
| PHP integration | elapsed/calls unavailable | 31 focused + 148 legacy tests | Persisted config/status well, but independent review found child images were never actually built. |
| PHP four-plane probes | elapsed/calls unavailable | 27 focused tests | Correct bounded probe argv/timeout design; live build/apply proof remained outstanding. |
| Architecture rework | ~7–8m; calls unavailable | 29 architecture + 74 related tests | Restored the architecture counter to 113 without weakening the gate. |
| Final managed preflight rework | ~10m / ~12 calls | 111 focused tests | Invalid extension plans now leave repository bytes and network count unchanged. |
| Final PHP image-cache rework | ~5m / ~20 calls | 38 focused tests | Added label/provenance receipts and zero-Docker rejection for observation-only requests. |
| Final nested-host upgrade rework | ~20–28m / ~26 calls across two passes | 200 focused/contract tests | Real bare-repository proof covers legacy full-tree to subtree migration without force-push. |
| Final apply rollback rework | elapsed/calls unavailable | 43 focused tests | Rollback now covers persistence, Compose generation, reconcile, and four-plane verification. |
| Post-deploy workspace triage | elapsed/calls unavailable | 18 workspace + 35 job/CLI tests before review | Proposed identity lookup was reverted after root review found it could hide legacy path-keyed records; useful evidence, not shippable output. |

## Quality assessment

Luna Max was fast and effective on bounded, well-owned seams with explicit contracts and
focused regressions. Its output quality fell materially when correctness depended on an
end-to-end lifecycle spanning config, persistence, image materialization, Compose,
runtime probing, and apply. The independent review was essential: it found release
blockers that isolated unit tests did not expose.

Observed first-pass defects and omissions:

1. embedded remote-parser source became syntactically invalid;
2. restore confirmation was not initially propagated through every caller;
3. explicit missing-label compatibility conflicted with the new requirement;
4. PECL recipes were introduced despite the approved prohibition, then removed;
5. normalized PHP-extension intent was not initially persisted into instance state;
6. generated PHP child images were planned but not built/applied;
7. configured-remotes used a test-only `lookup(None)` convention not wired in production;
8. nested hosting deploys did not initially prove source-root isolation;
9. feedback reads trusted unknown legacy fields;
10. new architecture branches/imports exceeded the baseline before rework.

The practical conclusion is to keep Luna Max for bounded implementation, inventories,
and regression writing, but require stronger composition tests plus independent Sol/root
review for cross-cutting runtime or security boundaries. No durable model-routing policy
should be changed from this single project evaluation.

## Cost and remaining evidence

- Token count: unavailable from the subagent runtime.
- Currency cost: unavailable from the subagent runtime.
- Wall-clock times above are approximate where agents reported them; missing values are
  intentionally not reconstructed.
- Final full-suite, live PHP image/probe evidence, branch commit/push, and remote update
  are recorded separately when they occur.

## Root integration outcome

Independent review initially scored the broad Luna output 58/100 and blocked release.
Its P1 findings drove the rework. After rework, a root-owned live local proof exposed two
additional Compose build defects not caught by mocked tests: profile modules were being
recompiled unnecessarily, and the official WP-CLI parent is Alpine/non-root. The final
implementation asserts the parent's core modules, uses a literal Alpine package mapping,
restores the WP-CLI runtime user, and binds the Dockerfile generator plus recipes into the
cache digest.

Observed final gates and delivery:

- the combined focused integration/architecture/security gate passed;
- 2,325 full-discovery tests ran with seven skips and only the three established
  unrelated baseline failures (macOS `/private/var` normalization and the `fix` and
  `snapshot` skill mirrors);
- live local build/ensure/status/apply proved all four PHP planes ready and fresh, while
  DB/Mailpit containers plus database/uploads markers were preserved across apply;
- independent Sol High verdicts progressed from 58/100 to 64/100 and 82/100 as upgrade
  and rollback gaps were reproduced, then to a final GO at 94/100;
- the branch was merged with current `origin/latest`, pushed, and the supported remote
  update completed; canonical remote job-list now succeeds with zero active jobs.

This confirms the earlier quality conclusion: Luna Max provided useful breadth and fast
bounded fixes, but composition review and live execution were necessary for release-grade
confidence. Exact token and currency cost remain unavailable.
