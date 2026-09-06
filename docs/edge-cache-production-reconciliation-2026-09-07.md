# Edge-cache and production controller reconciliation

Source-only integration of `f0370472dd839c6658eaaf7ea1a0feee00b745fc`
(edge-cache purge) with `7902e44614c152f8f9622ddae55642829edc2eb8`
(authenticated machine identity and production activation fixes).
The merge was conflict-free. Both histories and their matching documentation
and tests are preserved. No controller was installed and no hosted target changed.

Local durable job `0c9410715ec79740ba9c398dfeab205b` completed with
lifecycle `succeeded`, exit 0: 152 tests across edge cache, activation CLI,
private source, provisioning, staging process/secrets/v2, activation v2,
activation recovery, and remote hosting images. `git diff --cached --check`
also passed. This is source validation, not live activation evidence.

The production task still owns the live controller and has a further admission
fix underway. Lenzora must not pin this intermediate merge or advance its held
dev/main branches until that final revision is reconciled and validated.

Follow-up source integration includes `931eda2c25c3327148d36ebd4a15cfb00fcf08a6`.
It allows initial edge planning after terminal pre-effect refusal history,
while retaining verification for any effect-bearing or uncertain history.
Local durable job `467c587e56055e243a690449c329ef2a` completed `succeeded`,
exit 0: 54 tests across edge cache, activation CLI/v2, and recovery.
This follow-up merged without conflicts. The live production hold still applies.

The next production fix, `dc2fa2ffbcb6d1f866de884ca9835c82b1ad6930`,
normalizes missing/null Compose dependencies to an empty mapping while
retaining malformed-value refusal. It merged cleanly. Local durable job
`7552f18c35277de3d6198948d825a135` passed 37 helper/private-source,
activation CLI, and edge-cache tests, lifecycle `succeeded`, exit 0.

Recovery scope repair `e69be7f91d25789250d94908113b438dc37b67ca`
adds the declared initializer services to recovery render admission while
retaining the persistent runtime projection. It merged cleanly. Local durable
jobs `b5163a1e09cbd7f648f2f24188381722` (54 regression tests) and
`0cc23a31a170fe9f83915757f5d63bd5` (two recovery CLI tests) both completed
`succeeded`, exit 0. The live uncertain transaction remains owned by the
production task; this branch performed no runtime effects.

Zero-container recovery repair `caa9a46ee8021d907d3e60a370835e1c748f06a1`
merged cleanly. The empty-runtime exception remains limited to a generation-zero
first activation with stable observation identities and no retained prior generation.
Partial or malformed runtime observations remain refused. Local durable job
`c3701a77c27811e6548b6f93441bf966` passed 52 activation/recovery/private-source
and edge-cache tests, lifecycle `succeeded`, exit 0.
