"""Defense-in-depth one-shot payload launcher inside the nspawn boundary."""

from __future__ import annotations

from pathlib import PurePosixPath


CREDENTIAL_SOURCE_ROOT = "/run/sandbox-native-credentials"
CREDENTIAL_TARGET_ROOT = "/run/credentials/sandbox"


class BubblewrapCompiler:
    def __init__(self, executable="bwrap"): self.executable = executable

    def argv(self, *, root="/", working_dir="/workspace", environment=None,
             writable_targets=(), credential_names=(), command):
        if not isinstance(command, (tuple, list)) or not command \
                or len(command) > 256 or sum(len(arg) for arg in command if isinstance(arg, str)) > 65536 \
                or any(not isinstance(arg, str) or "\x00" in arg for arg in command):
            raise ValueError("managed payload argv is invalid")
        if root != "/" or working_dir != "/workspace":
            raise ValueError("managed bubblewrap roots are fixed")
        targets = []
        for value in tuple(writable_targets):
            target = PurePosixPath(value)
            text = str(target)
            if (not target.is_absolute() or ".." in target.parts or text in {"/", "/proc", "/sys", "/dev"}
                    or any(text.startswith(prefix) for prefix in ("/proc/", "/sys/", "/dev/", "/run/host", "/run/systemd"))):
                raise ValueError("managed writable target is invalid")
            targets.append(text)
        if len(targets) != len(set(targets)):
            raise ValueError("managed writable target is duplicated")
        credentials = []
        for value in tuple(credential_names):
            if (not isinstance(value, str) or not value
                    or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                           for char in value)):
                raise ValueError("managed credential name is invalid")
            credentials.append(value)
        if len(credentials) != len(set(credentials)):
            raise ValueError("managed credential name is duplicated")
        args = [self.executable, "--die-with-parent", "--new-session", "--clearenv",
                "--unshare-user", "--disable-userns", "--assert-userns-disabled",
                "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
                "--ro-bind", root, "/"]
        for target in sorted(targets):
            args.extend(("--bind", target, target))
        args.extend(("--proc", "/proc", "--dev", "/dev",
                     "--tmpfs", "/tmp", "--tmpfs", "/run/credentials",
                     "--dir", CREDENTIAL_TARGET_ROOT))
        for name in sorted(credentials):
            source = f"{CREDENTIAL_SOURCE_ROOT}/{name}"
            target = f"{CREDENTIAL_TARGET_ROOT}/{name}"
            args.extend(("--ro-bind", source, target))
        args.extend(("--tmpfs", CREDENTIAL_SOURCE_ROOT,
                     "--tmpfs", "/run/systemd", "--tmpfs", "/run/dbus"))
        args.extend(("--chdir", working_dir,
                     "--cap-drop", "ALL", "--uid", "33", "--gid", "33"))
        for key, value in sorted((environment or {}).items()):
            args.extend(("--setenv", key, str(value)))
        return tuple((*args, "--", *command))
