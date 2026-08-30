from __future__ import annotations
import unittest
from unittest.mock import patch
from sandbox.resources.host_memory.provider import HostProvider

class Usage:
    total=100*1024**3; free=80*1024**3

class HostMemoryProviderTest(unittest.TestCase):
    def test_non_linux_is_explicit_and_read_only(self):
        provider=HostProvider(read_text=lambda _p: self.fail("must not read"))
        with patch("platform.system",return_value="Darwin"):
            self.assertEqual(provider.observe()["evidence_state"],"unsupported")
    def test_linux_observation_is_aggregate_only(self):
        def read(path):
            if str(path)=="/proc/meminfo": return "MemTotal: 16777216 kB\nMemAvailable: 12582912 kB\n"
            if str(path)=="/proc/swaps": return "Filename Type Size Used Priority\n"
            raise OSError
        with patch("platform.system",return_value="Linux"), patch("shutil.disk_usage",return_value=Usage()):
            result=HostProvider(read_text=read).observe()
        self.assertEqual(result["evidence_state"],"known"); self.assertNotIn("processes",result)
