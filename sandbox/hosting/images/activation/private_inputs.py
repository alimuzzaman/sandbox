"""Private preparation primitives. Values here must never enter public state.

Source is captured locally from a clean exact Git revision. The resulting frame
travels on private stdin; a remote mutable application checkout is not authority.
"""

from __future__ import annotations

import base64
import copy
import ctypes
import fcntl
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid


MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")


def _directory_fd(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("candidate_refused")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _owned_directory(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("candidate_refused")


def _publish_at(parent: int, temporary: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        call, flag = libc.renameat2, 1
    elif sys.platform == "darwin":
        call, flag = libc.renameatx_np, 4
    else:
        raise ValueError("candidate_refused")
    call.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    call.restype = ctypes.c_int
    if call(parent, os.fsencode(temporary), parent, os.fsencode(destination), flag) != 0:
        raise OSError(ctypes.get_errno(), "candidate_refused")


def publish_candidate(runtime: Path, candidate_id: str, content: dict[str, bytes]) -> str:
    """Publish using pinned directory descriptors and no-replace atomic rename.

    Replays verify every owner-only byte and sync the parent again, including
    when an earlier process died after rename but before its acknowledgement.
    """
    runtime_fd = parent_fd = candidate_fd = None
    temporary = None
    written = []
    try:
        if (type(candidate_id) is not str or re.fullmatch(r"[0-9a-f]{64}", candidate_id) is None
                or type(content) is not dict or not 1 <= len(content) <= 128
                or any(type(name) is not str or _NAME.fullmatch(name) is None
                       or type(value) is not bytes or len(value) > MAX_FILE
                       for name, value in content.items())
                or sum(map(len, content.values())) > MAX_TOTAL):
            raise ValueError()
        runtime_fd = _directory_fd(Path(runtime))
        _owned_directory(runtime_fd)
        try:
            os.mkdir("activation-inputs", 0o700, dir_fd=runtime_fd)
            os.fsync(runtime_fd)
        except FileExistsError:
            pass
        parent_fd = os.open("activation-inputs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=runtime_fd)
        _owned_directory(parent_fd)
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError()
                time.sleep(0.02)
        try:
            candidate_fd = os.open(candidate_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=parent_fd)
        except FileNotFoundError:
            candidate_fd = None
        if candidate_fd is not None:
            _owned_directory(candidate_fd)
            if set(os.listdir(candidate_fd)) != set(content):
                raise ValueError()
            for name, value in content.items():
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=candidate_fd)
                try:
                    before = os.fstat(descriptor)
                    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                            or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) & 0o077
                            or before.st_size != len(value)):
                        raise ValueError()
                    data = bytearray()
                    while len(data) <= len(value):
                        block = os.read(descriptor, min(65536, len(value) + 1 - len(data)))
                        if not block:
                            break
                        data.extend(block)
                    after = os.fstat(descriptor)
                    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
                              "st_mode", "st_uid", "st_nlink")
                    if bytes(data) != value or any(getattr(before, k) != getattr(after, k) for k in fields):
                        raise ValueError()
                finally:
                    os.close(descriptor)
            os.fsync(candidate_fd)
            os.fsync(parent_fd)
            return "replayed"
        temporary = ".candidate-" + uuid.uuid4().hex
        os.mkdir(temporary, 0o700, dir_fd=parent_fd)
        candidate_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        _owned_directory(candidate_fd)
        for name, value in content.items():
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=candidate_fd)
            written.append(name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
        os.fsync(candidate_fd)
        _publish_at(parent_fd, temporary, candidate_id)
        temporary = None
        os.fsync(parent_fd)
        return "prepared"
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError("candidate_refused") from None
    finally:
        try:
            if temporary is not None and candidate_fd is not None:
                try:
                    named = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    named = None
                held = os.fstat(candidate_fd)
                # A lost rename acknowledgement leaves this descriptor pointing
                # at the published candidate. Never clean through it in that case.
                if named is not None and (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino):
                    for name in written:
                        os.unlink(name, dir_fd=candidate_fd)
                    os.rmdir(temporary, dir_fd=parent_fd)
        finally:
            for descriptor in (candidate_fd, parent_fd, runtime_fd):
                if descriptor is not None:
                    os.close(descriptor)


def _read_exact_file(root: Path, name: str) -> bytes:
    """Walk with descriptors so replacing an ancestor cannot redirect a read."""
    path = root / name
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("source_refused")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE:
                raise ValueError("source_refused")
            data = bytearray()
            while len(data) <= MAX_FILE:
                block = os.read(file_fd, min(65536, MAX_FILE + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(file_fd)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
                      "st_mode", "st_uid", "st_nlink")
            if len(data) > MAX_FILE or any(getattr(before, k) != getattr(after, k) for k in fields):
                raise ValueError("source_refused")
            return bytes(data)
        finally:
            os.close(file_fd)
    finally:
        os.close(descriptor)


def capture_source(root: Path, revision: str, compose_files: tuple[str, ...],
                   manifest: str) -> dict[str, str]:
    """Return bounded base64 tracked bytes; errors never contain file contents."""
    try:
        root = Path(root)
        if not root.is_absolute() or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError()
        if type(compose_files) is not tuple or not 1 <= len(compose_files) <= 32:
            raise ValueError()
        names = (*compose_files, manifest)
        if len(set(names)) != len(names) or any(type(name) is not str or not name
                or Path(name).is_absolute() or ".." in Path(name).parts
                or any(c in name for c in "\0\n\r:") for name in names):
            raise ValueError()

        def git(*args: str) -> bytes:
            result = subprocess.run(("git", "-C", str(root), *args), env=_ENV,
                capture_output=True, timeout=15, check=True)
            if len(result.stdout) > MAX_FILE:
                raise ValueError()
            return result.stdout

        def clean() -> None:
            if (git("rev-parse", "HEAD").strip() != revision.encode()
                    or git("status", "--porcelain", "--untracked-files=no")):
                raise ValueError()

        clean()
        captured = {}
        total = 0
        for name in names:
            identity = revision + ":" + name
            if not 0 <= int(git("cat-file", "-s", identity)) <= MAX_FILE:
                raise ValueError()
            data = git("cat-file", "blob", identity)
            if _read_exact_file(root, name) != data:
                raise ValueError()
            total += len(data)
            if total > MAX_TOTAL:
                raise ValueError()
            captured[name] = base64.b64encode(data).decode("ascii")
        clean()
        return captured
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise ValueError("source_refused") from None


def materialize_secrets(compose: dict, environment: dict[str, str], candidate: str,
                        key: bytes) -> tuple[dict, dict[str, bytes], str]:
    """Transform only captured environment-backed secrets into private files.

    The caller atomically publishes the returned files with this effective render.
    No filesystem mutation occurs here. The HMAC includes both secret bytes and
    the complete effective render, including service-to-secret mount mappings.
    """
    try:
        if (type(compose) is not dict or type(environment) is not dict
                or type(key) is not bytes or len(key) != 32
                or type(candidate) is not str or not candidate.startswith("/")
                or ".." in Path(candidate).parts or "\0" in candidate):
            raise ValueError()
        if any(compose.get(name) for name in ("configs", "include", "extends")):
            raise ValueError()
        services = compose.get("services")
        networks = compose.get("networks", {})
        volumes = compose.get("volumes", {})
        sources = compose.get("secrets", {})
        if (type(services) is not dict or not 1 <= len(services) <= 80
                or type(sources) is not dict or len(sources) > 64
                or type(networks) is not dict or type(volumes) is not dict):
            raise ValueError()
        if any(type(row) is not dict or row.get("external") not in (None, False)
               for row in networks.values()):
            raise ValueError()
        used = set()
        for name, service in services.items():
            if type(name) is not str or _NAME.fullmatch(name) is None or type(service) is not dict:
                raise ValueError()
            if service.get("configs") or service.get("extends") or service.get("env_file"):
                raise ValueError()
            mounts = service.get("volumes", [])
            if type(mounts) is not list or any(type(row) is not dict or row.get("type") != "volume"
                    or row.get("source") not in volumes for row in mounts):
                raise ValueError()
            secrets = service.get("secrets", [])
            if type(secrets) is not list:
                raise ValueError()
            targets = set()
            for mount in secrets:
                if (type(mount) is not dict or set(mount) - {"source", "target", "uid", "gid", "mode"}
                        or type(mount.get("source")) is not str or mount["source"] not in sources):
                    raise ValueError()
                target = mount.get("target", mount["source"])
                if type(target) is not str or _NAME.fullmatch(target) is None or target in targets:
                    raise ValueError()
                targets.add(target)
                used.add(mount["source"])
        if used != set(sources):
            raise ValueError()
        rendered = copy.deepcopy(compose)
        files = {}
        bound = {}
        for index, name in enumerate(sorted(sources)):
            source = sources[name]
            if (type(name) is not str or _NAME.fullmatch(name) is None
                    or type(source) is not dict or set(source) not in ({"environment"}, {"environment", "name"})
                    or ("name" in source and (type(source["name"]) is not str or _NAME.fullmatch(source["name"]) is None))
                    or type(source["environment"]) is not str
                    or _VARIABLE.fullmatch(source["environment"]) is None):
                raise ValueError()
            value = environment.get(source["environment"])
            if type(value) is not str or "\0" in value:
                raise ValueError()
            raw = value.encode("utf-8")
            if len(raw) > 1024 * 1024:
                raise ValueError()
            filename = f"secret-{index}"
            files[filename] = raw
            rendered["secrets"][name] = {"file": candidate + "/" + filename,
                **({"name": source["name"]} if "name" in source else {})}
            bound[name] = base64.b64encode(raw).decode("ascii")
        if sum(map(len, files.values())) > MAX_TOTAL:
            raise ValueError()
        subject = json.dumps({"render": rendered, "material": bound},
                             sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        binding = "sha256:" + hmac.new(key, b"sandbox-hosting-candidate-secrets.v1\0" + subject,
                                       hashlib.sha256).hexdigest()
        return rendered, files, binding
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("secret_source_refused") from None


def render_candidate(content: dict[str, bytes], *, project_name: str,
                     image_environment: dict[str, str], captured_environment: dict[str, str],
                     key: bytes, candidate: str, expected_services: tuple[str, ...]) -> dict[str, bytes]:
    """Render privately without running containers, then freeze mounted secrets."""
    try:
        if (type(project_name) is not str or _NAME.fullmatch(project_name) is None
                or type(image_environment) is not dict or not image_environment
                or any(type(name) is not str or _VARIABLE.fullmatch(name) is None
                       or type(value) is not str or re.fullmatch(
                           r"[a-z0-9.]+/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}", value) is None
                       for name, value in image_environment.items())
                or type(expected_services) is not tuple or not 1 <= len(expected_services) <= 80
                or len(set(expected_services)) != len(expected_services)
                or any(type(name) is not str or _NAME.fullmatch(name) is None for name in expected_services)):
            raise ValueError()
        with tempfile.TemporaryDirectory(prefix="sandbox-private-render-") as directory:
            root = Path(directory)
            for name, raw in content.items():
                if type(name) is not str or _NAME.fullmatch(name) is None or type(raw) is not bytes:
                    raise ValueError()
                descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
            names = sorted((name for name in content if re.fullmatch(r"compose-[0-9]+\.yml", name)),
                           key=lambda name: int(name[8:-4]))
            if not names or names != [f"compose-{index}.yml" for index in range(len(names))]:
                raise ValueError()
            base = ["docker", "compose", "--env-file", str(root / "environment.env")]
            for name in (*names, "compose.override.yml"):
                base.extend(("--file", str(root / name)))
            base.extend(("--project-directory", str(root), "--project-name", project_name))
            environment = {**_ENV, **image_environment}
            def run(args):
                result = subprocess.run(args, env=environment, capture_output=True, timeout=60)
                if result.returncode != 0 or result.stderr or len(result.stdout) > MAX_FILE:
                    raise ValueError()
                return result.stdout
            def render(profile=None):
                args = base if profile is None else base[:2] + ["--profile", profile] + base[2:]
                raw = run([*args, "config", "--format", "json"])
                document = json.loads(raw)
                if type(document) is not dict or type(document.get("services")) is not dict:
                    raise ValueError()
                return document
            document = render()
            if set(document["services"]) != set(expected_services):
                profiles = run([*base, "config", "--profiles"]).decode().split()
                if (not 1 <= len(profiles) <= 16 or len(set(profiles)) != len(profiles)
                        or any(_NAME.fullmatch(name) is None for name in profiles)):
                    raise ValueError()
                matches = [value for name in profiles if set((value := render(name))["services"]) == set(expected_services)]
                if len(matches) != 1:
                    raise ValueError()
                document = matches[0]
            effective, files, _binding = materialize_secrets(document, captured_environment, candidate, key)
            # The selected complete topology is already explicit. Runtime
            # operations select exact graph members and never discover profiles.
            for service in effective["services"].values():
                service.pop("profiles", None)
            return {**content, **files, "effective.json": json.dumps(effective,
                sort_keys=True, separators=(",", ":"), allow_nan=False).encode()}
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise ValueError("candidate_render_refused") from None


def retained_secret_material(compose: dict, directory: str) -> dict[str, str]:
    """Read only immutable candidate-owned secret files for keyed render binding."""
    descriptor = None
    try:
        sources = compose.get("secrets", {})
        if type(sources) is not dict or len(sources) > 64:
            raise ValueError()
        if not sources:
            return {}
        descriptor = _directory_fd(Path(directory))
        _owned_directory(descriptor)
        material = {}
        filenames = set()
        total = 0
        for name, source in sources.items():
            if (type(name) is not str or _NAME.fullmatch(name) is None
                    or type(source) is not dict or set(source) - {"file", "name"}
                    or type(source.get("file")) is not str):
                raise ValueError()
            path = Path(source["file"])
            if str(path.parent) != directory or re.fullmatch(r"secret-[0-9]+", path.name) is None or path.name in filenames:
                raise ValueError()
            filenames.add(path.name)
            secret_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            try:
                before = os.fstat(secret_fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                        or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) & 0o077
                        or before.st_size > 1024 * 1024):
                    raise ValueError()
                raw = bytearray()
                while len(raw) <= 1024 * 1024:
                    block = os.read(secret_fd, min(65536, 1024 * 1024 + 1 - len(raw)))
                    if not block:
                        break
                    raw.extend(block)
                after = os.fstat(secret_fd)
                named = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_nlink")
                if (len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k)
                        or getattr(after, k) != getattr(named, k) for k in fields)):
                    raise ValueError()
                total += len(raw)
                if total > MAX_TOTAL:
                    raise ValueError()
                material[name] = base64.b64encode(raw).decode("ascii")
            finally:
                os.close(secret_fd)
        return material
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("candidate_secret_refused") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def retained_secret_program() -> str:
    """Fixed helper definitions shared by preparation and runtime composition."""
    return (f"MAX_TOTAL={MAX_TOTAL}\n"
            "from pathlib import Path\n_NAME=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\\Z')\n"
            + "\n".join(inspect.getsource(function) for function in (
                _directory_fd, _owned_directory, retained_secret_material)))


