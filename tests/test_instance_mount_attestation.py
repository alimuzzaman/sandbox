"""Source-only mount-attestation contracts for the ready ensure fast path."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.core import _docker, _instances  # noqa: E402


class _Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _inspect_run(mounts_by_service, *, unavailable=False, malformed=False):
    """Return a source-only Docker observation fake for every web plane."""
    def run(command, **_kwargs):
        if unavailable:
            return _Result(1, "")
        if command[:3] == ["docker", "ps", "-aq"]:
            label = next(item for item in command if item.startswith("label=com.docker.compose.service="))
            service = label.rsplit("=", 1)[-1]
            return _Result(0, f"container-{service}\n")
        if command[:2] == ["docker", "inspect"]:
            service = command[2].removeprefix("container-")
            document = {"Mounts": "malformed" if malformed else mounts_by_service[service]}
            return _Result(0, json.dumps([document]))
        raise AssertionError(command)
    return run


class TestSourceMountAttestation(unittest.TestCase):
    def _mounts(self, sources):
        # Reverse order and include a non-source bind to prove the source set is
        # canonical and order-independent without treating generated state as a source bind.
        return [
            {"Type": "bind", "Source": source, "Destination": source, "RW": False}
            for source in reversed(sources)
        ] + [{"Type": "bind", "Source": "/runtime/wp-fixture",
               "Destination": "/var/www/html", "RW": True}]

    def test_apache_nginx_and_litespeed_accept_equal_canonical_source_sets(self):
        sources = ["/tmp/plugins-home", "/tmp/extra-source"]
        for server, services in (("apache", ("wp", "wpcli")),
                                 ("nginx", ("wp", "wpcli", "nginx")),
                                 ("litespeed", ("wp", "wpcli"))):
            mounts = {service: self._mounts(sources) for service in services}
            with self.subTest(server=server), \
                    mock.patch.object(_docker, "run", _inspect_run(mounts)):
                result = _docker.attest_source_mounts("fixture", server,
                                                      ["/tmp/unused/../plugins-home", *sources[1:]])
            self.assertEqual(result, {"ok": True})

    def test_added_removed_or_changed_source_bind_refuses_as_drift(self):
        sources = ["/tmp/plugins-home", "/tmp/extra-source"]
        cases = {
            "added": sources + ["/tmp/unexpected"],
            "removed": [sources[0]],
            "writable": sources,
            "destination_changed": sources,
        }
        for case, observed_sources in cases.items():
            mounts = {service: self._mounts(observed_sources) for service in ("wp", "wpcli")}
            if case == "writable":
                mounts["wp"][0]["RW"] = True
            if case == "destination_changed":
                mounts["wp"][0]["Destination"] = "/tmp/changed-destination"
            with self.subTest(case=case), mock.patch.object(_docker, "run", _inspect_run(mounts)):
                result = _docker.attest_source_mounts("fixture", "apache", sources)
            self.assertEqual(result, {"ok": False, "code": "instance_mount_drift"})

    def test_unavailable_or_malformed_docker_evidence_refuses_without_fallback(self):
        sources = ["/tmp/plugins-home"]
        for label, fake in (("unavailable", _inspect_run({}, unavailable=True)),
                            ("malformed", _inspect_run({}, malformed=True))):
            with self.subTest(label=label), mock.patch.object(_docker, "run", fake):
                result = _docker.attest_source_mounts("fixture", "apache", sources)
            self.assertEqual(result, {"ok": False, "code": "instance_mount_state_unavailable"})

    def test_ready_refusal_precedes_every_write_capable_ensure_step(self):
        missing_plugins_home = Path(tempfile.mkdtemp()) / "not-created"

        class State:
            ConfigError = RuntimeError

            @staticmethod
            @contextlib.contextmanager
            def project_lock(_value):
                yield

            @staticmethod
            def load_project_config(_project, label=None):
                return {"root": "/project", "server": "nginx"}

            @staticmethod
            def registry_get(_root, label=None):
                return {"instance": "fixture", "status": "ready", "server": "nginx"}

            registry_put = mock.Mock()

        state = State()
        cfg = {"defaults": {"plugins_home": str(missing_plugins_home)}}
        writes = ["_resolve_port_conflicts", "_write_local_yaml", "write_compose_files",
                  "prepare_php_extension_runtime", "_wire_project_plugins", "_wire_project_themes"]
        patches = [mock.patch.object(_instances, name, side_effect=AssertionError(name)) for name in writes]
        with mock.patch.object(_instances, "_core", return_value=state), \
                mock.patch.object(_instances, "_instance_reachable", return_value=False), \
                mock.patch.object(_instances, "resolve_instances", return_value={
                    "fixture": {"server": "nginx", "extra_mounts": ["/tmp/extra-source"]},
                }), \
                mock.patch.object(_instances, "attest_source_mounts", return_value={
                    "ok": False, "code": "instance_mount_drift",
                }) as attest, \
                contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            result = _instances.ensure_instance(cfg, "/project")

        self.assertFalse(missing_plugins_home.exists())
        self.assertEqual(result["error"]["code"], "instance_mount_drift")
        self.assertFalse(result["mutated"])
        state.registry_put.assert_not_called()
        self.assertEqual(set(attest.call_args.args[2]), {
            str(missing_plugins_home.resolve()), str(Path("/tmp/extra-source").resolve()),
        })

    def test_herd_bypasses_docker_observation(self):
        self.assertEqual(_docker.attest_source_mounts("fixture", "herd", []), {"ok": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
