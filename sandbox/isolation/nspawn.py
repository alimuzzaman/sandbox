"""Compile fixed nspawn descriptors from a verified policy."""

from __future__ import annotations


class NspawnCompiler:
    def compile(self, policy):
        mounts = [f"{item['source']}:{item['target']}:norbind" for item in policy.read_only_mounts]
        writable = [f"{item['source']}:{item['target']}:norbind" for item in policy.writable_mounts]
        return {
            "Exec": {
                "Boot": "yes", "PrivateUsers": "pick",
                "PrivateUsersOwnership": "map", "Capability": "",
                "DropCapability": "all", "NoNewPrivileges": "yes",
                "SystemCallFilter": "@system-service ~@mount ~@raw-io ~@reboot ~@swap",
                "SystemCallErrorNumber": "EPERM",
            },
            "Files": {"ReadOnly": "yes", "BindReadOnly": tuple(mounts),
                      "Bind": tuple(writable), "PrivateUsersChown": "no"},
            "Network": {"Private": "yes", "VirtualEthernet": "yes",
                        "Interface": policy.network.get("veth")},
            "Identity": {"MachineID": policy.machine_id,
                         "PolicyDigest": policy.digest},
        }
