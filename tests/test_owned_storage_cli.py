"""Tests for owned storage authority CLI adapter."""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from sandbox.application.owned_storage_service import (
    OwnedStorageApplicationService,
    build_owned_storage_application_service,
)
from sandbox.commands.owned_storage import cmd_storage, configure_parser
from sandbox.owned_storage.models import (
    AuthorityOwnedObject,
    ObjectKind,
    ObjectLifecycle,
)
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import OwnedStorageService


class TestOwnedStorageCLI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.storage_root = self.root / "storage"
        os.environ["SANDBOX_STORAGE_ROOT"] = str(self.storage_root)

        self.db_path = self.storage_root / "authority.db"
        self.repo = StorageAuthorityRepository(self.db_path)
        self.authority_service = OwnedStorageService(self.storage_root, self.repo)

        self.parser = argparse.ArgumentParser()
        configure_parser(self.parser)

        self.remote_id = "rem_test"
        self.project_id = "proj_test"

    def tearDown(self):
        os.environ.pop("SANDBOX_STORAGE_ROOT", None)
        self.tmp_dir.cleanup()

    def test_cli_status_and_preview_json_output(self):
        # Insert test object
        obj_dir = self.storage_root / "objects" / self.project_id / "rel_1" / "gen_1"
        obj_dir.mkdir(parents=True, exist_ok=True)
        (obj_dir / "data.bin").write_bytes(b"hello world")
        stat = self.authority_service.adapter.stat_identity(obj_dir)

        obj = AuthorityOwnedObject(
            object_id="obj_test_1",
            object_kind=ObjectKind.SYNC_GENERATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id="rel_1",
            workspace_id=None,
            job_id=None,
            parent_object_id=None,
            created_by_operation_id="op_1",
            lifecycle=ObjectLifecycle.SUPERSEDED,
            policy_id=None,
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            evidence_id=None,
            authority_binding_id=None,
            retention_policy_digest="sha256:ret",
            content_evidence={"generation_id": "gen_1"},
            filesystem_identity=stat,
            known_bytes=11,
            created_at="2026-09-04T00:00:00Z",
        )
        self.repo.save_object(obj)

        # 1. Test status CLI command
        args = self.parser.parse_args([
            "authority", "status",
            "--remote", self.remote_id,
            "--project-identity", self.project_id,
            "--json",
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_storage(args)
        status_out = json.loads(buf.getvalue())
        self.assertTrue(status_out["ok"])
        self.assertEqual(len(status_out["objects"]), 1)
        self.assertEqual(status_out["objects"][0]["object_id"], "obj_test_1")

        # 2. Test preview CLI command
        args = self.parser.parse_args([
            "authority", "preview",
            "--remote", self.remote_id,
            "--project-identity", self.project_id,
            "--json",
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_storage(args)
        preview_out = json.loads(buf.getvalue())
        self.assertTrue(preview_out["ok"])
        self.assertEqual(preview_out["estimated_reclaimable_bytes"], 11)
        prev_id = preview_out["preview_id"]

        # 3. Test reclaim CLI command
        args = self.parser.parse_args([
            "authority", "reclaim",
            "--remote", self.remote_id,
            "--project-identity", self.project_id,
            "--preview-id", prev_id,
            "--object-id", "obj_test_1",
            "--request-id", "req_cli_reclaim",
            "--confirm",
            "--json",
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_storage(args)
        reclaim_out = json.loads(buf.getvalue())
        self.assertTrue(reclaim_out["ok"])
        self.assertEqual(reclaim_out["status"], "completed")


if __name__ == "__main__":
    unittest.main()
