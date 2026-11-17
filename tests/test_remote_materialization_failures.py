"""A failed measure releases the source lock.

No real SSH. Linux cases run the standalone coordinator locally through
`/bin/sh -c <one argument>`, the shape a remote login shell receives.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sandbox.core import _remote
from sandbox.core.remote_materialization import (
    RemoteMaterializationLease, RemoteMaterializationMeasureFailed,
    RemoteMaterializationUnknown,
)
from tests.subprocess_support import run_test_process
from tests.test_remote_materialization_completion import ReceiptTransport


def shell_transport(_remote_record, command, *, timeout, input_data=None):
    """Run the joined coordinator command as one shell argument, stdin attached."""
    command = command.replace("python3", shlex.quote(sys.executable), 1)
    return run_test_process(["/bin/sh", "-c", command], timeout=timeout,
                            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
                            input=input_data if input_data is not None else b"",
                            capture_output=True)


class MeasureTransport(ReceiptTransport):
    """The receipt model, plus a measure the coordinator completes with failure."""
    def __call__(self, remote, command, **kwargs):
        args = shlex.split(command)
        if args[3] != "measure":
            return super().__call__(remote, command, **kwargs)
        self.calls.append("measure")
        self.record.update(state="complete", step=self.record["step"] + 1,
                           intent_digest=args[8], exit_code=1)
        return subprocess.CompletedProcess(command, 0, json.dumps(self.record), "")


class FailedMeasureReleasesTest(unittest.TestCase):
    def test_completed_failed_measure_releases_and_records_nothing(self):
        transport = MeasureTransport()
        with tempfile.TemporaryDirectory() as runtime, \
                patch.object(_remote, "RUNTIME_DIR", Path(runtime)), \
                patch.object(_remote, "ssh_run", side_effect=transport):
            with self.assertRaises(RemoteMaterializationMeasureFailed) as failure:
                _remote.update_target_to({}, "/srv/source", "a" * 40,
                                         content_receipt={})
            self.assertNotIsInstance(failure.exception, RemoteMaterializationUnknown)
            self.assertEqual(_remote.recorded_materialization_operations({}), {})
        self.assertEqual(transport.calls, ["acquire", "run", "measure", "release"])
        self.assertIsNone(transport.record)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux subreaper contract")
class RealCoordinatorFailuresTest(unittest.TestCase):
    def test_measure_raise_completes_the_receipt_and_the_lock_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "source"
            target.mkdir()
            os.mkfifo(target / "pipe")  # content_digest refuses a fifo
            lease = RemoteMaterializationLease({}, str(target), shell_transport).acquire()
            with self.assertRaises(RemoteMaterializationMeasureFailed):
                lease.measure(str(target))
            self.assertFalse(lease.unknown)
            evidence = RemoteMaterializationLease(
                {}, str(target), shell_transport, operation_id=lease.operation_id).inspect()
            self.assertEqual((evidence["state"], evidence["exit_code"]), ("complete", 1))
            self.assertEqual(lease.release()["state"], "released")
            RemoteMaterializationLease({}, str(target), shell_transport).acquire().release()

    def test_measure_success_still_reports_the_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "source"
            target.mkdir()
            (target / "a.txt").write_text("a")
            lease = RemoteMaterializationLease({}, str(target), shell_transport).acquire()
            self.assertRegex(lease.measure(str(target)), r"^sha256:[0-9a-f]{64}$")
            lease.release()


if __name__ == "__main__":
    unittest.main()
