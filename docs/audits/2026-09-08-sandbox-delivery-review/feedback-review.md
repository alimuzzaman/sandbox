# Sandbox feedback review

Feedback supports the same conclusion as the source review: failures cluster around creating the right instance, crossing real adapter boundaries, and recovering enough evidence to finish safely. Feedback also contains older fixes, duplicate symptoms, infrastructure incidents, and proposed capabilities. Counting each record as an open product defect would be misleading.

This review used the supported feedback CLI. It did not change feedback statuses, implement fixes, access credentials, or replay reported runtime operations. Raw detail/review prose and sensitive operational payloads were not saved. The sanitized detail indexes retain the claims, safe source/error references, evidence categories, and explicit limits.

## Coverage and meaning of the counts

The full snapshot contains **748 unique records**, collected in eight pages with no missing/invalid records and a final `has_more=false`. The snapshot time is `2026-09-08T16:56:09.302196Z`; a later supplemental query found no new records after it.

| Recorded status | Count |
|---|---:|
| Unreviewed | 101 |
| Blocked | 109 |
| Resolved | 268 |
| Verified | 112 |
| Duplicate | 73 |
| Not applicable | 82 |
| Invalid | 3 |

The initial summary-based filter selected 166 high/critical records: 43 unreviewed, 27 blocked, 54 resolved, 33 verified, seven duplicate, and two not applicable. All 166 details were successfully read. It missed obvious core cases whose summaries said `ensure`, `host apply`, or `autologin` without matching its original phrases. The expanded collection read the remaining 112 high/critical records plus three selected medium cancellation/history records. **Final detail coverage: all 278 high/critical records and three medium records, 281 unique successful reads, zero failures.** The remaining 467 medium/low records have metadata coverage only. See the [detail and disposition index](research/feedback-dispositions.md).

The 70 initial “unresolved” candidates were just the unreviewed/blocked subset of that keyword filter. They are neither 70 confirmed defects nor a complete unresolved count. “Verified” and “resolved” must be interpreted through the actual review scope. In the first 166 details, 119 reviews contain mechanical evidence words and 43 have no review record. A word such as “test” or “live” is not proof that a test passed or that a release was accepted.

Summary-based families overlap: deployment/activation 136, jobs/transport 97, startup 76, recovery 34, and clean URLs 29. They show where diagnosis work repeats. They do not establish a product-wide failure rate or an exact root-cause distribution.

## Feedback corroborated by this review

| Feedback | Assessment | Required action |
|---|---|---|
| `66035a8ff701c7010103f76ab117d838` | F15: separate backup sets share capture replay identity; the actual generated cache branch reuses a synthetic old capture | W12: distinct backup operation identity, same-operation replay, content-only change acceptance |
| `b572b3d55192b8bc9e7c70dec0722d9f` | F02: the URL helper loses preview identity and the real selector chooses the existing default | W1: carry exact instance identity and verify both option updates |
| `6704dc49fbf1d94c5578a7857e34010e`, `ea85cb298e201b4ba63e1ae71c9149df` | F16: real installation progress can print a login link before final JSON redaction; reproduced only with a synthetic marker | W3: secret-free output at the producer; inspect full stdout/stderr on success and failure |
| `0f0960d8c6ce36083fb195fb4a9fb3de` | F03: real Compose-shaped identity output fails the baseline proof; separate owner later corrected formats and resolved-model hashing | W2: reuse reviewed c7ed709 and its supplied acceptance; keep missing fresh exactly-once coverage explicit |
| `29031eb6f69e28ef4aea7ec5fdad2b3c`, `3126f1d654a039c32481c0522d831870` | F05: ordinary apply can proceed without the promised durable recovery authority | W5: require recovery eligibility/receipt before effects |
| `08771395ef4d93355a5c53d9dfe3f893` | F12: inspected Lenzora production records cannot identify a complete last-successful exact release | W7: joined outcome receipt and bounded history query |
| `5a30b5cda8378a014d0ae9b15cdf826d`, `aaa61571984fb074981f7608e6423aeb` | F09: cancel then force raises on cancelling-to-cancelling before force signaling | W4: state-aware idempotency and ownership-checked escalation |
| `6647c8aaf2ddb69b736e29548b30db02` | F10: live 100-row history was valid 1,181,179-byte JSON, above the 1,048,576-byte client bound | W4: compact byte-bounded pages, continuation and typed size diagnostics |

The exact impact/reproduction limits are in the [findings report](report.md). Synthetic interleavings establish a source defect without asserting a historical production data loss. Feedback `4ae99c62c095ceb966f053c297343e1a` is related foreign-initializer evidence, but its precise original cause was not separately isolated here.

## Open reports with later correction or capability evidence

