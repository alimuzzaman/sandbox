"""Remote source receipt and status probe for controller auto-update
(spec 072, contracts/remote-receipt.md). ssh is stubbed; nothing leaves the
machine."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._remote as sr  # noqa: E402
from sandbox.controller_update import state  # noqa: E402

SOURCE = "f" * 40
OTHER = "e" * 40


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _staged_source(revision=SOURCE):
    digest = "sha256:" + "a" * 64
    receipt = json.dumps({"schema_version": 1, "source_revision": revision,
                          "runtime_revision": sr._remote_mcp_runtime_revision(),
                          "archive_digest": digest}, sort_keys=True, separators=(",", ":"))
    return {"stage_name": f".sb-src-stage-{revision[:12]}-" + "1" * 32,
            "source_revision": revision,
            "runtime_revision": sr._remote_mcp_runtime_revision(),
            "archive_digest": digest, "receipt": receipt}


class RemoteCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime = Path(self._tmp.name) / "runtime"
        runtime_patch = patch.object(sr, "RUNTIME_DIR", self.runtime)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        self.state_path = state.default_path(self.runtime)

    def hints(self):
        return {hint.remote: hint for hint in state.load(self.state_path).hints}


class InstallReceiptTests(RemoteCase):
    def migrate(self, remote):
        return sr.migrate_remote_mcp_service(
            remote, "127.0.0.1", 9174, "c" * 64, "https://sandbox.example.test",
            confirm=True, source_revision=SOURCE, staged_source=_staged_source())

    @patch("sandbox.core._remote.ssh_run")
    def test_install_writes_the_receipt_after_the_rollback_trap_is_cleared(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        self.migrate({"ssh": "ubuntu@203.0.113.10", "_remote_name": "demo"})
        command = mock_ssh_run.call_args.args[1]
        self.assertIn("$HOME/sandbox/runtime/source-revision.json", command)
        self.assertIn('{"schema_version":1,"source_revision":"' + SOURCE + '"}', command)
        self.assertLess(command.rindex("trap - EXIT"), command.index("source-revision.json"))
        # Best effort: a failed receipt write cannot fail the finished install.
        receipt_segment = command[command.index("source-revision.json") - 400:]
        self.assertIn("|| true", receipt_segment)

    @patch("sandbox.core._remote.ssh_run")
    def test_successful_install_records_the_local_hint(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        self.migrate({"ssh": "ubuntu@203.0.113.10", "_remote_name": "demo"})
        hint = self.hints()["demo"]
        self.assertEqual((hint.revision, hint.source), (SOURCE, "install"))

    @patch("sandbox.core._remote.ssh_run")
    def test_failed_install_records_nothing(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=1)
        with self.assertRaises(RuntimeError):
            self.migrate({"ssh": "ubuntu@203.0.113.10", "_remote_name": "demo"})
        self.assertEqual(self.hints(), {})

    @patch("sandbox.core._remote.ssh_run")
    def test_install_without_a_remote_name_records_nothing(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        self.migrate({"ssh": "ubuntu@203.0.113.10"})
        self.assertEqual(self.hints(), {})


class StatusProbeTests(RemoteCase):
    def status(self, mock_ssh_run, extra, name="demo"):
        mock_ssh_run.return_value = _completed(stdout=(
            "enabled=enabled\nactive=active\n"
            f"remote_revision={sr._remote_mcp_runtime_revision()}\n"
            f"{extra}probe_state=complete\nprobe_error=none\n"))
        remote = {"ssh": "registered-target",
                  "mcp_service": sr.remote_mcp_service_record("127.0.0.1", 9174)}
        if name:
            remote["_remote_name"] = name
        return sr.remote_mcp_service_status(remote), mock_ssh_run.call_args.args[1]

    @patch("sandbox.core._remote.ssh_run")
    def test_probe_reads_the_receipt_in_the_same_call(self, mock_ssh_run):
        status, command = self.status(mock_ssh_run, f"remote_source={SOURCE}\n")
        self.assertEqual(mock_ssh_run.call_count, 1)
        self.assertIn("source-revision.json", command)
        self.assertEqual(status["installed_source_revision"], SOURCE)
        hint = self.hints()["demo"]
        self.assertEqual((hint.revision, hint.source), (SOURCE, "probe"))

    @patch("sandbox.core._remote.ssh_run")
    def test_malformed_or_missing_receipt_is_null_and_not_recorded(self, mock_ssh_run):
        for extra in ("remote_source=unknown\n", "remote_source=unavailable\n",
                      "remote_source=" + SOURCE.upper() + "\n", "remote_source=abc\n", ""):
            status, _ = self.status(mock_ssh_run, extra)
            self.assertIsNone(status["installed_source_revision"], extra)
        self.assertEqual(self.hints(), {})

    @patch("sandbox.core._remote.ssh_run")
    def test_probe_without_a_remote_name_does_not_record(self, mock_ssh_run):
        status, _ = self.status(mock_ssh_run, f"remote_source={OTHER}\n", name=None)
        self.assertEqual(status["installed_source_revision"], OTHER)
        self.assertEqual(self.hints(), {})

    @patch("sandbox.core._remote.ssh_run")
    def test_unavailable_status_carries_a_null_source_revision(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=255)
        status = sr.remote_mcp_service_status({
            "ssh": "registered-target", "_remote_name": "demo",
            "mcp_service": sr.remote_mcp_service_record("127.0.0.1", 9174)})
        self.assertIn("installed_source_revision", status)
        self.assertIsNone(status["installed_source_revision"])


if __name__ == "__main__":
    unittest.main()
