"""Truthful runtime mode declarations; only evidence-backed modes are adoptable."""

RUNTIME_DECLARATIONS = (
    {"adapter_id": "compose", "modes": ("compose",),
     "platforms": ("linux", "darwin", "windows"), "isolation": "compose",
     "support_tier": "adoptable", "evidence_id": "existing-compose-live-suite",
     "adoptable": True},
    {"adapter_id": "ubuntu-nspawn", "modes": ("managed_native",),
     "platforms": ("ubuntu-24.04",), "isolation": "managed_container",
     "support_tier": "implemented_unproven", "evidence_id": None,
     "adoptable": False},
    {"adapter_id": "herd", "modes": ("incumbent_native",),
     "platforms": ("linux", "darwin"), "isolation": "trusted_shared_host",
     "support_tier": "implemented_unproven", "evidence_id": None,
     "adoptable": False},
    {"adapter_id": "valet", "modes": ("incumbent_native",),
     "platforms": ("darwin",), "isolation": "trusted_shared_host",
     "support_tier": "implemented_unproven", "evidence_id": None,
     "adoptable": False},
    {"adapter_id": "declared-posix", "modes": ("incumbent_native",),
     "platforms": ("linux", "darwin"), "isolation": "trusted_shared_host",
     "support_tier": "conditional", "evidence_id": None,
     "adoptable": False},
)
