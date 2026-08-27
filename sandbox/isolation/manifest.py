"""Exact managed-isolation support matrix; proof and code presence stay distinct."""


# Feature declarations are intentionally separate from the prerequisite matrix.
# Registering a capability here records its contract ownership; it does not make
# the capability available to a runtime or promote an unproven host.
MANAGED_ISOLATION_CAPABILITIES = ({
    "capability_id": "outbound_credential_mediation",
    "runtime": "managed-native",
    "contract_modules": (
        "sandbox.isolation.credential_resolver",
        "sandbox.isolation.credential_binding",
        "sandbox.isolation.credential_request_broker",
        "sandbox.isolation.credential_controller_protocol_v2",
        "sandbox.isolation.credential_controller_service_v2",
        "sandbox.isolation.credential_controller_authority_v2",
        "sandbox.isolation.credential_controller_audit_v2",
        "sandbox.isolation.credential_controller_lifecycle_v2",
        "sandbox.isolation.capability_report",
        "sandbox.runtimes.managed.credential_repository",
    ),
    "support_tier": "implemented_unproven",
    "evidence_id": None,
    "adoptable": False,
},)

MANAGED_ISOLATION_MATRICES = ({
    "matrix_id": "ubuntu-24.04-systemd-255",
    "platform": {"id": "ubuntu", "version": "24.04"},
    "systemd_min": 255,
    "required_commands": (
        "systemd-nspawn", "machinectl", "bwrap", "nft", "ip", "nsenter", "apparmor_parser",
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
