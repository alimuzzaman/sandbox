"""Private container equality checks for the ordered activation helper.

Never return inspected configuration. Only the caller's bounded receipt may
cross the private helper boundary.
"""


def _graph_value(value):
    # Compose config escapes dollar signs for a subsequent Compose invocation.
    if isinstance(value, str):
        return value.replace("$$", "$")
    if isinstance(value, list):
        return [_graph_value(item) for item in value]
    return value


def _graph_environment(rows):
    if not isinstance(rows, list) or any(not isinstance(row, str) or "=" not in row for row in rows):
        raise ValueError("graph_configuration_mismatch")
    result = {}
    for row in rows:
        name, value = row.split("=", 1)
        if name in result:
            raise ValueError("graph_configuration_mismatch")
        result[name] = value
    return result


def _secret_file_mounts(*, document, service_name, material):
    """Load the private candidate-v2 archive adapter lazily.

    The graph is also emitted as a closed stdlib-only program, so keeping this
    import out of module import time preserves the candidate-v1 path and lets
    focused graph tests replace the adapter with a small fake.
    """
    from .private_secret_files import secret_file_mounts
    return secret_file_mounts(document, service_name, material)


def _prepare_secret_files(*, identity, mounts, command, inspect):
    from .private_secret_files import prepare_container_secret_files
    return prepare_container_secret_files(identity, mounts, command, inspect)


def _verify_secret_files(*, identity, mounts, command, inspect):
    from .private_secret_files import verify_container_secret_files
    return verify_container_secret_files(identity, mounts, command, inspect)


def _secret_program():
    from .private_secret_files import private_secret_program
    return private_secret_program()


def _retained_secret_material(document, directory):
    from .private_inputs import retained_secret_material
    return retained_secret_material(document, directory)


def _archive_command(command):
    """Give Docker archive reads their bounded tar budget."""
    def invoke(argv, **kwargs):
        if len(argv) >= 2 and argv[0:2] == ["docker", "cp"]:
            kwargs.setdefault("max_output_bytes", 9 * 1024 * 1024)
        return command(argv, **kwargs)
    return invoke


def _require_archive_capability(command):
    """Prove the daemon and CLI can perform the private Linux archive path."""
    server_os = command(["docker", "version", "--format", "{{.Server.Os}}"]).strip()
    if server_os != b"linux":
        raise ValueError("graph_archive_unavailable")
    help_text = command(["docker", "cp", "--help"], max_output_bytes=64 * 1024)
    if b"docker cp" not in help_text and b"Usage" not in help_text:
        raise ValueError("graph_archive_unavailable")


def _validate_generated_secret_sources(document, service_name):
    import re
    service = document["services"][service_name]
    sources = document.get("secrets")
    if not isinstance(sources, dict):
        raise ValueError("graph_configuration_mismatch")
    indexes = []
    for source in sources.values():
        if (not isinstance(source, dict)
                or set(source) - {"environment", "name"}
                or not isinstance(source.get("environment"), str)):
            raise ValueError("graph_configuration_mismatch")
        match = re.fullmatch(r"SANDBOX_ACTIVATION_SECRET_([0-9]+)", source["environment"])
        if match is None:
            raise ValueError("graph_configuration_mismatch")
        indexes.append(int(match.group(1)))
    if sorted(indexes) != list(range(len(indexes))):
        raise ValueError("graph_configuration_mismatch")
    for row in service.get("secrets") or []:
        if not isinstance(row, dict):
            raise ValueError("graph_configuration_mismatch")
        source = sources.get(row.get("source"))
        if (not isinstance(source, dict)
                or set(source) - {"environment", "name"}
                or not isinstance(source.get("environment"), str)
                or re.fullmatch(r"SANDBOX_ACTIVATION_SECRET_[0-9]+", source["environment"]) is None):
            raise ValueError("graph_configuration_mismatch")


