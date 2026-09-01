import hashlib
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.subprocess_support import run_test_process, synthetic_environment


class ActivationPrivateComposeSourceTests(unittest.TestCase):
    def test_real_remote_helper_refuses_bad_rerenders_and_injects_only_private_value(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        from sandbox.hosting.images.activation.repository import empty_activation_state

        sentinel = "private-sentinel-never-public"
        clean = {"services": {"migrate": {"image": "image-a",
                                           "platform": "linux/amd64"}}}
        render_digest = "sha256:" + hashlib.sha256(json.dumps(
            clean, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        source = {"compose_files": ["compose.yml"], "project_name": "widget",
                  "environment": {}, "render_digest": render_digest,
                  "service": "migrate", "keys": ["DECLARED_KEY"]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            marker = root / "private-injection-observed"
            docker.write_text("\n".join((
                "#!/usr/bin/env python3",
                "import json, os, sys",
                "mode=os.environ.get('SYNTHETIC_DOCKER_MODE','success')",
                "if len(sys.argv)>1 and sys.argv[1]=='compose':",
                " if mode=='nonzero': sys.exit(7)",
                " if mode=='stderr': print('synthetic warning',file=sys.stderr)",
                " if mode=='malformed': print('{'); sys.exit(0)",
                " environment={'DECLARED_KEY':'private-sentinel-never-public'}",
                " if mode=='missing_key': environment={'OTHER_KEY':'synthetic'}",
                " image='image-divergent' if mode=='divergent' else 'image-a'",
                " print(json.dumps({'services':{'migrate':{'image':image,'platform':'linux/amd64','environment':environment}}}))",
                " sys.exit(0)",
                "if os.environ.get('DECLARED_KEY')!='private-sentinel-never-public': sys.exit(9)",
                f"open({str(marker)!r},'w').write('present')",
                "print('container-private-source')",
                "print(os.environ['DECLARED_KEY'])",
                "print(os.environ['DECLARED_KEY'],file=sys.stderr)",
            )))
            docker.chmod(0o700)
            commands = []

            def ssh_run(entry, command, **kwargs):
                commands.append(command)
                return run_test_process(
                    shlex.split(command),
                    env=synthetic_environment({"PATH": f"{root}:/usr/bin:/bin"}),
                    input=kwargs.get("input_data"), text=True, capture_output=True)

            runner = _host_image_argv_runner({"name": "synthetic"})
            public_argv = ("docker", "create", "--env", "DECLARED_KEY", "image-a")
            outcomes = {}
            with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run):
                for mode in ("nonzero", "stderr", "malformed", "missing_key", "divergent",
                             "success"):
                    outcomes[mode] = runner(
                        argv=public_argv,
                        environment={"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                                     "SYNTHETIC_DOCKER_MODE": mode},
                        private_environment={}, private_environment_source=source,
                        redact_environment_keys=None, timeout_seconds=30,
                        max_output_bytes=4096)
            marker_present = marker.exists() and marker.read_text() == "present"

        for mode in ("nonzero", "stderr", "malformed", "missing_key", "divergent"):
            self.assertNotEqual(outcomes[mode]["returncode"], 0, mode)
        self.assertEqual(outcomes["success"]["returncode"], 0)
        self.assertTrue(marker_present)
        self.assertIn("container-private-source", outcomes["success"]["stdout"])
        self.assertIn("[redacted]", outcomes["success"]["stdout"])
        self.assertIn("[redacted]", outcomes["success"]["stderr"])
        self.assertNotIn(sentinel, repr(public_argv))
        self.assertNotIn(sentinel, "".join(commands))
        self.assertNotIn(sentinel, "".join(
            value["stdout"] + value["stderr"] for value in outcomes.values()))
        self.assertNotIn(sentinel, json.dumps(empty_activation_state(), sort_keys=True))
        self.assertNotIn(sentinel, json.dumps({"receipt": "container-private-source"}))


if __name__ == "__main__":
    unittest.main()
