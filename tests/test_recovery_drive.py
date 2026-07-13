import json
import unittest

from sandbox.recovery.drive import MemoryDrive, RcloneDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.services.process import ProcessResult


class RcloneRunner:
    def __init__(self): self.calls = []
    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        if argv[1] == "lsjson":
            return ProcessResult(tuple(argv), 0, json.dumps([{"Path": "sets/a/manifest.json"}]), "")
        if argv[1] == "copyto" and str(argv[2]).startswith("gdrive:"):
            # downloading needs a payload at its requested destination
            from pathlib import Path
            Path(argv[3]).write_bytes(b"payload")
        return ProcessResult(tuple(argv), 0, "", "")


class TestDrive(unittest.TestCase):
    def test_memory_drive_is_immutable_and_lists_pending_objects(self):
        drive = MemoryDrive(); drive.put("sets/a/archive.bin", b"payload")
        with self.assertRaises(RecoveryError): drive.put("sets/a/archive.bin", b"again")
        self.assertEqual(drive.list(), ({"Path": "sets/a/archive.bin", "Size": 7},))

    def test_rclone_uses_copyto_and_rejects_bad_destination_or_key(self):
        with self.assertRaises(RecoveryError): RcloneDrive(RcloneRunner(), "not-a-remote")
        runner = RcloneRunner(); drive = RcloneDrive(runner, "gdrive:recovery")
        drive.put("sets/a/archive.bin", b"payload")
        self.assertEqual(runner.calls[0][0][:3], ("rclone", "copyto", "--immutable"))
        with self.assertRaises(RecoveryError): drive.get("../outside")
        self.assertEqual(drive.list()[0]["Path"], "sets/a/manifest.json")
