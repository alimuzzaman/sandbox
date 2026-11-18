"""`sb preview destroy/cleanup` after a delete step fails (Fable 1118 [4]).

A delete step that ran and failed is a known outcome: its phase goes back so
the destroy can be rerun. Only a lost or timed-out step stays unknown. One row
that cannot be removed no longer stops cleanup of the other expired previews.
Every remote and provider call is a fake; nothing leaves the process.
"""
from __future__ import annotations

import io
import json
import subprocess
import tempfile
import types
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import sandbox.core._cloudflare as cloudflare
import sandbox.core._remote as remote
from sandbox.commands import preview

RECEIPT = {"instance_id": "inst1", "relation": "created", "completion": "succeeded",
           "instance_incarnation_id": "inc1"}
CONTEXT = {"project_root_digest": "digest", "label": "preview"}
DNS = {"id": "dns1", "name": "p1.example.test", "type": "A", "content": "192.0.2.1",
       "proxied": True, "comment": "sandbox"}


def _row(preview_id="p1", **extra):
    return {"id": preview_id, "remote": "r", "instance": "inst1", "domain": f"{preview_id}.example.test",
            "url": f"https://{preview_id}.example.test", "zone_id": "z1", "dns_record_id": "dns1",
            "expires_at": "2026-01-01T00:00:00+00:00", "host_identity": "host",
            "creation_context": CONTEXT, "creation_receipt": RECEIPT, "target_path": "/srv/preview-p1",
            "route_port": 8080, "dns_record": {**DNS, "name": f"{preview_id}.example.test"}, **extra}


class _Client:
    def __init__(self):
        self.deleted = []
        self.failure = None

    def records(self, _zone, domain):
        return [{**DNS, "name": domain}]

    def delete_record(self, zone, record_id):
        if self.failure is not None:
            raise self.failure
        self.deleted.append((zone, record_id))


def _http_error(code):
    error = urllib.error.HTTPError("https://api.test", code, "x", {}, io.BytesIO(b"{}"))
    try:
        raise cloudflare.CloudflareError(f"Cloudflare request failed: HTTP {code}") from error
    except cloudflare.CloudflareError as exc:
        return exc


def _failed(returncode):
    error = RuntimeError("could not delete remote Sandbox instance")
    error.returncode = returncode
    return error


class RemoteDeleteExitStatusTests(unittest.TestCase):
    def test_remote_delete_steps_report_the_remote_exit_status(self):
        reply = subprocess.CompletedProcess([], 3, stdout="", stderr="refused")
        with patch.object(remote, "ssh_run", return_value=reply), \
                patch.object(remote, "remote_sb_path", return_value="/srv/sb"), \
                patch.object(remote, "_require_front_door"):
            for call in (lambda: remote.delete_remote_instance({}, "inst1"),
                         lambda: remote.remove_instance_https_route({}, "p1.example.test")):
                with self.assertRaises(RuntimeError) as caught:
                    call()
                self.assertEqual(getattr(caught.exception, "returncode", None), 3)


class PreviewDestroyFailureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.object(preview, "_STATE_PATH", Path(directory.name) / "remote-previews.json"))
        self.client = _Client()
        self.enterContext(patch.object(preview, "PreviewProvider", return_value=self.client))
        self.enterContext(patch.object(preview, "registered_host_digest", return_value="host"))
        self.enterContext(patch.object(preview, "is_owner_target", return_value=False))
        self.enterContext(patch("sandbox.server_config.models.validate_creation_context", side_effect=lambda v: v))
        self.enterContext(patch("sandbox.server_config.models.validate_creation_receipt", side_effect=lambda v: v))
        self.enterContext(patch("sandbox.server_config.models.creation_digest", return_value="digest"))
        self.enterContext(patch.object(remote, "verify_instance_https_route"))
        self.enterContext(patch.object(remote, "read_remote_creation_receipt",
                                       return_value={"ok": True, "creation_receipt": RECEIPT}))
        self.delete = self.enterContext(patch.object(remote, "delete_remote_instance"))
        self.route = self.enterContext(patch.object(remote, "remove_instance_https_route"))
        self.entry = {"_remote_name": "r"}

    def state(self, *rows):
        return {"version": 1, "previews": {row["id"]: row for row in rows}}

    def destroy(self, state, preview_id="p1"):
        preview._destroy(self.entry, state, preview_id)

    def test_a_delete_that_ran_and_failed_can_be_rerun(self):
        state = self.state(_row())
        self.delete.side_effect = _failed(1)
        with self.assertRaises(RuntimeError):
            self.destroy(state)
        self.assertNotIn("delete_phase", state["previews"]["p1"])
        self.delete.side_effect = None
        self.destroy(state)
        self.assertEqual(self.delete.call_count, 2)
        self.assertNotIn("p1", state["previews"])
        self.assertEqual(self.client.deleted, [("z1", "dns1")])

    def test_a_failed_route_or_dns_step_goes_back_to_the_step_before_it(self):
        state = self.state(_row())
        self.route.side_effect = _failed(1)
        with self.assertRaises(RuntimeError):
            self.destroy(state)
        self.assertEqual(state["previews"]["p1"]["delete_phase"], "instance_deleted")
        self.route.side_effect = None
        self.client.failure = _http_error(400)
        with self.assertRaises(cloudflare.CloudflareError):
            self.destroy(state)
        self.assertEqual(state["previews"]["p1"]["delete_phase"], "route_deleted")
        self.client.failure = cloudflare.CloudflareError("Cloudflare rejected the request: x")
        with self.assertRaises(cloudflare.CloudflareError):
            self.destroy(state)
        self.assertEqual(state["previews"]["p1"]["delete_phase"], "route_deleted")
        self.client.failure = None
        self.destroy(state)
        self.assertEqual(self.delete.call_count, 1)
        self.assertEqual(self.route.call_count, 2)
        self.assertNotIn("p1", state["previews"])

    def test_a_lost_or_ambiguous_step_stays_unknown_and_is_never_replayed(self):
        for name, failure, phase, step in (
                ("ssh 255", _failed(255), "unknown", "delete"),
                ("remote timeout", _failed(124), "unknown", "delete"),
                ("ssh timeout", subprocess.TimeoutExpired("ssh", 300), "unknown", "delete"),
                ("route lost", _failed(255), "route_unknown", "route"),
                ("dns 5xx", _http_error(502), "dns_unknown", "dns"),
                ("dns lost", cloudflare.CloudflareError("Cloudflare request failed: provider_timeout",
                                                        "provider_timeout"), "dns_unknown", "dns")):
            with self.subTest(name=name):
                state = self.state(_row())
                self.delete.side_effect = failure if step == "delete" else None
                self.route.side_effect = failure if step == "route" else None
                self.client.failure = failure if step == "dns" else None
                with self.assertRaises(BaseException):
                    self.destroy(state)
                self.assertEqual(state["previews"]["p1"]["delete_phase"], phase)
                with self.assertRaisesRegex(ValueError, "delete_unknown"):
                    self.destroy(state)

    def test_cleanup_removes_every_other_expired_preview_and_names_the_skipped_ones(self):
        legacy = {"id": "old", "remote": "r", "instance": "inst0", "domain": "old.example.test",
                  "url": "https://old.example.test", "zone_id": "z1", "dns_record_id": "dns0",
                  "expires_at": "2026-01-01T00:00:00+00:00"}
        stuck = _row("p2", delete_phase="unknown")
        timed_out = _row("p4")
        # Rows go in order: old and p2 refuse before any delete, p4's times out.
        self.delete.side_effect = [subprocess.TimeoutExpired("ssh", 300), None]
        preview._save_state(self.state(legacy, stuck, timed_out, _row("p3")))
        args = types.SimpleNamespace(action="cleanup", remote="r", confirm=True, json=False)
        with patch.object(preview, "preview_target", return_value=self.entry), \
                patch.object(preview, "can_operate_host", return_value=True), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err, \
                self.assertRaises(SystemExit) as exit_:
            preview._cmd_preview(None, args)
        self.assertNotEqual(exit_.exception.code, 0)
        left = json.loads(preview._STATE_PATH.read_text())["previews"]
        self.assertEqual(sorted(left), ["old", "p2", "p4"])
        self.assertEqual(left["p4"]["delete_phase"], "unknown")
        message = err.getvalue()
        self.assertIn("removed 1 expired", message)
        self.assertIn("old (preview_owner_alias_or_machine_mismatch)", message)
        self.assertIn("p2 (preview_custody_unavailable_or_delete_unknown)", message)


if __name__ == "__main__":
    unittest.main()
