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

The built-in public-route check is reachability diagnostics only. Until an edge adapter
returns a durable receipt bound to the exact target, generation, runtime observation,
route plan, and deployment identity, required-edge activation refuses `edge_incomplete`.
Machine policy bundles and their public rollback verification key are read no-follow from
an owner-only single-link regular file; Feature 051 never receives the signing key.
The private Compose render and its raw hash never cross SSH or enter state. The helper
returns only a closed allowlisted projection and a machine-keyed, target-scoped opaque
identity over the complete raw render. The owner-only machine master stays local. Only its
machine/target-derived binding key uses private stdin, and it is removed before Docker runs.
Arbitrary rendered values stay private. Top-level configs/secrets and external networks
refuse until exact byte/object snapshot authority exists.
Each service also retains only a target-scoped HMAC identity of its private Compose
configuration hash. Fresh running, post-edge, and recovery observations reconstruct that
identity inside the remote helper and require an exact match. Raw `docker ps` labels,
inspect environment/arbitrary labels, and raw Compose hashes never cross SSH.
Activation admission runs only after the stage ledger has decoded and canonical-byte
compared the complete retained proof under the target -> host -> stage lock order and has
matched its exact ledger authority and record revision.
