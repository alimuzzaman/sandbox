"""Defense-in-depth one-shot payload launcher inside the nspawn boundary."""

from __future__ import annotations


class BubblewrapCompiler:
    def __init__(self, executable="bwrap"): self.executable = executable

    def argv(self, *, root="/", working_dir="/workspace", environment=None, command):
        if not isinstance(command, (tuple, list)) or not command \
                or len(command) > 256 or sum(len(arg) for arg in command if isinstance(arg, str)) > 65536 \
                or any(not isinstance(arg, str) or "\x00" in arg for arg in command):
            raise ValueError("managed payload argv is invalid")
        if root != "/" or working_dir != "/workspace":
            raise ValueError("managed bubblewrap roots are fixed")
        args = [self.executable, "--die-with-parent", "--new-session", "--clearenv",
                "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
                "--unshare-cgroup", "--uid", "65534", "--gid", "65534",
                "--ro-bind", root, "/", "--proc", "/proc", "--dev", "/dev",
                "--tmpfs", "/tmp", "--chdir", working_dir,
                "--cap-drop", "ALL"]
        for key, value in sorted((environment or {}).items()):
            args.extend(("--setenv", key, str(value)))
        return tuple((*args, "--", *command))
