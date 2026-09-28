"""Private Docker archive delivery for candidate-v2 Compose secrets.

This module is private transport code. It returns only fixed status values and
never puts secret bytes in argv, diagnostics, or public activation state.
"""

from __future__ import annotations

import base64
import io
import re
import tarfile


SECRET_ARCHIVE_MAX_FILE = 1024 * 1024
SECRET_ARCHIVE_MAX_TOTAL = 8 * SECRET_ARCHIVE_MAX_FILE
SECRET_ARCHIVE_MAX_ARCHIVE = SECRET_ARCHIVE_MAX_TOTAL + 1024 * 1024
_SECRET_IDENTITY = re.compile(r"[0-9a-f]{64}\Z")
_SECRET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SECRET_VARIABLE = re.compile(r"SANDBOX_ACTIVATION_SECRET_[0-9]+\Z")


def _refuse() -> None:
    raise ValueError("secret_file_refused")


def _identity(identity: str) -> None:
    if type(identity) is not str or _SECRET_IDENTITY.fullmatch(identity) is None:
        _refuse()


def _mounts(mounts: object) -> list[dict]:
    if type(mounts) is not list or len(mounts) > 64:
        _refuse()
    result = []
    names = set()
    total = 0
    for row in mounts:
        if (type(row) is not dict or set(row) != {"filename", "uid", "gid", "mode", "data"}
                or type(row["filename"]) is not str or _SECRET_NAME.fullmatch(row["filename"]) is None
                or len(row["filename"].encode()) > 100
                or row["filename"] in names or type(row["uid"]) is not int
                or type(row["gid"]) is not int or not 0 <= row["uid"] <= 65534
                or not 0 <= row["gid"] <= 65534 or type(row["mode"]) is not int
                or not 0 < row["mode"] <= 0o777 or row["mode"] & 0o222
                or type(row["data"]) is not bytes or len(row["data"]) > SECRET_ARCHIVE_MAX_FILE):
            _refuse()
        total += len(row["data"])
        if total > SECRET_ARCHIVE_MAX_TOTAL:
            _refuse()
        names.add(row["filename"])
        result.append(dict(row))
    return result


def _number(value: object, limit: int) -> int:
    if type(value) is int:
        number = value
    elif type(value) is str and re.fullmatch(r"[0-9]{1,5}", value):
        number = int(value, 10)
    else:
        _refuse()
    if not 0 <= number <= limit:
        _refuse()
    return number


def _mode(value: object) -> int:
    if type(value) is int:
        mode = value
    elif type(value) is str and re.fullmatch(r"0?[0-7]{3}", value):
        mode = int(value, 8)
    else:
        _refuse()
    if not 0 < mode <= 0o777 or mode & 0o222:
        _refuse()
    return mode


def secret_file_mounts(document: dict, service_name: str,
                       material: dict[str, str]) -> list[dict]:
    """Resolve one service's generated v2 secrets to bounded archive rows."""
    try:
        if type(document) is not dict or type(service_name) is not str or _SECRET_NAME.fullmatch(service_name) is None:
            _refuse()
        services = document.get("services")
        sources = document.get("secrets", {})
        if type(services) is not dict or type(sources) is not dict or service_name not in services:
            _refuse()
        service = services[service_name]
        if type(service) is not dict:
            _refuse()
        declared = service.get("secrets") or []
        if (type(declared) is not list or type(material) is not dict or set(material) - set(sources)
                or (declared and service.get("read_only") is not False and service.get("read_only") is not None)):
            _refuse()
        rows = []
        targets = set()
        for mount in declared:
            if (type(mount) is not dict or set(mount) - {"source", "target", "uid", "gid", "mode"}
                    or type(mount.get("source")) is not str or mount["source"] not in sources):
                _refuse()
            source_name = mount["source"]
            source = sources[source_name]
            index = sorted(sources).index(source_name)
            expected_variable = f"SANDBOX_ACTIVATION_SECRET_{index}"
            if (type(source) is not dict or set(source) - {"environment", "name"}
                    or source.get("environment") != expected_variable
                    or source_name not in material or type(material[source_name]) is not str):
                _refuse()
            target = mount.get("target", source_name)
            if type(target) is not str:
                _refuse()
            target = target if target.startswith("/run/secrets/") else "/run/secrets/" + target
            filename = target.removeprefix("/run/secrets/")
            if not _SECRET_NAME.fullmatch(filename) or target != "/run/secrets/" + filename or filename in targets:
                _refuse()
            raw = base64.b64decode(material[source_name], validate=True)
            if len(raw) > SECRET_ARCHIVE_MAX_FILE:
                _refuse()
            rows.append({"filename": filename, "uid": _number(mount.get("uid", 0), 65534),
                         "gid": _number(mount.get("gid", 0), 65534),
                         "mode": _mode(mount.get("mode", 0o444)),
                         "data": raw})
            targets.add(filename)
        return _mounts(rows)
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("secret_file_refused") from None


