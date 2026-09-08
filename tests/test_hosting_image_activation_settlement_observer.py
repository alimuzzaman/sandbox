import base64
import copy
import json
import time
import unittest
from unittest.mock import patch

from sandbox.hosting.images.activation.private_settlement import inventory
from sandbox.hosting.images.activation.settlement_observer import SettlementObserver
from tests.test_hosting_image_activation_settlement_repository import _state, _plan


CID = "1" * 64
KEY = b"k" * 32
TARGET = _plan().target.as_mapping()


def row():
    return {"Id": CID, "Image": "sha256:" + "a" * 64,
        "Config": {"Labels": {"com.docker.compose.project": "app"}, "Env": ["SECRET=synthetic-value"]},
        "State": {"Status": "exited", "Running": False, "Restarting": False, "Paused": False, "Pid": 0},
        "HostConfig": {"RestartPolicy": {"Name": "no"}},
        "Mounts": [{"Type": "volume", "Name": "app_data", "Source": "/synthetic/data"}],
        "GraphDriver": {"Name": "overlay2", "Data": {"UpperDir": "/synthetic/layer"}}}


class Commands:
    def __init__(self):
        self.container = row(); self.calls = []; self.ids = [CID]; self.daemon = "daemon-a"
        self.foreign = None; self.storage = {"Driver": "overlay2", "DriverStatus": []}

    def __call__(self, argv, *, max_output_bytes):
        self.calls.append(argv)
        if argv[:2] == ["docker", "info"]: return json.dumps({"ID": self.daemon, **self.storage}).encode()
        if argv[:3] == ["docker", "ps", "-aq"]: return "\n".join(self.ids).encode()
        if argv == ["docker", "ps", "-q", "--no-trunc"]:
            return b"" if self.foreign is None else self.foreign["Id"].encode()
        if argv[:3] == ["docker", "volume", "ls"]: return b"app_data\n"
        if argv[:3] == ["docker", "volume", "inspect"]:
            return json.dumps([{"Name": "app_data", "Driver": "local", "Options": {}, "Mountpoint": "/synthetic/data"}]).encode()
        if argv == ["docker", "inspect", CID]: return json.dumps([self.container]).encode()
        if self.foreign and argv == ["docker", "inspect", self.foreign["Id"]]: return json.dumps([self.foreign]).encode()
        raise AssertionError(argv)


