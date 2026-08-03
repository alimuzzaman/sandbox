"""Compile the fixed supervisor-to-guest AppArmor transition profile."""

from __future__ import annotations


def compile_apparmor_profile(machine_id, policy_digest):
    profile = f"sandbox-native-{machine_id}"
    return f"""#include <tunables/global>

# Sandbox policy {policy_digest}
profile {profile} flags=(attach_disconnected,mediate_deleted) {{
  #include <abstractions/base>
  capability,
  network,
  mount,
  remount,
  umount,
  pivot_root,
  ptrace,
  signal,
  dbus,
  userns,
  # `/**` matches paths BELOW the root, never the root directory itself, so
  # systemd-nspawn was denied `open /` while pinning the outer mount namespace
  # and every machine failed to start. Read access to the directory entry is
  # not access to its contents, which `/**` already governs.
  / r,
  /** rwklm,
  /** ix,
  /usr/lib/systemd/systemd cx -> guest,
  /lib/systemd/systemd cx -> guest,
  /sbin/init cx -> guest,

  profile guest flags=(attach_disconnected,mediate_deleted) {{
    capability audit_write,
    capability chown,
    capability dac_override,
    capability fowner,
    capability fsetid,
    capability kill,
    capability net_bind_service,
    capability setfcap,
    capability setgid,
    capability setpcap,
    capability setuid,
    capability sys_chroot,
    # The machine's PID 1 needs sys_admin for the typed API-filesystem mounts
    # enumerated below, and nothing else in this profile grants a mount
    # primitive. Every service that runs untrusted code strips the capability
    # in its own unit (CapabilityBoundingSet), and exec payloads transition into
    # the payload profile, which denies it outright (FR-044).
    capability sys_admin,
    network inet stream,
    network inet6 stream,
    network unix stream,
    network unix dgram,
    signal,
    dbus,
    # Read-only ptrace confined to this machine's own processes (systemd's
    # generators read sibling /proc entries, and PID 1 reads its children's).
    # Three things were wrong here and each one alone breaks the guest:
    #   * the emitted peer was `@sandbox-native-<id>` -- an AppArmor variable
    #     reference to a variable that does not exist, so it matched nothing;
    #   * the peer must name the CHILD profile, which is what confined
    #     processes actually run under;
    #   * the kernel checks `read` on the reader and `readby` on the target,
    #     so granting only `read` still denies half of every pair.
    # A denial here breaks systemd's session bookkeeping, and `machinectl
    # shell` then exits 0 with no output at all. A blanket `ptrace,` would
    # reach outside the machine and is deliberately absent.
    ptrace (read, readby) peer={profile}//guest,
    ptrace (read, readby) peer={profile},
    # The guest's PID 1 mounts its own API filesystems inside the machine's
    # mount namespace; without them it dies with "Failed to mount tmpfs ...
    # Permission denied" before any service starts. These are enumerated by
    # type and target on purpose: the guest must not hold a general mount
    # primitive, which stays with the bwrap profile.
    mount fstype=tmpfs -> /run/lock/,
    mount fstype=tmpfs -> /dev/shm/,
    mount fstype=tmpfs -> /tmp/,
    mount fstype=cgroup2 -> /sys/fs/cgroup/,
    mount fstype=mqueue -> /dev/mqueue/,
    # systemd-logind binds the machine's own root aside while it sets up its
    # private mounts, and the credential generator needs a ramfs for secrets
    # that must never reach disk. Both stay inside the machine's namespace.
    mount options=(rw,rbind) -> /run/systemd/mount-rootfs/,
    mount options=(rw,rbind) -> /run/systemd/mount-rootfs/**,
    mount fstype=ramfs -> /dev/shm/,
    mount options=(rw,remount) -> /run/lock/,
    # Propagation changes only: no filesystem is attached, and the machine's
    # own init needs them during early boot (`(sd-gens)` makes / rslave).
    mount options=(rw,rslave),
    mount options=(rw,rprivate),
    mount options=(rw,rshared),
    mount options=(rw,runbindable),
    # Remounting an existing bind read-only only ever removes access. Flag
    # sets are matched exactly, so every combination the guest's generators
    # actually use is listed: /dev/pts/ arrives without `nodev`, and / arrives
    # with `nodev` but without `nosuid,noexec`.
    mount options=(ro,remount,bind),
    mount options=(ro,remount,bind,nodev),
    mount options=(ro,remount,bind,nosuid,noexec),
    mount options=(ro,remount,bind,nosuid,nodev,noexec),
    umount /run/lock/,
    umount /dev/shm/,
    umount /tmp/,
    / r,
    /** rwklm,
    # `cx` names a child of the CURRENT profile, so the kernel looked for
    # `guest//bwrap` and refused the exec with "profile transition not found".
    # The bwrap profile is a sibling child of the top-level profile, so it has
    # to be addressed by its full name.
    /usr/bin/bwrap px -> {profile}//bwrap,
    /** ix,
  }}

  # Only root can execute /usr/bin/bwrap in the managed image (0750). This
  # transition owns the narrowly-scoped namespace/mount setup, then every
  # command exec transitions irreversibly into the payload profile.
  profile bwrap flags=(attach_disconnected,mediate_deleted) {{
    capability chown,
    capability dac_override,
    capability fowner,
    capability setgid,
    capability setuid,
    capability sys_admin,
    capability sys_chroot,
    # bwrap drops the bounding set itself (setpcap) and reads its child's
    # /proc entry while wiring the sandbox up (sys_ptrace). Without them it
    # fails part-way through, and the symptom surfaces as an unrelated
    # "Can't mount proc on /newroot/proc: Operation not permitted". This is
    # the trusted root-only setup step; the payload profile below denies both.
    capability setpcap,
    capability sys_ptrace,
    userns,
    mount,
    remount,
    umount,
    pivot_root,
    network inet stream,
    network inet6 stream,
    network unix stream,
    network unix dgram,
    signal,
    /** rwklm,
    /** cx -> payload,
  }}

  profile payload flags=(attach_disconnected,mediate_deleted) {{
    #include <abstractions/base>
    network inet stream,
    network inet6 stream,
    network unix stream,
    network unix dgram,
    signal,
    /** rwklm,
    /run/credentials/sandbox/* r,
    deny /run/credentials/** wklmx,
    deny /run/sandbox-native-credentials/** rwklmx,
    deny /run/systemd/** rwklmx,
    deny /run/dbus/** rwklmx,
    /** ix,
  }}
}}
"""


