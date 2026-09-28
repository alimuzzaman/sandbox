"""Candidate-v2 private Docker archive secret preparation."""

import base64
import io
import tarfile
import unittest


class PrivateSecretFileTests(unittest.TestCase):
    def _document(self):
        return {
            "services": {"web": {"secrets": [{"source": "token", "target": "token",
                                                  "uid": "1001", "gid": "1001", "mode": "0400"}]}},
            "secrets": {"token": {"environment": "SANDBOX_ACTIVATION_SECRET_0"}},
        }

    def test_resolves_generated_source_and_prepares_only_absent_stopped_directory(self):
        from sandbox.hosting.images.activation.private_secret_files import (
            prepare_container_secret_files, secret_file_mounts,
        )

        identity = "a" * 64
        material = {"token": base64.b64encode(b"synthetic-secret").decode()}
        mounts = secret_file_mounts(self._document(), "web", material)
        self.assertEqual(mounts, [{"filename": "token", "uid": 1001, "gid": 1001,
                                   "mode": 0o400, "data": b"synthetic-secret"}])
        state = {"Status": "created", "Running": False}
        written = {}

        empty_run = io.BytesIO()
        with tarfile.open(fileobj=empty_run, mode="w") as archive:
            info = tarfile.TarInfo("run")
            info.type = tarfile.DIRTYPE
            info.uid, info.gid, info.mode = 0, 0, 0o755
            archive.addfile(info)

        def inspect(value):
            self.assertEqual(value, identity)
            return {"Id": identity, "State": state, "HostConfig": {"ReadonlyRootfs": False},
                    "Mounts": []}

        def command(argv, input=None, max_output_bytes=None):
            self.assertLessEqual(max_output_bytes, 9 * 1024 * 1024)
            if argv == ["docker", "cp", identity + ":/run", "-"]:
                if "archive" not in written:
                    return empty_run.getvalue()
                return written["archive"]
            if argv == ["docker", "cp", "-", identity + ":/run"]:
                self.assertIsInstance(input, bytes)
                converted = io.BytesIO()
                with tarfile.open(fileobj=converted, mode="w") as target:
                    for name, mode in (("run", 0o755), ("run/secrets", 0o755)):
                        info = tarfile.TarInfo(name)
                        info.type = tarfile.DIRTYPE
                        info.uid, info.gid, info.mode = 0, 0, mode
                        target.addfile(info)
                    with tarfile.open(fileobj=io.BytesIO(input), mode="r:") as source:
                        for member in source:
                            if member.name == "secrets":
                                continue
                            info = tarfile.TarInfo("run/" + member.name)
                            info.uid, info.gid, info.mode, info.size = member.uid, member.gid, member.mode, member.size
                            target.addfile(info, source.extractfile(member))
                written["archive"] = converted.getvalue()
                return b""
            raise AssertionError(argv)

        self.assertEqual(prepare_container_secret_files(identity, mounts, command, inspect), "prepared")
        self.assertEqual(prepare_container_secret_files(identity, mounts, command, inspect), "replayed")
        self.assertTrue(written["archive"])

    def test_verify_accepts_running_container_but_prepare_requires_created(self):
        from sandbox.hosting.images.activation.private_secret_files import (
            prepare_container_secret_files, verify_container_secret_files,
        )

        identity = "b" * 64
        mounts = [{"filename": "token", "uid": 0, "gid": 0, "mode": 0o444, "data": b"x"}]
        archive_stream = io.BytesIO()
        with tarfile.open(fileobj=archive_stream, mode="w") as archive_writer:
            for name, mode in (("run", 0o755), ("run/secrets", 0o755)):
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                info.uid, info.gid, info.mode = 0, 0, mode
                archive_writer.addfile(info)
            info = tarfile.TarInfo("run/secrets/token")
            info.uid = info.gid = 0
            info.mode = 0o444
            info.size = 1
            archive_writer.addfile(info, io.BytesIO(b"x"))
        archive = archive_stream.getvalue()
        state = {"Status": "running", "Running": True}

        def inspect(value):
            return {"Id": identity, "State": state, "HostConfig": {"ReadonlyRootfs": False},
                    "Mounts": []}

        def command(argv, **kwargs):
            self.assertEqual(argv, ["docker", "cp", identity + ":/run", "-"])
            return archive

        self.assertTrue(verify_container_secret_files(identity, mounts, command, inspect))
        with self.assertRaisesRegex(ValueError, "secret_file_refused"):
            prepare_container_secret_files(identity, mounts, command, inspect)

    def test_refuses_mount_overlap_read_only_and_unsafe_archives(self):
        from sandbox.hosting.images.activation.private_secret_files import (
            _read_archive, secret_file_mounts,
        )

        for destination in ("/", "/run", "/run/secrets", "/run/secrets/extra"):
            identity = "c" * 64
            document = self._document()
            with self.subTest(destination=destination):
                def inspect(_value, destination=destination):
                    return {"Id": identity, "State": {"Status": "created", "Running": False},
                            "HostConfig": {"ReadonlyRootfs": False},
                            "Mounts": [{"Destination": destination}]}
                from sandbox.hosting.images.activation.private_secret_files import verify_container_secret_files
                with self.assertRaisesRegex(ValueError, "secret_file_refused"):
                    verify_container_secret_files(identity, [{"filename": "token", "uid": 0,
                        "gid": 0, "mode": 0o444, "data": b"x"}], lambda *a, **k: b"", inspect)

        readonly = {**self._document(), "services": {"web": {"read_only": True,
            "secrets": [{"source": "token"}]}}}
        with self.assertRaisesRegex(ValueError, "secret_file_refused"):
            secret_file_mounts(readonly, "web", {"token": base64.b64encode(b"x").decode()})

        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo("secrets/link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
        with self.assertRaisesRegex(ValueError, "secret_file_refused"):
            _read_archive(stream.getvalue())

    def test_private_program_is_self_contained_source(self):
        from sandbox.hosting.images.activation.private_secret_files import private_secret_program
        source = private_secret_program()
        self.assertIn("prepare_container_secret_files", source)
        self.assertNotIn("synthetic-secret", source)

    def test_private_program_executes_mount_resolution_without_repository_imports(self):
        from sandbox.hosting.images.activation.private_secret_files import private_secret_program
        namespace = {"__name__": "private_secret_program"}
        exec(private_secret_program(), namespace)
        document = self._document()
        rows = namespace["secret_file_mounts"](
            document, "web", {"token": base64.b64encode(b"synthetic").decode()})
        self.assertEqual(rows[0]["filename"], "token")
        self.assertEqual(rows[0]["data"], b"synthetic")

    def test_empty_mounts_are_a_noop_and_exact_mismatch_refuses(self):
        from sandbox.hosting.images.activation.private_secret_files import (
            prepare_container_secret_files, verify_container_secret_files,
        )
        calls = []
        self.assertEqual(prepare_container_secret_files("d" * 64, [],
            lambda *args, **kwargs: calls.append((args, kwargs)),
            lambda *args, **kwargs: calls.append((args, kwargs))), "replayed")
        self.assertEqual(calls, [])

        identity = "e" * 64
        state = {"Status": "created", "Running": False}
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as writer:
            for name, mode in (("run", 0o755), ("run/secrets", 0o755)):
                info = tarfile.TarInfo(name); info.type = tarfile.DIRTYPE
                info.uid = info.gid = 0; info.mode = mode; writer.addfile(info)
            info = tarfile.TarInfo("run/secrets/token"); info.uid = info.gid = 0
            info.mode = 0o444; info.size = 1; writer.addfile(info, io.BytesIO(b"x"))
        def inspect(_identity):
            return {"Id": identity, "State": state, "HostConfig": {"ReadonlyRootfs": False}, "Mounts": []}
        with self.assertRaisesRegex(ValueError, "secret_file_refused"):
            verify_container_secret_files(identity, [{"filename": "token", "uid": 0,
                "gid": 0, "mode": 0o444, "data": b"changed"}],
                lambda *args, **kwargs: archive.getvalue(), inspect)

    def test_lost_archive_ack_is_not_reported_prepared(self):
        from sandbox.hosting.images.activation.private_secret_files import (
            prepare_container_secret_files, secret_file_mounts,
        )
        identity = "f" * 64
        mounts = secret_file_mounts(self._document(), "web",
            {"token": base64.b64encode(b"synthetic").decode()})
        calls = []
        def inspect(_identity):
            return {"Id": identity, "State": {"Status": "created", "Running": False},
                    "HostConfig": {"ReadonlyRootfs": False}, "Mounts": []}
        def command(argv, **kwargs):
            calls.append(argv)
            if kwargs.get("input") is not None:
                raise RuntimeError("lost archive acknowledgement")
            empty = io.BytesIO()
            with tarfile.open(fileobj=empty, mode="w") as writer:
                info = tarfile.TarInfo("run"); info.type = tarfile.DIRTYPE
                info.uid = info.gid = 0; info.mode = 0o755; writer.addfile(info)
            return empty.getvalue()
        with self.assertRaisesRegex(RuntimeError, "lost archive acknowledgement"):
            prepare_container_secret_files(identity, mounts, command, inspect)
        self.assertEqual(len(calls), 2)