def _candidate_main() -> None:
    """Closed private-stdin entrypoint; emits fixed status only."""
    try:
        raw = sys.stdin.buffer.read(2 * MAX_TOTAL + 1)
        if len(raw) > 2 * MAX_TOTAL:
            raise ValueError()
        def unique(pairs):
            result = {}
            for name, value in pairs:
                if name in result:
                    raise ValueError()
                result[name] = value
            return result
        frame = json.loads(raw, object_pairs_hook=unique)
        if (type(frame) is not dict or set(frame) != {"schema_version", "source_revision",
                "runtime_directory", "candidate_id", "compose_files", "manifest", "source_files",
                "environment", "compose_override", "render_contract"}
                or type(frame["schema_version"]) is not int or frame["schema_version"] != 2
                or type(frame["source_revision"]) is not str
                or re.fullmatch(r"[0-9a-f]{40}", frame["source_revision"]) is None):
            raise ValueError()
        names = frame["compose_files"]
        manifest = frame["manifest"]
        if (type(names) is not list or not 1 <= len(names) <= 32
                or any(type(name) is not str or not name or Path(name).is_absolute()
                       or ".." in Path(name).parts or any(c in name for c in "\0\n\r:")
                       for name in [*names, manifest])
                or len(set([*names, manifest])) != len(names) + 1
                or type(frame["source_files"]) is not dict
                or set(frame["source_files"]) != set([*names, manifest])):
            raise ValueError()
        source = {}
        for name, value in frame["source_files"].items():
            if type(value) is not str or len(value) > MAX_FILE * 2:
                raise ValueError()
            source[name] = base64.b64decode(value, validate=True)
            if len(source[name]) > MAX_FILE:
                raise ValueError()
        for field in ("environment", "compose_override"):
            if type(frame[field]) is not str or "\0" in frame[field] or len(frame[field].encode()) > MAX_FILE:
                raise ValueError()
        content = {f"compose-{index}.yml": source[name] for index, name in enumerate(names)}
        content.update({"manifest.yml": source[manifest], "environment.env": frame["environment"].encode(),
                        "compose.override.yml": frame["compose_override"].encode(),
                        "source.json": json.dumps({"schema_version": 2,
                            "source_revision": frame["source_revision"], "compose_files": names,
                            "manifest": manifest}, sort_keys=True, separators=(",", ":")).encode()})
        contract = frame["render_contract"]
        if type(contract) is not dict or set(contract) != {"project_name", "image_environment",
                "captured_environment", "configuration_key", "expected_services"}:
            raise ValueError()
        if type(contract["expected_services"]) is not list or type(contract["configuration_key"]) is not str:
            raise ValueError()
        key = base64.b64decode(contract["configuration_key"], validate=True)
        candidate = str(Path(frame["runtime_directory"]) / "activation-inputs" / frame["candidate_id"])
        content = render_candidate(content, project_name=contract["project_name"],
            image_environment=contract["image_environment"], captured_environment=contract["captured_environment"],
            key=key, candidate=candidate, expected_services=tuple(contract["expected_services"]))
        result = publish_candidate(Path(frame["runtime_directory"]), frame["candidate_id"], content)
    except Exception:
        sys.stdout.write('{"ok":false,"result":"refused"}\n')
        sys.exit(1)
    sys.stdout.write(json.dumps({"ok": True, "result": result}, separators=(",", ":")) + "\n")


def candidate_program() -> str:
    """Assemble fixed stdlib-only code, with no frame values in process argv."""
    header = ("import base64,copy,ctypes,fcntl,hashlib,hmac,json,os,re,stat,subprocess,sys,tempfile,time,uuid\n"
              "from pathlib import Path\n"
              f"MAX_FILE={MAX_FILE}\nMAX_TOTAL={MAX_TOTAL}\n"
              "_NAME=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\\Z')\n"
              "_VARIABLE=re.compile(r'[A-Z][A-Z0-9_]{0,127}\\Z')\n"
              f"_ENV={_ENV!r}\n")
    return header + "\n".join(inspect.getsource(function) for function in (
        _directory_fd, _owned_directory, _publish_at, publish_candidate, materialize_secrets,
        render_candidate, _candidate_main)) + "\n_candidate_main()\n"
