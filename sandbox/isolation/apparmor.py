"""Compile the fixed supervisor-to-guest AppArmor transition profile."""

from __future__ import annotations


def compile_apparmor_profile(machine_id, policy_digest):
    profile = f"sandbox-native-{machine_id}"
    guest = f"{profile}//guest"
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
  /** rwklm,
  /** ix,
  /usr/lib/systemd/systemd cx -> guest,
  /lib/systemd/systemd cx -> guest,
  /sbin/init cx -> guest,

  profile guest flags=(attach_disconnected,mediate_deleted) {{
    #include <abstractions/base>
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
    network inet stream,
    network inet6 stream,
    network unix stream,
    network unix dgram,
    signal,
    dbus,
    /** rwklm,
    /** ix,
  }}
}}
"""


class AppArmorCompiler:
    def compile(self, policy):
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "profile": f"sandbox-native-{policy.machine_id}",
                "guest_profile": f"sandbox-native-{policy.machine_id}//guest",
                "content": compile_apparmor_profile(policy.machine_id, policy.digest)}


class ManagedAppArmor:
    """Install and observe only the digest-bound per-machine profile."""

    def __init__(self, *, process, helper):
        self.process = process
        self.helper = helper

    def plan(self, policy):
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "profile": f"sandbox-native-{policy.machine_id}",
                "guest_profile": f"sandbox-native-{policy.machine_id}//guest"}

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
