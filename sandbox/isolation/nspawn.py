"""Compile fixed nspawn descriptors from a verified policy."""

from __future__ import annotations


class NspawnCompiler:
    def compile(self, policy):
        mounts = [f"{item['source']}:{item['target']}:norbind" for item in policy.read_only_mounts]
        writable = [f"{item['source']}:{item['target']}:norbind" for item in policy.writable_mounts]
        # Container init needs a small bounding set to switch service users and
        # bind the web port. Host-control and kernel-facing powers are removed.
        dropped = (
            "CAP_AUDIT_CONTROL", "CAP_DAC_READ_SEARCH", "CAP_IPC_OWNER", "CAP_LEASE",
            "CAP_LINUX_IMMUTABLE", "CAP_MKNOD", "CAP_NET_ADMIN", "CAP_NET_BROADCAST",
            "CAP_NET_RAW", "CAP_SYS_ADMIN", "CAP_SYS_BOOT", "CAP_SYS_NICE",
            "CAP_SYS_PTRACE", "CAP_SYS_RESOURCE", "CAP_SYS_TTY_CONFIG",
        )
        return {
            "Exec": {
                "Boot": "yes",
                "PrivateUsers": f"{policy.uid_map['base']}:{policy.uid_map['count']}",
                "PrivateUsersOwnership": "map", "Capability": (),
                "DropCapability": dropped, "AmbientCapability": (),
                "NoNewPrivileges": "yes",
                # Mount/user-namespace setup is admitted only for the root-only
                # bwrap transition by AppArmor. Guest and payload profiles deny it.
                "SystemCallFilter": "@system-service ~@raw-io ~@reboot ~@swap",
                "SystemCallErrorNumber": "EPERM",
            },
            # The bounded ext4 root must remain writable for database/web runtime
            # state. Host project source is independently bind-mounted read-only.
            "Files": {"ReadOnly": "no", "BindReadOnly": tuple(mounts),
                      "Bind": tuple(writable), "PrivateUsersChown": "no"},
            "Network": {"Private": "yes", "VirtualEthernet": "no",
                        "VirtualEthernetExtra": f"{policy.network.get('veth')}:host0"},
            "Identity": {"MachineID": policy.machine_id,
                         "PolicyDigest": policy.digest},
            "Service": {
                "DevicePolicy": "closed",
                # Loop access belongs to the trusted nspawn supervisor so it can
                # attach the image. Those nodes are absent from the guest /dev.
                "DeviceAllow": ("/dev/null rw", "/dev/zero rw", "/dev/full rw",
                                "/dev/random r", "/dev/urandom r",
                                "/dev/loop-control rw", "block-loop rwm"),
                "ContainerDevices": ("/dev/null", "/dev/zero", "/dev/full",
                                     "/dev/random", "/dev/urandom"),
                "RestrictAddressFamilies": ("AF_UNIX", "AF_INET", "AF_INET6", "AF_NETLINK"),
                "LockPersonality": "yes", "RestrictSUIDSGID": "yes",
            },
            "Security": {"AppArmorProfile": f"sandbox-native-{policy.machine_id}"},
        }
