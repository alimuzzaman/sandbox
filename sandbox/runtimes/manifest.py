"""Truthful runtime mode declarations; only evidence-backed modes are adoptable."""

from __future__ import annotations

import shutil

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

DETECT_ONLY_RUNTIME_DECLARATIONS = (
    {"adapter_id": "local", "platforms": ("linux", "darwin", "windows"), "executable": None},
    {"adapter_id": "xampp", "platforms": ("linux", "darwin", "windows"), "executable": None},
    {"adapter_id": "laragon", "platforms": ("windows",), "executable": None},
    {"adapter_id": "wamp", "platforms": ("windows",), "executable": None},
)


def detect_only_runtime_declarations(platform, *, which=shutil.which):
    """Report public executable/platform signals without reading product state."""
    return tuple({**declaration, "mode": "detect_only", "isolation": "unknown",
                  "adoptable": False, "available": platform in declaration["platforms"],
                  "reason": "detection_only_no_private_state_access"}
                 for declaration in DETECT_ONLY_RUNTIME_DECLARATIONS)
