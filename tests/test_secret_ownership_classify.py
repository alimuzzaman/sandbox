"""Focused regressions for secret ownership classification (T051).

Covers pure classification engine:
- Precedence rules for state assignment.
- All blocking reasons: unmapped, undecided, multiply_assigned, duplicate_destination,
  consumer_collision, source_key_missing, other_domain, source_unsupported.
- Equal names in isolated projects non-blocking.
- Exact 5-tuple duplicate blocking.
- Destination name uppercase naming validation.
- Existence mapping: ABSENT -> would_create, PRESENT -> pre_existing, UNKNOWN -> unknown.
- Cross-domain shared scope blocking.
- Profile unknown refusal.
"""

from __future__ import annotations

import unittest

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership.classify import (
    BLOCKING_STATES, CONSUMER_COLLISION, SOURCE_KEY_MISSING, classify,
)
from sandbox.secrets.ownership.models import (
    BlockReason, Destination, Existence, MapEntry, OwnershipConfig,
    OwnershipProfile,
)
from tests.secret_ownership_support import require


def _sample_destination(project_id: str = "proj-1") -> Destination:
    return Destination(
        endpoint="https://vault.example.invalid",
        project_id=project_id,
        environment="staging",
        path="/",
        read_client_id="reader",
        create_client_id="creator",
        timeout_seconds=30,
        gateway="none",
    )


def _make_config(profiles: dict[str, OwnershipProfile], entries: list[MapEntry]) -> OwnershipConfig:
    block = {
        "profiles": {
            name: {
                "domain": prof.domain,
                "destination": prof.destination.as_normalized(),
            }
            for name, prof in sorted(profiles.items())
        },
        "entries": [e.as_normalized() for e in entries],
    }
    return OwnershipConfig.from_normalized(block)


