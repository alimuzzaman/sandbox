import unittest

from tests.subprocess_support import synthetic_environment


def inspection_fixture():
    image_ref = "ghcr.io/example/worker@sha256:" + "a" * 64
    document = {"services": {"migrate": {"image": image_ref, "user": "0:0",
        "command": ["node", "migrate.js"], "environment": {"TOKEN": "synthetic$$value"}}}}
    declaration = {"service": "migrate", "image_ref": image_ref, "config_digest": "sha256:" + "c" * 64}
    image = {"Id": "sha256:" + "c" * 64, "RepoDigests": [image_ref],
        "Os": "linux", "Architecture": "amd64", "Config": {"User": "node", "Env": ["PATH=/bin"], "Entrypoint": None,
                         "WorkingDir": "/app", "Volumes": None}}
    container = {"Id": "a" * 64, "Image": image["Id"], "Name": "/init-a", "RestartCount": 0,
        "Config": {"Image": image_ref, "User": "0:0", "Cmd": ["node", "migrate.js"],
            "Entrypoint": None, "WorkingDir": "/app", "Env": ["PATH=/bin", "TOKEN=synthetic$value"],
            "Labels": {"org.sandbox.init-owner.v2": "owner-a", "com.docker.compose.project": "app",
                "com.docker.compose.service": "migrate", "com.docker.compose.config-hash": "b" * 64}},
        "HostConfig": {"Privileged": False, "ReadonlyRootfs": False, "CapAdd": None, "CapDrop": None,
            "SecurityOpt": None, "Init": False, "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}},
        "Mounts": [], "NetworkSettings": {"Networks": {}}}
    kwargs = dict(document=document, declaration=declaration, project="app", owner="owner-a",
        container=container, image=image, expected_hash="b" * 64, container_name="init-a")
    return kwargs


class PrivateGraphTests(unittest.TestCase):
    def test_foreign_container_ownership_is_rejected(self):
        from sandbox.hosting.images.activation.private_graph import validate_init_container
        declaration = {"service": "migrate", "image_ref": "ghcr.io/example/worker@sha256:" + "a" * 64}
        for action in ("start", "cleanup"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                validate_init_container(document={"services": {"migrate": {}}},
                    declaration=declaration, project="app", owner="owner-a",
                    container={"Config": {"Labels": {"org.sandbox.init-owner.v2": "foreign"}}},
                    image={}, expected_hash="a" * 64, container_name="init-a")

    def test_command_and_user_are_checked_beyond_compose_labels(self):
        from sandbox.hosting.images.activation.private_graph import validate_init_container
        kwargs = inspection_fixture()
        container, image = kwargs["container"], kwargs["image"]
        validate_init_container(**kwargs)
        for key, value in (("Cmd", ["node", "different.js"]), ("User", "node"),
                           ("Env", ["TOKEN=wrong"]), ("WorkingDir", "/other")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_init_container(**{**kwargs, "container": {**container,
                    "Config": {**container["Config"], key: value}}})
        for key, value in (("Privileged", True), ("CapAdd", ["SYS_ADMIN"]), ("Init", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_init_container(**{**kwargs, "container": {**container,
                    "HostConfig": {**container["HostConfig"], key: value}}})
        with self.assertRaises(ValueError):
            validate_init_container(**{**kwargs, "container": {**container,
                "Mounts": [{"Type": "bind", "Source": "/foreign", "Destination": "/data", "RW": True}]}})

        for change in ({"Image": "sha256:" + "d" * 64}, {"RestartCount": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_init_container(**{**kwargs, "container": {**container, **change}})
        for change in ({"RepoDigests": []}, {"Architecture": "arm64"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_init_container(**{**kwargs, "image": {**image, **change}})

    def test_network_mode_none_matches_docker_network_identity(self):
        from sandbox.hosting.images.activation.private_graph import validate_init_container

        kwargs = inspection_fixture()
        kwargs["document"]["services"]["migrate"]["network_mode"] = "none"
        kwargs["container"]["HostConfig"]["NetworkMode"] = "none"
        kwargs["container"]["NetworkSettings"] = {"Networks": {"none": {}}}
        validate_init_container(**kwargs)
        with self.assertRaises(ValueError):
            validate_init_container(**{**kwargs, "container": {
                **kwargs["container"], "HostConfig": {
                    **kwargs["container"]["HostConfig"], "NetworkMode": "host"}}})
        with self.assertRaises(ValueError):
            validate_init_container(**{**kwargs, "container": {
                **kwargs["container"], "NetworkSettings": {"Networks": {}}}})

    def test_candidate_v2_accepts_only_generated_environment_secret_sources(self):
        from sandbox.hosting.images.activation.private_graph import validate_init_container
        kwargs = inspection_fixture()
        kwargs["document"]["secrets"] = {"token": {
            "environment": "SANDBOX_ACTIVATION_SECRET_0"}}
        kwargs["document"]["services"]["migrate"]["secrets"] = [{
            "source": "token", "target": "/run/secrets/token"}]
        validate_init_container(**kwargs, input_contract="candidate-v2")
        with self.assertRaises(ValueError):
            validate_init_container(**{**kwargs, "document": {
                **kwargs["document"], "secrets": {"token": {"file": "/private/secret"}}}},
                input_contract="candidate-v2")

    def test_start_only_once_and_cleanup_only_after_terminal_exit(self):
        from sandbox.hosting.images.activation.private_graph import run_init_action
        import copy
        kwargs = inspection_fixture()
        container = kwargs.pop("container")
        container["State"] = {"Status": "created", "Running": False, "ExitCode": 0}
        calls = []
        def inspect(identity):
            self.assertEqual(identity, container["Id"])
            return copy.deepcopy(container)
        def command(argv):
            calls.append(argv)
            if argv[1] == "start":
                container["State"] = {"Status": "running", "Running": True, "ExitCode": 0}
                return b""
            if argv[1] == "wait":
                container["State"] = {"Status": "exited", "Running": False, "ExitCode": 17}
                return b"17\n"
            if argv[1] == "rm":
                return b""
            self.fail(argv)
        args = dict(**kwargs, container_identity=container["Id"], inspect=inspect, command=command)
        self.assertIsNone(run_init_action(action="start", **args))
        with self.assertRaises(ValueError):
            run_init_action(action="start", **args)
        with self.assertRaises(ValueError):
            run_init_action(action="cleanup", **args)
        self.assertEqual(len(calls), 1)
        self.assertEqual(run_init_action(action="wait", **args), 17)
        self.assertIsNone(run_init_action(action="cleanup", **args))
        self.assertEqual([row[1] for row in calls], ["start", "wait", "rm"])
        self.assertEqual(calls[-1], ["docker", "rm", container["Id"]])

    def test_wrong_identity_and_unproved_wait_never_cleanup(self):
        from sandbox.hosting.images.activation.private_graph import run_init_action
        kwargs = inspection_fixture()
        container = kwargs.pop("container")
        container["State"] = {"Status": "running", "Running": True, "ExitCode": 0}
        calls = []
        args = dict(**kwargs, container_identity=container["Id"], inspect=lambda identity: container,
                    command=lambda argv: calls.append(argv) or b"0\n")
        with self.assertRaises(ValueError):
            run_init_action(action="wait", **args)
        self.assertEqual([row[1] for row in calls], ["wait"])
        calls.clear()
        with self.assertRaises(ValueError):
            run_init_action(action="start", **{**args, "container_identity": "b" * 64})
        self.assertEqual(calls, [])

    def test_create_uses_single_service_and_never_reuses_a_collision(self):
        from sandbox.hosting.images.activation.private_graph import create_init_container
        import json
        kwargs = inspection_fixture()
        container = kwargs.pop("container")
        kwargs.pop("expected_hash")
        container["State"] = {"Status": "created", "Running": False}
        calls = []
        def command(argv, *, input):
            calls.append((argv, json.loads(input)))
            if "--hash" in argv:
                return b"migrate " + b"b" * 64 + b"\n"
            return b""
        common = dict(**kwargs, project_directory="/private/candidate", command=command,
                      inspect=lambda identity: container)
        with self.assertRaises(ValueError):
            create_init_container(**common, find=lambda name: ["foreign"])
        self.assertEqual(calls, [])
        lookups = []
        def find(name):
            lookups.append(name)
            return [] if len(lookups) == 1 else [container["Id"]]
        self.assertEqual(create_init_container(**common, find=find), container["Id"])
        self.assertEqual(len(calls), 2)
        argv, document = calls[-1]
        self.assertEqual(argv[-6:], ["create", "--no-build", "--pull", "never", "--no-recreate", "migrate"])
        self.assertEqual(set(document["services"]), {"migrate"})
        self.assertNotIn("depends_on", document["services"]["migrate"])
        self.assertEqual(document["services"]["migrate"]["labels"]["org.sandbox.init-owner.v2"], "owner-a")

    def test_private_dispatch_uses_bounded_local_docker_and_returns_only_receipt(self):
        from sandbox.hosting.images.activation.private_graph import private_graph_program
        namespace = {}
        exec(private_graph_program(), namespace)
        execute_private_graph = namespace["execute_private_graph"]
        from unittest.mock import patch
        import hashlib, hmac, json, subprocess
        fixture = inspection_fixture()
        subject_digest = "sha256:" + "d" * 64
        key = b"k" * 32
        owner = hmac.new(key, b"sandbox-init-owner.v2\0" + subject_digest.encode(), hashlib.sha256).hexdigest()
        fixture["container"]["Config"]["Labels"]["org.sandbox.init-owner.v2"] = owner
        fixture["container"]["Name"] = "/sandbox-init-" + owner[:32]
        fixture["container"]["State"] = {"Status": "created", "Running": False}
        declaration = {**fixture["declaration"], "configuration_digest": "sha256:" + "e" * 64,
                       "environment_keys": ["TOKEN"], "timeout_seconds": 60}
        source = {"subject": {"kind": "initializer", "services": ["migrate"], "subject_digest": subject_digest},
            "action": "start", "container_identity": fixture["container"]["Id"],
            "project_name": "app", "project_directory": "/private/candidate",
            "render_digest": declaration["configuration_digest"],
            "execution_contract": {"declarations": [declaration]}}
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 60)
            if argv[1:3] == ["image", "inspect"]:
                output = json.dumps([fixture["image"]]).encode()
            elif "--hash" in argv:
                output = b"migrate " + b"b" * 64 + b"\n"
            elif argv[1] == "inspect":
                output = json.dumps([fixture["container"]]).encode()
            elif argv[1] == "start":
                output = b""
            else:
                self.fail(argv)
            return subprocess.CompletedProcess(argv, 0, output, b"synthetic progress" if argv[1] == "start" else b"")
        import os
        class PopenStub:
            def __init__(self, argv, **kwargs):
                result = run(argv, timeout=60, env=kwargs.get("env"))
                self.returncode = result.returncode
                if kwargs.get("stdin") is subprocess.DEVNULL:
                    self._input_reader = None
                    self.stdin = None
                else:
                    input_r, input_w = os.pipe()
                    self._input_reader = os.fdopen(input_r, "rb")
                    self.stdin = os.fdopen(input_w, "wb")
                out_r, out_w = os.pipe()
                err_r, err_w = os.pipe()
                os.write(out_w, result.stdout)
                os.close(out_w)
                os.write(err_w, result.stderr)
                os.close(err_w)
                self.stdout = os.fdopen(out_r, "rb")
                self.stderr = os.fdopen(err_r, "rb")
            def wait(self, timeout=None):
                if self._input_reader is not None:
                    self._input_reader.close()
                return self.returncode
            def kill(self):
                return None
        with patch("subprocess.Popen", side_effect=PopenStub):
            receipt = execute_private_graph(source=source, document=fixture["document"],
                environment={"PATH": "/synthetic/bin"}, configuration_key=key, timeout_seconds=60)
        self.assertEqual(receipt, {"subject_digest": subject_digest, "container_identity": "a" * 64,
                                  "exit_code": None, "terminated": True})
        self.assertEqual(calls[-1], ["docker", "start", "a" * 64])
        self.assertNotIn("synthetic$value", json.dumps(receipt))

    def test_candidate_v2_init_file_proof_failure_never_starts(self):
        from sandbox.hosting.images.activation.private_graph import run_init_action
        kwargs = inspection_fixture()
        container = kwargs.pop("container")
        container["State"] = {"Status": "created", "Running": False, "ExitCode": 0}
        calls = []

        def inspect(identity):
            return container

        def command(argv):
            calls.append(argv)
            if argv[1] == "start":
                self.fail("start must not run after private file proof failure")
            return b""

        def verify(**_kwargs):
            raise ValueError("secret_file_refused")

        with self.assertRaisesRegex(ValueError, "secret_file_refused"):
            run_init_action(action="start", container_identity=container["Id"],
                inspect=inspect, command=command, secret_mounts=[],
                verify_secret_files=verify, **kwargs)
        self.assertEqual(calls, [])

    def test_candidate_v2_inspect_prepares_once_and_later_actions_only_verify(self):
        from sandbox.hosting.images.activation.private_graph import run_init_action
        kwargs = inspection_fixture()
        container = kwargs.pop("container")
        container["State"] = {"Status": "created", "Running": False, "ExitCode": 0}
        calls, prepared, verified = [], [], []

        def inspect(identity):
            return container

        def command(argv):
            calls.append(argv)
            if argv[1] == "start":
                container["State"] = {"Status": "running", "Running": True, "ExitCode": 0}
            return b""

        def prepare(**kwargs):
            prepared.append(kwargs["identity"])

        def verify(**kwargs):
            verified.append(kwargs["identity"])

        args = dict(action="inspect", container_identity=container["Id"], inspect=inspect,
                    command=command, secret_mounts=[], prepare_secret_files=prepare,
                    verify_secret_files=verify, **kwargs)
        self.assertIsNone(run_init_action(**args))
        args["action"] = "start"
        self.assertIsNone(run_init_action(**args))
        self.assertEqual(prepared, [container["Id"]])
        self.assertEqual(verified, [container["Id"], container["Id"]])
        self.assertEqual([row[1] for row in calls], ["start"])

    def test_candidate_v2_replace_creates_selected_ids_and_never_starts_on_prepare_failure(self):
        from sandbox.hosting.images.activation.private_graph import execute_private_runtime
        import json
        import subprocess
        from unittest.mock import patch

        fixture = inspection_fixture()
        service = fixture["document"]["services"].pop("migrate")
        fixture["document"]["services"]["queue"] = service
        fixture["container"]["Config"]["Labels"]["com.docker.compose.service"] = "queue"
        fixture["container"]["Config"]["Labels"]["com.docker.compose.config-hash"] = "b" * 64
        fixture["container"]["State"] = {"Status": "created", "Running": False}
        identity = {"image_ref": fixture["declaration"]["image_ref"],
                    "config_digest": fixture["declaration"]["config_digest"],
                    "local_image_id": fixture["image"]["Id"]}
        source = {"input_contract": "candidate-v2", "subject": {"kind": "consumer", "services": ["queue"],
            "subject_digest": "sha256:" + "d" * 64}, "action": "replace", "container_identity": None,
            "project_name": "app", "project_directory": "/private/candidate",
            "image_identities": {"queue": identity}, "execution_contract": {"graph": {"readiness_timeout_seconds": 60}}}
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            if argv[1] == "version":
                out = b"linux\n"
            elif argv[1:3] == ["cp", "--help"]:
                out = b"Usage: docker cp [OPTIONS] CONTAINER:SRC_PATH DEST_PATH|-\n"
            elif argv[1:3] == ["image", "inspect"]:
                out = json.dumps([fixture["image"]]).encode()
            elif "config" in argv and "--hash" in argv:
                out = b"queue " + b"b" * 64 + b"\n"
            elif "ps" in argv:
                out = (fixture["container"]["Id"] + "\n").encode()
            elif argv[1] == "inspect":
                out = json.dumps([fixture["container"]]).encode()
            elif argv[1] == "compose" and "up" in argv:
                out = b""
            else:
                self.fail(argv)
            return subprocess.CompletedProcess(argv, 0, out, b"")

        def graph_port(_environment, _timeout):
            return (lambda argv, **_kwargs: run(argv).stdout, float("inf"))

        with patch("sandbox.hosting.images.activation.private_graph.graph_command_port", side_effect=graph_port), \
             patch("sandbox.hosting.images.activation.private_graph._retained_secret_material", return_value={}), \
             patch("sandbox.hosting.images.activation.private_graph._secret_file_mounts", return_value=[]), \
             patch("sandbox.hosting.images.activation.private_graph._prepare_secret_files",
                   side_effect=ValueError("secret_file_refused")), \
             patch("subprocess.run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "secret_file_refused"):
                execute_private_runtime(source=source, document=fixture["document"],
                    environment={"PATH": "/synthetic/bin"}, timeout_seconds=60,
                    input_contract="candidate-v2")
        compose_up = [argv for argv in calls if argv[1] == "compose" and "up" in argv]
        self.assertEqual(len(compose_up), 1)
        self.assertEqual(compose_up[0][-8:], ["up", "--no-start", "--no-deps", "--no-build",
                                              "--pull", "never", "--force-recreate", "queue"])
        self.assertFalse(any(argv[1] == "start" for argv in calls))

    def test_graph_command_bounds_streamed_output(self):
        import sys
        from sandbox.hosting.images.activation.private_graph import graph_command_port
        command, _deadline = graph_command_port(synthetic_environment(), 10)
        with self.assertRaisesRegex(ValueError, "graph_command_oversized"):
            command([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 65536)"],
                    max_output_bytes=1024)

    def test_graph_command_deadline_kills_child_that_never_reads_input(self):
        import sys
        from sandbox.hosting.images.activation.private_graph import graph_command_port
        command, _deadline = graph_command_port(synthetic_environment(), 1)
        with self.assertRaisesRegex(ValueError, "graph_deadline_exceeded"):
            command([sys.executable, "-c", "import time; time.sleep(5)"],
                    input=b"x" * (8 * 1024 * 1024), max_output_bytes=9 * 1024 * 1024)

    def test_graph_command_rejects_short_read_before_input_is_sent(self):
        import sys
        from sandbox.hosting.images.activation.private_graph import graph_command_port
        command, _deadline = graph_command_port(synthetic_environment(), 5)
        with self.assertRaisesRegex(ValueError, "graph_command_unproven"):
            command([sys.executable, "-c", "import os; os.read(0, 1)"],
                    input=b"x" * (8 * 1024 * 1024), max_output_bytes=9 * 1024 * 1024)

    def test_candidate_v2_replace_starts_only_the_exact_selected_ids(self):
        from sandbox.hosting.images.activation.private_graph import execute_private_runtime
        import copy
        import json
        import subprocess
        from unittest.mock import patch

        fixture = inspection_fixture()
        service = fixture["document"]["services"].pop("migrate")
        fixture["document"]["services"] = {"queue": copy.deepcopy(service), "web": copy.deepcopy(service)}
        image = fixture["image"]
        ids = {"queue": "a" * 64, "web": "b" * 64}
        identities = {name: {"image_ref": fixture["declaration"]["image_ref"],
            "config_digest": fixture["declaration"]["config_digest"], "local_image_id": image["Id"]}
            for name in ids}
        source = {"input_contract": "candidate-v2", "subject": {"kind": "consumer",
            "services": ["queue", "web"], "subject_digest": "sha256:" + "e" * 64},
            "action": "replace", "container_identity": None, "project_name": "app",
            "project_directory": "/private/candidate", "image_identities": identities,
            "execution_contract": {"graph": {"readiness_timeout_seconds": 60}}}
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            if argv[1] == "version":
                out = b"linux\n"
            elif argv[1:3] == ["cp", "--help"]:
                out = b"Usage: docker cp [OPTIONS] CONTAINER:SRC_PATH DEST_PATH|-\n"
            elif argv[1:3] == ["image", "inspect"]:
                out = json.dumps([image]).encode()
            elif "config" in argv and "--hash" in argv:
                out = argv[-1].encode() + b" " + (b"c" * 64) + b"\n"
            elif "ps" in argv:
                out = (ids[argv[-1]] + "\n").encode()
            elif argv[1] == "inspect":
                identity = argv[-1]
                name = next(name for name, value in ids.items() if value == identity)
                row = {"Id": identity, "Image": image["Id"], "Config": {
                    "Image": identities[name]["image_ref"], "Labels": {
                        "com.docker.compose.project": "app", "com.docker.compose.service": name,
                        "com.docker.compose.config-hash": "c" * 64}},
                    "State": {"Status": "created", "Running": False}}
                out = json.dumps([row]).encode()
            elif argv[1] == "compose" and "up" in argv:
                out = b""
            elif argv[1] == "start":
                out = b""
            else:
                self.fail(argv)
            return subprocess.CompletedProcess(argv, 0, out, b"")

        def graph_port(_environment, _timeout):
            return (lambda argv, **_kwargs: run(argv).stdout, float("inf"))

        with patch("sandbox.hosting.images.activation.private_graph.graph_command_port", side_effect=graph_port), \
             patch("sandbox.hosting.images.activation.private_graph._retained_secret_material", return_value={}), \
             patch("sandbox.hosting.images.activation.private_graph._secret_file_mounts", return_value=[]), \
             patch("sandbox.hosting.images.activation.private_graph._prepare_secret_files"), \
             patch("sandbox.hosting.images.activation.private_graph._verify_secret_files"), \
             patch("subprocess.run", side_effect=run):
            execute_private_runtime(source=source, document=fixture["document"],
                environment={"PATH": "/synthetic/bin"}, timeout_seconds=60,
                input_contract="candidate-v2")
        compose_up = [argv for argv in calls if argv[1] == "compose" and "up" in argv]
        self.assertEqual(len(compose_up), 1)
        self.assertEqual(compose_up[0][-9:], ["up", "--no-start", "--no-deps", "--no-build",
                                              "--pull", "never", "--force-recreate", "queue", "web"])
        self.assertEqual({argv[2] for argv in calls if argv[1] == "start"}, set(ids.values()))
