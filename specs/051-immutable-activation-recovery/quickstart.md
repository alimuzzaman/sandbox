# Quickstart: Immutable Activation and Recovery

This is a post-implementation verification guide. Use only synthetic artifacts and fake
runtime/edge adapters until separate remote/deployment authorization exists.

1. Run the focused 051 model/policy/repository/init/runtime/service/CLI suites.
2. Prove caller plan/proof alone refuses; exact machine activation binding plus a Feature
   050 prepared proof lease/pin acquired before validation and held through durable host
   acceptance is required. Cover lock order, durable activation-owner/request holder,
   pre-acceptance deadline refusal, post-acceptance promotion after deadline, no process or
   unrelated recovery adoption, compaction races, every acceptance crash point, idempotent
   promote/cancel, and terminal release.
3. Run every test with credentials/trust/broker/pull/build witnesses and require zero.
4. Run the crash matrix at every durable/effect boundary; possible effects must fence.
5. Run the pairwise race matrix across activation, adoption, rollback, Feature 048,
   apply, sync, login/setup, edge continuation, and registered target mutations.
6. Run distinct `sb host image recover` tests for first observation, non-authorizing 051
   provisional durability, crash resume, second observation, exact pre/post evidence/epoch
   comparison, and every activate/rollback phase crossed with `exact_new`, `exact_prior`,
   `neither`, and `ambiguous`. Prove `neither`/`ambiguous` never promote, `exact_prior`
   never advances generation, only receipt-complete phase-legal `exact_new` promotes, and
   failed-apply `sb host recover` remains unchanged.
7. Run zero-init adoption positives and all init-bearing/external-receipt negatives.
8. Run deterministic forward-subject rollback positives and all grant/data negatives.
9. Run existing non-opt-in hosting and Feature 048 suites unchanged.
10. Verify old opaque state is preserved and no artifact claims remote/production proof.
11. Prove the shared recovery repository is the only outer `hosts.json` parser/writer/locker,
    the activation repository has nested-codec/candidate authority only, and
    `activation/__init__.py` exports only the planned closed interfaces.

Suggested local gate after implementation:

```text
python3 -m unittest \
  tests.test_hosting_image_activation_models \
  tests.test_hosting_image_activation_policy \
  tests.test_hosting_image_activation_repository \
  tests.test_hosting_image_activation_init \
  tests.test_hosting_image_activation_runtime \
  tests.test_hosting_image_activation_service \
  tests.test_hosting_image_activation_recovery \
  tests.test_hosting_image_activation_races \
  tests.test_hosting_image_activation_cli \
  tests.test_architecture_boundaries
```

This gate is local acceptance only. Live registered-host, edge, deployment, rollback,
and production proof remain open until separately authorized and observed.
