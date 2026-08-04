# Ubuntu 24.04 nginx: live provisioning and lifecycle (039 T047)

**Scope**: whether a managed-native instance provisions end to end on a real host with the
nginx stack, passes every effective-isolation gate, serves, and destroys cleanly. Everything
below went through the runtime service; no step was performed by hand.

**Host**: Ubuntu 24.04.4 LTS, systemd 255, AppArmor 4, x86_64. 2026-08-04.

**Status**: provisioning, isolation and lifecycle proven. The hostile-probe matrix,
resource-exhaustion runs, Apache variant and foreign-service coexistence are NOT covered
here and remain open under T047.

## Provisioning

```json
{"ok": true, "state": "ready",
 "backend": {"address": "10.203.118.246", "port": 8080},
 "health": {"ok": true, "state": "ready"}}
```

All four guest units active: `mariadb.service php8.3-fpm.service nginx.service cron.service`.
The backend answers over its veth (`HTTP 301`, WordPress redirecting to its configured
home URL).

## Effective isolation, read from the running payload

```text
apparmor_profile         sandbox-native-<id>//bwrap//&sandbox-native-<id>//payload
nested_userns            False
seccomp                  True
no_new_privileges        True
capabilities             []
control_sockets          []
unexpected_host_mounts   []
reachability             host false, sibling false, metadata false, public false
cgroup_limits            match the policy exactly
```

## Lifecycle

```text
destroy    ok=true  cleanup_complete  10.3 s   residual: none
repeat     ok=true  cleanup_complete  unmutated
host after machines 0, nft tables 0, profiles 0, images 0, records 0
```

Detail in `cleanup.md`.

## What it took to get here

Provisioning had never before reached a running machine. Reaching it required, in order,
each found by running the product against the host and reading the kernel's own audit
records rather than reasoning about them:

1. The payload profile entered by stacking rather than a domain transition, which
   NoNewPrivileges forbids — and with any `px` rule present, every exec inside bubblewrap
   was refused, including `/bin/sh`.
2. Three bubblewrap flags dropped that cannot work inside a machine: `--disable-userns` and
   `--assert-userns-disabled` write read-only `/proc/sys`, and `--unshare-pid` forces a
   fresh procfs that a non-initial user namespace may only mount when `/proc` is fully
   visible, which nspawn deliberately prevents.
3. A seccomp filter for nested user namespaces, because AppArmor cannot carry that
   guarantee here (see `payload-boundary.md`).
4. Seven AppArmor mount forms the guest needs for systemd's own per-unit sandboxing:
   MariaDB uses `ProtectSystem` and `ProtectHome`, and without them it failed with status
   226, `EXIT_NAMESPACE`, reported only as "the control process exited with error code".
5. Ownership of the document root and the PHP socket directory moved to the identity that
   actually writes them. Everything reaches them through bubblewrap, which maps the
   machine's root to 33 inside, so a directory owned by www-data is one the sandbox cannot
   write — WordPress could not create `wp-config.php`.
6. `NoNewPrivileges` and `RestrictNamespaces` removed from the units that launch
   bubblewrap. Both block it: entering the bwrap profile is a domain transition, and
   bubblewrap must unshare the namespaces it builds the sandbox from. Bubblewrap applies
   NoNewPrivileges itself before executing the payload, so untrusted code still runs under
   it, and those units still strip the escape capabilities.
7. php-fpm given a log it is allowed to write; the distro default is read-only inside the
   sandbox and FPM refuses to start without it.

## Not covered

- The hostile-probe matrix through every untrusted execution path.
- Resource exhaustion and sibling isolation under load.
- The Apache variant (`ubuntu-apache.md`).
- Coexistence with foreign host services on the default ports
  (`ubuntu-package-coexistence.md`).
