# Contract: Recovery Profile Catalog

The committed catalog is versioned and non-secret. Loading validates the entire catalog before
returning any profile. Unknown fields fail closed for schema v1. Profiles may reference approved
environment variable names but never values. Source paths are resolved on the target and must
remain under declared roots after symlink resolution. Dependency cycles, duplicate IDs, unknown
capture adapters, shell strings, and broad implicit roots are errors.
