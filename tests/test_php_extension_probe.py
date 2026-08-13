from __future__ import annotations

import base64
import json
from types import SimpleNamespace
import unittest

from sandbox.php_extensions.probe import (
    PlaneObservation,
    ProbeResult,
    STANDALONE_PROBE_PAYLOAD,
    build_probe_command,
    compare_planes,
    parse_probe_output,
    run_probe,
    version_matches,
)
from sandbox.php_extensions.service import (
    PhpExtensionService,
    build_provenance,
    extension_digest,
)


def _document(*, gd=True, version="2.3.3", php="8.3.10"):
    return json.dumps({
        "schema_version": 1,
        "php_version": php,
        "sapi": "cli",
        "extensions": {
            "gd": {"enabled": gd, "version": version},
            "curl": {"enabled": True, "version": None},
        },
    })


class _Runner:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.argv = None
        self.timeout = None

    def run(self, argv, *, timeout=None):
        self.argv = tuple(argv)
        self.timeout = timeout
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


class PhpExtensionProbeTests(unittest.TestCase):
    def test_payload_is_standalone_and_reflection_based(self):
        self.assertIn("extension_loaded", STANDALONE_PROBE_PAYLOAD)
        self.assertIn("ReflectionExtension", STANDALONE_PROBE_PAYLOAD)
        self.assertNotIn("wp-load", STANDALONE_PROBE_PAYLOAD)
        self.assertNotIn("require", STANDALONE_PROBE_PAYLOAD.lower())
        argv = build_probe_command({"gd": True})
        self.assertEqual(argv[0], "php")
        self.assertEqual(argv[1:6], ("-d", "display_errors=0", "-d", "log_errors=0", "-r"))
        names = json.loads(base64.b64decode(argv[-1]).decode())
        self.assertEqual(names, ["gd"])

    def test_success_and_missing_are_structured(self):
        result = parse_probe_output(_document(), {"gd": {"state": "enabled", "version": "2.3.*"}}, plane="cli")
        self.assertTrue(result.ok)
        missing = parse_probe_output(_document(gd=False), {"gd": True}, plane="web")
        self.assertFalse(missing.ok)
        self.assertEqual(missing.errors[0].code, "missing")
        self.assertEqual(missing.errors[0].plane, "web")

    def test_version_errors_are_distinct(self):
        mismatch = parse_probe_output(_document(version="3.0.0"), {"gd": "2.3.3"})
        self.assertEqual(mismatch.errors[0].code, "version_mismatch")
        unobservable = parse_probe_output(_document(version=None), {"gd": "2.3.3"})
        self.assertEqual(unobservable.errors[0].code, "version_unobservable")
        self.assertTrue(version_matches("2.3.*", "2.3.3"))
        self.assertTrue(version_matches("php", "8.3.1", php_version="8.3.10"))
        self.assertFalse(version_matches("php", "8.2.1", php_version="8.3.10"))

    def test_run_probe_uses_bounded_runner(self):
        runner = _Runner(_document())
        result = run_probe(runner, {"gd": True}, plane="exec", timeout=2)
        self.assertTrue(result.ok)
        self.assertEqual(runner.timeout, 2)
        self.assertEqual(runner.argv[0], "php")
        failed = run_probe(_Runner("", returncode=2, stderr="safe error"), {"gd": True})
        self.assertFalse(failed.ok)
        self.assertEqual(failed.errors[0].code, "probe_failed")

    def test_plane_drift_is_fail_closed(self):
        observations = {
            plane: parse_probe_output(_document(), {"gd": True}, plane=plane)
            for plane in ("web", "cli", "exec", "phpunit")
        }
        observations["exec"] = parse_probe_output(
            _document(php="8.2.9"), {"gd": True}, plane="exec"
        )
        comparison = compare_planes(observations, {"gd": True})
        self.assertFalse(comparison.ok)
        self.assertTrue(any(error.code == "plane_drift" for error in comparison.errors))

    def test_service_digest_includes_all_inputs_and_secret_free_provenance(self):
        base = extension_digest(
            {"gd": True}, parent_image_digest="sha256:" + "a" * 64,
            php_version="8.3", server="apache", platform="linux", architecture="amd64",
        )
        changed = extension_digest(
            {"gd": True}, parent_image_digest="sha256:" + "a" * 64,
            php_version="8.3", server="nginx", platform="linux", architecture="amd64",
        )
        self.assertNotEqual(base, changed)
        provenance = build_provenance(
            {"gd": True}, parent_image_digest="sha256:" + "a" * 64,
            php_version="8.3", server="apache", platform="linux", architecture="amd64",
            package_provenance=({"name": "php8.3-gd", "version": "8.3.1", "source": "official"},),
        )
        serialized = json.dumps(provenance.to_dict())
        self.assertNotIn("password", serialized.lower())
        self.assertIn("catalog_digest", provenance.to_dict())

    def test_service_verifies_all_execution_planes(self):
        probes = {
            plane: parse_probe_output(_document(), {"gd": True}, plane=plane)
            for plane in ("web", "cli", "exec", "phpunit")
        }
        verification = PhpExtensionService().verify({"gd": True}, probes)
        self.assertTrue(verification.ok)
        self.assertTrue(verification.comparison.ok)


if __name__ == "__main__":
    unittest.main()
