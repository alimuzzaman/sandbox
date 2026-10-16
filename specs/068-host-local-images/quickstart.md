# Quickstart Validation: Local Images for Hosted Deployments

1. Prepare a clean tiny throwaway hosted project under `tmp/` with explicit images, a built primary, shared consumer, initializer, and fake approved BuildKit secret canary.
2. Run structured `host image build-local` for its exact revision.
3. Verify owner-only closed receipt, exact `linux/amd64` identities, complete service coverage, and zero canary occurrences in output/receipt/tracked files.
4. Repeat under unrelated ambient variables; render identity must not change. Change one declared input; identity must change or build must refuse.
5. After this real CLI acceptance, add focused transport regressions for load/skip, external verify, target/daemon/platform mismatch, corrupted archive, interruption-as-unknown, and bounded output.
6. Add apply regressions proving matching receipt admission and every mismatch refusal before `_run_compose` mutation.
7. Run focused `PYTHONPATH=. python3 -m unittest ...` modules using synthetic subprocess environments; no new/changed test may exceed 5 seconds.
8. Run required broader gates once focused behavior is proven.

Real remote transfer/apply is separately gated by the user's deployment timing and migration authorization.
