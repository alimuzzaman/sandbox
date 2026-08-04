"""Compile the cBPF filter that stops a payload nesting a user namespace.

AppArmor cannot carry this guarantee here. Ubuntu 24.04 mediates *unprivileged*
user-namespace creation, but bubblewrap has already put the payload inside a user
namespace where it holds CAP_SYS_ADMIN, so the creation is no longer unprivileged
and `deny userns create` does not apply -- measured on a live machine, which
reported `nested_userns: True` with the rule in place. Bubblewrap's own mechanism
cannot work either: `--disable-userns` writes `/proc/sys/user/max_user_namespaces`,
and nspawn mounts `/proc/sys` read-only inside a machine.

What is left is a seccomp filter, which is how the container runtimes solve the
same problem. `clone3` is deliberately answered with ENOSYS rather than filtered:
its arguments arrive in a `struct clone_args` behind a pointer, and seccomp cannot
dereference a pointer, so its flags are simply not inspectable. Returning ENOSYS
makes glibc fall back to `clone`, whose flags are a register argument this filter
can read. Docker and podman do exactly this for the same reason.
"""

from __future__ import annotations

import struct


# Linux/seccomp constants. Spelled out rather than imported so the compiled
# filter is readable next to the kernel documentation it implements.
BPF_LD, BPF_W, BPF_ABS, BPF_JMP, BPF_JEQ, BPF_JSET, BPF_K, BPF_RET = (
    0x00, 0x00, 0x20, 0x05, 0x10, 0x40, 0x00, 0x06)
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_ERRNO = 0x00050000
EPERM, ENOSYS = 1, 38
CLONE_NEWUSER = 0x10000000

# Offsets into `struct seccomp_data`: nr, arch, instruction_pointer, args[6].
OFFSET_NR, OFFSET_ARCH, OFFSET_ARG0 = 0, 4, 16

AUDIT_ARCH_X86_64 = 0xC000003E
AUDIT_ARCH_AARCH64 = 0xC00000B7

# (audit arch, clone, unshare, clone3)
ARCHITECTURES = {
    "x86_64": (AUDIT_ARCH_X86_64, 56, 272, 435),
    "aarch64": (AUDIT_ARCH_AARCH64, 220, 97, 435),
}

# The guest is always a Linux image, but the name may be read on a host that
# spells the same architecture differently -- macOS reports `arm64` where Linux
# reports `aarch64`, and the compiled filter must not depend on which machine
# happened to render the configuration.
ARCHITECTURE_ALIASES = {"arm64": "aarch64", "amd64": "x86_64", "x86-64": "x86_64"}


def _instruction(code, jt, jf, k):
    return struct.pack("=HBBI", code, jt, jf, k)


def compile_userns_filter(machine):
    """cBPF refusing `clone`/`unshare` with CLONE_NEWUSER, and `clone3` outright.

    The program is written for one architecture and checks that it is running on
    it. A filter compiled for the wrong architecture would read another ABI's
    syscall numbers, so a mismatch refuses rather than falls through to allow.
    """
    machine = ARCHITECTURE_ALIASES.get(machine, machine)
    if machine not in ARCHITECTURES:
        raise ValueError(f"unsupported architecture for the userns filter: {machine}")
    arch, clone, unshare, clone3 = ARCHITECTURES[machine]

    # Written as an explicit instruction list because every jump is an offset to
    # the instructions after it, and those offsets must move together.
    #
    #  0  load arch
    #  1  arch == expected ? continue : -> deny            (jf = 8, index 10)
    #  2  load nr
    #  3  nr == clone3 ? -> enosys (index 9)
    #  4  nr == clone   ? -> flags (index 7)
    #  5  nr == unshare ? -> flags (index 7)
    #  6  -> allow (index 11)
    #  7  load args[0]        (flags is the first argument of both clone and unshare)
    #  8  flags & CLONE_NEWUSER ? -> deny (index 10) : -> allow (index 11)
    #  9  return ENOSYS
    # 10  return EPERM
    # 11  return ALLOW
    program = [
        _instruction(BPF_LD | BPF_W | BPF_ABS, 0, 0, OFFSET_ARCH),
        _instruction(BPF_JMP | BPF_JEQ | BPF_K, 0, 8, arch),
        _instruction(BPF_LD | BPF_W | BPF_ABS, 0, 0, OFFSET_NR),
        _instruction(BPF_JMP | BPF_JEQ | BPF_K, 5, 0, clone3),
        _instruction(BPF_JMP | BPF_JEQ | BPF_K, 2, 0, clone),
        _instruction(BPF_JMP | BPF_JEQ | BPF_K, 1, 0, unshare),
        _instruction(BPF_JMP | 0x00 | BPF_K, 0, 0, 4),
        _instruction(BPF_LD | BPF_W | BPF_ABS, 0, 0, OFFSET_ARG0),
        _instruction(BPF_JMP | BPF_JSET | BPF_K, 1, 2, CLONE_NEWUSER),
        _instruction(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ERRNO | ENOSYS),
        _instruction(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ERRNO | EPERM),
        _instruction(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ALLOW),
    ]
    return b"".join(program)