| Feedback | Later evidence | Remaining limit |
|---|---|---|
| `6cc6f10ce7cd1d1f3e6da51294caca6c`, `d10b637f22211ec447c5ece88e57e506` | Baseline Caddy rendering clears the forwarded auth query through `uri /v1/activate?`; source correction bba2de1 and a renderer regression exist | This review did not rerun their exact live browser cases. Keep the gateway's query rejection guard; the fix is in forwarding |
| `8e73e0097028218b3216103c86596b9b` | Later retained lifecycle smoke passed all 23 checks, including restart clean URL | This does not repair F04's separate ignored false readiness result |
| `4d413785f6471c09424195af3ca1755e` | Read-only image transaction status now exists and was successfully used by this review | It does not supply a complete cross-store deployment history; F12 remains |
| `6c8b5ebfab40731d9b245eacbcfbdfbe` | A concrete PostgreSQL helper and later real isolated drill/canary now exist | The 19 constraint differences and broader verification contract remain F07 |
| Recent initializer diagnostic/refusal reports | Final Amar Sonar apply/status shows generation 10, exact application revision agreement, five healthy services, and runtime/source/edge ready | This is reconciliation of existing runtime, not fresh initializer rerun proof. Exact editor bundle digest and installed control revision remain supplied owner assertions |

These should be reviewed for precise status updates after their owning task attaches the actual evidence. This research did not mutate the feedback log or broadly close an entire family.

## Prior closures whose scope must be preserved

`009845006beb244f191c461c3d75cf55` described both a failed large page and exposure of retained job data in the error. Its actual closure cites d3f3af4 and the error-boundary tests, explicitly says a small page passed, and explicitly says the large page was not replayed. Current source keeps the safe error boundary. F10 establishes a remaining paging defect; it does not invalidate that narrower security correction.

`a1fc66d41f603894e18bc4092a0a9aba` records a prior correction that rejects a global instance selector before remote lookup/mutation. That scope does not cover F01's inventory-based cleanup ownership or F02's unqualified nested URL command. Similarly, `b459d15a68e801aa605881b9864a81ee` records branch rejection before remote submission and a small successful remote history read. Neither is a complete exact-release deployment receipt.

`ae5d9f9f931491295cb69a91e4517a60` is a useful UX report: a bare accepted job ID and immediate exit zero are easy for a wrapper to mistake for passed tests. The JSON reportedly says accepted/queued correctly. Do not redefine every acceptance exit as a test failure; make the asynchronous outcome explicit and provide the exact follow command, while CI callers wait for a terminal result.

## High-value reports that still need bounded verification

The following remain operational leads, not newly confirmed findings:

- `24e6756299d9f7e39e28d87125338f2b` reports reproducible macOS/OrbStack failure under piped stdout but success through a TTY. The baseline Docker preflight delegates to the normal captured subprocess path; that source alone does not establish the reported TTY root cause. Test the real shell, Node wrapper and preflight separately on the stated platform. Preserve stdio and timing evidence; do not make a PTY workaround or longer timeout the default without isolating the cause.
- `171fd36929aa7c319b848232afbba857` alleges signed stale runtime environment; `0062bddad85e9b8091b4baa569793274`, `7dc12e973591c4f46d1fe0be41ca4d46`, and related records report protected activation refusals. Check exact source, manifest, artifact, controller capability and original transaction. A refusal alone does not justify loosening artifact verification or rebuilding a retained image.
- `d5ff87fa40df14ba6a87778e268a0f9b` reports that settlement cannot explain which owned effect is not quiescent. This supports W7's bounded diagnostics design. Any containment capability is separate authority-changing work requiring a scoped contract and negative ownership cases.
- Earlier supervisor launch, SSH timeout, network capacity and resource-inventory reports need their exact target/runtime and terminal-job evidence. A currently healthy controller or a passing small page does not prove every old case fixed. Conversely, an old unreachable host is not proof of a current parser defect.
- Preview TLS/public-route and hosted source-sync reports need owned disposable fixtures. F11 confirms missing completion proof in source; the exact reported certificate or concurrent-source cause remains separate.

The [per-record disposition file](research/feedback-dispositions.md) keeps unverified cases visible. It intentionally does not convert an untrusted detail or a mechanical overlap count into another numbered finding.

## Repair and closure order

Use the [detailed plan](plan.md): first preserve cleanup/URL ownership, capture freshness and secret-free output; then accept the initializer, readiness and job-control corrections through real adapter/product gates. Complete pre-effect recovery admission and the existing restore correction under their current owners. Improve joined diagnosis/receipts so the next failure does not require another private forensic session.

Close a feedback record only with its original trigger, exact correction revision, actual regression result, required runtime receipt, and stated remaining limits. Link duplicates to that accepted cause. Keep source-fixed, runtime-accepted, unsupported and not-reproduced decisions distinguishable. No retrospective claim can recover evidence that was never retained.
