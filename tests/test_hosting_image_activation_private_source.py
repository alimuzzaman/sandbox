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
            docker = root / "docker"
            rendered = {"services": {"web": {"image": image, "build": None,
                "pull_policy": "never", "platform": "linux/amd64", "depends_on": {},
                "environment": {"DATABASE_URL": canary}}}}
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
                "provider_revision": "provider-v2", "target": target,
                "compose_files": ("/synthetic/compose.yml",), "project_name": "widget",
                "project_directory": "/synthetic", "environment_file": str(environment_file)}
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
                    snapshot_id=provider["snapshot_id"], provider_revision="provider-v2")
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn(canary, "".join(
            (item.stdout or "") + (item.stderr or "") for item in results))

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
