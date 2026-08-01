import unittest


class TestIsolationCredentials(unittest.TestCase):
    def test_environment_fds_credentials_and_control_sockets_fail_closed(self):
        from sandbox.isolation.credentials import sanitize_execution_context
        result = sanitize_execution_context(
            {"PATH": "/usr/bin", "AWS_SECRET_ACCESS_KEY": "leak", "SSH_AUTH_SOCK": "/sock"},
            ("db-password-ref",),
        )
        self.assertEqual(result["environment"], {"PATH": "/usr/bin"})
        self.assertEqual(result["close_fds_from"], 3)
        self.assertEqual(result["pass_fds"], ())
        self.assertEqual(result["control_sockets"], ())
        with self.assertRaises(ValueError):
            sanitize_execution_context({}, ("password=leak",))


if __name__ == "__main__": unittest.main()
