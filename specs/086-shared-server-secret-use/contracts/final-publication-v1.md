# Final success publication source v1

An effect's successful CAS is not public success. The generic owner factory must
exit its actual credential/native contexts with final current checks and expose
its private same-process completed observer. Only that source-owned dispatcher
may then finish the credential row internally.

`admission.require_succeeded_operation(receipt, *, intent_digest)` is terminal
observation only. It requires the original private committed receipt and issuer,
full canonical intent/admission/preflight identity, current actor, registered
resolver and original broker proof/source revision, retained SUCCEEDED state,
no cancellation and unexpired original deadline. It grants no consumption;
`require_committed` deliberately refuses a succeeded row.

085's concrete `writer.retire_completed(receipt)` uses that terminal observation
and its genuine completed observer to retire the exact held canonical lease.
After retirement and final current checks, `ledger.publish_success` writes the
separate credential_publications marker through the jobs transaction owner.
Until that marker exists, even an internal succeeded row's public projection is
effects_unknown with credential_publication_unknown. Restart/lost response cannot
turn an unpublished row into success or reconstruct authority from JSON.

Retirement or final persistence failure invokes `writer.withhold_success` for
the exact generation and `ledger.withhold_success` for the exact unpublished
row/version. Unknown remains unknown and cannot rerun an effect. A marker lost
after safe retirement can require supported inspection; it never authorizes a
new identity or blind replay. The extra table is additive in the existing jobs
database and does not change the job submission schema version7. No migration
or live state was executed during source implementation.

Original-request lookup uses authenticated installation/incarnation, actor,
project and request identity before mutable source/artifact resolution. Public
results contain names/status only, never paths, tokens or private receipts.
