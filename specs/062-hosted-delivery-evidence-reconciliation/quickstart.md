# Quickstart: validate delivery evidence reconciliation

Prerequisites: 054 T040-T047 accepted; 060 conversion and lease landed; disposable remote.

1. **Tests**: `.cli-venv/bin/python -m unittest tests.test_delivery_reconcile tests.test_delivery_fence_release tests.test_delivery_identity_scope tests.test_delivery_job_outcome tests.test_delivery_retry_link tests.test_architecture_boundaries`
2. **Lost client, landed deploy**: `host apply` a target; kill the local process during the Compose phase; let the remote finish. `host apply` again: prints `adopted_by_observation`, admits without retire.
3. **Killed before Compose**: kill during transfer; next apply auto-releases (Compose never started) with the notice line.
4. **Second checkout**: clone the project elsewhere; `delivery inspect`, `host reconcile`, `host retire-delivery` succeed there.
5. **Image**: interrupt an image activation after preflight; `host reconcile` uses the request-bound record and corroborating observation.
6. **Mismatch**: change one config value on the remote by hand (disposable only); reconcile reports `diverged` and names settlement; no writes (compare remote state before/after).
7. **Waiter**: `job-start --wait` a hosted apply, kill its supervisor; waiter reconciles once and exits by outcome.
