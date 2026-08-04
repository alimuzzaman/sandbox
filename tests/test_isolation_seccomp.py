"""The payload must not be able to nest a user namespace (039 FR-046)."""

from __future__ import annotations

import struct
import unittest

from sandbox.isolation.seccomp import (
    ARCHITECTURES, CLONE_NEWUSER, ENOSYS, EPERM, OFFSET_ARCH, OFFSET_ARG0,
    OFFSET_NR, SECCOMP_RET_ALLOW, SECCOMP_RET_ERRNO, compile_userns_filter,
)


def _decode(program):
    return [struct.unpack("=HBBI", program[index:index + 8])
            for index in range(0, len(program), 8)]


class TestUsernsFilter(unittest.TestCase):
    def test_the_filter_is_a_well_formed_cbpf_program(self):
        program = compile_userns_filter("x86_64")
        self.assertEqual(len(program) % 8, 0)
        instructions = _decode(program)
        # Every jump must land inside the program: an offset past the end is
        # rejected by the kernel's verifier, and the filter would never load.
        for index, (code, jt, jf, _k) in enumerate(instructions):
            if code & 0x07 == 0x05:  # BPF_JMP
                self.assertLess(index + 1 + jt, len(instructions))
                self.assertLess(index + 1 + jf, len(instructions))
        self.assertTrue(any(code & 0x07 == 0x06 for code, _jt, _jf, _k in instructions))

    def test_every_architecture_checks_its_own_and_names_its_own_syscalls(self):
        for machine, (arch, clone, unshare, clone3) in ARCHITECTURES.items():
            with self.subTest(machine=machine):
                constants = {value for _c, _jt, _jf, value in _decode(
                    compile_userns_filter(machine))}
                self.assertIn(arch, constants)
                for number in (clone, unshare, clone3):
                    self.assertIn(number, constants)
                self.assertIn(CLONE_NEWUSER, constants)

    def test_a_foreign_architecture_refuses_rather_than_allows(self):
        # A filter compiled for another ABI would read the wrong syscall numbers,
        # so a mismatch must not fall through to allow.
        instructions = _decode(compile_userns_filter("x86_64"))
        _code, _jt, jf, _k = instructions[1]
        target = instructions[1 + 1 + jf]
        self.assertEqual(target[0] & 0x07, 0x06)
        self.assertEqual(target[3], SECCOMP_RET_ERRNO | EPERM)

    def test_clone3_is_answered_enosys_because_its_flags_cannot_be_read(self):
        # clone3 passes its arguments in a struct behind a pointer and seccomp
        # cannot dereference, so the flags are not inspectable. ENOSYS makes libc
        # fall back to clone, whose flags this filter can read.
        returns = {value for code, _jt, _jf, value in _decode(
            compile_userns_filter("x86_64")) if code & 0x07 == 0x06}
        self.assertIn(SECCOMP_RET_ERRNO | ENOSYS, returns)
        self.assertIn(SECCOMP_RET_ERRNO | EPERM, returns)
        self.assertIn(SECCOMP_RET_ALLOW, returns)

    def test_the_flags_argument_is_read_from_the_first_syscall_argument(self):
        loads = {value for code, _jt, _jf, value in _decode(
            compile_userns_filter("x86_64")) if code == 0x20}
        self.assertEqual(loads, {OFFSET_ARCH, OFFSET_NR, OFFSET_ARG0})

    def test_an_unknown_architecture_is_refused_at_compile_time(self):
        for machine in ("mips", "riscv64", ""):
            with self.subTest(machine=machine):
                with self.assertRaises(ValueError):
                    compile_userns_filter(machine)

    def test_a_host_spelling_of_the_same_architecture_compiles_the_same_filter(self):
        self.assertEqual(compile_userns_filter("arm64"), compile_userns_filter("aarch64"))
        self.assertEqual(compile_userns_filter("amd64"), compile_userns_filter("x86_64"))

    def test_the_helper_compiles_the_identical_filter(self):
        import importlib.util
        from pathlib import Path

        path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                / "native-helper.py")
        spec = importlib.util.spec_from_file_location("native_helper_seccomp", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for machine in ARCHITECTURES:
            with self.subTest(machine=machine):
                self.assertEqual(module.compile_userns_filter(machine),
                                 compile_userns_filter(machine))


class TestPayloadLaunchCarriesTheFilter(unittest.TestCase):
    def test_bubblewrap_is_given_the_filter_on_the_fixed_descriptor(self):
        from sandbox.isolation.bubblewrap import (
            BubblewrapCompiler, GUEST_USERNS_FILTER, USERNS_FILTER_FD,
        )

        argv = BubblewrapCompiler().argv(command=("php", "-v"))
        self.assertEqual(argv[:2], ("/bin/sh", "-c"))
        # The filter has to be open before bubblewrap starts: bubblewrap applies
        # it to the sandboxed process after its own setup, and it is bubblewrap
        # that creates the user namespace the payload must not nest inside.
        self.assertIn(f"exec {USERNS_FILTER_FD}<{GUEST_USERNS_FILTER}", argv[2])
        self.assertIn("--seccomp", argv)
        self.assertEqual(argv[argv.index("--seccomp") + 1], str(USERNS_FILTER_FD))
        self.assertLess(argv.index("--seccomp"), argv.index("--"))


if __name__ == "__main__":
    unittest.main()
