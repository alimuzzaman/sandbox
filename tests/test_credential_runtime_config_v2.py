import hashlib
import json
import os
import stat
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.isolation.credential_controller_lifecycle_v2 import DerivedServiceConfigV2
from tests import test_credential_controller_authority_v2 as authority_fixtures
from tests import test_credential_controller_lifecycle_v2 as lifecycle_fixtures


broker = authority_fixtures.broker
MACHINE = authority_fixtures.MACHINE


class Kernel:
    def __init__(self, payload, **changes):
        self.payload = payload
        self.offset = 0
        self.closed = 0
        self.opened = []
        self.observed = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=2001,
            st_size=len(payload),
        )
        for name, value in changes.items():
            setattr(self.observed, name, value)

    def open(self, path, flags):
        self.opened.append((path, flags))
        return 17
    def fstat(self, descriptor):
        self.assert_descriptor(descriptor)
        return self.observed
    def read(self, descriptor, size):
        self.assert_descriptor(descriptor)
        value = self.payload[self.offset:self.offset + size]
        self.offset += len(value)
        return value
    def close(self, descriptor):
        self.assert_descriptor(descriptor)
        self.closed += 1
    @staticmethod
    def assert_descriptor(value):
        if value != 17:
            raise OSError("bad descriptor")


def plan():
    return DerivedServiceConfigV2.derive(lifecycle_fixtures.document("broker"))


class TestCredentialRuntimeConfigV2(unittest.TestCase):
    def load(self, selected=None, **kwargs):
        selected = selected or plan()
        kernel = kwargs.pop("kernel", Kernel(selected.canonical_bytes))
        value = broker.load_runtime_config_v2(
            broker.runtime_config_path_v2(MACHINE), machine_id=MACHINE,
            expected_group_gid=2001, expected_digest=selected.config_digest,
            kernel=kernel, **kwargs,
        )
        return value, kernel

    def test_exact_canonical_owned_config_and_nofollow(self):
        value, kernel = self.load()
        self.assertEqual(value, plan())
        self.assertEqual(kernel.closed, 1)
        self.assertTrue(kernel.opened[0][1] & getattr(os, "O_NOFOLLOW", 0))

    def test_fixed_path_machine_gid_and_digest_are_preopen_gates(self):
        selected = plan()
        for changes in (
            {"path": "/tmp/forged"}, {"machine_id": "sb-ffffffffffff"},
            {"expected_group_gid": 2002}, {"expected_digest": "0" * 64},
        ):
            values = dict(
                path=broker.runtime_config_path_v2(MACHINE), machine_id=MACHINE,
                expected_group_gid=2001, expected_digest=selected.config_digest,
                kernel=Kernel(selected.canonical_bytes),
            )
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                broker.load_runtime_config_v2(**values)
            if "expected_digest" in changes or "expected_group_gid" in changes:
                self.assertEqual(len(values["kernel"].opened), 1)
                self.assertEqual(values["kernel"].closed, 1)
            else:
                self.assertEqual(values["kernel"].opened, [])

    def test_metadata_mutations_close_once(self):
        selected = plan()
        cases = (
            {"st_mode": stat.S_IFLNK | 0o640}, {"st_uid": 1}, {"st_gid": 1},
            {"st_mode": stat.S_IFREG | 0o644}, {"st_size": 0},
            {"st_size": broker.MAX_CONFIG_BYTES + 1},
        )
        for changes in cases:
            kernel = Kernel(selected.canonical_bytes, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load(selected, kernel=kernel)
            self.assertEqual(kernel.closed, 1)

    def test_short_oversize_digest_noncanonical_extra_component_machine_gid_refuse(self):
        selected = plan()
        documents = []
        for name, value in (("component", "controller"),
                            ("machine_id", "sb-ffffffffffff"),
                            ("service_gid", 2002)):
            changed = dict(selected.document)
            changed["bounds"] = dict(changed["bounds"])
            changed[name] = value
            documents.append(json.dumps(changed, sort_keys=True,
                                        separators=(",", ":")).encode("ascii"))
        changed = json.loads(selected.canonical_bytes)
        changed["extra"] = True
        documents.append(json.dumps(changed, sort_keys=True,
                                    separators=(",", ":")).encode("ascii"))
        documents.extend((selected.canonical_bytes + b"\n", b"{}",
                          b"x" * (broker.MAX_CONFIG_BYTES + 1)))
        for payload in documents:
            kernel = Kernel(payload)
            with self.subTest(size=len(payload)), self.assertRaises(ValueError):
                broker.load_runtime_config_v2(
                    broker.runtime_config_path_v2(MACHINE), machine_id=MACHINE,
                    expected_group_gid=2001,
                    expected_digest=hashlib.sha256(payload).hexdigest(), kernel=kernel,
                )
            self.assertEqual(kernel.closed, 1)

    def test_read_and_close_errors_do_not_succeed(self):
        selected = plan()
        class ReadError(Kernel):
            def read(self, *_args): raise OSError("private")
        with self.assertRaises(OSError):
            self.load(selected, kernel=ReadError(selected.canonical_bytes))
        class CloseError(Kernel):
            def close(self, descriptor):
                super().close(descriptor)
                raise OSError("private")
        with self.assertRaises(OSError):
            self.load(selected, kernel=CloseError(selected.canonical_bytes))

    def test_cli_requires_exact_v2_protocol_and_never_constructs_endpoint(self):
        selected = plan()
        path = broker.runtime_config_path_v2(MACHINE)
        good = ["--protocol", "credential-broker-controller-v2",
                "--machine-id", MACHINE, "--config", path,
                "--config-digest", selected.config_digest]
        with mock.patch.object(broker, "_running_as_root", return_value=False), \
                mock.patch.object(broker, "load_runtime_config_v2", return_value=selected) as load, \
                mock.patch.object(broker, "LinuxControllerV2Listener") as endpoint, \
                mock.patch("builtins.print") as output:
            self.assertEqual(broker.main(good), 4)
        load.assert_called_once()
        endpoint.assert_not_called()
        rendered = output.call_args.args[0]
        self.assertLessEqual(len(rendered.encode()), broker.MAX_SAFE_DOCUMENT_BYTES)
        self.assertNotIn("source", rendered.lower())
        for protocol in ("credential-broker-controller-v1",
                         "credential-broker-controller-v3"):
            bad = list(good); bad[1] = protocol
            with mock.patch.object(broker, "load_runtime_config_v2") as denied, \
                    mock.patch("builtins.print"):
                self.assertEqual(broker.main(bad), 4)
            denied.assert_not_called()


if __name__ == "__main__":
    unittest.main()
