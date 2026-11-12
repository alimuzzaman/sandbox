"""Fixed nonsecret publisher cases inside an original retained owner job."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import stat

CONTRACT = "job.publisher-proof.v1"
TEXT = "Sandbox publisher proof\nবাংলা\x00fixed public bytes\n"


def contract_available():
    from sandbox.application.command_selection import selected_invocation, CommandSelectionUnavailable
    try:
        selected = selected_invocation(("publisher-proof", "--remote", "proof",
                                       "--request-id", "proof", "--json"))
        return selected is not None and selected.command == ("publisher-proof", None)
    except CommandSelectionUnavailable:
        return False


def _directory(path, uid):
    """Open the exact existing ancestor chain without following links."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid not in (0, uid) or stat.S_IMODE(info.st_mode) & 0o022:
                raise ValueError("unsafe diagnostic ancestor")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private_child(fd, name, uid, *, exclusive):
    try:
        os.mkdir(name, 0o700, dir_fd=fd)
        os.fsync(fd)
    except FileExistsError:
        if exclusive:
            raise
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    info = os.fstat(child)
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(child)
        raise ValueError("unsafe diagnostic directory")
    return child


def _verify(fd, name, uid):
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(child)
        expected = TEXT.encode()
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size != len(expected)):
            raise ValueError("diagnostic recipient mismatch")
        body = os.read(child, len(expected) + 1)
        after = os.fstat(child)
        named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        signature = lambda value: (value.st_dev, value.st_ino, value.st_mode,
            value.st_uid, value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if body != expected or signature(before) != signature(after) or signature(after) != signature(named):
            raise ValueError("diagnostic recipient changed")
        return signature(after)
    finally:
        os.close(child)


def run_proof(target):
    from sandbox.hosting.server_cli_target import require_owner_target, withhold_owner_completion
    from sandbox.commands.hosting import _write_remote_text
    from sandbox.hosting.runtime_file_writer import PROGRAM
    context = require_owner_target(target)
    if re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{32}", context.job_id) is None:
        raise ValueError("invalid original diagnostic job")
    home = Path(context.owner.sandbox_home)
    if not home.is_absolute() or str(home) != os.path.normpath(str(home)):
        raise ValueError("invalid owner home")
    fds = []
    cases = []
    case = "prepare"
    started = False
    result = {"schema_version": 1, "contract": CONTRACT, "ok": False,
              "completion_known": True, "cases": cases}
    try:
        fds.append(_directory(home, context.owner.uid))
        target.require_current()
        started = True
        fds.append(_private_child(fds[-1], "publisher-canaries", context.owner.uid, exclusive=False))
        fds.append(_private_child(fds[-1], context.job_id, context.owner.uid, exclusive=True))
        directory = home / "publisher-canaries" / context.job_id
        def check_binding():
            target.require_current()
            checked = _directory(directory, context.owner.uid)
            try:
                observed, original = os.fstat(checked), os.fstat(fds[-1])
                if (observed.st_dev, observed.st_ino) != (original.st_dev, original.st_ino):
                    raise ValueError("diagnostic directory changed")
            finally:
                os.close(checked)
        previous = None
        for case in ("publish", "idempotent"):
            check_binding()
            _write_remote_text(target, str(directory / "sample"), TEXT)
            check_binding()
            signature = _verify(fds[-1], "sample", context.owner.uid)
            if previous is not None and signature != previous:
                raise ValueError("idempotent publication changed recipient")
            previous = signature
            cases.append({"case": case, "ok": True, "whole_file_match": True,
                          "private_owner_file": True})
        case = "short_frame"
        check_binding()
        response = target.process(shlex.join(["python3", "-c", PROGRAM,
            str(directory / "short-frame"), "0600"]), timeout=30, input_data=b"\x00")
        check_binding()
        receipt = json.loads(response.stdout)
        if (type(response.returncode) is not int or response.returncode != 1
                or type(receipt) is not dict or set(receipt) != {"ok", "status"}
                or receipt["ok"] is not False or receipt["status"] != "not_published"):
            raise ValueError("short frame outcome unavailable")
        try:
            os.stat("short-frame", dir_fd=fds[-1], follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("short frame created recipient")
        target.require_current()
        cases.append({"case": case, "ok": True, "writer_exit": 1,
                      "writer_receipt": "not_published", "recipient_absent": True})
        result["ok"] = True
    except Exception as error:
        # No raw process output, recipient path, payload or exception escapes.
        known_refusal = str(error) == "runtime file publication refused before replacement"
        known = not started or known_refusal
        result["completion_known"] = known
        failure = {"case": case, "ok": False, "writer_receipt": "not_published" if known_refusal else "unavailable"}
        metadata = re.search(r"writer_exit=(-?[0-9]{1,3}|unavailable); writer_receipt=([a-z_]+);", str(error))
        if metadata and metadata[2] in {"empty", "invalid_json", "invalid_shape", "already_current",
                                      "published", "not_published", "publication_unknown"}:
            failure["writer_receipt"] = metadata[2]
            if metadata[1] != "unavailable" and -255 <= int(metadata[1]) <= 255:
                failure["writer_exit"] = int(metadata[1])
        cases.append(failure)
        if not known:
            withhold_owner_completion(target)
    finally:
        for fd in reversed(fds):
            os.close(fd)
    return result