def validate_init_container(*, document, declaration, project, owner, container,
                            image, expected_hash, container_name,
                            input_contract="candidate-v1"):
    service = document["services"][declaration["service"]]
    cfg = container.get("Config") or {}
    labels = cfg.get("Labels") or {}
    if (labels.get("org.sandbox.init-owner.v2") != owner
            or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != declaration["service"]
            or labels.get("com.docker.compose.config-hash") != expected_hash
            or container.get("Name") != "/" + container_name
            or cfg.get("Image") != declaration["image_ref"]
            or container.get("RestartCount") != 0):
        raise ValueError("graph_configuration_mismatch")
    local_id = image.get("Id")
    if (container.get("Image") != local_id
            or local_id not in {declaration["config_digest"], declaration["image_ref"],
                                declaration["image_ref"].rsplit("@", 1)[-1]}
            or image.get("Os") != "linux" or image.get("Architecture") != "amd64"
            or image.get("Variant") or not isinstance(image.get("RepoDigests"), list)
            or image["RepoDigests"].count(declaration["image_ref"]) != 1):
        raise ValueError("graph_configuration_mismatch")
    defaults = image.get("Config") or {}
    for source, destination, default in (("command", "Cmd", None), ("entrypoint", "Entrypoint", None),
                                       ("user", "User", ""), ("working_dir", "WorkingDir", "")):
        expected = service.get(source)
        if expected is None:
            expected = defaults.get(destination, default)
        else:
            expected = _graph_value(expected)
        if source in {"command", "entrypoint"} and expected is not None:
            if not isinstance(expected, list) or any(not isinstance(item, str) for item in expected):
                raise ValueError("graph_configuration_mismatch")
        if cfg.get(destination, default) != expected:
            raise ValueError("graph_configuration_mismatch")
    environment = _graph_environment(defaults.get("Env") or [])
    declared_environment = service.get("environment") or {}
    if not isinstance(declared_environment, dict):
        raise ValueError("graph_configuration_mismatch")
    for name, value in declared_environment.items():
        if value is None:
            environment.pop(name, None)
        elif isinstance(value, str):
            environment[name] = _graph_value(value)
        else:
            raise ValueError("graph_configuration_mismatch")
    if _graph_environment(cfg.get("Env") or []) != environment:
        raise ValueError("graph_configuration_mismatch")
    host = container.get("HostConfig") or {}
    for source, destination in (("privileged", "Privileged"), ("read_only", "ReadonlyRootfs"), ("init", "Init")):
        expected = service.get(source, False)
        if type(expected) is not bool or bool(host.get(destination)) != expected:
            raise ValueError("graph_configuration_mismatch")
    for source, destination in (("cap_add", "CapAdd"), ("cap_drop", "CapDrop"), ("security_opt", "SecurityOpt")):
        if sorted(host.get(destination) or []) != sorted(service.get(source) or []):
            raise ValueError("graph_configuration_mismatch")
    restart = host.get("RestartPolicy") or {}
    if service.get("restart", "no") != "no" or restart.get("Name", "no") != "no" or restart.get("MaximumRetryCount", 0) != 0:
        raise ValueError("graph_configuration_mismatch")
    if input_contract not in {"candidate-v1", "candidate-v2"}:
        raise ValueError("graph_configuration_mismatch")
    expected_mounts = []
    for mount in service.get("volumes") or []:
        if not isinstance(mount, dict) or mount.get("type") != "volume" or not mount.get("source"):
            raise ValueError("graph_configuration_mismatch")
        volume = (document.get("volumes") or {}).get(mount["source"])
        if not isinstance(volume, dict) or volume.get("external") not in (None, False):
            raise ValueError("graph_configuration_mismatch")
        name = volume.get("name") or project + "_" + mount["source"]
        expected_mounts.append(("volume", name, mount["target"], not bool(mount.get("read_only"))))
    if input_contract == "candidate-v1":
        for secret in service.get("secrets") or []:
            if not isinstance(secret, dict):
                raise ValueError("graph_configuration_mismatch")
            source = (document.get("secrets") or {}).get(secret.get("source"))
            if not isinstance(source, dict) or not isinstance(source.get("file"), str):
                raise ValueError("graph_configuration_mismatch")
            target = secret.get("target") or secret["source"]
            target = target if target.startswith("/") else "/run/secrets/" + target
            expected_mounts.append(("bind", source["file"], target, False))
    else:
        _validate_generated_secret_sources(document, declaration["service"])
    actual_mounts = [(row.get("Type"), row.get("Name") if row.get("Type") == "volume" else row.get("Source"),
                      row.get("Destination"), row.get("RW")) for row in container.get("Mounts") or []]
    if sorted(actual_mounts) != sorted(expected_mounts):
        raise ValueError("graph_configuration_mismatch")
    networks = service.get("networks") or {}
    if not isinstance(networks, dict):
        raise ValueError("graph_configuration_mismatch")
    expected_networks = set()
    for name in networks:
        network = (document.get("networks") or {}).get(name)
        if not isinstance(network, dict) or network.get("external") not in (None, False):
            raise ValueError("graph_configuration_mismatch")
        expected_networks.add(network.get("name") or project + "_" + name)
    actual_networks = (container.get("NetworkSettings") or {}).get("Networks") or {}
    if set(actual_networks) != expected_networks:
        raise ValueError("graph_configuration_mismatch")