class AppArmorCompiler:
    def compile(self, policy):
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "profile": f"sandbox-native-{policy.machine_id}",
                "guest_profile": f"sandbox-native-{policy.machine_id}//guest",
                "bwrap_profile": f"sandbox-native-{policy.machine_id}//bwrap",
                "payload_profile": f"sandbox-native-{policy.machine_id}//payload",
                "content": compile_apparmor_profile(policy.machine_id, policy.digest)}


class ManagedAppArmor:
    """Install and observe only the digest-bound per-machine profile."""

    def __init__(self, *, process, helper):
        self.process = process
        self.helper = helper

    def plan(self, policy):
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "profile": f"sandbox-native-{policy.machine_id}",
                "guest_profile": f"sandbox-native-{policy.machine_id}//guest",
                "bwrap_profile": f"sandbox-native-{policy.machine_id}//bwrap",
                "payload_profile": f"sandbox-native-{policy.machine_id}//payload"}

    def _run(self, verb, plan):
        return self.process.run(("sudo", "-n", self.helper, verb,
                                 plan["machine_id"], plan["policy_digest"]), timeout=120)

    def install(self, plan):
        result = self._run("apparmor-install", plan)
        if result.returncode != 0: raise RuntimeError("managed AppArmor install failed")
        return {"ok": True, "mutated": True}

    def status(self, plan):
        result = self._run("apparmor-status", plan)
        return {"ok": result.returncode == 0, "mutated": False,
                "stdout": result.stdout or ""}

    def remove(self, plan):
        result = self._run("apparmor-remove", plan)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}