def _archive(rows: list[dict]) -> bytes:
    rows = _mounts(rows)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        root = tarfile.TarInfo("secrets")
        root.type = tarfile.DIRTYPE
        root.uid, root.gid, root.mode = 0, 0, 0o755
        archive.addfile(root)
        for row in sorted(rows, key=lambda item: item["filename"]):
            info = tarfile.TarInfo("secrets/" + row["filename"])
            info.uid, info.gid, info.mode = row["uid"], row["gid"], row["mode"]
            info.size = len(row["data"])
            archive.addfile(info, io.BytesIO(row["data"]))
    raw = stream.getvalue()
    if len(raw) > SECRET_ARCHIVE_MAX_TOTAL + 1024 * 1024:
        _refuse()
    return raw


def _read_tar(raw: bytes) -> list[tuple[object, bytes]]:
    """Parse actual header boundaries; payload bytes are never tar headers."""
    if (type(raw) is not bytes or not 1024 <= len(raw) <= SECRET_ARCHIVE_MAX_ARCHIVE
            or len(raw) % 512):
        _refuse()
    members = []
    names = set()
    total = 0
    offset = 0
    try:
        while offset < len(raw):
            header = raw[offset:offset + 512]
            if header == bytes(512):
                if len(raw) - offset < 1024 or any(raw[offset:]):
                    _refuse()
                return members
            # Reject extension/link/device records before a tar library can
            # reinterpret their path or identity fields. USTAR alone is used.
            if header[156:157] not in (b"0", b"\0", b"5"):
                _refuse()
            checksum = header[148:156].strip(b" \0")
            size_field = header[124:136].strip(b" \0")
            if (not re.fullmatch(rb"[0-7]{1,8}", checksum)
                    or not re.fullmatch(rb"[0-7]{1,12}", size_field)
                    or int(checksum, 8) != sum(header[:148]) + 256 + sum(header[156:])):
                _refuse()
            size = int(size_field, 8)
            end = offset + 512 + ((size + 511) // 512) * 512
            if size > SECRET_ARCHIVE_MAX_FILE or end > len(raw):
                _refuse()
            info = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
            name = info.name
            if (len(members) >= 128 or not name or name.startswith("/")
                    or any(part in ("", ".", "..") for part in name.split("/"))
                    or name in names or info.pax_headers or info.linkname
                    or info.mode & ~0o7777 or not 0 <= info.uid <= 65534
                    or not 0 <= info.gid <= 65534 or info.size != size
                    or info.devmajor or info.devminor):
                _refuse()
            if info.isdir():
                if size:
                    _refuse()
                data = b""
            elif info.isfile():
                data = raw[offset + 512:offset + 512 + size]
                total += size
                if total > SECRET_ARCHIVE_MAX_TOTAL:
                    _refuse()
            else:
                _refuse()
            if any(raw[offset + 512 + size:end]):
                _refuse()
            names.add(name)
            members.append((info, data))
            offset = end
    except (OSError, tarfile.TarError, ValueError, UnicodeError, OverflowError):
        raise ValueError("secret_file_refused") from None
    _refuse()


def _read_archive(raw: bytes) -> tuple[dict | None, list[dict]]:
    members = _read_tar(raw)
    root = None
    files = []
    for info, data in members:
        name = info.name
        if name == "secrets" and info.isdir():
            if root is not None:
                _refuse()
            root = {"uid": info.uid, "gid": info.gid, "mode": info.mode}
        elif name.startswith("secrets/") and name.count("/") == 1 and info.isfile():
            filename = name.split("/", 1)[1]
            if not _SECRET_NAME.fullmatch(filename):
                _refuse()
            files.append({"filename": filename, "uid": info.uid, "gid": info.gid,
                          "mode": info.mode, "data": data})
        else:
            _refuse()
    if root is None:
        _refuse()
    return root, _mounts(files)


def _read_run_archive(raw: bytes) -> tuple[dict, list[dict]]:
    members = _read_tar(raw)
    root = None
    secret_root = None
    files = []
    for info, data in members:
        name = info.name
        if name == "run" and info.isdir():
            if root is not None:
                _refuse()
            root = {"uid": info.uid, "gid": info.gid, "mode": info.mode}
        elif name == "run/secrets" and info.isdir():
            if secret_root is not None:
                _refuse()
            secret_root = {"uid": info.uid, "gid": info.gid, "mode": info.mode}
        elif name.startswith("run/secrets/") and name.count("/") == 2 and info.isfile():
            filename = name.split("/", 2)[2]
            if not _SECRET_NAME.fullmatch(filename):
                _refuse()
            files.append({"filename": filename, "uid": info.uid, "gid": info.gid,
                          "mode": info.mode, "data": data})
        elif name == "run/secrets" or name.startswith("run/secrets/"):
            _refuse()
        elif name.startswith("run/"):
            # Other /run entries are allowed only as ordinary validated tar
            # members; they are irrelevant to the secret directory.
            continue
        else:
            _refuse()
    if root != {"uid": 0, "gid": 0, "mode": 0o755}:
        _refuse()
    return secret_root, _mounts(files)


def _mount_overlaps_secrets(destination: str) -> bool:
    if (type(destination) is not str or not destination.startswith("/")
            or destination == "/" or any(part in ("", ".", "..")
                                         for part in destination[1:].split("/"))):
        return True
    target = "/run/secrets"
    return (destination == target or destination.startswith(target + "/")
            or target.startswith(destination + "/"))


def _state(identity: str, inspect, *, require_created: bool) -> dict:
    _identity(identity)
    row = inspect(identity)
    if type(row) is not dict or row.get("Id") != identity:
        _refuse()
    state, host, mounts = row.get("State"), row.get("HostConfig"), row.get("Mounts")
    if type(state) is not dict or type(host) is not dict or type(mounts) is not list:
        _refuse()
    status = state.get("Status")
    if (status not in ("created", "running", "exited")
            or state.get("Running") is not (status == "running")
            or (require_created and status != "created")
            or any(state.get(flag, False) is not False for flag in ("Paused", "Restarting", "Dead"))
            or host.get("ReadonlyRootfs") is not False):
        _refuse()
    for mount in mounts:
        if type(mount) is not dict or _mount_overlaps_secrets(mount.get("Destination")):
            _refuse()
    # tmpfs destinations may not appear in Mounts while a container is stopped.
    tmpfs = host.get("Tmpfs") or {}
    declared = host.get("Mounts") or []
    if type(tmpfs) is not dict or type(declared) is not list:
        _refuse()
    if any(_mount_overlaps_secrets(destination) for destination in tmpfs):
        _refuse()
    for mount in declared:
        if type(mount) is not dict or _mount_overlaps_secrets(mount.get("Target")):
            _refuse()
    # Health may progress during a read-only proof, but identity, configuration,
    # mount graph and start/restart epoch must remain identical.
    return {key: row.get(key) for key in (
        "Id", "Image", "Path", "Args", "Config", "HostConfig", "Mounts", "RestartCount")} | {
        "state": {key: state.get(key) for key in (
            "Status", "Running", "Paused", "Restarting", "Dead", "StartedAt", "FinishedAt")}}


def _read_secret_archive(identity: str, command) -> tuple[dict | None, list[dict]]:
    raw = command(["docker", "cp", identity + ":/run", "-"],
                  max_output_bytes=SECRET_ARCHIVE_MAX_ARCHIVE)
    if type(raw) is not bytes:
        _refuse()
    return _read_run_archive(raw)


def verify_container_secret_files(identity: str, mounts: list[dict], command, inspect) -> bool:
    """Return true only for a complete exact proof; every mismatch refuses."""
    expected = _mounts(mounts)
    if not expected:
        return True
    before = _state(identity, inspect, require_created=False)
    root, rows = _read_secret_archive(identity, command)
    if (root != {"uid": 0, "gid": 0, "mode": 0o755}
            or sorted(rows, key=lambda row: row["filename"]) !=
            sorted(expected, key=lambda row: row["filename"])
            or _state(identity, inspect, require_created=False) != before):
        _refuse()
    return True


def prepare_container_secret_files(identity: str, mounts: list[dict], command, inspect) -> str:
    """Write only an absent directory in the same proven stopped container."""
    expected = _mounts(mounts)
    if not expected:
        return "replayed"
    before = _state(identity, inspect, require_created=True)
    root, rows = _read_secret_archive(identity, command)
    if _state(identity, inspect, require_created=True) != before:
        _refuse()
    if root is not None:
        if (root != {"uid": 0, "gid": 0, "mode": 0o755}
                or sorted(rows, key=lambda row: row["filename"]) !=
                sorted(expected, key=lambda row: row["filename"])):
            _refuse()
        return "replayed"
    if rows:
        _refuse()
    # With tar stdin, preserve the numeric archive headers. Docker's --archive
    # option remaps every entry to Config.User on the observed Linux daemon,
    # including the root-owned parent; exact readback catches that mismatch.
    command(["docker", "cp", "-", identity + ":/run"], input=_archive(expected),
            max_output_bytes=SECRET_ARCHIVE_MAX_ARCHIVE)
    verify_container_secret_files(identity, expected, command, inspect)
    if _state(identity, inspect, require_created=True) != before:
        _refuse()
    return "prepared"


def private_secret_program() -> str:
    """Return the fixed stdlib-only source used by the private runner."""
    import inspect
    return ("import base64,io,re,tarfile\n"
            f"SECRET_ARCHIVE_MAX_FILE={SECRET_ARCHIVE_MAX_FILE}\n"
            f"SECRET_ARCHIVE_MAX_TOTAL={SECRET_ARCHIVE_MAX_TOTAL}\n"
            f"SECRET_ARCHIVE_MAX_ARCHIVE={SECRET_ARCHIVE_MAX_ARCHIVE}\n"
            "_SECRET_IDENTITY=re.compile(r'[0-9a-f]{64}\\Z')\n"
            "_SECRET_NAME=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\\Z')\n"
            "_SECRET_VARIABLE=re.compile(r'SANDBOX_ACTIVATION_SECRET_[0-9]+\\Z')\n"
            + "\n".join(inspect.getsource(function) for function in (
                _refuse, _identity, _mounts, _number, _mode, secret_file_mounts, _archive,
                _read_tar, _read_archive, _read_run_archive, _mount_overlaps_secrets,
                _state, _read_secret_archive,
                verify_container_secret_files, prepare_container_secret_files)))
