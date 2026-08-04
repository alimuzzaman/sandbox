"""Defense-in-depth one-shot payload launcher inside the nspawn boundary."""

from __future__ import annotations

from pathlib import PurePosixPath


CREDENTIAL_SOURCE_ROOT = "/run/sandbox-native-credentials"
CREDENTIAL_TARGET_ROOT = "/run/credentials/sandbox"
GUEST_USERNS_FILTER = "/etc/sandbox-native/userns-filter.bpf"
# Single digit on purpose: /bin/sh is dash on the guest, and it parses
# `exec 10<file` as the command `10` with a redirect, not as a redirect
# on descriptor 10 -- the payload died with "exec: 10: not found".
USERNS_FILTER_FD = 9


def userns_filtered_argv(argv):
    """Open the userns seccomp filter on a fixed fd, then exec bubblewrap.

    bubblewrap takes the filter as a file descriptor and applies it to the
    sandboxed process after its own setup, which is the only point where it can
    hold: bubblewrap itself has to create a user namespace, so a filter applied
    any earlier would block bubblewrap instead of the payload.

    The redirect fails closed. Without `set -e` a missing filter would leave the
    fd unopened and bubblewrap would refuse anyway, but an explicit failure says
    which file was missing instead of reporting an unrelated bubblewrap error.
    """
    return ("/bin/sh", "-c",
            f"exec {USERNS_FILTER_FD}<{GUEST_USERNS_FILTER} || exit 126\nexec \"$@\"\n",
            "sandbox-userns-filter", *tuple(argv))


def stacked_command(payload_profile, command):
    """Stack the payload profile onto the final exec, then run the command.

    A domain transition cannot be used: bubblewrap sets NoNewPrivileges before
    exec, under which the kernel refuses one, and with any `px` rule present
    every exec inside bubblewrap is refused before the payload starts. Stacking
    yields the intersection of the bwrap and payload profiles instead, and is
    irreversible because the payload profile grants no change_profile.

    The write must succeed. Without the check a failed stack would run the
    command under the weaker bwrap profile, which is the one outcome this exists
    to prevent, so failure exits rather than proceeding unconfined.
    """
    if (not isinstance(payload_profile, str) or not payload_profile
            or any(char in payload_profile for char in "\x00\n '\"\\")):
        raise ValueError("managed payload profile name is invalid")
    # `sh -c SCRIPT NAME ARG...` puts NAME in $0 and the command in $@.
    script = (f"printf %s 'stack {payload_profile}' > /proc/self/attr/apparmor/exec "
              "|| exit 126\nexec \"$@\"\n")
    return ("/bin/sh", "-c", script, "sandbox-payload", *tuple(command))


class BubblewrapCompiler:
    def __init__(self, executable="bwrap"): self.executable = executable

    def argv(self, *, root="/", working_dir="/workspace", environment=None,
             writable_targets=(), credential_names=(), command,
             payload_profile=None):
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
                "--unshare-user", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
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
                     "--tmpfs", "/run/systemd", "--tmpfs", "/run/dbus",
                     # nspawn exposes the host os-release and an incoming
                     # bind point under /run/host; the payload has no use
                     # for either.
                     "--tmpfs", "/run/host"))
        args.extend(("--chdir", working_dir,
                     "--cap-drop", "ALL", "--uid", "33", "--gid", "33",
                     # AppArmor cannot carry this: the payload is already inside
                     # bubblewrap's user namespace, so creating another is not an
                     # unprivileged creation and `deny userns create` does not
                     # apply -- measured live as `nested_userns: True` with the
                     # rule in place (FR-046).
                     "--seccomp", str(USERNS_FILTER_FD)))
        for key, value in sorted((environment or {}).items()):
            args.extend(("--setenv", key, str(value)))
        if payload_profile:
            command = stacked_command(payload_profile, command)
        return userns_filtered_argv((*args, "--", *command))
