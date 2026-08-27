"""Offline acceptance and evidence harness for the Credential Vault (spec 045).

This package prepares the future authorized Ubuntu 24.04 proof run for T022 and
T029. It plans, records, validates, and reports; it never executes a live check
against this machine, never opens a socket, and never treats its own local
tests as evidence.

Nothing here promotes the capability: support stays `implemented_unproven`,
`adoptable` stays false, and the evidence identity stays null until a real
authorized run and an independent review under T031 say otherwise.
"""

from __future__ import annotations

HARNESS_VERSION = 1
SUPPORT_TIER = "implemented_unproven"
ADOPTABLE = False
EVIDENCE_ID = None

__all__ = ["ADOPTABLE", "EVIDENCE_ID", "HARNESS_VERSION", "SUPPORT_TIER"]
