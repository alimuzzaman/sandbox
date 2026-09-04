"""Tests for owned storage authority MCP tools."""

from __future__ import annotations

import os
import tempfile
import unittest
import sys
from pathlib import Path

MCP_ROOT = Path(__file__).resolve().parents[1] / "mcp" / "wp-server"
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))

from tools import owned_storage as mcp_storage
from sandbox.owned_storage.models import (
    AuthorityOwnedObject,
    ObjectKind,
    ObjectLifecycle,
)
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import OwnedStorageService


class TestOwnedStorageMCP(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.storage_root = self.root / "storage"
        os.environ["SANDBOX_STORAGE_ROOT"] = str(self.storage_root)

        self.db_path = self.storage_root / "authority.db"
        self.repo = StorageAuthorityRepository(self.db_path)
        self.authority_service = OwnedStorageService(self.storage_root, self.repo)
        mcp_storage._service = None

        self.remote_id = "rem_mcp"
        self.project_id = "proj_mcp"

    def tearDown(self):
        mcp_storage._service = None
        os.environ.pop("SANDBOX_STORAGE_ROOT", None)
        self.tmp_dir.cleanup()

    def test_mcp_tools_status_preview_and_reclaim(self):
        # Insert test object
        obj_dir = self.storage_root / "objects" / self.project_id / "rel_mcp" / "gen_mcp"
        obj_dir.mkdir(parents=True, exist_ok=True)
        (obj_dir / "file.bin").write_bytes(b"mcp payload")
        stat = self.authority_service.adapter.stat_identity(obj_dir)

        obj = AuthorityOwnedObject(
            object_id="obj_mcp_1",
            object_kind=ObjectKind.SYNC_GENERATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id="rel_mcp",
            workspace_id=None,
            job_id=None,
            parent_object_id=None,
            created_by_operation_id="op_mcp_1",
            lifecycle=ObjectLifecycle.SUPERSEDED,
            policy_id=None,
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            evidence_id=None,
            authority_binding_id=None,
            retention_policy_digest="sha256:mcp_ret",
            content_evidence={"generation_id": "gen_mcp"},
            filesystem_identity=stat,
            known_bytes=11,
            created_at="2026-09-04T00:00:00Z",
        )
        self.repo.save_object(obj)

        # 1. MCP status
        st_res = mcp_storage.owned_storage_status(
            remote=self.remote_id,
            project_identity=self.project_id,
        )
        self.assertTrue(st_res["ok"])
        self.assertEqual(len(st_res["objects"]), 1)
        self.assertEqual(st_res["objects"][0]["object_id"], "obj_mcp_1")

        # 2. MCP preview
        pv_res = mcp_storage.owned_storage_preview(
            remote=self.remote_id,
            project_identity=self.project_id,
        )
        self.assertTrue(pv_res["ok"])
        self.assertEqual(pv_res["estimated_reclaimable_bytes"], 11)
        prev_id = pv_res["preview_id"]

        # 3. MCP reclaim without confirm -> fails
        err_res = mcp_storage.owned_storage_reclaim(
            remote=self.remote_id,
            project_identity=self.project_id,
            preview_id=prev_id,
            object_id="obj_mcp_1",
            request_id="req_mcp_rec_1",
            confirm=False,
        )
        self.assertFalse(err_res["ok"])
        self.assertEqual(err_res["code"], "request_invalid")

        # 4. MCP reclaim with confirm=True -> succeeds
        rc_res = mcp_storage.owned_storage_reclaim(
            remote=self.remote_id,
            project_identity=self.project_id,
            preview_id=prev_id,
            object_id="obj_mcp_1",
            request_id="req_mcp_rec_2",
            confirm=True,
        )
        self.assertTrue(rc_res["ok"])
        self.assertEqual(rc_res["status"], "completed")


if __name__ == "__main__":
    unittest.main()
