import base64
import shlex
import unittest
import zlib
from pathlib import Path

from sandbox.hosting import login_service as login
from sandbox.hosting import owner_panel_gate

# Linux MAX_ARG_STRLEN: one argv string longer than this makes execve fail
# with E2BIG, so the gate never starts and every site verb exits 1 silently.
MAX_ARG_STRLEN = 131072


class RuntimeArgvLimitTest(unittest.TestCase):
    def test_runtime_argument_stays_well_under_the_kernel_limit(self):
        self.assertLess(len(login._runtime_source()), MAX_ARG_STRLEN // 2)
        for argument in shlex.split(login.site_command("docker compose", "list", "share")):
            self.assertLess(len(argument.encode()), MAX_ARG_STRLEN)

    def test_both_loaders_decode_exactly_the_runtime_file(self):
        runtime = (Path(login.__file__).with_name("runtime.py")).read_bytes()
        self.assertEqual(zlib.decompress(base64.b64decode(login._runtime_source())), runtime)
        for loader in (login._LOADER, owner_panel_gate._LOADER):
            self.assertIn("zlib.decompress(base64.b64decode(sys.argv[1]))", loader)


if __name__ == "__main__":
    unittest.main()
