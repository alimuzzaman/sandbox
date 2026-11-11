# Spec 089 source checkpoint, 2026-11-11

This is an incomplete source checkpoint for shadow integration, not a release or
cutover candidate. Root owns integration tests and live authority.

US1 source implements genuine explicit owner/managed identity initialization,
service-context read/validate, root-controlled extension metadata, no automatic
execution capabilities, authenticated discovery and managed HTTP admission. The
first ten identity/admission fixtures passed before subsequent review fixes.
Those fixes and new extension/HTTP/owner-lifecycle/uninstall fixtures have not
been run or independently re-reviewed in this checkpoint.

US2/US3 lifecycle and migration modules are unreviewed partial source. There is no
installed production host-maintenance/fencing/copy/switch adapter or CLI managed
install/update/remove composition. Per-request migration locking does not prove
installation-wide exclusion. Consistent locator/bind migration, stage continuity,
post-write recovery and install crash/idempotence handling remain unfinished.
No actual install/migration/backup/restore proof exists here. Do not call these
partial host-effect APIs on a real host.

The runtime tasks in tasks.md remain unchecked. Existing project/workspace/job/
delivery/lease/volume/domain/credential identities, root xCloud ingress and shared
workloads remain protected. Nothing in this checkpoint authorizes destructive
uninstall, a live migration or a service update. Full detailed check commands,
prior reports and limitations are in the user-requested scratch checkpoint report.
