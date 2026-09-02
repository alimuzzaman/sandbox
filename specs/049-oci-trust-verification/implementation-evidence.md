# Implementation Evidence: OCI Trust and Verification

This table records source provenance only. It is not test, registry, host, deployment,
or production evidence. No test, repository import, compile, lint, diff check, or
product runtime command was run during implementation. Fixed vector digests were
authored by piping independently written literal canonical bytes, prefixed by their
literal domain and NUL separator, through standalone `openssl dgst -sha256`; no
repository code or fixture was imported or executed.

| Old path or current primitive | Owning task | Why valid for Feature 049 | Focused review evidence |
|---|---|---|---|
| `sandbox/config/manifest.py` explicit provider tuples | T017 | Current manifest is the supported config registration boundary and keeps project and machine ownership separate. | Added distinct optional common and nested machine providers; the common provider removes inherited values unless the primary carrier explicitly declares the key. |
| `sandbox/config/compose.py` raw common-provider carriers | T016 | Current Compose schema already preserves raw domain/runtime/secret inputs for later common normalization. | Captures an explicit `{declared, project_primary}` carrier before override/label handling; those later layers never merge `hostingImages`. |
| `sandbox/config/wordpress.py` raw common-provider carriers | T016 | Current WordPress schema uses the same common-provider boundary. | Captures the same primary carrier before legacy/global, override, and label loading; inherited values are ignored. |
| `sandbox/hosting/recovery/models.py` bounded immutable model conventions | T013-T015, T025 | Existing frozen values and closed parser style are safe local conventions; recovery digest authority is not reused. | New receipt, policy, and plan digests have separate Feature 049 domains and no recovery import. |
| staged `f047-final:sandbox/config/hosting_images.py` closed schema and unique-service checks | T014, T016 | Rewritten from pattern after checking that strict key equality and duplicate refusal are independently useful pure validation. | No Cosign, credential, Compose inspection, file path, or policy field from the abandoned implementation was copied. |
| staged `f047-final:tests/hosting_image_fixtures.py` canonical synthetic fixture style | T001-T003 | Rewritten from pattern: public repeated digest markers and builder functions make mutations explicit and secret-free. | Expected receipt, policy, plan digests and complete plan bytes are fixed independent vectors; production verification is not used to build the expected plan. |
| `sandbox/hosting/images/models.py` current canonical-value primitives | T013-T015, T025 | New owner for exact built-in traversal, running budgets, immutable channels, the delivery projection, and whole-plan consumer validation. | Focused source review checked pre-iteration subclass refusal, cumulative node/byte charging, closed four-field provenance, separate digest domains, and recursive plan equality. |
| `sandbox/hosting/images/trust.py` exact immutable inputs | T018, T021-T023, T026 | New sole pure decision surface with no mapping/callback/effect parameter and an opaque legacy refusal adapter. | Focused source review checked exact trusted/project/receipt types, machine-owned primary topology, catch-all safe projection without exception text, and no partial plan. |

## Sol High review repairs

| Review finding | Repair | Focused source evidence |
|---|---|---|
| Channel types could be interchanged or parsed too late. | Machine config issues a private exact policy token; project and receipt have separate owning normalizers; the verifier accepts only the three exact immutable types. | The token/issuer are absent from public exports, ordinary token construction lacks the closure-owned capability and refuses, mapping/list/string/integer subclasses are rejected before traversal, and raw/interchanged calls refuse. |
| Provenance was an extensible mapping. | Replaced it with exactly four SHA-256 provenance digests plus a SHA-256 build identity, canonical source repository, and exact source revision. | Models, contracts, docs, fixtures, and negative path/traversal/token/authorization/API-key cases use namespace-specific allowlist grammars; there is no blacklist or arbitrary metadata retention. |
| Size checks occurred after copying/serialization. | Added a shared traversal budget charged for every container, key, scalar, and encoded byte before adding it to the normalized copy. | Cumulative small-node, cumulative byte, depth, collection, string, and pre-decimal integer refusal cases are authored. |
| Project intent could inherit from non-primary layers. | Both schema kinds capture the raw primary project layer before other loaders; common normalization deletes global/legacy/override/label values when the primary key is absent. | Equivalence, primary-wins, override, label, global, and absent cases are authored for WordPress and Compose. |
| Expected plan data was self-derived and mutation coverage was partial. | The fixture contains fixed canonical bytes and fixed receipt/policy/plan digests; recursive tests mutate every leaf and remove/add fields at every plan/channel object. | Coverage explicitly reaches schema, authority/policy, receipt, image, projection, topology, signature mode, and `plan_digest`, plus adversarial containers and effect witnesses around channel objects/boundaries. |

All repairs above received independent Sol High source review before final validation.

## Final Sol High closure repairs

- The machine authority token is now a closure-defined private exact type. Its
  construction capability is retained only inside the models module's issuer closure;
  only machine config normalization imports that private issuer. The public package
  exports neither token nor issuer, and direct ordinary construction refuses.
- Generic opaque text grammars were removed. Authority, selector, scope, service,
  canonical-domain, repository, source-revision, digest, platform, and media-type
  namespaces now have separate validation. Provenance and build identities are
  digest-only, while source repository/revision cannot carry paths or free-form secrets.
- Test and documentation tasks affected by these findings were reopened and re-marked
  complete after editing. Final focused validation passed after reviewed corrections.

## Open evidence gates

- Focused Feature 049 tests passed: 37 tests.
- Config/hosting compatibility selectors passed: 192 tests.
- Compile and `git diff --check` passed.
- Pre-source RED was waived by the production-before-tests instruction; no observed
  RED evidence is claimed. The required independent Sol High security/source review
  passed, but it was not a human review.
- Registry, artifact, remote, staging, runtime, deployment, edge, and production proof
  are outside Feature 049 and remain open.
