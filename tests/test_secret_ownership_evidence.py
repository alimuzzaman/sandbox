"""Focused regressions for storage catalog evidence admission (T053 cleaner seam).

Covers pinned SQL queries, bound params, cast-tolerant predicate parsing,
timestamp windows, destination binding checks, file safety, and operator_attested
attestation.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.ownership.evidence import (
    INDEX_STATE_QUERY, STORAGE_CONTRACT_CONFIRMED, V2_SELECTION_QUERY,
    _shared_predicate, admit_evidence,
)
from sandbox.secrets.ownership.models import Destination, StorageAdmission
from tests.secret_ownership_support import private_write, require


def _sample_destination(endpoint: str = "https://manager.example.invalid",
                        project_id: str = "proj-1") -> Destination:
    return Destination(
        endpoint=endpoint,
        project_id=project_id,
        environment="dev",
        path="/",
        read_client_id="reader",
        create_client_id="creator",
        timeout_seconds=30,
        gateway="none",
    )


def _valid_evidence_dict(destination: Destination, now: float) -> dict:
    captured_dt = datetime.fromtimestamp(now - 60, tz=timezone.utc).isoformat()
    expires_dt = datetime.fromtimestamp(now + 300, tz=timezone.utc).isoformat()
    index_output = "uq_secrets_v2_key_folder_id_shared|secrets_v2|t|t|t|t|CREATE UNIQUE INDEX uq_secrets_v2_key_folder_id_shared ON public.secrets_v2 USING btree (key, \"folderId\") WHERE type = 'shared'\n"
    v2_output = f"{destination.project_id}|3|t\n"

    return {
        "kind": "sandbox-ownership-storage-evidence",
        "version": 1,
        "producer": "operator_db_catalog_query",
        "destination": {
            "endpoint": destination.endpoint,
            "projectId": destination.project_id,
        },
        "capturedAt": captured_dt,
        "expiresAt": expires_dt,
        "queries": [
            {
                "id": "index_state",
                "sql": INDEX_STATE_QUERY,
                "output": index_output,
            },
            {
                "id": "v2_selection",
                "sql": V2_SELECTION_QUERY,
                "params": [destination.project_id],
                "output": v2_output,
            },
        ],
    }


class TestSecretOwnershipEvidence(unittest.TestCase):
    """Catalog evidence admission regressions."""

    def test_admit_evidence_valid_baseline(self) -> None:
        """Valid operator-attested evidence produces StorageAdmission."""
        dest = _sample_destination()
        now = time.time()
        data = _valid_evidence_dict(dest, now)

        with tempfile.TemporaryDirectory() as tmp_dir:
            ev_path = Path(tmp_dir) / "evidence.json"
            private_write(ev_path, json.dumps(data).encode("utf-8"))

            admission = admit_evidence(ev_path, dest, now=now)
            require(isinstance(admission, StorageAdmission), "must return StorageAdmission")
            require(admission.attestation == "operator_attested",
                    "attestation must be operator_attested")
            require(admission.destination_origin == dest.endpoint, "destination_origin must match")
            require(admission.project_id == dest.project_id, "project_id must match")
            require(len(admission.evidence_sha256) == 64, "sha256 must be 64-char hex")

    def test_evidence_fixed_sql_queries_and_bound_params(self) -> None:
        """SQL queries match pinned contracts; v2 selection requires version 3."""
        require("uq_secrets_v2_key_folder_id_shared" in INDEX_STATE_QUERY,
                "INDEX_STATE_QUERY must reference unique index")
        require("p.version = 3" in V2_SELECTION_QUERY,
                "V2_SELECTION_QUERY must check version = 3")
        require("WHERE p.id = $1" in V2_SELECTION_QUERY,
                "V2_SELECTION_QUERY must bind project_id as $1")

        dest = _sample_destination()
        now = time.time()
        data = _valid_evidence_dict(dest, now)

        # Alter v2_selection SQL
        data["queries"][1]["sql"] = "SELECT 1;"
        with tempfile.TemporaryDirectory() as tmp_dir:
            ev_path = Path(tmp_dir) / "evidence.json"
            private_write(ev_path, json.dumps(data).encode("utf-8"))
            with self.assertRaises(SecretBrokerError) as ctx:
                admit_evidence(ev_path, dest, now=now)
            require(ctx.exception.code == "evidence_inadmissible",
                    "altered SQL must be inadmissible")

    def test_evidence_cast_tolerant_predicate_parser(self) -> None:
        """Predicate parser handles casts and parentheses for type = 'shared'."""
        valid_predicates = [
            "type = 'shared'",
            "\"type\" = 'shared'",
            "(type) = ('shared')",
            "((type) = ('shared'))",
            "type::text = 'shared'",
            "type::character varying = 'shared'::text",
            "((type)::text = ('shared')::text)",
        ]
        for pred in valid_predicates:
            require(_shared_predicate(pred) is True, f"must accept valid predicate: {pred}")

        invalid_predicates = [
            "type = 'personal'",
            "type != 'shared'",
            "type IS NULL",
            "type = 'shared' OR 1=1",
            "other_col = 'shared'",
            "type = 123",
            "",
        ]
        for pred in invalid_predicates:
            require(_shared_predicate(pred) is False, f"must reject invalid predicate: {pred}")

    def test_evidence_rejection_of_expired_or_future_timestamps(self) -> None:
        """Captured in future or expired admission window is rejected with evidence_expired."""
        dest = _sample_destination()
        now = time.time()

        # Captured in future
        future_data = _valid_evidence_dict(dest, now)
        future_data["capturedAt"] = datetime.fromtimestamp(now + 100, tz=timezone.utc).isoformat()
        future_data["expiresAt"] = datetime.fromtimestamp(now + 500, tz=timezone.utc).isoformat()

        with tempfile.TemporaryDirectory() as tmp_dir:
            p1 = Path(tmp_dir) / "future.json"
            private_write(p1, json.dumps(future_data).encode("utf-8"))
            with self.assertRaises(SecretBrokerError) as ctx:
                admit_evidence(p1, dest, now=now)
            require(ctx.exception.code == "evidence_expired",
                    "future capturedAt must raise evidence_expired")

        # Expired (expiresAt < now)
        expired_data = _valid_evidence_dict(dest, now)
        expired_data["capturedAt"] = datetime.fromtimestamp(now - 400, tz=timezone.utc).isoformat()
        expired_data["expiresAt"] = datetime.fromtimestamp(now - 10, tz=timezone.utc).isoformat()

        with tempfile.TemporaryDirectory() as tmp_dir:
            p2 = Path(tmp_dir) / "expired.json"
            private_write(p2, json.dumps(expired_data).encode("utf-8"))
            with self.assertRaises(SecretBrokerError) as ctx:
                admit_evidence(p2, dest, now=now)
            require(ctx.exception.code == "evidence_expired",
                    "past expiresAt must raise evidence_expired")

    def test_evidence_destination_mismatch_refused(self) -> None:
        """Evidence bound to a different endpoint or projectId must be refused."""
        dest = _sample_destination(endpoint="https://manager.example.invalid", project_id="proj-1")
        now = time.time()
        data = _valid_evidence_dict(dest, now)

        # Mismatched projectId
        different_dest = _sample_destination(endpoint="https://manager.example.invalid",
                                            project_id="proj-2")
        with tempfile.TemporaryDirectory() as tmp_dir:
            ev_path = Path(tmp_dir) / "evidence.json"
            private_write(ev_path, json.dumps(data).encode("utf-8"))
            with self.assertRaises(SecretBrokerError) as ctx:
                admit_evidence(ev_path, different_dest, now=now)
            require(ctx.exception.code == "evidence_inadmissible",
                    "mismatched projectId must raise evidence_inadmissible")

    def test_evidence_file_safety_and_mode_refusal(self) -> None:
        """Evidence file must be 0600 regular file; broad permissions refused."""
        dest = _sample_destination()
        now = time.time()
        data = _valid_evidence_dict(dest, now)

        with tempfile.TemporaryDirectory() as tmp_dir:
            ev_path = Path(tmp_dir) / "evidence_broad.json"
            ev_path.write_bytes(json.dumps(data).encode("utf-8"))
            os.chmod(ev_path, 0o644)  # Broad permissions
            with self.assertRaises(SecretBrokerError) as ctx:
                admit_evidence(ev_path, dest, now=now)
            require(ctx.exception.code == "evidence_inadmissible",
                    "mode 0644 must raise evidence_inadmissible")

    def test_evidence_unconfirmed_contract_refusal(self) -> None:
        """If storage contract is unconfirmed, admit_evidence raises provider_contract_unconfirmed."""
        dest = _sample_destination()
        now = time.time()
        data = _valid_evidence_dict(dest, now)

        with tempfile.TemporaryDirectory() as tmp_dir:
            ev_path = Path(tmp_dir) / "evidence.json"
            private_write(ev_path, json.dumps(data).encode("utf-8"))

            import sandbox.secrets.ownership.evidence as ev_mod
            orig = ev_mod.STORAGE_CONTRACT_CONFIRMED
            try:
                ev_mod.STORAGE_CONTRACT_CONFIRMED = False
                with self.assertRaises(SecretBrokerError) as ctx:
                    admit_evidence(ev_path, dest, now=now)
                require(ctx.exception.code == "provider_contract_unconfirmed",
                        "unconfirmed contract must raise provider_contract_unconfirmed")
            finally:
                ev_mod.STORAGE_CONTRACT_CONFIRMED = orig


if __name__ == "__main__":
    unittest.main()