class SettlementObserverTests(unittest.TestCase):
    def test_containment_preserves_only_closed_native_refusals(self):
        transaction = _state()['active']
        target = transaction['recovery_context']['target']
        for native, expected in (('container_restarting', 'container_restarting'),
                                ('private-secret-canary', 'observation_unavailable')):
            observer = SettlementObserver(runner=lambda **_kwargs: {'ok': False, 'code': native},
                identity_observer=lambda: {key: target[key] for key in ('machine_identity', 'target_identity')},
                binding_key=KEY)
            with self.assertRaisesRegex(ValueError, '^' + expected + '$'):
                observer.containment(transaction=transaction, generation=0)

    def invoke(self, commands, epoch=None):
        frame = {"target": TARGET, "compose_project": "app", "transaction_digest": _plan().transaction_digest,
            "generation": 0, "binding_key": base64.b64encode(KEY).decode()}
        with patch("sandbox.hosting.images.activation.private_settlement._process_epoch",
                   side_effect=epoch, return_value={"boot": "synthetic", "daemon": {"pid": 3}}), \
             patch("sandbox.hosting.images.activation.private_settlement._path_identity", return_value={"inode": 9}):
            return inventory(frame, commands, deadline=time.monotonic() + 10)

    def test_closed_inventory_is_stable_and_never_returns_private_paths_or_environment(self):
        command = Commands()
        first = self.invoke(command)
        self.assertEqual(first, self.invoke(command))
        encoded = json.dumps(first)
        for private in ("synthetic-value", "SECRET", "/synthetic", "app_data", "UpperDir"):
            self.assertNotIn(private, encoded)
        self.assertEqual(first["container_identities"], [CID])
        self.assertEqual(len(first["preserved_identities"]), 2)
        self.assertTrue(all(argv[1] in {"ps", "inspect", "info", "volume"} for argv in command.calls))
        command.container["Config"]["Env"] = ["SECRET=changed-synthetic-value"]
        self.assertNotEqual(first["inventory_digest"], self.invoke(command)["inventory_digest"])

    def test_running_restartable_foreign_consumer_and_changed_epoch_refuse(self):
        for mutation in ("running", "restart", "foreign", "epoch", "daemon"):
            with self.subTest(mutation=mutation):
                command = Commands(); epochs = None
                if mutation == "running": command.container["State"]["Running"] = True
                if mutation == "restart": command.container["HostConfig"]["RestartPolicy"]["Name"] = "unless-stopped"
                if mutation == "foreign": command.foreign = {"Id": "f" * 64, "Mounts": copy.deepcopy(command.container["Mounts"])}
                if mutation == "epoch": epochs = [{"epoch": 1}, {"epoch": 2}]
                if mutation == "daemon": command.daemon = "changed"
                with self.assertRaisesRegex(ValueError, "not_quiescent|evidence_changed"):
                    self.invoke(command, epochs)

    def test_containerd_snapshotter_identity_is_explicit_and_change_bound(self):
        command = Commands()
        command.storage = {"Driver": "overlayfs", "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]}
        command.container.update(GraphDriver=None, Driver="overlayfs", Created="2026-09-08T00:00:00Z",
            ImageManifestDescriptor={"mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": "sha256:" + "a" * 64, "size": 123,
                "platform": {"os": "linux", "architecture": "amd64"}})
        first = self.invoke(command)
        self.assertEqual(first, self.invoke(command))
        command.container["ImageManifestDescriptor"]["digest"] = "sha256:" + "b" * 64
        self.assertNotEqual(first["preserved_identities"], self.invoke(command)["preserved_identities"])
        command.storage["DriverStatus"] = []
        with self.assertRaisesRegex(ValueError, "observation_unavailable"):
            self.invoke(command)

    def test_layer_identity_rejects_malformed_image_and_platform(self):
        for invalid in ("image", "platform", "size"):
            with self.subTest(invalid=invalid):
                command = Commands()
                command.storage = {"Driver": "overlayfs", "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]}
                command.container.update(GraphDriver=None, Driver="overlayfs", Created="2026-09-08T00:00:00Z",
                    ImageManifestDescriptor={"mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": "sha256:" + "a" * 64, "size": 123,
                        "platform": {"os": "linux", "architecture": "amd64"}})
                if invalid == "image": command.container["Image"] = "malformed"
                if invalid == "platform": command.container["ImageManifestDescriptor"]["platform"] = None
                if invalid == "size": command.container["ImageManifestDescriptor"]["size"] = 2 ** 40
                with self.assertRaisesRegex(ValueError, "observation_unavailable"):
                    self.invoke(command)

    def test_same_container_that_started_and_stopped_during_inventory_refuses(self):
        command = Commands()
        def changing(argv, **kwargs):
            if argv == ["docker", "ps", "-q", "--no-trunc"]:
                command.container["State"]["StartedAt"] = "changed"
            return command(argv, **kwargs)
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.invoke(changing)

    def test_preserved_volume_metadata_and_path_replacement_refuse(self):
        command = Commands(); reads = 0
        def changing(argv, **kwargs):
            nonlocal reads
            raw = command(argv, **kwargs)
            if argv[:3] == ["docker", "volume", "inspect"]:
                reads += 1
                if reads == 2:
                    value = json.loads(raw); value[0]["CreatedAt"] = "replacement"
                    return json.dumps(value).encode()
            return raw
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.invoke(changing)
        frame = {"target": TARGET, "compose_project": "app", "transaction_digest": _plan().transaction_digest,
            "generation": 0, "binding_key": base64.b64encode(KEY).decode()}
        with patch("sandbox.hosting.images.activation.private_settlement._process_epoch", return_value={"epoch": 1}), \
             patch("sandbox.hosting.images.activation.private_settlement._path_identity", side_effect=[{"inode": 1}, {"inode": 2}]), \
             self.assertRaisesRegex(ValueError, "evidence_changed"):
            inventory(frame, Commands(), deadline=time.monotonic() + 10)

    def test_new_foreign_consumer_at_final_fence_refuses(self):
        command = Commands(); reads = 0
        def changing(argv, **kwargs):
            nonlocal reads
            if argv == ["docker", "ps", "-q", "--no-trunc"]:
                reads += 1
                if reads == 2: return ("f" * 64).encode()
            return command(argv, **kwargs)
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.invoke(changing)

    def test_adapter_binds_fresh_machine_target_and_retained_transaction(self):
        observation = self.invoke(Commands())
        calls = []
        def runner(**values):
            calls.append(values)
            self.assertEqual(values["timeout_seconds"], 50)
            self.assertNotIn(base64.b64encode(KEY).decode(), values["program"])
            return {"ok": True, "observation": copy.deepcopy(observation)}
        identity = {key: TARGET[key] for key in ("machine_identity", "target_identity")}
        observer = SettlementObserver(runner=runner, identity_observer=lambda: identity, binding_key=KEY)
        result = observer.observe(transaction=_state()["active"], generation=0)
        self.assertEqual(result.transaction_digest, _plan().transaction_digest)
        observation["generation"] = 1
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            observer.observe(transaction=_state()["active"], generation=0)
        identity["machine_identity"] = "other"
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            observer.observe(transaction=_state()["active"], generation=0)
        self.assertEqual(len(calls), 2)

    def test_adapter_refusal_never_propagates_remote_error_text(self):
        identity = {key: TARGET[key] for key in ("machine_identity", "target_identity")}
        observer = SettlementObserver(runner=lambda **kw: {"ok": False, "code": "SECRET=synthetic-value"},
            identity_observer=lambda: identity, binding_key=KEY)
        with self.assertRaisesRegex(ValueError, "^observation_unavailable$"):
            observer.observe(transaction=_state()["active"], generation=0)
