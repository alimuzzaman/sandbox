from __future__ import annotations

import unittest

from sandbox.services.process import ProcessResult


class FakeProcess:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def run(self, argv, **_kwargs):
        command = tuple(argv)
        self.calls.append(command)
        return self.outputs.get(command, ProcessResult(command, 1, "", "missing"))


class TestDomainDetection(unittest.TestCase):
    def test_systemd_resolved_detection_is_read_only_and_reports_answer(self):
        from sandbox.network.detection import ResolverDetector

        process = FakeProcess({
            ("resolvectl", "status"): ProcessResult(
                ("resolvectl", "status"), 0,
                "Global\n       Protocols: -LLMNR -mDNS\nresolv.conf mode: stub\n", "",
            ),
            ("resolvectl", "query", "demo.test"): ProcessResult(
                ("resolvectl", "query", "demo.test"), 0,
                "demo.test: 127.0.0.77 -- link: lo\n", "",
            ),
        })
        detector = ResolverDetector(
            process=process, platform="linux",
            readlink=lambda path: "/run/systemd/resolve/stub-resolv.conf",
            exists=lambda path: path == "/run/systemd/resolve/stub-resolv.conf",
        )
        result = detector.observe("demo.test")

        self.assertEqual(result.manager, "resolved")
        self.assertEqual(result.current_answers, ("127.0.0.77",))
        self.assertEqual(result.support_tier, "implemented_unproven")
        self.assertEqual(process.calls, [
            ("resolvectl", "status"), ("resolvectl", "query", "demo.test"),
        ])

    def test_wsl2_is_detect_only_without_windows_mutation(self):
        from sandbox.network.detection import ResolverDetector

        detector = ResolverDetector(
            process=FakeProcess({}), platform="linux",
            read_text=lambda path: "Linux microsoft-standard-WSL2" if path == "/proc/version" else "",
            readlink=lambda _path: "/run/WSL/resolv.conf",
            exists=lambda _path: False,
        )
        result = detector.observe("demo.test")
        self.assertEqual(result.support_tier, "outside_platform")
        self.assertEqual(result.manager, "unknown")

    def test_unknown_manager_returns_bounded_evidence_without_mutation(self):
        from sandbox.network.detection import ResolverDetector

        process = FakeProcess({})
        detector = ResolverDetector(
            process=process, platform="linux", readlink=lambda _path: "/etc/resolv.conf",
            read_text=lambda _path: "nameserver 10.0.0.1\n", exists=lambda _path: False,
        )
        result = detector.observe("demo.test")
        self.assertEqual(result.manager, "unknown")
        self.assertEqual(result.support_tier, "detect_only")
        self.assertLessEqual(len("\n".join(result.evidence)), 2000)


if __name__ == "__main__":
    unittest.main()
