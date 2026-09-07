import hashlib
import hmac
import json
import base64
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.subprocess_support import run_test_process, synthetic_environment


CONFIGURATION_KEY = b"k" * 32
CONFIGURATION_KEY_ENV = "SANDBOX_ACTIVATION_CONFIGURATION_HMAC_KEY"


def private_render_identity(raw):
    return "sha256:" + hmac.new(
        CONFIGURATION_KEY, b"sandbox-feature-051-compose-v1\0" + raw,
        hashlib.sha256).hexdigest()


def private_config_hash_identity(service, raw_hash):
    return "sha256:" + hmac.new(
        CONFIGURATION_KEY,
        b"sandbox-feature-051-compose-config-hash-v1\0" + service.encode() +
        b"\0" + raw_hash.encode(), hashlib.sha256).hexdigest()


class ActivationPrivateComposeSourceTests(unittest.TestCase):
    def test_lenzora_private_transport_refuses_unsnapshotted_secret_sources(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        from sandbox.hosting.images.activation.v2_models import PrivateComposeInputSnapshotV2
        from sandbox.transports.remote_hosting_activation import (
            RegisteredRemoteActivationTransport, RemoteActivationError,
        )
        from tests.fixtures.hosting_image_activation import lenzora_compose_fixture
        from tests.test_hosting_image_activation_v2 import artifacts, TARGET

        plan, _proof, _snapshot = artifacts()
        fixture = lenzora_compose_fixture(plan)
        rendered = fixture["compose"]
        self.assertEqual(len(fixture["persistent_services"]), 17)
        self.assertEqual(len(fixture["initializer_order"]), 3)
        bindings = plan.as_mapping()["service_image_bindings"]
        images = {row["service"]: row["image_ref"] for row in bindings if row["kind"] == "persistent"}
        by_image = {row.name: row.image_ref for row in plan.receipt.images}
        environment = {variable: by_image[name] for name, variable in plan.policy.activation_environment_bindings}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            env_file = root / "environment.env"
            env_file.write_text("WORKER_TOKEN=" + fixture["environment"]["WORKER_TOKEN"] + "\n")
            env_file.chmod(0o600)
            docker = root / "docker"
            docker.write_text("\n".join((
                "#!/usr/bin/env python3", "import json,sys", "a=sys.argv[1:]",
                "if a and a[0]=='info': print('daemon-a'); sys.exit(0)",
                "if '--hash' in a: print(a[-1] + ' ' + 'b' * 64); sys.exit(0)",
                "if '--profiles' in a: print('job-orchestration'); sys.exit(0)",
                "if a and a[0]=='compose' and 'config' in a: print(" + repr(json.dumps(rendered)) + "); sys.exit(0)",
                "sys.exit(99)")))
            docker.chmod(0o700)
            calls = []
            def ssh(_entry, command, **kwargs):
                result = run_test_process(shlex.split(command),
                    env=synthetic_environment({"PATH": f"{root}:/usr/bin:/bin"}),
                    input=kwargs.get("input_data"), text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls.append(result); return result
            provider = {"snapshot_id": "compose-snapshot/full-topology",
                "provider_revision": "fixture-v2", "target": TARGET,
                "compose_files": (str(root / "compose.yml"),), "project_name": "fixture",
                "project_directory": str(root), "environment_file": str(env_file)}
            closed = {"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
            with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh), \
                    patch("sandbox.transports.remote_hosting_activation.CLOSED_ENVIRONMENT", closed):
                transport = RegisteredRemoteActivationTransport(argv_runner=_host_image_argv_runner(
                    {}, compose_snapshot_provider=provider), configuration_binding_key=CONFIGURATION_KEY)
                arguments = {"compose_files": provider["compose_files"], "project_name": "fixture",
                    "selected_services": fixture["persistent_services"],
                    "allowed_services": tuple(sorted(rendered["services"])),
                    "service_image_bindings": images, "environment_bindings": environment}
                digest = transport.prepare_compose_snapshot_v2(**arguments, target=TARGET,
                    snapshot_id=provider["snapshot_id"], provider_revision=provider["provider_revision"])
                snapshot = PrivateComposeInputSnapshotV2.create(snapshot_id=provider["snapshot_id"],
                    provider_revision=provider["provider_revision"], target=TARGET,
                    plan_set_digest=plan.plan_set_digest, selected_services=fixture["persistent_services"],
                    configuration_digest=digest, expires_at=4_000_000_000)
                provider.update(snapshot_digest=snapshot.snapshot_digest, render_digest=digest)
                transport = RegisteredRemoteActivationTransport(argv_runner=_host_image_argv_runner(
                    {}, compose_snapshot_provider=provider), configuration_binding_key=CONFIGURATION_KEY)
                with self.assertRaises(RemoteActivationError):
                    transport.render_topology_v2(**arguments, topology_digest="sha256:" + "a" * 64,
                        private_compose_snapshot=snapshot.as_mapping())
            self.assertNotIn(fixture["environment"]["WORKER_TOKEN"],
                             "".join(result.stdout + result.stderr for result in calls))

    def test_candidate_inputs_are_private_atomic_and_bound_to_signed_source(self):
        from sandbox.transports.remote_hosting_activation import PRIVATE_CANDIDATE_INPUT_PROGRAM

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source = root / "source"; source.mkdir()
            source.chmod(0o700)
            compose = source / "compose.yml"
            compose.write_text("services:\n  web:\n    image: synthetic\n")
            for argv in (("git", "init", "-q"), ("git", "add", "compose.yml"),
                         ("git", "-c", "user.name=Fixture", "-c",
                          "user.email=fixture@example.invalid", "commit", "-qm", "fixture")):
                run_test_process(argv, cwd=source, check=True, capture_output=True)
            revision = run_test_process(("git", "rev-parse", "HEAD"), cwd=source,
                                        check=True, capture_output=True, text=True).stdout.strip()
            runtime = root / "runtime"; runtime.mkdir(mode=0o700)
            legacy = runtime / "environment.env"; legacy.write_text("STALE=1\n")
            canary = "synthetic-private-candidate-value"
            frame = {"source_directory": str(source), "source_revision": revision,
                     "runtime_directory": str(runtime), "candidate_id": "a" * 64,
                     "compose_files": ["compose.yml"],
                     "environment": "TOKEN=" + canary + "\n",
                     "compose_override": "services: {}\n"}

            def invoke(value):
                return run_test_process(("python3", "-c", PRIVATE_CANDIDATE_INPUT_PROGRAM),
                    input=json.dumps(value), text=True, capture_output=True)

            first = invoke(frame)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout), {"ok": True, "result": "prepared"})
            candidate = runtime / "activation-inputs" / ("a" * 64)
            self.assertEqual((candidate / "environment.env").read_text(), frame["environment"])
            self.assertEqual((candidate / "compose-0.yml").read_bytes(), compose.read_bytes())
            self.assertEqual(candidate.stat().st_mode & 0o777, 0o700)
            self.assertEqual((candidate / "environment.env").stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(invoke(frame).stdout)["result"], "replayed")
            changed = invoke({**frame, "environment": "TOKEN=changed\n"})
            self.assertNotEqual(changed.returncode, 0)
            self.assertEqual((candidate / "environment.env").read_text(), frame["environment"])
            mismatch = invoke({**frame, "candidate_id": "b" * 64,
                               "source_revision": "f" * 40})
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertFalse((runtime / "activation-inputs" / ("b" * 64)).exists())
            original_compose = compose.read_bytes()
            compose.write_text("services: {changed: {}}\n")
            dirty = invoke({**frame, "candidate_id": "c" * 64})
            self.assertNotEqual(dirty.returncode, 0)
            self.assertFalse((runtime / "activation-inputs" / ("c" * 64)).exists())
            compose.write_bytes(original_compose)
            foreign = root / "foreign"; foreign.mkdir()
            collision = runtime / "activation-inputs" / ("d" * 64)
            collision.symlink_to(foreign, target_is_directory=True)
            self.assertNotEqual(invoke({**frame, "candidate_id": "d" * 64}).returncode, 0)
            self.assertTrue(collision.is_symlink())
            self.assertEqual(list(foreign.iterdir()), [])
            interrupted_program = PRIVATE_CANDIDATE_INPUT_PROGRAM.replace(
                "publish(temporary, destination)", "raise OSError('synthetic interruption')")
            interrupted = run_test_process(("python3", "-c", interrupted_program),
                input=json.dumps({**frame, "candidate_id": "e" * 64}),
                text=True, capture_output=True)
            self.assertNotEqual(interrupted.returncode, 0)
            self.assertFalse((runtime / "activation-inputs" / ("e" * 64)).exists())
            self.assertEqual(list((runtime / "activation-inputs").glob(".candidate-*")), [])
            self.assertEqual(legacy.read_text(), "STALE=1\n")
            self.assertNotIn(canary, PRIVATE_CANDIDATE_INPUT_PROGRAM + first.stdout +
                             first.stderr + changed.stdout + changed.stderr +
                             dirty.stdout + dirty.stderr + interrupted.stdout + interrupted.stderr)

    def test_v2_prepare_identifies_private_render_without_exposing_it(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport
        image = "ghcr.io/acme/widget@sha256:" + "a" * 64
        canary = "private-prepare-canary-never-output"
        target = {"machine_identity": "machine-a", "target_identity": "target-a",
                  "daemon_identity": "daemon-a"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); environment_file = root / "environment.env"
            environment_file.write_text(f"DATABASE_URL={canary}\n"); environment_file.chmod(0o600)
            root = root.resolve()
            secret_file = root / "secret-0"
            secret_file.write_text(canary); secret_file.chmod(0o600)
            docker = root / "docker"
            rendered = {"services": {"web": {"image": image, "build": None,
                "pull_policy": "never", "platform": "linux/amd64", "depends_on": {},
                "environment": {"DATABASE_URL": canary},
                "secrets": [{"source": "token", "target": "token"}]}},
                "secrets": {"token": {"file": str(secret_file)}}}
            docker.write_text("\n".join((
                "#!/usr/bin/env python3", "import json,sys", "a=sys.argv[1:]",
                "if a and a[0]=='info': print('daemon-a'); sys.exit(0)",
                "if a and a[0]=='compose' and '--hash' in a: print('web '+'b'*64); sys.exit(0)",
                "if a and a[0]=='compose': print(" + repr(json.dumps(rendered)) + "); sys.exit(0)",
                "sys.exit(8)")))
            docker.chmod(0o700); results = []
            def ssh_run(_entry, command, **kwargs):
                result = run_test_process(shlex.split(command),
                    env=synthetic_environment({"PATH": f"{root}:/usr/bin:/bin"}),
                    input=kwargs.get("input_data"), text=True, capture_output=True)
                results.append(result); return result
            provider = {"snapshot_id": "compose-snapshot/test-a",
                "input_contract": "candidate-v1",
                "provider_revision": "provider-v2", "target": target,
                "compose_files": ("/synthetic/compose.yml",), "project_name": "widget",
                "project_directory": str(root), "environment_file": str(environment_file)}
            closed = {"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
            with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run), \
                    patch("sandbox.transports.remote_hosting_activation.CLOSED_ENVIRONMENT", closed):
                transport = RegisteredRemoteActivationTransport(
                    argv_runner=_host_image_argv_runner(
                        {"name": "synthetic"}, compose_snapshot_provider=provider),
                    configuration_binding_key=CONFIGURATION_KEY)
                digest = transport.prepare_compose_snapshot_v2(
                    compose_files=provider["compose_files"], project_name="widget",
                    selected_services=("web",), service_image_bindings={"web": image},
                    environment_bindings={"WEB_IMAGE": image}, target=target,
                    snapshot_id=provider["snapshot_id"], provider_revision="provider-v2",
                    input_contract="candidate-v1")
                from sandbox.hosting.images.activation.v2_models import PrivateComposeInputSnapshotV2
                snapshot = PrivateComposeInputSnapshotV2.create(
                    snapshot_id=provider["snapshot_id"], provider_revision="provider-v2",
                    target=target, plan_set_digest="sha256:" + "c" * 64,
                    selected_services=("web",), configuration_digest=digest,
                    expires_at=4_000_000_000, input_contract="candidate-v1")
                provider.update(snapshot_digest=snapshot.snapshot_digest, render_digest=digest)
                rendered_topology = transport.render_topology_v2(
                    compose_files=provider["compose_files"], project_name="widget",
                    selected_services=("web",), service_image_bindings={"web": image},
                    environment_bindings={"WEB_IMAGE": image}, topology_digest="sha256:" + "d" * 64,
                    private_compose_snapshot=snapshot.as_mapping())
                self.assertEqual(rendered_topology["configuration_digest"], digest)
                secret_file.write_text(canary + "-changed")
                from sandbox.transports.remote_hosting_activation import RemoteActivationError
                with self.assertRaises(RemoteActivationError):
                    transport.render_topology_v2(
                        compose_files=provider["compose_files"], project_name="widget",
                        selected_services=("web",), service_image_bindings={"web": image},
                        environment_bindings={"WEB_IMAGE": image}, topology_digest="sha256:" + "d" * 64,
                        private_compose_snapshot=snapshot.as_mapping())
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn(canary, "".join(
            (item.stdout or "") + (item.stderr or "") for item in results))

    def test_v2_prepare_selects_the_unique_profile_matching_declared_services(self):
        from sandbox.transports.remote_hosting_activation import (
            RegisteredRemoteActivationTransport,
        )

        image = "ghcr.io/acme/widget@sha256:" + "a" * 64
        base = {"services": {"web": {"image": image, "build": None,
            "pull_policy": "never", "platform": "linux/amd64", "depends_on": {}}},
            "x-sandbox-configuration-digest": "sha256:" + "b" * 64}
        profiled = {"services": {
            "web": base["services"]["web"],
            "worker": {"image": image, "build": None, "pull_policy": "never",
                "platform": "linux/amd64", "depends_on": {}},
        }, "x-sandbox-configuration-digest": "sha256:" + "c" * 64}
        calls = []

        def runner(*, argv, **kwargs):
            calls.append(argv)
            if "--profiles" in argv:
                return {"returncode": 0, "stdout": "object-storage\njob-orchestration\n",
                        "stderr": "", "terminated": True}
            if "--profile" in argv:
                profile = argv[argv.index("--profile") + 1]
                self.assertEqual(kwargs["environment"].get("COMPOSE_PROFILES"), profile)
                rendered = profiled if profile == "job-orchestration" else base
            else:
                rendered = base
            return {"returncode": 0, "stdout": json.dumps(rendered),
                    "stderr": "", "terminated": True}

        transport = RegisteredRemoteActivationTransport(
            argv_runner=runner, configuration_binding_key=CONFIGURATION_KEY)
        digest = transport.prepare_compose_snapshot_v2(
            compose_files=("/synthetic/compose.yml",), project_name="widget",
            selected_services=("web", "worker"),
            service_image_bindings={"web": image, "worker": image},
            environment_bindings={"WEB_IMAGE": image, "WORKER_IMAGE": image},
            target={"machine_identity": "machine-a", "target_identity": "target-a",
                    "daemon_identity": "daemon-a"},
            snapshot_id="compose-snapshot/test-profile", provider_revision="provider-v2")

        self.assertEqual(digest, "sha256:" + "c" * 64)
        self.assertTrue(any("--profile" in call and
                            call[call.index("--profile") + 1] == "job-orchestration"
                            for call in calls))

    def test_running_projection_keeps_env_labels_and_raw_config_hash_remote(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        from sandbox.transports.remote_hosting_activation import (
            RegisteredRemoteActivationTransport,
        )
        env_canary = "PRIVATE-RUNTIME-ENV-NEVER-PUBLIC"
        label_canary = "PRIVATE-ARBITRARY-LABEL-NEVER-PUBLIC"
        raw_hash = "e" * 64
        image_id = "sha256:" + "d" * 64
        image = "ghcr.io/acme/widget@sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); docker = root / "docker"
            docker.write_text("\n".join((
                "#!/usr/bin/env python3",
                "import json, os, sys",
                "a=sys.argv[1:]",
                "if 'SANDBOX_ACTIVATION_CONFIGURATION_HMAC_KEY' in os.environ: sys.exit(97)",
                "if a and a[0]=='info': print('daemon-a'); sys.exit(0)",
                "if a and a[0]=='ps': print('container-a'); sys.exit(0)",
                "if a[:2]==['inspect','container-a']:",
                " print(json.dumps([{'Id':'container-runtime-a','Image':" + repr(image_id) +
                    ",'Config':{'Image':" + repr(image) + ",'Env':['TOKEN=" + env_canary +
                    "'],'Labels':{'com.docker.compose.project':'widget','com.docker.compose.service':'web','com.docker.compose.config-hash':" +
                    repr(raw_hash) + ",'org.sandbox.application-topology.v1':'sha256:'+'b'*64,'private.label':" + repr(label_canary) +
                    "}},'State':{'Health':{'Status':'healthy'}}}]))",
                " sys.exit(0)",
                "if a[:2]==['image','inspect']:",
                " print(json.dumps([{'Id':" + repr(image_id) + ",'Os':'linux','Architecture':'amd64'}])); sys.exit(0)",
                "sys.exit(8)",
            )))
            docker.chmod(0o700)
            results = []
            def ssh_run(entry, command, **kwargs):
                result = run_test_process(
                    shlex.split(command),
                    env=synthetic_environment({"PATH": f"{root}:/usr/bin:/bin"}),
                    input=kwargs.get("input_data"), text=True, capture_output=True)
                results.append(result)
                return result
            closed = {"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
            with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run), \
                    patch("sandbox.transports.remote_hosting_activation.CLOSED_ENVIRONMENT", closed):
                transport = RegisteredRemoteActivationTransport(
                    argv_runner=_host_image_argv_runner({"name": "synthetic"}),
                    target_identity_observer=lambda: {
                        "machine_identity": "machine-a", "target_identity": "target-a"},
                    configuration_binding_key=CONFIGURATION_KEY)
                observed = transport.observe_running(
                    target={}, services=("web",), compose_project="widget")
        public = "".join((item.stdout or "") + (item.stderr or "") for item in results)
        self.assertEqual(observed["services"][0]["compose_config_hash"],
                         private_config_hash_identity("web", raw_hash))
        for canary in (env_canary, label_canary, raw_hash):
            self.assertNotIn(canary, public)
            self.assertNotIn(canary, json.dumps(observed, sort_keys=True))

    def test_transport_and_real_helper_use_one_render_for_effect_and_runtime_hash_proof(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        from sandbox.transports.remote_hosting_activation import (
            RegisteredRemoteActivationTransport,
        )
        image = "ghcr.io/acme/widget@sha256:" + "a" * 64
        sentinel = "private-effect-value-never-public"
        rendered = {"services": {"web": {"image": image, "build": None,
            "pull_policy": "never", "platform": "linux/amd64", "depends_on": {},
            "environment": {"DATABASE_URL": sentinel},
            "labels": {"org.sandbox.application-topology.v1": "topology-a"}}}}
        raw_render = (json.dumps(rendered) + "\n").encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); docker = root / "docker"
            effect = root / "effect-render"
            docker.write_text("\n".join((
                "#!/usr/bin/env python3",
                "import json, os, sys",
                "a=sys.argv[1:]",
                "if 'SANDBOX_ACTIVATION_CONFIGURATION_HMAC_KEY' in os.environ: sys.exit(97)",
                "if a and a[0]=='info': print('daemon-a'); sys.exit(0)",
                "if a and a[0]=='inspect': print('a'*64); sys.exit(0)",
                "if a and a[0]=='compose':",
                " data=sys.stdin.buffer.read()",
                " if '--format' in a and 'json' in a: print(" + repr(json.dumps(rendered)) + "); sys.exit(0)",
                " if 'up' in a: open(" + repr(str(effect)) + ",'wb').write(data); print(" + repr(sentinel) + "); sys.exit(0)",
                " if '--hash' in a: print('web '+'a'*64); sys.exit(0)",
                " if 'ps' in a and '--quiet' in a: print('container-a'); sys.exit(0)",
                "sys.exit(8)",
            )))
            docker.chmod(0o700)
            commands = []; results = []
            def ssh_run(entry, command, **kwargs):
                commands.append(command)
                result = run_test_process(
                    shlex.split(command),
                    env=synthetic_environment({"PATH": f"{root}:/usr/bin:/bin"}),
                    input=kwargs.get("input_data"), text=True, capture_output=True)
                results.append(result)
                return result
            closed = {"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
            with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run), \
                    patch("sandbox.transports.remote_hosting_activation.CLOSED_ENVIRONMENT", closed):
                transport = RegisteredRemoteActivationTransport(
                    argv_runner=_host_image_argv_runner({"name": "synthetic"}),
                    target_identity_observer=lambda: {
                        "machine_identity": "machine-a", "target_identity": "target-a"},
                    configuration_binding_key=CONFIGURATION_KEY)
                observed = transport.render_topology(
                    compose_files=("/synthetic/compose.yml",), project_name="widget",
                    selected_services=("web",), image_overrides={"web": image})
                transport.replace_services(
                    compose_files=("/synthetic/compose.yml",), project_name="widget",
                    services=("web",), exact_image=image,
                    environment_overrides={"SANDBOX_ACTIVATION_IMAGE_WEB": image},
                    timeout_seconds=30)
            self.assertEqual(effect.read_bytes(), raw_render)
            self.assertEqual(observed["configuration_digest"],
                             private_render_identity(raw_render))
            self.assertEqual(observed["services"]["web"]["compose_config_hash"],
                             private_config_hash_identity("web", "a" * 64))
            self.assertNotEqual(observed["configuration_digest"],
                                "sha256:" + hashlib.sha256(raw_render).hexdigest())
            self.assertTrue(any("--file -" in command for command in commands))
            self.assertNotIn(sentinel, "".join(commands))
            self.assertNotIn(sentinel, "".join(
                (item.stdout or "") + (item.stderr or "") for item in results))
            encoded_key = base64.b64encode(CONFIGURATION_KEY).decode()
            self.assertNotIn(encoded_key, "".join(commands))
            self.assertNotIn(encoded_key, "".join(
                (item.stdout or "") + (item.stderr or "") for item in results))

    def test_real_remote_helper_refuses_bad_rerenders_and_injects_only_private_value(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        from sandbox.hosting.images.activation.repository import empty_activation_state

        sentinel = "private-sentinel-never-public"
        clean = {"services": {"migrate": {"image": "image-a",
                                           "platform": "linux/amd64"}}}
        rendered_with_private = {"services": {"migrate": {"image": "image-a",
            "platform": "linux/amd64", "environment": {
                "DECLARED_KEY": sentinel}}}}
        render_digest = private_render_identity(
            (json.dumps(rendered_with_private) + "\n").encode())
        source = {"compose_files": ["compose.yml"], "project_name": "widget",
                  "project_directory": "/synthetic",
                  "environment": {}, "render_digest": render_digest,
                  "runtime_epoch": "daemon-a",
                  "service": "migrate", "keys": ["DECLARED_KEY"]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            marker = root / "private-injection-observed"
            docker.write_text("\n".join((
                "#!/usr/bin/env python3",
                "import json, os, sys",
                "mode=os.environ.get('SYNTHETIC_DOCKER_MODE','success')",
                "if len(sys.argv)>1 and sys.argv[1]=='info': print('daemon-a'); sys.exit(0)",
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
                        private_environment={CONFIGURATION_KEY_ENV: base64.b64encode(
                            CONFIGURATION_KEY).decode()}, private_environment_source=source,
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

    def test_real_helper_redacts_inline_content_and_all_duplicate_key_values(self):
        from sandbox.commands.hosting import _host_image_argv_runner

        first = "SYNTHETIC_SECRET"
        second = "SYNTHETIC_SECRET_WITH_SUFFIX"
        inline = "SYNTHETIC_INLINE_CONFIG"
        command_value = "SYNTHETIC_COMMAND_PIN_0042"
        entrypoint_value = "SYNTHETIC_ENTRYPOINT_PIN_0042"
        label_value = "SYNTHETIC_LABEL_PIN_0042"
        label_key = "SYNTHETIC_INTERPOLATED_LABEL_KEY_0042"
        annotation_value = "SYNTHETIC_ANNOTATION_PIN_0042"
        health_value = "SYNTHETIC_HEALTH_PIN_0042"
        url_value = "https://SYNTHETIC_URL_PIN_0042.invalid"
        logging_value = "SYNTHETIC_LOGGING_PIN_0042"
        extension_value = "SYNTHETIC_EXTENSION_PIN_0042"
        rendered = {"configs": {"settings": {"content": inline}}, "services": {
            "web": {"image": "image-a", "platform": "linux/amd64",
                    "environment": {"TOKEN": first},
                    "command": ["serve", command_value],
                    "entrypoint": [entrypoint_value],
                    "labels": {label_key: label_value},
                    "annotations": [annotation_value],
                    "healthcheck": {"test": ["CMD", health_value]},
                    "extra_hosts": [url_value],
                    "logging": {"options": {"tag": logging_value}}},
            "worker": {"image": "image-a", "platform": "linux/amd64",
                       "environment": {"TOKEN": second}}},
            "x-private-extension": extension_value}
        source = {"kind": "compose_replace_v1", "compose_files": ["compose.yml"],
                  "project_name": "widget", "project_directory": "/synthetic",
                  "environment": {}, "render_digest": private_render_identity(
                      (json.dumps(rendered) + "\n").encode()),
                  "runtime_epoch": "daemon-a", "services": ["web", "worker"]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); docker = root / "docker"
            docker.write_text("\n".join((
                "#!/usr/bin/env python3",
                "import json, sys",
                "a=sys.argv[1:]",
                "if a and a[0]=='info': print('daemon-a'); sys.exit(0)",
                "if a and a[0]=='compose' and 'config' in a: print(" +
                    repr(json.dumps(rendered)) + "); sys.exit(0)",
                "print(" + repr(" ".join((first, second, inline, command_value,
                    entrypoint_value, label_key, label_value, annotation_value, health_value,
                    url_value, logging_value, extension_value))) + ")",
                "print(" + repr(" ".join((first, second, inline, command_value,
                    entrypoint_value, label_key, label_value, annotation_value, health_value,
                    url_value, logging_value, extension_value))) + ",file=sys.stderr)",
                "sys.exit(7)",
            )))
            docker.chmod(0o700)
            def ssh_run(entry, command, **kwargs):
                return run_test_process(
                    shlex.split(command),
                    env=synthetic_environment({"PATH": f"{root}:/usr/bin:/bin"}),
                    input=kwargs.get("input_data"), text=True, capture_output=True)
            with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run):
                result = _host_image_argv_runner({"name": "synthetic"})(
                    argv=("docker", "compose", "--file", "-", "up"),
                    environment={"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                    private_environment={CONFIGURATION_KEY_ENV: base64.b64encode(
                        CONFIGURATION_KEY).decode()}, private_environment_source=source,
                    redact_environment_keys=None, timeout_seconds=30,
                    max_output_bytes=4096)
                rendered_result = _host_image_argv_runner({"name": "synthetic"})(
                    argv=("docker", "compose", "--file", "compose.yml", "config",
                          "--format", "json"),
                    environment={"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                    private_environment={CONFIGURATION_KEY_ENV: base64.b64encode(
                        CONFIGURATION_KEY).decode()}, private_environment_source={},
                    redact_environment_keys=None, timeout_seconds=30,
                    max_output_bytes=4096)
        combined = result["stdout"] + result["stderr"] + rendered_result["stdout"]
        for value in (first, second, inline, command_value, entrypoint_value,
                      label_key, label_value, annotation_value, health_value, url_value,
                      logging_value, extension_value):
            self.assertNotIn(value, combined)
        self.assertIn("[redacted]", combined)

    def test_v2_helper_reads_owner_only_env_by_fd_and_redacts_effect_stderr(self):
        from sandbox.commands.hosting import _host_image_argv_runner

        secret = "v2-private-env-file-sentinel"
        image = "ghcr.io/lenzora/lenzora/web@sha256:" + "a" * 64
        rendered = {"services": {"web": {"image": image,
            "environment": {"DATABASE_URL": secret}}}}
        raw_render = (json.dumps(rendered) + "\n").encode()
        render_digest = "sha256:" + hmac.new(
            CONFIGURATION_KEY,
            b"sandbox-hosting-private-compose-render.v2\0" + raw_render,
            hashlib.sha256).hexdigest()
        digest = "sha256:" + "b" * 64
        target = {"machine_identity": "machine-a", "target_identity": "target-a",
                  "daemon_identity": "daemon-a"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "environment.env"
            env_file.write_text(f"DATABASE_URL={secret}\n")
            env_file.chmod(0o600)
            docker = root / "docker"
            docker.write_text("\n".join((
                "#!/usr/bin/env python3",
                "import sys",
                "a=sys.argv[1:]",
                "if a and a[0]=='info': print('daemon-a'); sys.exit(0)",
                "if a and a[0]=='compose' and 'config' in a: print(" +
                    repr(json.dumps(rendered)) + "); sys.exit(0)",
                "print(" + repr(secret) + ",file=sys.stderr)",
                "sys.exit(7)",
            )))
            docker.chmod(0o700)
            provider = {"snapshot_id": "compose-snapshot/test-v2",
                "snapshot_digest": digest, "provider_revision": "provider-v2",
                "target": target, "compose_files": (str(root / "compose.yml"),),
                "project_name": "lenzora", "project_directory": str(root),
                "environment_file": str(env_file), "render_digest": render_digest}
            source = {"kind": "compose_replace_v2",
                "snapshot_id": provider["snapshot_id"],
                "snapshot_digest": digest, "provider_revision": "provider-v2",
                "target": target, "services": ["web"],
                "render_digest": render_digest, "topology_digest": digest}
            captured = []

            def ssh_run(entry, command, **kwargs):
                captured.append((command, kwargs.get("input_data", "")))
                return run_test_process(
                    shlex.split(command),
                    env=synthetic_environment({"PATH": f"{root}:/usr/bin:/bin"}),
                    input=kwargs.get("input_data"), text=True, capture_output=True)

            with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run):
                result = _host_image_argv_runner(
                    {"name": "synthetic"}, compose_snapshot_provider=provider)(
                    argv=("docker", "compose", "--file", "-", "up"),
                    environment={"PATH": f"{root}:/usr/bin:/bin", "LANG": "C",
                                 "LC_ALL": "C", "LENZORA_PRODUCTION_WEB_IMAGE": image},
                    private_environment={CONFIGURATION_KEY_ENV: base64.b64encode(
                        CONFIGURATION_KEY).decode()}, private_environment_source=source,
                    redact_environment_keys=None, timeout_seconds=30,
                    max_output_bytes=4096)

        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("[redacted]", result["stderr"])
        self.assertNotIn(secret, result["stdout"] + result["stderr"])
        self.assertNotIn(secret, "".join(command + frame for command, frame in captured))
        self.assertIn("/proc/self/fd/", "".join(command for command, _ in captured))

    def test_real_private_observer_selects_unique_digest_matching_profile(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        import hmac
        image = "ghcr.io/acme/widget@sha256:" + "a" * 64
        rendered = {"services": {"web": {"image": image}}}
        raw = (json.dumps(rendered) + "\n").encode()
        digest = "sha256:" + hmac.new(CONFIGURATION_KEY,
            b"sandbox-hosting-private-compose-render.v2\0" + raw, hashlib.sha256).hexdigest()
        target = {"machine_identity": "machine-a", "target_identity": "target-a", "daemon_identity": "daemon-a"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); env_file = root / "environment.env"
            env_file.write_text("# synthetic\n"); env_file.chmod(0o600)
            provider = {"snapshot_id": "compose-snapshot/test-profile", "snapshot_digest": digest,
                "provider_revision": "provider-v2", "target": target,
                "compose_files": (str(root / "compose.yml"),), "project_name": "widget",
                "project_directory": str(root), "environment_file": str(env_file), "render_digest": digest}
            source = {key: provider[key] for key in (
                "snapshot_id", "snapshot_digest", "provider_revision", "target", "render_digest")}
            source.update(kind="compose_observe_v2", services=("web",), topology_digest=digest,
                compose_config_hashes={"web": digest}, image_identities={"web": {
                    "image_ref": image, "config_digest": "sha256:" + "b" * 64,
                    "local_image_id": image.rsplit("@", 1)[-1]}})
            docker = root / "docker"
            def ssh_run(entry, command, **kwargs):
                return run_test_process(shlex.split(command),
                    env=synthetic_environment({"PATH": f"{root}:/usr/bin:/bin"}),
                    input=kwargs.get("input_data"), text=True, capture_output=True)
            for mode in ("unique", "ambiguous", "mismatch"):
                docker.write_text("\n".join(("#!/usr/bin/env python3", "import sys,os",
                    "a=sys.argv[1:]",
                    "if a[0]=='info': print('daemon-a'); sys.exit(0)",
                    "if a[0]=='ps': sys.exit(0)",
                    "if '--profiles' in a: print('job-orchestration\\nother'); sys.exit(0)",
                    "profile=a[a.index('--profile')+1] if '--profile' in a else None",
                    "match=" + repr(mode) + "!='mismatch' and (profile=='job-orchestration' or (" + repr(mode) + "=='ambiguous' and profile=='other'))",
                    "if match and os.environ.get('COMPOSE_PROFILES')==profile: print(" + repr(json.dumps(rendered)) + "); sys.exit(0)",
                    "print('{\"services\":{}}')")))
                docker.chmod(0o700)
                with self.subTest(mode=mode), patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run):
                    result = _host_image_argv_runner({"name": "synthetic"}, compose_snapshot_provider=provider)(
                        argv=("sandbox-activation-observe-running-v2", "widget", "web"),
                        environment={"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                        private_environment={CONFIGURATION_KEY_ENV: base64.b64encode(CONFIGURATION_KEY).decode()},
                        private_environment_source=source, redact_environment_keys=None,
                        timeout_seconds=30, max_output_bytes=4096)
                    if mode == "unique":
                        self.assertEqual(result["returncode"], 0)
                        self.assertEqual(json.loads(result["stdout"]), [])
                    else:
                        self.assertNotEqual(result["returncode"], 0)

    def test_real_private_replacement_requires_unique_digest_matching_profile(self):
        from sandbox.commands.hosting import _host_image_argv_runner

        image = "ghcr.io/acme/widget@sha256:" + "a" * 64
        rendered = {"services": {"web": {"image": image}}}
        raw = (json.dumps(rendered) + "\n").encode()
        digest = "sha256:" + hmac.new(CONFIGURATION_KEY,
            b"sandbox-hosting-private-compose-render.v2\0" + raw, hashlib.sha256).hexdigest()
        target = {"machine_identity": "machine-a", "target_identity": "target-a",
                  "daemon_identity": "daemon-a"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "environment.env"
            env_file.write_text("# synthetic\n")
            env_file.chmod(0o600)
            provider = {"snapshot_id": "compose-snapshot/test-replace-profile",
                "snapshot_digest": digest, "provider_revision": "provider-v2",
                "target": target, "compose_files": (str(root / "compose.yml"),),
                "project_name": "widget", "project_directory": str(root),
                "environment_file": str(env_file), "render_digest": digest}
            source = {key: provider[key] for key in (
                "snapshot_id", "snapshot_digest", "provider_revision", "target", "render_digest")}
            source.update(kind="compose_replace_v2", services=("web",), topology_digest=digest)
            docker = root / "docker"
            effect = root / "effect.json"

            def ssh_run(entry, command, **kwargs):
                return run_test_process(shlex.split(command),
                    env=synthetic_environment({"PATH": f"{root}:/usr/bin:/bin"}),
                    input=kwargs.get("input_data"), text=True, capture_output=True)

            for mode in ("unique", "ambiguous", "mismatch"):
                effect.unlink(missing_ok=True)
                docker.write_text("\n".join((
                    "#!/usr/bin/env python3", "import sys,os,json", "from pathlib import Path",
                    "a=sys.argv[1:]",
                    "if a[0]=='info': print('daemon-a'); sys.exit(0)",
                    "if a[0]=='inspect': print('hash-a'); sys.exit(0)",
                    "if '--hash' in a: print('web hash-a'); sys.exit(0)",
                    "if 'ps' in a: print('container-a'); sys.exit(0)",
                    "if 'up' in a:",
                    " p=Path(" + repr(str(effect)) + "); assert not p.exists()",
                    " p.write_text(json.dumps({'argv':a,'stdin':sys.stdin.read()})); sys.exit(0)",
                    "if '--profiles' in a: print('job-orchestration\\nother'); sys.exit(0)",
                    "profile=a[a.index('--profile')+1] if '--profile' in a else None",
                    "match=" + repr(mode) + "!='mismatch' and (profile=='job-orchestration' or (" + repr(mode) + "=='ambiguous' and profile=='other'))",
                    "if match and os.environ.get('COMPOSE_PROFILES')==profile: print(" + repr(json.dumps(rendered)) + "); sys.exit(0)",
                    "print('{\"services\":{}}')")))
                docker.chmod(0o700)
                argv = ("docker", "compose", "--file", "-", "--project-directory", str(root),
                        "--project-name", "widget", "up", "--detach", "--no-build",
                        "--pull", "never", "--no-deps", "web")
                with self.subTest(mode=mode), patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run):
                    result = _host_image_argv_runner({"name": "synthetic"}, compose_snapshot_provider=provider)(
                        argv=argv,
                        environment={"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                        private_environment={CONFIGURATION_KEY_ENV: base64.b64encode(CONFIGURATION_KEY).decode()},
                        private_environment_source=source, redact_environment_keys=None,
                        timeout_seconds=30, max_output_bytes=4096)
                    if mode == "unique":
                        self.assertEqual(result["returncode"], 0)
                        recorded = json.loads(effect.read_text())
                        self.assertEqual(recorded["argv"], list(argv[1:]))
                        self.assertEqual(recorded["stdin"].encode(), raw)
                    else:
                        self.assertNotEqual(result["returncode"], 0)
                        self.assertFalse(effect.exists())

    def test_private_observer_admits_only_verified_manifest_local_identity(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        from types import SimpleNamespace
        digest = "sha256:" + "a" * 64
        image = "ghcr.io/acme/widget@" + digest
        target = {"machine_identity": "machine-a", "target_identity": "target-a",
                  "daemon_identity": "daemon-a"}
        provider = {"snapshot_id": "compose-snapshot/test-v2", "snapshot_digest": digest,
            "provider_revision": "provider-v2", "target": target,
            "compose_files": ("/synthetic/compose.yml",), "project_name": "widget",
            "project_directory": "/synthetic", "environment_file": "/synthetic/environment.env",
            "render_digest": digest}
        source = {key: provider[key] for key in (
            "snapshot_id", "snapshot_digest", "provider_revision", "target", "render_digest")}
        source.update(kind="compose_observe_v2", services=("web",), topology_digest=digest,
            compose_config_hashes={"web": digest}, image_identities={"web": {
                "image_ref": image, "config_digest": "sha256:" + "b" * 64,
                "local_image_id": digest}})
        runner = _host_image_argv_runner({"name": "synthetic"}, compose_snapshot_provider=provider)
        with patch("sandbox.commands.hosting.remote.ssh_run", return_value=SimpleNamespace(
                returncode=0, stdout="[]", stderr="")) as remote_call:
            kwargs = dict(argv=("sandbox-activation-observe-running-v2", "widget", "web"),
                environment={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                private_environment={CONFIGURATION_KEY_ENV: base64.b64encode(CONFIGURATION_KEY).decode()},
                private_environment_source=source, redact_environment_keys=None,
                timeout_seconds=30, max_output_bytes=4096)
            self.assertEqual(runner(**kwargs)["returncode"], 0)
            remote_call.assert_called_once(); remote_call.reset_mock()
            for invalid in ("sha256:" + "c" * 64, "malformed"):
                source["image_identities"]["web"]["local_image_id"] = invalid
                with self.assertRaises(ValueError): runner(**kwargs)
            remote_call.assert_not_called()

    def test_real_helper_projects_absent_dependencies_as_empty(self):
        from sandbox.commands.hosting import _host_image_argv_runner

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); docker = root / "docker"
            closed = {"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
            def ssh_run(entry, command, **kwargs):
                return run_test_process(shlex.split(command),
                    env=synthetic_environment(closed), input=kwargs.get("input_data"),
                    text=True, capture_output=True)
            for service, expected in (({}, {}), ({"depends_on": None}, {}),
                    ({"depends_on": {}}, {}), ({"depends_on": {"db": {}}}, {"db": {}}),
                    ({"depends_on": []}, {"__invalid__": {}}),
                    ({"depends_on": "db"}, {"__invalid__": {}})):
                docker.write_text("\n".join(("#!/usr/bin/env python3", "import sys",
                    "if '--hash' in sys.argv: print('worker ' + 'a'*64); sys.exit(0)",
                    "print(" + repr(json.dumps({"services": {"worker": service}})) + ")")))
                docker.chmod(0o700)
                with self.subTest(service=service), patch(
                        "sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run):
                    result = _host_image_argv_runner({"name": "synthetic"})(
                        argv=("docker", "compose", "--file", "compose.yml",
                              "--project-directory", str(root), "--project-name", "synthetic", "config",
                              "--format", "json"), environment=closed,
                        private_environment={CONFIGURATION_KEY_ENV: base64.b64encode(
                            CONFIGURATION_KEY).decode()}, private_environment_source={},
                        redact_environment_keys=None, timeout_seconds=30,
                        max_output_bytes=4096)
                    self.assertEqual(result["returncode"], 0)
                    self.assertEqual(json.loads(result["stdout"])["services"]["worker"][
                        "depends_on"], expected)

    def test_real_helper_marks_every_unsnapshotted_resource_for_refusal(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        from sandbox.transports.remote_hosting_activation import (
            RegisteredRemoteActivationTransport, RemoteActivationError,
        )

        image = "ghcr.io/acme/widget@sha256:" + "a" * 64
        base = {"services": {"web": {"image": image, "build": None,
            "pull_policy": "never", "platform": "linux/amd64", "depends_on": {},
            "labels": {"org.sandbox.application-topology.v1": "topology-a"}}}}
        cases = (
            ("configs-file", {**base, "configs": {"x": {"file": "/private/file"}}}),
            ("configs-environment", {**base, "configs": {"x": {"environment": "PRIVATE"}}}),
            ("configs-external", {**base, "configs": {"x": {"external": True}}}),
            ("configs-content", {**base, "configs": {"x": {"content": "PRIVATE-CONTENT"}}}),
            ("secrets-file", {**base, "secrets": {"x": {"file": "/private/file"}}}),
            ("secrets-environment", {**base, "secrets": {"x": {"environment": "PRIVATE"}}}),
            ("secrets-external", {**base, "secrets": {"x": {"external": True}}}),
            ("secrets-content", {**base, "secrets": {"x": {"content": "PRIVATE-SECRET"}}}),
            ("network-external", {**base, "networks": {"x": {"external": True}}}),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); docker = root / "docker"
            closed = {"PATH": f"{root}:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
            for label, rendered in cases:
                docker.write_text("\n".join((
                    "#!/usr/bin/env python3",
                    "import json",
                    "print(" + repr(json.dumps(rendered)) + ")",
                )))
                docker.chmod(0o700)
                captured = []
                def ssh_run(entry, command, **kwargs):
                    result = run_test_process(
                        shlex.split(command), env=synthetic_environment(closed),
                        input=kwargs.get("input_data"), text=True, capture_output=True)
                    captured.append(result)
                    return result
                with self.subTest(label=label), \
                        patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run), \
                        patch("sandbox.transports.remote_hosting_activation.CLOSED_ENVIRONMENT",
                              closed):
                    transport = RegisteredRemoteActivationTransport(
                        argv_runner=_host_image_argv_runner({"name": "synthetic"}),
                        target_identity_observer=lambda: {
                            "machine_identity": "machine-a", "target_identity": "target-a"},
                        configuration_binding_key=CONFIGURATION_KEY)
                    with self.assertRaisesRegex(RemoteActivationError, "topology_mismatch"):
                        transport.render_topology(
                            compose_files=("/synthetic/compose.yml",), project_name="widget",
                            selected_services=("web",), image_overrides={"web": image})
                public_output = "".join(
                    (item.stdout or "") + (item.stderr or "") for item in captured)
                self.assertNotIn("PRIVATE-CONTENT", public_output)
                self.assertNotIn("PRIVATE-SECRET", public_output)

    def test_full_private_identity_changes_for_managed_network_and_alias_changes(self):
        base = {"services": {"web": {"image": "image-a", "networks": {
            "app": {"aliases": ["web"]}}}}, "networks": {"app": {
                "name": "app-a", "driver": "bridge", "ipam": {
                    "config": [{"subnet": "172.30.0.0/24"}]}}}}
        cases = []
        for mutate in (
                lambda value: value["networks"]["app"].update(name="app-b"),
                lambda value: value["networks"]["app"].update(driver="overlay"),
                lambda value: value["networks"]["app"]["ipam"]["config"][0].update(
                    subnet="172.31.0.0/24"),
                lambda value: value["services"]["web"]["networks"]["app"].update(
                    aliases=["worker"])):
            candidate = json.loads(json.dumps(base)); mutate(candidate); cases.append(candidate)
        original = private_render_identity((json.dumps(base) + "\n").encode())
        for candidate in cases:
            self.assertNotEqual(original, private_render_identity(
                (json.dumps(candidate) + "\n").encode()))

    def test_keyed_digest_is_not_an_unkeyed_low_entropy_private_value_oracle(self):
        base = {"services": {"web": {"image": "image-a", "platform": "linux/amd64",
            "environment": {"PIN": "0042"}}}}
        raw = (json.dumps(base) + "\n").encode()
        identity = private_render_identity(raw)
        guesses = []
        for pin in ("0041", "0042", "0043"):
            candidate = json.loads(json.dumps(base))
            candidate["services"]["web"]["environment"]["PIN"] = pin
            guesses.append("sha256:" + hashlib.sha256(
                (json.dumps(candidate) + "\n").encode()).hexdigest())
        self.assertNotIn(identity, guesses)

    def test_configuration_binding_key_is_required_before_remote_render(self):
        from sandbox.transports.remote_hosting_activation import (
            RegisteredRemoteActivationTransport, RemoteActivationError,
        )

        calls = []
        transport = RegisteredRemoteActivationTransport(
            argv_runner=lambda **kwargs: calls.append(kwargs),
            target_identity_observer=lambda: {
                "machine_identity": "machine-a", "target_identity": "target-a"})
        with self.assertRaisesRegex(RemoteActivationError, "topology_mismatch"):
            transport.render_topology(
                compose_files=("/synthetic/compose.yml",), project_name="widget",
                selected_services=("web",), image_overrides={
                    "web": "ghcr.io/acme/widget@sha256:" + "a" * 64})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
