# Data Model: Local Images for Hosted Deployments

## Local Build Receipt

A closed, self-digesting, size-bounded document binding project/environment, exact source/subtree revision, Compose project, `linux/amd64`, hermetic render identity, service roles, image references, symbolic secret bindings, and per-artifact configuration/layer/archive identities. Unknown fields, duplicates, unsafe archive names, inconsistent digests, or excess size/count refuse before effects.

## Image Requirement

- **built**: one or more services consume one locally built exact artifact;
- **external**: a non-built explicit reference pinned by registry digest.

Every declared primary/background/init service maps to exactly one requirement. Built requirements map to one archive; external requirements never do.

## Remote Verification Receipt

A closed, digest-addressed document embedding the validated local receipt, registered remote binding, authenticated machine identity, daemon identity, engine platform, fresh per-reference observations, and transfer dispositions (`loaded`, `already_present`, `verified_external`). It is published only after complete verification and never asserts deployment.

## Render Identity

Canonical identity over source revision, Compose project/files/profiles, service roles, path-normalized build definitions, image refs, literal non-secret inputs, derived source values, and symbolic BuildKit secret bindings. It excludes secret values, timestamps, controller paths, and ambient variables.

## State Transitions

```text
eligible source
  -> local receipt complete
  -> transfer refused | transfer unknown | remote receipt complete
  -> apply refused before Compose | ordinary no-build apply admitted
  -> existing apply success/failure/recovery lifecycle
```

Build and transfer never transition a delivery to deployed, active, or healthy.
