import unittest


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
        with patch("subprocess.run", side_effect=run):
            receipt = execute_private_graph(source=source, document=fixture["document"],
                environment={"PATH": "/synthetic/bin"}, configuration_key=key, timeout_seconds=60)
        self.assertEqual(receipt, {"subject_digest": subject_digest, "container_identity": "a" * 64,
                                  "exit_code": None, "terminated": True})
        self.assertEqual(calls[-1], ["docker", "start", "a" * 64])
        self.assertNotIn("synthetic$value", json.dumps(receipt))
