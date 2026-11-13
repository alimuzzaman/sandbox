"""The fixed nested apply argv keeps the caller's explicit apply intent."""
from argparse import Namespace
import unittest

from sandbox.commands import hosting


class HostApplyArgvTests(unittest.TestCase):
    validated = {'project_root': '/staged/root', 'environment': 'sandbox'}

    def argv(self, **fields):
        return hosting._host_apply_argv(self.validated, 'box', Namespace(**fields))

    def test_generate_is_forwarded_with_its_real_flag(self):
        argv = self.argv(generate_secrets=True, json=True)
        self.assertEqual(argv.count('--generate'), 1)
        self.assertNotIn('--generate-secrets', argv)
        self.assertEqual(argv[:8], ['host', 'apply', '--project-dir', '/staged/root',
                                    '--remote', 'box', '--environment', 'sandbox'])

    def test_generate_is_absent_unless_requested(self):
        self.assertNotIn('--generate', self.argv(generate_secrets=False))
        self.assertNotIn('--generate', self.argv())
        self.assertNotIn('--generate', hosting._host_apply_argv(self.validated, 'box', None))


if __name__ == '__main__':
    unittest.main()
