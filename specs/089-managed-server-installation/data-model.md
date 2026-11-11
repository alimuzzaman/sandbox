# Data model

Binding schema1: installation_id and incarnation random32hex; machine_identity
canonical24hex; ownership_marker24hex; runtime_revision24hex; policy_revision
nonempty bounded string; policy_digest64hex; execution_capabilities mapping of
installed handler name to positive integer version; layout_kind owner|managed.
Identity/incarnation persist across moves/upgrades; explicit replacement differs.

Persistent identity record contains only stable identities/layout, initialized
under exclusive owner-only lock. Policy record declares admitted-owner authority
and revision; managed policy root-controlled, owner policy owner-controlled.
Current binding is composed read-only from those records, current machine/service,
actual runtime and assembled extension verifiers. Policy digest covers canonical
policy. Extension descriptors do not contain or persist live listener FDs.

Lifecycle plan binds action/request/installation/incarnation/revision/path/owner
and observations. Migration journal binds source/destination and plan digest,
phases planned->fenced->staged->verified->activated->writes_open; failure/recovery
states retain completed checkpoints. Backup proof names verified encrypted set,
restore coverage and matching inventory digest. Post-write rollback is prohibited.
Paths in original receipts remain unchanged; live locator migration belongs to
schema-specific adapters. Conflicts remain visible rather than reset.
