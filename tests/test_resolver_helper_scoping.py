"""The privileged resolver helper must not clobber its callers' variables.

POSIX sh has no function scope. `require_root_directory` assigned `owner` from
a directory's uid while the calling verb held the project's owner DIGEST in the
same name, so every receipt was written under owner "0": unattributable between
projects (FR-018) and impossible to remove later, because cleanup looks the
receipt up by the real digest (FR-019, FR-020).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "tools" / "resolver-helper.sh"
# Names the verbs hold while calling shared validators.
CALLER_VARIABLES = frozenset({"owner", "suffix", "address", "port", "expected",
                              "adapter", "hostname"})
VERB_ARGUMENT_LINE = re.compile(r"^\s{8}\w+=\$1")


def _functions(text: str) -> dict[str, list[str]]:
    """Crude but sufficient: split top-level `name() {` blocks."""
    functions: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        opened = re.match(r"^([a-z_]+)\(\)\s*[({]\s*$", line)
        if opened:
            current = opened.group(1)
            functions[current] = []
            continue
        if current is not None:
            if line in {"}", ")"}:
                current = None
                continue
            functions[current].append(line)
    return functions


class TestHelperVariableScoping(unittest.TestCase):
    def test_shared_validators_do_not_assign_caller_variables(self):
        text = HELPER.read_text()
        offenders = []
        for name, body in _functions(text).items():
            for line in body:
                for assignment in re.findall(r"(?:^|;)\s*([a-z_]+)=", line):
                    if assignment in CALLER_VARIABLES and "$1" not in line \
                            and "$2" not in line and "$3" not in line \
                            and "$4" not in line and "$5" not in line \
                            and "$6" not in line and "$7" not in line:
                        offenders.append(f"{name}: {line.strip()}")
        self.assertEqual(offenders, [], "helper functions clobber caller state")

    def test_directory_check_uses_its_own_variable_names(self):
        text = HELPER.read_text()
        self.assertIn("directory_owner=${identity%%:*}", text)
        self.assertIsNone(re.search(r"(?<![a-z_])owner=\$\{identity", text),
                          "the directory check still writes the caller's owner")


@unittest.skipUnless(shutil.which("bash"), "bash is required")
class TestHelperSyntax(unittest.TestCase):
    def test_helper_parses(self):
        result = subprocess.run(("bash", "-n", str(HELPER)),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
