"""Exact managed-isolation support matrix; proof and code presence stay distinct."""

MANAGED_ISOLATION_MATRICES = ({
    "matrix_id": "ubuntu-24.04-systemd-255",
    "platform": {"id": "ubuntu", "version": "24.04"},
    "systemd_min": 255,
    "required_commands": (
        "systemd-nspawn", "machinectl", "bwrap", "nft", "ip", "apparmor_parser",
        "debootstrap", "mkfs.ext4",
    ),
    "required_effective_gates": (
        "pid1_systemd", "cgroup_v2", "cgroup_delegation", "user_namespaces",
        "private_network", "nftables", "apparmor_enforcing", "seccomp",
    ),
    "support_tier": "implemented_unproven",
    "evidence_id": None,
    "adoptable": False,
},)
