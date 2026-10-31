"""Focused regressions for secret ownership configuration normalization (T050).

Covers pure normalization:
- Single-layer enforcement (cannot configure ownership in both project and machine_override).
- Exact 6-key entry format validation: source, key, profile, name, consumer, origin.
- Rejection of unknown keys, reserved profile names, invalid domain, invalid destination.
- Rejection of secret-bearing value fields.
- Gateway enum validation ("none", "cloudflare-access").
- Domain isolation (project ID and client ID isolation across domains).
- Canonical sorting of profiles and entries.
- Legacy configuration normalization omitting ownership.
- Stable byte-identical pinned legacy a1- authority digest.
- Idempotent normalization.
- Safe audit ancestor symlink guard (legacy allowed, ownership refused).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from sandbox.config.secrets import normalize_secret_config
from sandbox.secrets.audit import SecretAudit
from sandbox.secrets.authority import bind_provider_authorities
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership.config import normalize_ownership
from sandbox.secrets.ownership.models import canonical_json
from tests.secret_ownership_support import require


def _minimal_profile() -> dict:
    return {
        "domain": "personal",
        "destination": {
            "endpoint": "https://vault.example.invalid",
            "projectId": "proj-cfg",
            "environment": "staging",
            "path": "/",
            "readClientId": "reader",
            "createClientId": "creator",
            "timeoutSeconds": 30,
            "gateway": "none",
        },
    }


def _minimal_sources() -> dict:
    return {
        "dotenv-file": {
            "type": "file",
            "path": ".env",
            "format": "dotenv",
        },
    }


class TestSecretOwnershipConfig(unittest.TestCase):
    """Configuration normalization and schema enforcement regressions."""

    def test_normalize_ownership_single_layer_enforcement(self) -> None:
        """Ownership defined in both project and machine_override must be refused."""
        raw_both = {
            "root": "/project",
            "_secrets_raw": {
                "project": {
                    "sources": _minimal_sources(),
                    "ownership": {
                        "profiles": {"personal": _minimal_profile()},
                        "entries": [
                            {"source": "dotenv-file", "key": "ALPHA", "profile": "personal", "name": "APP_A", "consumer": "APP_A", "origin": "source"},
                        ],
                    },
                },
                "machine_override": {
                    "ownership": {
                        "profiles": {"personal": _minimal_profile()},
                        "entries": [
                            {"source": "dotenv-file", "key": "BETA", "profile": "personal", "name": "APP_B", "consumer": "APP_B", "origin": "source"},
                        ],
                    },
                },
            },
        }

        with self.assertRaises(ValueError) as ctx:
            normalize_secret_config(raw_both)
        require("exactly one layer" in str(ctx.exception),
                "must refuse ownership configured in multiple layers")

    def test_normalize_ownership_entry_exact_keys(self) -> None:
        """Entries must contain exactly (source, key, profile, name, consumer, origin)."""
        # Extra key rejected
        bad_entry_extra = {
            "source": "dotenv-file", "key": "ALPHA", "profile": "personal",
            "name": "ALPHA", "consumer": "ALPHA", "origin": "source", "extra": "invalid",
        }
        raw_extra = {
            "root": "/project",
            "secrets": {
                "sources": _minimal_sources(),
                "ownership": {
                    "profiles": {"personal": _minimal_profile()},
                    "entries": [bad_entry_extra],
                },
            },
        }
        with self.assertRaises(ValueError) as ctx:
            normalize_secret_config(raw_extra)
        require("unexpected" in str(ctx.exception).lower() or "exact" in str(ctx.exception).lower() or "key" in str(ctx.exception).lower(),
                "must refuse unexpected entry keys")

        # Missing key rejected
        bad_entry_missing = {
            "source": "dotenv-file", "key": "ALPHA", "profile": "personal",
            # missing "name", "consumer", "origin"
        }
        raw_missing = {
            "root": "/project",
            "secrets": {
                "sources": _minimal_sources(),
                "ownership": {
                    "profiles": {"personal": _minimal_profile()},
                    "entries": [bad_entry_missing],
                },
            },
        }
        with self.assertRaises(ValueError):
            normalize_secret_config(raw_missing)

    def test_normalize_ownership_rejects_empty_profiles_and_invalid_dest(self) -> None:
        """Profiles mapping rejects reserved profile name, invalid domain, and invalid destination."""
        sources = _minimal_sources()

        # Reserved profile name "undecided"
        raw_reserved = {
            "profiles": {"undecided": _minimal_profile()},
            "entries": [],
        }
        with self.assertRaises(ValueError) as ctx1:
            normalize_ownership(raw_reserved, None, sources, "/project")
        require("undecided is reserved" in str(ctx1.exception),
                "must refuse profile named undecided")

        # Invalid domain (neither personal nor office)
        bad_profile_domain = _minimal_profile()
        bad_profile_domain["domain"] = "enterprise"
        raw_bad_domain = {
            "profiles": {"enterprise": bad_profile_domain},
            "entries": [],
        }
        with self.assertRaises(ValueError) as ctx2:
            normalize_ownership(raw_bad_domain, None, sources, "/project")
        require("office or personal" in str(ctx2.exception),
                "must refuse domain not in (office, personal)")

        # Invalid destination (missing required createClientId)
        raw_bad_dest = {
            "profiles": {
                "personal": {
                    "domain": "personal",
                    "destination": {"endpoint": "https://vault.example.invalid"},
                },
            },
            "entries": [],
        }
        with self.assertRaises(ValueError):
            normalize_ownership(raw_bad_dest, None, sources, "/project")

    def test_normalize_ownership_rejects_unknown_fields(self) -> None:
        """Unknown fields in configuration root, profile, or destination fail closed."""
        sources = _minimal_sources()

        # Unknown field on root ownership block
        raw_root_unknown = {
            "profiles": {"personal": _minimal_profile()},
            "entries": [],
            "extraRootField": "unsupported",
        }
        with self.assertRaises(ValueError) as ctx1:
            normalize_ownership(raw_root_unknown, None, sources, "/project")
        require("unknown keys" in str(ctx1.exception).lower(),
                "must refuse unknown keys at configuration root")

        # Unknown field on profile block
        bad_prof = _minimal_profile()
        bad_prof["unexpectedProfileAttr"] = "unsupported"
        raw_prof_unknown = {
            "profiles": {"personal": bad_prof},
            "entries": [],
        }
        with self.assertRaises(ValueError) as ctx2:
            normalize_ownership(raw_prof_unknown, None, sources, "/project")
        require("unknown keys" in str(ctx2.exception).lower(),
                "must refuse unknown keys at profile level")

        # Unknown field on destination block
        bad_dest = dict(_minimal_profile()["destination"])
        bad_dest["extraDestinationSetting"] = "unsupported"
        raw_dest_unknown = {
            "profiles": {
                "personal": {
                    "domain": "personal",
                    "destination": bad_dest,
                },
            },
            "entries": [],
        }
        with self.assertRaises(ValueError) as ctx3:
            normalize_ownership(raw_dest_unknown, None, sources, "/project")
        require("unknown keys" in str(ctx3.exception).lower(),
                "must refuse unknown keys at destination level")

    def test_normalize_ownership_rejects_value_fields(self) -> None:
        """Secret-bearing value fields anywhere in ownership configuration must fail closed."""
        sources = _minimal_sources()

        for forbidden in ("value", "secret", "secretValue", "token"):
            raw_bad = {
                "profiles": {"personal": _minimal_profile()},
                "entries": [],
                forbidden: "synthetic-secret-leak",
            }
            with self.assertRaises(ValueError) as ctx:
                normalize_ownership(raw_bad, None, sources, "/project")
            require("must not contain secret values" in str(ctx.exception),
                    f"must refuse forbidden secret-bearing token {forbidden}")

    def test_normalize_ownership_gateway_enum(self) -> None:
        """Destination gateway must be strictly 'none' or 'cloudflare-access'."""
        sources = _minimal_sources()

        # Valid gateway: none
        prof_none = _minimal_profile()
        prof_none["destination"]["gateway"] = "none"
        norm_none = normalize_ownership({"profiles": {"personal": prof_none}, "entries": []},
                                        None, sources, "/project")
        require(norm_none["profiles"]["personal"]["destination"]["gateway"] == "none",
                "gateway 'none' must be accepted")

        # Valid gateway: cloudflare-access
        prof_cf = _minimal_profile()
        prof_cf["destination"]["gateway"] = "cloudflare-access"
        norm_cf = normalize_ownership({"profiles": {"personal": prof_cf}, "entries": []},
                                      None, sources, "/project")
        require(norm_cf["profiles"]["personal"]["destination"]["gateway"] == "cloudflare-access",
                "gateway 'cloudflare-access' must be accepted")

        # Invalid gateway
        prof_bad = _minimal_profile()
        prof_bad["destination"]["gateway"] = "aws-sigv4"
        with self.assertRaises(ValueError) as ctx:
            normalize_ownership({"profiles": {"personal": prof_bad}, "entries": []},
                                None, sources, "/project")
        require("gateway must be none or cloudflare-access" in str(ctx.exception),
                "must refuse invalid gateway identifier")

    def test_normalize_ownership_isolation_project_and_client(self) -> None:
        """Cross-domain sharing of projectId or client IDs and same-profile read==create are refused."""
        sources = _minimal_sources()

        # Same profile readClientId == createClientId
        bad_same_client = _minimal_profile()
        bad_same_client["destination"]["readClientId"] = "same-id"
        bad_same_client["destination"]["createClientId"] = "same-id"
        with self.assertRaises(ValueError) as ctx1:
            normalize_ownership({"profiles": {"personal": bad_same_client}, "entries": []},
                                None, sources, "/project")
        require("readClientId and createClientId must differ" in str(ctx1.exception),
                "must refuse identical read and create client ids in single profile")

        # Cross-domain sharing of projectId
        p_personal = _minimal_profile()
        p_personal["domain"] = "personal"
        p_personal["destination"]["projectId"] = "shared-proj-123"

        p_office = _minimal_profile()
        p_office["domain"] = "office"
        p_office["destination"]["projectId"] = "shared-proj-123"
        p_office["destination"]["readClientId"] = "office-reader"
        p_office["destination"]["createClientId"] = "office-creator"

        with self.assertRaises(ValueError) as ctx2:
            normalize_ownership({"profiles": {"personal": p_personal, "office": p_office}, "entries": []},
                                None, sources, "/project")
        require("profiles of different domains must not share projectId" in str(ctx2.exception),
                "must refuse cross-domain projectId sharing")

        # Cross-domain sharing of client ID
        p_office_diff_proj = _minimal_profile()
        p_office_diff_proj["domain"] = "office"
        p_office_diff_proj["destination"]["projectId"] = "office-proj-999"
        p_office_diff_proj["destination"]["readClientId"] = p_personal["destination"]["readClientId"]
        p_office_diff_proj["destination"]["createClientId"] = "office-creator"

        with self.assertRaises(ValueError) as ctx3:
            normalize_ownership({"profiles": {"personal": p_personal, "office": p_office_diff_proj}, "entries": []},
                                None, sources, "/project")
        require("profiles of different domains must not share client ids" in str(ctx3.exception),
                "must refuse cross-domain client ID sharing")

    def test_normalize_ownership_canonical_sort(self) -> None:
        """Profiles are canonically sorted by name and entries by 6-tuple."""
        sources = {
            "src-b": {"type": "file", "path": ".env.b", "format": "dotenv"},
            "src-a": {"type": "file", "path": ".env.a", "format": "dotenv"},
        }
        prof_z = _minimal_profile()
        prof_a = _minimal_profile()
        prof_a["destination"]["projectId"] = "proj-a"

        entries_unsorted = [
            {"source": "src-b", "key": "KEY_Z", "profile": "zeta", "name": "NAME_Z", "consumer": "CZ", "origin": "source"},
            {"source": "src-a", "key": "KEY_A", "profile": "alpha", "name": "NAME_A", "consumer": "CA", "origin": "source"},
            {"source": "src-a", "key": "KEY_B", "profile": "alpha", "name": "NAME_B", "consumer": "CB", "origin": "source"},
        ]
        raw = {
            "profiles": {
                "zeta": prof_z,
                "alpha": prof_a,
            },
            "entries": entries_unsorted,
        }
        norm = normalize_ownership(raw, None, sources, "/project")
        # Profiles dictionary must have keys in alphabetical order
        require(list(norm["profiles"].keys()) == ["alpha", "zeta"],
                "profiles must be canonically sorted by key name")
        # Entries list must be ordered by (source, key, profile, name, consumer, origin)
        entry_keys = [e["key"] for e in norm["entries"]]
        require(entry_keys == ["KEY_A", "KEY_B", "KEY_Z"],
                "entries must be canonically sorted by (source, key, profile, name, consumer, origin)")

    def test_normalize_secret_config_omits_ownership_when_absent(self) -> None:
        """Normalizing configuration without ownership produces no 'ownership' key."""
        raw_config = {
            "root": "/project",
            "secrets": {
                "sources": _minimal_sources(),
                "useProfiles": {
                    "test": {
                        "source": "dotenv-file",
                        "key": "PORT",
                        "destination": "APP_PORT",
                        "argv": ["app", "--port", "{VALUE}"],
                    },
                },
            },
        }
        normalized = normalize_secret_config(raw_config)
        require("ownership" not in normalized, "legacy config must NOT have ownership key")
        require("sources" in normalized, "sources must be present in normalized config")
        require("useProfiles" in normalized, "useProfiles must be present in normalized config")

    def test_legacy_a1_digest_byte_identical_pinned_baseline(self) -> None:
        """bind_provider_authorities without ownership produces stable pre-change a1- baseline."""
        sources = {
            "infisical-app": {
                "type": "infisical",
                "endpoint": "https://vault.example.invalid",
                "projectId": "proj-legacy",
                "environment": "staging",
                "path": "/",
                "key": "SECRET_KEY",
                "clientId": "client-legacy",
                "mcpModes": ["keys"],
                "timeoutSeconds": 10,
            },
        }
        use_profiles = {
            "app": {
                "source": "infisical-app",
                "key": "SECRET_KEY",
                "argv": ["app", "{VALUE}"],
                "destination": "APP_KEY",
                "timeoutSeconds": 10,
                "maxOutputBytes": 1024,
                "mcp": False,
            },
        }

        # Authority digest without ownership
        bound = bind_provider_authorities(sources, use_profiles,
                                          project_root="/project/root",
                                          ownership=None)
        digest = bound["infisical-app"]["authorityDigest"]
        require(digest.startswith("a1-"), "digest must start with a1-")
        require(len(digest) == 67, "digest must be 67 characters (a1- + 64 hex)")

        # Verify against explicit canonical public authority metadata computation
        metadata = {
            "version": 1,
            "source": "infisical-app",
            "project_root": "/project/root",
            "descriptor": {
                key: sources["infisical-app"].get(key)
                for key in ("type", "endpoint", "projectId", "environment", "path", "key", "clientId", "mcpModes", "timeoutSeconds")
            },
            "profiles": [
                {
                    "name": "app",
                    "source": "infisical-app",
                    "key": "SECRET_KEY",
                    "argv": ["app", "{VALUE}"],
                    "destination": "APP_KEY",
                    "timeoutSeconds": 10,
                    "maxOutputBytes": 1024,
                    "mcp": False,
                },
            ],
        }
        expected = "a1-" + hashlib.sha256(
            json.dumps(metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        require(digest == expected, "baseline a1- digest must match byte-identical pinned reference")

    def test_audit_ancestor_symlink_guard_ownership_vs_legacy(self) -> None:
        """Legacy SecretAudit permits safe paths through symlink ancestors; ownership audit refuses."""
        with tempfile.TemporaryDirectory() as raw_tmp:
            canonical_base = Path(os.path.realpath(raw_tmp)) / "base_dir"
            canonical_base.mkdir(mode=0o700)

            # Create a benign symlink ancestor pointing to canonical_base
            symlink_ancestor = Path(os.path.realpath(raw_tmp)) / "symlink_dir"
            os.symlink(canonical_base, symlink_ancestor)

            # Directory under the symlink
            audit_dir = symlink_ancestor / "audit_store"
            audit_file = audit_dir / "audit.jsonl"

            # 1. Legacy non-ownership operation through symlink ancestor succeeds
            legacy_audit = SecretAudit(audit_file)
            legacy_corr_id = legacy_audit.intent(
                operation="read",
                source="dotenv-file",
                keys=["ALPHA"],
                surface="cli",
            )
            require(isinstance(legacy_corr_id, str) and len(legacy_corr_id) > 0,
                    "legacy audit write through symlink ancestor must succeed")
            legacy_audit.outcome(
                correlation_id=legacy_corr_id,
                operation="read",
                source="dotenv-file",
                keys=["ALPHA"],
                surface="cli",
                decision="succeeded",
            )

            # 2. Ownership operation through symlink ancestor MUST be refused with audit_unavailable
            ownership_audit = SecretAudit(audit_file)
            with self.assertRaises(SecretBrokerError) as ctx:
                ownership_audit.intent(
                    operation="ownership_migrate",
                    source="dotenv-file",
                    keys=["ALPHA"],
                    surface="cli",
                )
            require(ctx.exception.code == "audit_unavailable",
                    "ownership audit must refuse symlink ancestor with audit_unavailable")

            # 3. Ownership operation on canonical path (no symlink ancestor) succeeds
            canonical_audit_file = canonical_base / "audit_store" / "audit_canonical.jsonl"
            direct_audit = SecretAudit(canonical_audit_file)
            corr_id = direct_audit.intent(
                operation="ownership_migrate",
                source="dotenv-file",
                keys=["ALPHA"],
                surface="cli",
            )
            require(isinstance(corr_id, str) and len(corr_id) > 0,
                    "ownership audit on canonical path without symlink ancestor must succeed")

    def test_normalize_ownership_idempotent(self) -> None:
        """Normalizing an already-normalized config produces identical dictionary."""
        sources = _minimal_sources()
        ownership_block = {
            "profiles": {"personal": _minimal_profile()},
            "entries": [
                {"source": "dotenv-file", "key": "ALPHA", "profile": "personal",
                 "name": "ALPHA", "consumer": "ALPHA", "origin": "source"},
            ],
        }
        norm1 = normalize_ownership(ownership_block, None, sources, "/project")
        norm2 = normalize_ownership(norm1, None, sources, "/project")
        require(canonical_json(norm1) == canonical_json(norm2),
                "normalization of ownership must be strictly idempotent")


if __name__ == "__main__":
    unittest.main()