def run_init_action(*, action, container_identity, inspect, command,
                    secret_mounts=None, prepare_secret_files=None,
                    verify_secret_files=None, **context):
    """Operate on one proven ID. The caller persists exit before requesting rm."""
    import re
    if (action not in {"inspect", "start", "wait", "cleanup"}
            or type(container_identity) is not str
            or re.fullmatch(r"[0-9a-f]{64}", container_identity) is None):
        raise ValueError("graph_action_refused")

    def prove():
        container = inspect(container_identity)
        if not isinstance(container, dict) or container.get("Id") != container_identity:
            raise ValueError("graph_configuration_mismatch")
        validate_init_container(container=container, **context)
        state = container.get("State")
        if not isinstance(state, dict) or state.get("Paused") or state.get("Restarting") or state.get("Dead"):
            raise ValueError("graph_state_unproven")
        return state

    state = prove()
    secret_command = _archive_command(command)
    if action == "inspect" and secret_mounts is not None and prepare_secret_files is not None:
        prepare_secret_files(identity=container_identity, mounts=secret_mounts,
                             command=secret_command, inspect=inspect)
    if secret_mounts is not None and verify_secret_files is not None:
        # Re-prove the archive immediately before every later graph action.
        # The helper must inspect the same exact container ID and emits only a
        # fixed refusal on any changed file, identity, or state.
        verify_secret_files(identity=container_identity, mounts=secret_mounts,
                            command=secret_command, inspect=inspect)
    status = state.get("Status")
    if action in {"inspect", "start"}:
        if status != "created" or state.get("Running") is not False:
            raise ValueError("graph_state_unproven")
        if action == "start":
            command(["docker", "start", container_identity])
        return None
    if action == "cleanup":
        if (status != "exited" or state.get("Running") is not False
                or type(state.get("ExitCode")) is not int or not 0 <= state["ExitCode"] <= 255):
            raise ValueError("graph_state_unproven")
        command(["docker", "rm", container_identity])
        return None
    if status not in {"running", "exited"}:
        raise ValueError("graph_state_unproven")
    raw = command(["docker", "wait", container_identity])
    if type(raw) is not bytes or re.fullmatch(rb"[0-9]{1,3}\n?", raw) is None:
        raise ValueError("graph_exit_unproven")
    exit_code = int(raw)
    after = prove()
    if (exit_code > 255 or after.get("Status") != "exited" or after.get("Running") is not False
            or type(after.get("ExitCode")) is not int or after["ExitCode"] != exit_code):
        raise ValueError("graph_exit_unproven")
    return exit_code


def init_compose_document(*, document, declaration, owner, container_name):
    import json
    selected = json.loads(json.dumps(document))
    service = selected["services"][declaration["service"]]
    if (service.get("image") != declaration["image_ref"] or service.get("build") is not None
            or service.get("pull_policy") not in (None, "never")
            or service.get("restart", "no") != "no" or service.get("ports")
            or service.get("deploy") or service.get("scale") not in (None, 1)):
        raise ValueError("graph_configuration_mismatch")
    service.pop("depends_on", None)
    service.pop("profiles", None)
    service["container_name"] = container_name
    labels = service.get("labels") or {}
    if not isinstance(labels, dict) or "org.sandbox.init-owner.v2" in labels:
        raise ValueError("graph_configuration_mismatch")
    service["labels"] = {**labels, "org.sandbox.init-owner.v2": owner}
    selected["services"] = {declaration["service"]: service}
    return selected


