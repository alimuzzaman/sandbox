# Registered credential resolution source v1

`RegisteredCredentialResolver(home=..., ledger=...)` uses the actual jobs
credential repository, project registry API, configured hosting profiles, and
genuine installation binding. Reads never initialize installation identity.
An installed execution capability and exact source/profile/artifact/manifest
selectors are required. Missing registration has a specific refusal.

Before actor admission,
`read_invocation_recipe(*, project_ref, profile_ref, environment, source_commit, name)`
returns the same typed recipe metadata using exact server-profile selectors and
current installation binding. It constructs a `RecipeSelection`, not a dummy
CredentialIntent, actor, target reference or runtime identity. The supervisor
must bind the actual kernel peer/original invocation before issuing actor proof.

For a new stack, `read_recipe(intent) -> RegisteredCredentialRecipe` reads
`hosting_credentials.recipes[profile_ref]` in the server project record. Recipe
fields are exactly project, environment, source_commit, artifact_relative,
artifact_digest, manifest_relative, manifest_digest, native_mutations and
consumer_actions. Paths are server-relative registrations, never request paths.
The reader validates owner, canonical installation/project/environment, Git
commit/subtree, every artifact blob, complete tree membership, and the manifest
blob and digest. It returns frozen metadata including source_root, checkout_root,
manifest_path, manifest_digest, source_tree, artifact_path/digest, canonical,
registration_digest, actual native_ports and owner_native_mutation. It never
reads an existing target lifetime as a substitute for a fresh preparation.

`resolve(stack_up intent)` additionally compares the exact retained preparation
to the full canonical intent and registered project. The 085 prepared-target
reader must independently validate the committed receipt and preparation before
turning this metadata into its sealed target/writer authority. This data record
alone is not a grant. Existing apply uses 085's genuine registered target reader.

Native mutations are actual `(RecoveryRepository, state_key)` tuples. Resolution
includes both the owner replica repository and every observed legacy alias whose
authenticated machine/marker pins identify this installation. Unknown alias pins
refuse. Registration must equal the observed set, including repository identity.
The `native_locks.hold_native_mutations` scope sorts by resolved state path, lock
directory, and key, and acquires the existing target mutation locks once. Exact
nested scopes reuse the hold. A different or partial nested set refuses.
Installation maintenance SH admission precedes this scope; the native scope
precedes canonical host/gate acquisition and remains through final authority
checks. Neither this lock scope nor controller-state serialization is a grant.

Production broker composition supplies the concrete resolver. Its registration
digest is pinned inside the authentic preflight proof and retained resolution
digest. Receipt, delivery, and completion revalidate the original proof, current
source revision, profile and registration. A repaired source cannot reopen the
old operation. Recovery without the actual process-held actor/preflight proofs
is inspection only, never receipt reconstruction from persisted claims.

Authenticated `recipe_registration.register_recipe` now measures and atomically
publishes metadata through the registry's compare-and-update API. It never
claims host resources or stages source. Same-name plans are immutable and repeat
only their exact original selection. A changed concurrent registry record
refuses; another recipe publication cannot silently erase it.

Optional hosting profile `recipeTemplate` binds the committed template path and
its source digest. The actual `stack_template.render` produces a distinct
canonical project and hosts for the requested new name. Candidate ports are
nonbinding metadata and require atomic admitted host availability checks.
The frozen rendered plan lives under `recipe_instances[profile/name]`, with its
own manifest and registration digests. Reads rerender and compare that plan;
they never reuse an unrelated existing target or old GS reservation. Typed
recipe data includes manifest_bytes, future manifest_path, template_path/digest
and rendered_ports. Source/manifest staging remains post-admission only.

The generic fresh/existing dispatcher, application
consumer facility, installed capability registration and runtime acceptance are
separate required integration work. This source contract advertises none.
