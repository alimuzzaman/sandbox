import json
import tempfile
import unittest
from pathlib import Path

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
        with self.assertRaises(RecoveryError): RcloneDrive(RcloneRunner(), "gdrive:recovery\0unsafe")
        with self.assertRaises(RecoveryError): RcloneDrive(RcloneRunner(), "gdrive:recovery\tunsafe")
        with self.assertRaises(RecoveryError): RcloneDrive(RcloneRunner(), "gdrive:recovery/../outside")
        runner = RcloneRunner(); drive = RcloneDrive(runner, "gdrive:recovery")
        drive.put("sets/a/archive.bin", b"payload")
        self.assertEqual(runner.calls[0][0][:3], ("rclone", "copyto", "--immutable"))
        with self.assertRaises(RecoveryError): drive.get("../outside")
        self.assertEqual(drive.list()[0]["Path"], "sets/a/manifest.json")

    def test_rclone_can_list_destination_root_for_legacy_classification(self):
        runner = RcloneRunner(); drive = RcloneDrive(runner, "gdrive:recovery")
        drive.list("")
        self.assertEqual(runner.calls[-1][0][2:], ("--recursive", "gdrive:recovery"))

    def test_rclone_rejects_malformed_listing_payloads(self):
        class MalformedRunner(RcloneRunner):
            def __init__(self, payload):
                super().__init__()
                self.payload = payload

            def run(self, argv, **kwargs):
                if argv[1] == "lsjson":
                    return ProcessResult(tuple(argv), 0, self.payload, "")
                return super().run(argv, **kwargs)

        for payload in ('{"Path":"sets/a/manifest.json"}', '[{"Path":"sets/a"}, "bad"]'):
            with self.subTest(payload=payload):
                drive = RcloneDrive(MalformedRunner(payload), "gdrive:recovery")
                with self.assertRaisesRegex(RecoveryError, "listing"):
                    drive.list()

    def test_rclone_rejects_symlink_upload_sources_and_non_string_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "target"; link = root / "link"
            target.write_bytes(b"payload"); link.symlink_to(target)
            drive = RcloneDrive(RcloneRunner(), "gdrive:recovery")
            with self.assertRaisesRegex(RecoveryError, "upload source"):
                drive.put_file("sets/a/archive.bin", link)
            with self.assertRaises(RecoveryError):
                drive.get(123)
            with self.assertRaises(RecoveryError):
                drive.get("sets/a\0unsafe")
            with self.assertRaises(RecoveryError):
                drive.get("sets/a\nunsafe")

    def test_rclone_rejects_non_regular_download_targets(self):
        class InvalidDownloadRunner(RcloneRunner):
            def __init__(self, kind):
                super().__init__()
                self.kind = kind

            def run(self, argv, **kwargs):
                if argv[1] == "copyto" and str(argv[2]).startswith("gdrive:"):
                    target = Path(argv[3])
                    if self.kind == "directory":
                        target.mkdir()
                    else:
                        source = target.with_name("source")
                        source.write_bytes(b"payload")
                        target.symlink_to(source)
                return ProcessResult(tuple(argv), 0, "", "")

        for kind in ("directory", "symlink"):
            with self.subTest(kind=kind):
                drive = RcloneDrive(InvalidDownloadRunner(kind), "gdrive:recovery")
                with self.assertRaisesRegex(RecoveryError, "download is invalid"):
                    drive.get("sets/a/archive.bin")
