"""Compile fixed nspawn descriptors from a verified policy."""

from __future__ import annotations


class NspawnCompiler:
    def compile(self, policy):
        mounts = [f"{item['source']}:{item['target']}:norbind" for item in policy.read_only_mounts]
        writable = [f"{item['source']}:{item['target']}:norbind" for item in policy.writable_mounts]
        return {
            "Exec": {
                "Boot": "yes",
                "PrivateUsers": f"{policy.uid_map['base']}:{policy.uid_map['count']}",
                "PrivateUsersDelegate": "0",
                "PrivateUsersOwnership": "map", "Capability": "",
                "DropCapability": "all", "NoNewPrivileges": "yes",
                "SystemCallFilter": "@system-service ~@mount ~@raw-io ~@reboot ~@swap",
                "SystemCallErrorNumber": "EPERM",
            },
            "Files": {"ReadOnly": "yes", "BindReadOnly": tuple(mounts),
                      "Bind": tuple(writable), "PrivateUsersChown": "no"},
            "Network": {"Private": "yes", "VirtualEthernet": "no",
                        "VirtualEthernetExtra": f"{policy.network.get('veth')}:host0"},
            "Identity": {"MachineID": policy.machine_id,
                         "PolicyDigest": policy.digest},
            "Service": {
                "DevicePolicy": "closed",
                "DeviceAllow": ("/dev/null rw", "/dev/zero rw", "/dev/full rw",
                                "/dev/random r", "/dev/urandom r"),
                "RestrictAddressFamilies": ("AF_UNIX", "AF_INET", "AF_INET6"),
                "LockPersonality": "yes", "RestrictSUIDSGID": "yes",
            },
            "Security": {"AppArmorProfile": f"sandbox-native-{policy.machine_id}"},
        }
