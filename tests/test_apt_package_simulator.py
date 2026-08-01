import subprocess
from pathlib import Path
import tempfile
import unittest


class Process:
    def __init__(self): self.calls = []
    def run(self, argv, *, timeout):
        self.calls.append(argv)
        if argv[0] == "apt-get":
            return subprocess.CompletedProcess(argv, 0, "Inst nginx (1.24.0 Ubuntu:24.04/noble [amd64])\n", "")
        if argv[0] == "apt-cache":
            return subprocess.CompletedProcess(argv, 0,
                "nginx:\n  Candidate: 1.24.0\n  Version table:\n     1.24.0 500\n        500 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "")


class TestAptPackageSimulator(unittest.TestCase):
    def test_reads_only_signed_official_sources_and_exact_simulated_version(self):
        from sandbox.runtimes.managed.packages import AptPackageSimulator
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); key = root / "key.gpg"; key.write_text("key")
            sources = root / "ubuntu.sources"; sources.write_text(
                f"Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\nSuites: noble\n"
                f"Components: main\nSigned-By: {key}\n")
            simulator = AptPackageSimulator(process=Process(), sources_path=sources)
            self.assertTrue(simulator.sources()[0]["signed"])
            rows = simulator.simulate("image", ("nginx",))
        self.assertEqual(rows[0]["version"], "1.24.0")
        self.assertEqual(rows[0]["origin"], "http://archive.ubuntu.com/ubuntu")
        apt_call = next(call for call in simulator.process.calls if call[0] == "apt-get")
        self.assertIn("Dir::State::status=/dev/null", apt_call)

    def test_unsigned_source_is_rejected(self):
        from sandbox.runtimes.managed.packages import AptPackageSimulator
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ubuntu.sources"
            source.write_text("Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\nSuites: noble\n")
            with self.assertRaises(ValueError):
                AptPackageSimulator(process=Process(), sources_path=source).sources()


if __name__ == "__main__": unittest.main()
