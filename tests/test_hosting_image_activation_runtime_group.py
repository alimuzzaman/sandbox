import json
import subprocess
import unittest
from unittest.mock import patch

from tests.test_hosting_image_activation_private_graph import inspection_fixture


class RuntimeGroupTests(unittest.TestCase):
    def test_readiness_polls_without_repeating_up(self):
        from sandbox.hosting.images.activation.private_graph import execute_private_graph
        fixture = inspection_fixture()
        service = fixture["document"]["services"].pop("migrate")
        fixture["document"]["services"]["queue"] = service
        fixture["image"]["Config"]["Healthcheck"] = {"Test": ["CMD", "probe"]}
        container = fixture["container"]
        container["Config"]["Labels"]["com.docker.compose.service"] = "queue"
        identity = {"image_ref": fixture["declaration"]["image_ref"],
            "config_digest": fixture["declaration"]["config_digest"], "local_image_id": fixture["image"]["Id"]}
        source = {"subject": {"kind": "prerequisite", "services": ["queue"], "subject_digest": "sha256:" + "d" * 64},
            "action": "replace", "container_identity": None, "project_name": "app",
            "project_directory": "/private/candidate", "image_identities": {"queue": identity},
            "execution_contract": {"graph": {"readiness_timeout_seconds": 60}}}
        calls, inspections = [], []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ["image", "inspect"]:
                out = json.dumps([fixture["image"]]).encode()
            elif "--hash" in argv:
                out = b"queue " + b"b" * 64 + b"\n"
            elif "ps" in argv:
                out = (container["Id"] + "\n").encode()
            elif argv[1] == "inspect":
                inspections.append(1)
                container["State"] = {"Status": "running", "Running": True,
                    "Health": {"Status": "healthy" if len(inspections) >= 2 else "starting"}}
                out = json.dumps([container]).encode()
            elif "up" in argv:
                out = b""
            else:
                self.fail(argv)
            return subprocess.CompletedProcess(argv, 0, out, b"")
        args = dict(source=source, document=fixture["document"], environment={"PATH": "/synthetic/bin"},
                    configuration_key=b"k" * 32, timeout_seconds=60)
        with patch("subprocess.run", side_effect=run), patch("time.sleep"):
            execute_private_graph(**args)
            source["action"] = "ready"
            receipt = execute_private_graph(**args)
        ups = [argv for argv in calls if "up" in argv]
        self.assertEqual(len(ups), 1)
        self.assertIn("--no-deps", ups[0])
        self.assertIn("--no-build", ups[0])
        self.assertEqual(ups[0][-1], "queue")
        self.assertEqual(len(inspections), 2)
        self.assertEqual(receipt["subject_digest"], source["subject"]["subject_digest"])
        self.assertIsNone(receipt["container_identity"])

        inspections.clear()
        tick = [0]
        with patch("subprocess.run", side_effect=run), patch("time.monotonic", side_effect=lambda: tick[0]), patch("time.sleep", side_effect=lambda seconds: tick.__setitem__(0, 61)):
            with self.assertRaises(ValueError):
                execute_private_graph(**args)
        self.assertEqual(len([argv for argv in calls if "up" in argv]), 1)
        self.assertEqual(len(inspections), 1)
        source["action"] = "replace"
        fixture["image"]["Id"] = "sha256:" + "f" * 64
        calls.clear()
        with patch("subprocess.run", side_effect=run), self.assertRaises(ValueError):
            execute_private_graph(**args)
        self.assertFalse(any("up" in argv for argv in calls))