def create_init_container(*, document, declaration, project, project_directory, owner,
                          container_name, image, command, find, inspect,
                          input_contract="candidate-v1", secret_mounts=None,
                          prepare_secret_files=None):
    import json
    import re
    # The name lookup must distinguish absence from observation failure. A
    # collision is never reused or removed, even when its labels look familiar.
    if find(container_name) != []:
        raise ValueError("graph_container_collision")
    selected = init_compose_document(document=document, declaration=declaration,
                                    owner=owner, container_name=container_name)
    raw = json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
    base = ["docker", "compose", "--file", "-", "--project-directory", project_directory,
            "--project-name", project]
    service = declaration["service"]
    output = command(base + ["config", "--hash", service], input=raw)
    if type(output) is not bytes:
        raise ValueError("graph_configuration_mismatch")
    parts = output.split()
    if len(parts) != 2 or parts[0] != service.encode() or re.fullmatch(rb"[0-9a-f]{64}", parts[1]) is None:
        raise ValueError("graph_configuration_mismatch")
    expected_hash = parts[1].decode()
    command(base + ["create", "--no-build", "--pull", "never", "--no-recreate", service], input=raw)
    identities = find(container_name)
    if (not isinstance(identities, list) or len(identities) != 1
            or type(identities[0]) is not str or re.fullmatch(r"[0-9a-f]{64}", identities[0]) is None):
        raise ValueError("graph_container_unproven")
    container = inspect(identities[0])
    if container.get("Id") != identities[0]:
        raise ValueError("graph_container_unproven")
    validate_init_container(document=document, declaration=declaration, project=project, owner=owner,
        container=container, image=image, expected_hash=expected_hash, container_name=container_name,
        input_contract=input_contract)
    state = container.get("State") or {}
    if state.get("Status") != "created" or state.get("Running") is not False:
        raise ValueError("graph_state_unproven")
    return identities[0]