class TestSecretOwnershipClassify(unittest.TestCase):
    """Classification engine and precedence regressions."""

    def test_classify_profile_unknown(self) -> None:
        """Classifying an unconfigured profile raises SecretBrokerError('profile_unknown')."""
        p_personal = OwnershipProfile("personal", "personal", _sample_destination())
        cfg = _make_config({"personal": p_personal}, [])

        with self.assertRaises(SecretBrokerError) as ctx:
            classify(cfg, "nonexistent-profile", {})
        require(ctx.exception.code == "profile_unknown",
                "must raise SecretBrokerError with code 'profile_unknown'")

    def test_classify_all_blocking_reasons(self) -> None:
        """Verify each blocking reason produces a blocking classification report."""
        p_personal = OwnershipProfile("personal", "personal", _sample_destination())
        prof_map = {"personal": p_personal}

        # 1. Unmapped source key
        # Source has ["K1", "K2"], but config entries only map "K1"
        e1 = MapEntry("file-1", "K1", "personal", "K1", "c1", "source")
        cfg_unmapped = _make_config(prof_map, [e1])
        rep_unmapped = classify(cfg_unmapped, "personal", {"file-1": ("K1", "K2")})
        require(rep_unmapped.blocking is True, "unmapped key must make report blocking")
        require(BlockReason.UNMAPPED.value in rep_unmapped.states, "must contain unmapped state")

        # 2. Undecided profile entry
        e_personal = MapEntry("file-1", "K1", "personal", "K1", "c1", "source")
        e_undecided = MapEntry("file-1", "K_UNDECIDED", "undecided", "K_UNDECIDED", "c2", "source")
        cfg_undecided = _make_config(prof_map, [e_personal, e_undecided])
        rep_undecided = classify(cfg_undecided, "personal", {"file-1": ("K1", "K_UNDECIDED")})
        require(rep_undecided.blocking is True, "undecided entry must make report blocking")
        require(BlockReason.UNDECIDED.value in rep_undecided.states, "must contain undecided state")

        # 3. Source key missing (entry maps key not in source_names)
        cfg_missing = _make_config(prof_map, [e1])
        rep_missing = classify(cfg_missing, "personal", {"file-1": ()})
        require(rep_missing.blocking is True, "missing source key must make report blocking")
        require(SOURCE_KEY_MISSING in rep_missing.states, "must contain source_key_missing state")

        # 4. Duplicate destination within profile
        e_dup1 = MapEntry("file-1", "KEY_A", "personal", "TARGET_DUP", "c1", "source")
        e_dup2 = MapEntry("file-1", "KEY_B", "personal", "TARGET_DUP", "c2", "source")
        cfg_dup = _make_config(prof_map, [e_dup1, e_dup2])
        rep_dup = classify(cfg_dup, "personal", {"file-1": ("KEY_A", "KEY_B")})
        require(rep_dup.blocking is True, "duplicate destination must make report blocking")
        require(BlockReason.DUPLICATE_DESTINATION.value in rep_dup.states,
                "must contain duplicate_destination state")

    def test_classify_missing_blocking_states_multiply_assigned_and_consumer_collision(self) -> None:
        """Verify multiply_assigned and consumer_collision produce concrete blocking states."""
        p_personal = OwnershipProfile("personal", "personal", _sample_destination())
        prof_map = {"personal": p_personal}

        # 1. Multiply assigned source key: (file-1, KEY_MULTI) mapped twice
        e_multi1 = MapEntry("file-1", "KEY_MULTI", "personal", "NAME_1", "c1", "source")
        e_multi2 = MapEntry("file-1", "KEY_MULTI", "personal", "NAME_2", "c2", "source")
        cfg_multi = _make_config(prof_map, [e_multi1, e_multi2])
        rep_multi = classify(cfg_multi, "personal", {"file-1": ("KEY_MULTI",)})
        require(rep_multi.blocking is True, "multiply assigned key must make report blocking")
        require(BlockReason.MULTIPLY_ASSIGNED.value in rep_multi.states,
                "must contain multiply_assigned state")
        require("KEY_MULTI" in rep_multi.states[BlockReason.MULTIPLY_ASSIGNED.value],
                "multiply_assigned must report the shared source key")

        # 2. Consumer collision: two entries within profile share identical consumer name
        e_col1 = MapEntry("file-1", "KEY_A", "personal", "NAME_A", "SHARED_CONSUMER", "source")
        e_col2 = MapEntry("file-1", "KEY_B", "personal", "NAME_B", "SHARED_CONSUMER", "source")
        cfg_col = _make_config(prof_map, [e_col1, e_col2])
        rep_col = classify(cfg_col, "personal", {"file-1": ("KEY_A", "KEY_B")})
        require(rep_col.blocking is True, "consumer collision must make report blocking")
        require(CONSUMER_COLLISION in rep_col.states, "must contain consumer_collision state")
        require("NAME_A" in rep_col.states[CONSUMER_COLLISION] and "NAME_B" in rep_col.states[CONSUMER_COLLISION],
                "consumer_collision must report the colliding destination names")

    def test_classify_equal_names_in_isolated_projects_allowed(self) -> None:
        """Equal destination names across isolated projects are allowed and non-blocking."""
        p_personal = OwnershipProfile("personal", "personal", _sample_destination("proj-isolated-1"))
        p_office = OwnershipProfile("office", "office", _sample_destination("proj-isolated-2"))
        prof_map = {"personal": p_personal, "office": p_office}

        # Both profiles have an entry with destination name "COMMON_VAR"
        e1 = MapEntry("file-1", "KEY_A", "personal", "COMMON_VAR", "c_personal", "source")
        e2 = MapEntry("file-2", "KEY_B", "office", "COMMON_VAR", "c_office", "source")
        cfg = _make_config(prof_map, [e1, e2])

        # Classify personal profile: project-1 target does not clash with project-2 target
        rep = classify(cfg, "personal", {"file-1": ("KEY_A",), "file-2": ("KEY_B",)},
                       existence={"COMMON_VAR": Existence.ABSENT})
        require(rep.blocking is False, "equal names in isolated projects must not block")
        require(BlockReason.DUPLICATE_DESTINATION.value not in rep.states,
                "must not report duplicate_destination across isolated projects")
        require("would_create" in rep.states, "absent destination must map to would_create")
        require(rep.states["would_create"] == ("COMMON_VAR",),
                "would_create state must contain COMMON_VAR")

    def test_classify_exact_destination_tuple_duplicate_blocked(self) -> None:
        """Two entries mapping to the exact same 5-tuple destination target must be blocked."""
        p_personal = OwnershipProfile("personal", "personal", _sample_destination("proj-dup"))
        prof_map = {"personal": p_personal}

        # Two entries with same target name under same profile (exact 5-tuple match)
        e1 = MapEntry("file-1", "KEY_1", "personal", "TARGET_EXACT", "c1", "source")
        e2 = MapEntry("file-1", "KEY_2", "personal", "TARGET_EXACT", "c2", "source")
        cfg = _make_config(prof_map, [e1, e2])

        rep = classify(cfg, "personal", {"file-1": ("KEY_1", "KEY_2")})
        require(rep.blocking is True, "exact destination 5-tuple duplicate must block")
        require(BlockReason.DUPLICATE_DESTINATION.value in rep.states,
                "must contain duplicate_destination state")
        require("TARGET_EXACT" in rep.states[BlockReason.DUPLICATE_DESTINATION.value],
                "duplicate_destination state must record the duplicated name")

    def test_classify_uppercase_name_rule(self) -> None:
        """Destination names failing the uppercase identifier regex are flagged as source_unsupported."""
        p_personal = OwnershipProfile("personal", "personal", _sample_destination())
        prof_map = {"personal": p_personal}

        # Entry with lowercase name fails provider naming regex ^[A-Z_][A-Z0-9_]*$
        bad_entry = MapEntry("file-1", "KEY_A", "personal", "lowercase_name", "c1", "source")
        cfg = _make_config(prof_map, [bad_entry])

        rep = classify(cfg, "personal", {"file-1": ("KEY_A",)})
        require(rep.blocking is True, "non-uppercase destination name must block")
        require(BlockReason.SOURCE_UNSUPPORTED.value in rep.states,
                "must contain source_unsupported state for invalid destination name")
        require("lowercase_name" in rep.states[BlockReason.SOURCE_UNSUPPORTED.value],
                "source_unsupported must record the invalid name")

    def test_classify_cross_domain_shared_scope_blocked(self) -> None:
        """Profiles in different domains sharing same endpoint/project are blocked with other_domain."""
        p_personal = OwnershipProfile("personal", "personal", _sample_destination("shared-project"))
        p_office = OwnershipProfile("office", "office", _sample_destination("shared-project"))
        prof_map = {"personal": p_personal, "office": p_office}

        e1 = MapEntry("file-1", "K1", "personal", "K1", "c1", "source")
        cfg = _make_config(prof_map, [e1])
        rep = classify(cfg, "personal", {"file-1": ("K1",)})
        require(rep.blocking is True, "cross-domain shared scope must make report blocking")
        require(BlockReason.OTHER_DOMAIN.value in rep.states, "must report other_domain state")

    def test_classify_existence_mapping(self) -> None:
        """Existence observations map to would_create, pre_existing, unknown."""
        p_personal = OwnershipProfile("personal", "personal", _sample_destination())
        e1 = MapEntry("file-1", "K1", "personal", "K1", "c1", "source")
        e2 = MapEntry("file-1", "K2", "personal", "K2", "c2", "source")
        e3 = MapEntry("file-1", "K3", "personal", "K3", "c3", "source")
        cfg = _make_config({"personal": p_personal}, [e1, e2, e3])

        existence = {
            "K1": Existence.ABSENT,
            "K2": Existence.PRESENT,
            "K3": Existence.UNKNOWN,
        }
        report = classify(cfg, "personal", {"file-1": ("K1", "K2", "K3")}, existence=existence)
        require("would_create" in report.states, "absent must map to would_create")
        require("pre_existing" in report.states, "present must map to pre_existing")
        require("unknown" in report.states, "unknown existence must map to unknown")
        require(report.blocking is False, "existence states alone are not in BLOCKING_STATES")

    def test_classify_report_counts_and_blocking_flag(self) -> None:
        """Verify report profile, counts, and blocking flag behavior."""
        p_personal = OwnershipProfile("personal", "personal", _sample_destination())
        e1 = MapEntry("file-1", "K1", "personal", "K1", "c1", "source")
        e2 = MapEntry("file-1", "K2", "personal", "K2", "c2", "source")
        cfg = _make_config({"personal": p_personal}, [e1, e2])

        # Clean report
        clean_rep = classify(cfg, "personal", {"file-1": ("K1", "K2")},
                             existence={"K1": Existence.ABSENT, "K2": Existence.ABSENT})
        require(clean_rep.profile == "personal", "profile name must match")
        require(clean_rep.blocking is False, "clean report must not be blocking")
        require(len(clean_rep.states.get("would_create", ())) == 2,
                "both entries must be in would_create state")

        # Blocking report with unmapped key
        blocked_rep = classify(cfg, "personal", {"file-1": ("K1", "K2", "EXTRA_UNMAPPED")})
        require(blocked_rep.blocking is True, "unmapped key must cause blocking=True")
        require(BlockReason.UNMAPPED.value in blocked_rep.states,
                "unmapped state must be present in report")


if __name__ == "__main__":
    unittest.main()
