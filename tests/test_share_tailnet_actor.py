"""Tailnet share actors retain genuine ControlGate and receiver authority."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sandbox.hosting.shared_credentials.authentication import (
    AuthenticatedControlReceipt,
)
from sandbox.hosting.share import tailnet_actor as actor
from sandbox.installation import binding, extensions

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT / "mcp" / "wp-server"))
import tailnet_access  # noqa: E402


class SyntheticWhois:
    def lookup(self, address):
        return {"login": "owner@example.test", "node": "owner-laptop", "reason": None}


class TailnetActorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.patches = [
            mock.patch.object(binding, "machine_identity", return_value="a" * 24),
            mock.patch.object(binding, "runtime_revision", return_value="b" * 24),
            mock.patch.dict(os.environ, {"SANDBOX_REMOTE_MCP_MARKER": "c" * 24}),
            mock.patch.object(extensions, "_PROVIDERS", {}),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        binding.initialize_installation(home=self.home, ownership_marker="c" * 24, confirm=True)
        self.members_path = self.home / "runtime" / "control-access.json"
        self.members_path.parent.mkdir(parents=True, exist_ok=True)
        self.members = tailnet_access.MemberAccess(self.home)
        self.gate = tailnet_access.ControlGate("test-token", self.members, SyntheticWhois())
        self.policy = actor.OwnerDelegationPolicy("share-policy-v1", "d" * 64)

    def loader(self):
        return self.policy

    def receipt(self, *, token=True, gate=None):
        selected = gate or self.gate
        admission, receipt = asyncio.run(selected.admit_request(
            "Bearer test-token" if token else None, "100.64.0.5"))
        self.assertIsNotNone(admission)
        self.assertIs(type(receipt), AuthenticatedControlReceipt)
        return receipt

    def admit(self, receipt=None):
        return actor.admit_share_actor(receipt or self.receipt(), policy_loader=self.loader,
                                       home=self.home)

    def test_genuine_gate_receipt_binds_distinct_epoch_revision_and_receiver(self):
        context = self.admit()
        self.assertEqual(context.principal_id, self.receipt().principal_id)
        self.assertEqual(context.gate_policy_epoch, self.gate._policy_epoch)
        self.assertEqual(context.policy_revision, "share-policy-v1")
        self.assertNotEqual(context.gate_policy_epoch, context.policy_revision)
        self.assertEqual(context.receiver_binding.installation_id,
                         binding.read_installation_binding(home=self.home)["installation_id"])

    def test_personal_destination_acl_is_not_a_prerequisite(self):
        self.assertIsNotNone(self.admit(self.receipt(token=False)))

    def test_raw_or_forged_receipts_and_contexts_refuse(self):
        for forged in ({"principal_id": "owner"}, object.__new__(AuthenticatedControlReceipt)):
            with self.subTest(forged=type(forged).__name__), self.assertRaises(actor.ShareActorError):
                actor.admit_share_actor(forged, policy_loader=self.loader, home=self.home)
        with self.assertRaises(actor.ShareActorError):
            actor.revalidate_share_actor({"principal_id": "owner"},
                                         policy_loader=self.loader, home=self.home)

    def test_gate_restart_and_member_disable_invalidate_retained_receipt(self):
        old = self.admit(self.receipt(token=False))
        self.gate._policy_epoch = "rotated-gate-epoch"
        with self.assertRaises(actor.ShareActorError) as failure:
            actor.revalidate_share_actor(old, policy_loader=self.loader, home=self.home)
        self.assertEqual(failure.exception.code, "share_actor_invalid")

        self.gate = tailnet_access.ControlGate("test-token", self.members, SyntheticWhois())
        old = self.admit(self.receipt(token=False))
        self.members_path.write_text('{"tailnet_members": false}')
        os.chmod(self.members_path, 0o600)
        with self.assertRaises(actor.ShareActorError) as failure:
            actor.revalidate_share_actor(old, policy_loader=self.loader, home=self.home)
        self.assertEqual(failure.exception.code, "share_actor_invalid")

    def test_owner_policy_revision_drift_and_changed_receiver_binding_refuse(self):
        context = self.admit()
        self.policy = actor.OwnerDelegationPolicy("share-policy-v2", "e" * 64)
        with self.assertRaises(actor.ShareActorError) as failure:
            actor.revalidate_share_actor(context, policy_loader=self.loader, home=self.home)
        self.assertEqual(failure.exception.code, "share_policy_changed")
        self.policy = actor.OwnerDelegationPolicy("share-policy-v1", "d" * 64)
        with mock.patch.object(actor, "read_installation_binding",
                               side_effect=RuntimeError("binding changed")):
            with self.assertRaises(actor.ShareActorError) as failure:
                actor.revalidate_share_actor(context, policy_loader=self.loader, home=self.home)
        self.assertEqual(failure.exception.code, "share_receiver_binding_changed")

    def test_context_and_receipt_serialization_refuse(self):
        context = self.admit()
        with self.assertRaises(TypeError):
            context.__reduce__()
        with self.assertRaises(TypeError):
            self.receipt().__reduce__()

    def test_sync_api_refuses_event_loop_and_async_bridge_validates_in_worker(self):
        receipt = self.receipt()
        async def exercise():
            with self.assertRaises(actor.ShareActorError) as failure:
                actor.admit_share_actor(receipt, policy_loader=self.loader, home=self.home)
            self.assertEqual(failure.exception.code, "share_actor_worker_required")
            return await actor.admit_share_actor_async(
                receipt, policy_loader=self.loader, home=self.home)

        context = asyncio.run(exercise())
        self.assertEqual(context.policy_revision, "share-policy-v1")

    def test_malformed_or_missing_trusted_policy_fails_closed(self):
        for loader in (lambda: {}, lambda: None,
                       lambda: actor.OwnerDelegationPolicy("bad revision", "d" * 64)):
            with self.subTest(loader=loader), self.assertRaises(actor.ShareActorError):
                actor.admit_share_actor(self.receipt(), policy_loader=loader, home=self.home)