def graph_command_port(environment, timeout_seconds):
    import os
    import subprocess
    import selectors
    import time
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ValueError("graph_deadline_exceeded")
    deadline = time.monotonic() + timeout_seconds

    def command(argv, *, input=None, max_output_bytes=1048576):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("graph_deadline_exceeded")
        if type(max_output_bytes) is not int or not 0 < max_output_bytes <= 16 * 1024 * 1024:
            raise ValueError("graph_command_unproven")
        try:
            process = subprocess.Popen(argv, env=environment,
                stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (OSError, ValueError):
            raise ValueError("graph_command_unproven") from None
        if input is not None and type(input) is not bytes:
            process.kill()
            process.wait()
            raise ValueError("graph_command_unproven")
        selector = selectors.DefaultSelector()
        output = {process.stdout: bytearray(), process.stderr: bytearray()}
        for stream in output:
            selector.register(stream, selectors.EVENT_READ)
        input_stream = process.stdin if input is not None else None
        input_offset = 0
        if input_stream is not None:
            os.set_blocking(input_stream.fileno(), False)
            if input:
                selector.register(input_stream, selectors.EVENT_WRITE)
            else:
                input_stream.close()
                input_stream = None
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise ValueError("graph_deadline_exceeded")
                events = selector.select(remaining)
                if not events:
                    process.kill()
                    process.wait()
                    raise ValueError("graph_deadline_exceeded")
                for key, mask in events:
                    if input_stream is not None and key.fileobj is input_stream:
                        try:
                            if mask & selectors.EVENT_WRITE:
                                written = os.write(input_stream.fileno(), input[input_offset:input_offset + 65536])
                                input_offset += written
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            try:
                                process.kill()
                            except OSError:
                                pass
                            process.wait()
                            raise ValueError("graph_command_unproven") from None
                        except OSError:
                            process.kill()
                            process.wait()
                            raise ValueError("graph_command_unproven") from None
                        if input_offset >= len(input):
                            selector.unregister(input_stream)
                            input_stream.close()
                            input_stream = None
                        continue
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    output[key.fileobj].extend(data)
                    if sum(len(value) for value in output.values()) > max_output_bytes:
                        process.kill()
                        process.wait()
                        raise ValueError("graph_command_oversized")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                raise ValueError("graph_deadline_exceeded")
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise ValueError("graph_deadline_exceeded") from None
        finally:
            selector.close()
            if input_stream is not None and not input_stream.closed:
                input_stream.close()
            for stream in output:
                if not stream.closed:
                    stream.close()
        stdout = bytes(output[process.stdout])
        stderr = bytes(output[process.stderr])
        returncode = process.returncode
        effect = (argv[1] in {"start", "rm"}
                  or (argv[1] == "cp" and input is not None)
                  or (argv[1] == "compose" and ("create" in argv or "up" in argv)))
        if (returncode != 0 or (stderr and not effect)):
            raise ValueError("graph_command_unproven")
        return stdout

    return command, deadline


def execute_private_graph(*, source, document, environment, configuration_key, timeout_seconds):
    import hashlib
    import hmac
    import json
    import re
    import subprocess
    import time
    subject = source["subject"]
    input_contract = source.get("input_contract", "candidate-v1")
    if input_contract not in {"candidate-v1", "candidate-v2"}:
        raise ValueError("graph_action_refused")
    if subject["kind"] in {"prerequisite", "consumer"}:
        return execute_private_runtime(source=source, document=document, environment=environment,
                                       timeout_seconds=timeout_seconds,
                                       input_contract=input_contract)
    if subject["kind"] != "initializer":
        raise ValueError("graph_action_refused")
    declarations = source["execution_contract"]["declarations"]
    selected = [row for row in declarations if row["service"] == subject["services"][0]]
    if len(selected) != 1:
        raise ValueError("graph_configuration_mismatch")
    declaration = selected[0]
    service = document["services"][declaration["service"]]
    if (declaration["configuration_digest"] != source["render_digest"]
            or service.get("image") != declaration["image_ref"]
            or sorted(service.get("environment") or {}) != declaration["environment_keys"]
            or type(timeout_seconds) is not int or timeout_seconds != declaration["timeout_seconds"]):
        raise ValueError("graph_configuration_mismatch")
    command, deadline = graph_command_port(environment, timeout_seconds)
    secret_command = _archive_command(command)

    secret_mounts = None
    if input_contract == "candidate-v2":
        material = _retained_secret_material(document, source["project_directory"])
        secret_mounts = _secret_file_mounts(
            document=document, service_name=declaration["service"], material=material)

    def inspect(identity):
        rows = json.loads(command(["docker", "inspect", identity]))
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("graph_container_unproven")
        return rows[0]

    def find(name):
        raw = command(["docker", "ps", "--all", "--no-trunc", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"])
        ids = raw.decode().split()
        if len(ids) > 1 or any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in ids):
            raise ValueError("graph_container_unproven")
        return ids

    rows = json.loads(command(["docker", "image", "inspect", declaration["image_ref"]]))
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("graph_image_unproven")
    image = rows[0]
    if (image.get("Id") not in {declaration["config_digest"], declaration["image_ref"],
                               declaration["image_ref"].rsplit("@", 1)[-1]}
            or image.get("Os") != "linux" or image.get("Architecture") != "amd64"
            or image.get("Variant") or not isinstance(image.get("RepoDigests"), list)
            or image["RepoDigests"].count(declaration["image_ref"]) != 1):
        raise ValueError("graph_image_unproven")
    owner = hmac.new(configuration_key, b"sandbox-init-owner.v2\0" + subject["subject_digest"].encode(), hashlib.sha256).hexdigest()
    name = "sandbox-init-" + owner[:32]
    context = {"document": document, "declaration": declaration, "project": source["project_name"],
               "owner": owner, "container_name": name, "image": image}
    action = source["action"]
    if input_contract == "candidate-v2" and action in {"create", "inspect"}:
        _require_archive_capability(command)
    if action == "create":
        identity = create_init_container(**context, project_directory=source["project_directory"],
                                         command=command, find=find, inspect=inspect,
                                         input_contract=input_contract,
                                         secret_mounts=secret_mounts)
        exit_code = None
    else:
        selected = init_compose_document(document=document, declaration=declaration, owner=owner, container_name=name)
        raw = json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
        output = command(["docker", "compose", "--file", "-", "--project-directory", source["project_directory"],
                          "--project-name", source["project_name"], "config", "--hash", declaration["service"]], input=raw)
        parts = output.split()
        if len(parts) != 2 or parts[0] != declaration["service"].encode() or re.fullmatch(rb"[0-9a-f]{64}", parts[1]) is None:
            raise ValueError("graph_configuration_mismatch")
        identity = source["container_identity"]
        exit_code = run_init_action(action=action, container_identity=identity, inspect=inspect,
                                   command=command, expected_hash=parts[1].decode(),
                                   input_contract=input_contract, secret_mounts=secret_mounts,
                                   prepare_secret_files=_prepare_secret_files,
                                   verify_secret_files=_verify_secret_files, **context)
    return {"subject_digest": subject["subject_digest"], "container_identity": identity,
            "exit_code": exit_code, "terminated": True}


def private_graph_program():
    import inspect
    # The graph is evaluated in a closed private subprocess. Include the
    # archive helper and retained-material reader as source rather than
    # importing the repository package from that process.
    try:
        helper_source = _secret_program()
    except ModuleNotFoundError:
        # Candidate-v1 tests and deployments predate the archive helper. The
        # v2 branch remains unavailable until the registered helper is present.
        helper_source = ""
    # The host runner may be Python 3.9 while repository code is authored on a
    # newer interpreter. Keep emitted helper annotations runtime-neutral; the
    # private program is validated structurally before it runs.
    helper_source = (helper_source.replace("tuple[dict | None, list[dict]] | None", "tuple")
                     .replace("tuple[dict | None, list[dict]]", "tuple"))
    from .private_inputs import retained_secret_program
    material_source = retained_secret_program()
    functions = "\n\n".join(inspect.getsource(function) for function in (
        _graph_value, _graph_environment, _archive_command, _require_archive_capability,
        _validate_generated_secret_sources,
        validate_init_container, run_init_action, init_compose_document,
        create_init_container, graph_command_port, execute_private_runtime,
        execute_private_graph))
    aliases = ("\n_retained_secret_material=retained_secret_material\n"
               "_secret_file_mounts=lambda *,document,service_name,material: secret_file_mounts(document,service_name,material)\n"
               "_prepare_secret_files=lambda *,identity,mounts,command,inspect: prepare_container_secret_files(identity,mounts,command,inspect)\n"
               "_verify_secret_files=lambda *,identity,mounts,command,inspect: verify_container_secret_files(identity,mounts,command,inspect)\n")
    return ("import base64,hashlib,hmac,json,os,re,stat,subprocess,time\n"
            + material_source + "\n" + helper_source + aliases + functions)


def execute_private_runtime(*, source, document, environment, timeout_seconds,
                            input_contract="candidate-v1"):
    import json
    import re
    import time
    subject = source["subject"]
    services = subject["services"]
    if (input_contract not in {"candidate-v1", "candidate-v2"}
            or source["action"] not in {"replace", "ready"} or source["container_identity"] is not None
            or timeout_seconds != source["execution_contract"]["graph"]["readiness_timeout_seconds"]):
        raise ValueError("graph_action_refused")
    command, deadline = graph_command_port(environment, timeout_seconds)
    secret_command = _archive_command(command)
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    base = ["docker", "compose", "--file", "-", "--project-directory", source["project_directory"],
            "--project-name", source["project_name"]]
    hashes = {}
    health_required = {}
    secret_mounts = {}
    if input_contract == "candidate-v2":
        material = _retained_secret_material(document, source["project_directory"])
        for name in services:
            secret_mounts[name] = _secret_file_mounts(
                document=document, service_name=name, material=material)
    for name in services:
        identity = source["image_identities"][name]
        service = document["services"][name]
        if (service.get("image") != identity["image_ref"] or service.get("build") is not None
                or service.get("pull_policy") not in (None, "never")):
            raise ValueError("graph_image_unproven")
        images = json.loads(command(["docker", "image", "inspect", identity["image_ref"]]))
        if not isinstance(images, list) or len(images) != 1:
            raise ValueError("graph_image_unproven")
        image = images[0]
        if (image.get("Id") != identity["local_image_id"]
                or image.get("Id") not in {identity["config_digest"], identity["image_ref"],
                                          identity["image_ref"].rsplit("@", 1)[-1]}
                or image.get("Os") != "linux" or image.get("Architecture") != "amd64" or image.get("Variant")
                or not isinstance(image.get("RepoDigests"), list) or image["RepoDigests"].count(identity["image_ref"]) != 1):
            raise ValueError("graph_image_unproven")
        healthcheck = service.get("healthcheck")
        if healthcheck is None:
            healthcheck = (image.get("Config") or {}).get("Healthcheck") or {}
        enabled = bool(healthcheck) and not healthcheck.get("disable") and healthcheck.get("test", healthcheck.get("Test")) != ["NONE"]
        dependency_requires_health = any(edge["dependency"] == name and edge["condition"] == "service_healthy"
            for edge in source["execution_contract"]["graph"].get("dependencies", []))
        if dependency_requires_health and not enabled:
            raise ValueError("graph_healthcheck_unavailable")
        health_required[name] = enabled
        parts = command(base + ["config", "--hash", name], input=raw).split()
        if len(parts) != 2 or parts[0] != name.encode() or re.fullmatch(rb"[0-9a-f]{64}", parts[1]) is None:
            raise ValueError("graph_configuration_mismatch")
        hashes[name] = parts[1].decode()
    def validate_container(name, identity, container, *, require_created=False):
        cfg = container.get("Config") or {}
        labels = cfg.get("Labels") or {}
        if (container.get("Id") != identity or container.get("Image") != source["image_identities"][name]["local_image_id"]
                or cfg.get("Image") != source["image_identities"][name]["image_ref"]
                or labels.get("com.docker.compose.project") != source["project_name"]
                or labels.get("com.docker.compose.service") != name
                or labels.get("com.docker.compose.config-hash") != hashes[name]):
            raise ValueError("graph_runtime_mismatch")
        state = container.get("State") or {}
        if (state.get("Status") not in ({"created"} if require_created else {"created", "running"})
                or state.get("Paused") or state.get("Restarting") or state.get("Dead")
                or (require_created and state.get("Running") is not False)):
            raise ValueError("graph_runtime_unproven")

    def inspect_runtime(name, identity, *, require_created=False):
        rows = json.loads(command(["docker", "inspect", identity]))
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("graph_runtime_unproven")
        container = rows[0]
        validate_container(name, identity, container, require_created=require_created)
        return container

    if source["action"] == "replace" and input_contract == "candidate-v1":
        command(base + ["up", "--detach", "--no-build", "--pull", "never", "--no-deps", *services], input=raw)
    elif source["action"] == "replace":
        # Candidate-v2 never delegates start or dependency selection to
        # Compose. Create the exact services, prove each stopped container,
        # prepare/read back every private file, then start only those IDs.
        _require_archive_capability(command)
        command(base + ["create", "--no-build", "--pull", "never", "--no-deps", *services], input=raw)
        selected = {}
        for name in services:
            ids = command(base + ["ps", "--all", "--quiet", name], input=raw).decode().split()
            if len(ids) != 1 or re.fullmatch(r"[0-9a-f]{64}", ids[0]) is None:
                raise ValueError("graph_runtime_unproven")
            identity = ids[0]
            inspect_runtime(name, identity, require_created=True)
            selected[name] = identity
        for name in services:
            identity = selected[name]
            _prepare_secret_files(identity=identity, mounts=secret_mounts[name],
                                 command=secret_command, inspect=lambda value, n=name: inspect_runtime(n, value, require_created=True))
            _verify_secret_files(identity=identity, mounts=secret_mounts[name],
                                 command=secret_command, inspect=lambda value, n=name: inspect_runtime(n, value, require_created=True))
        for name in services:
            command(["docker", "start", selected[name]])
    else:
        seen = {}
        while True:
            ready = True
            for name in services:
                ids = command(base + ["ps", "--all", "--quiet", name], input=raw).decode().split()
                if not ids:
                    if name in seen:
                        raise ValueError("graph_runtime_changed")
                    ready = False
                    continue
                if len(ids) != 1 or re.fullmatch(r"[0-9a-f]{64}", ids[0]) is None:
                    raise ValueError("graph_runtime_unproven")
                if name in seen and seen[name] != ids[0]:
                    raise ValueError("graph_runtime_changed")
                seen[name] = ids[0]
                rows = json.loads(command(["docker", "inspect", ids[0]]))
                if not isinstance(rows, list) or len(rows) != 1:
                    raise ValueError("graph_runtime_unproven")
                container = rows[0]
                validate_container(name, ids[0], container)
                state = container.get("State") or {}
                if input_contract == "candidate-v2":
                    _verify_secret_files(identity=ids[0], mounts=secret_mounts[name],
                                         command=secret_command, inspect=lambda value, n=name: inspect_runtime(n, value))
                healthy = state.get("Running") is True
                if health_required[name]:
                    healthy = healthy and (state.get("Health") or {}).get("Status") == "healthy"
                ready = ready and healthy
            if ready:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("graph_deadline_exceeded")
            time.sleep(min(0.5, remaining))
    return {"subject_digest": subject["subject_digest"], "container_identity": None,
            "exit_code": None, "terminated": True}
