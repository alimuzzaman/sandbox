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

    def test_credentials_move_as_bytes_not_environment_or_argv_values(self):
        from sandbox.isolation.credentials import CredentialInjector
        writes, installs = [], []
        injector = CredentialInjector(
            secret_provider=lambda ref: b"secret-bytes",
            staging_writer=lambda machine, name, value:
                writes.append((machine, name, value)) or f"/staging/{machine}/{name}",
            installer=lambda machine, digest, name, path:
                installs.append((machine, digest, name, path)),
        )
        result = injector.install(machine_id="sb-0123456789ab", policy_digest="a" * 64,
                                  references=("native/sb/db-credential",))
        self.assertEqual(writes[0][2], b"secret-bytes")
        self.assertNotIn("secret-bytes", repr(installs))
        self.assertEqual(result[0]["container_path"],
                         "/run/credentials/sandbox/db-credential")


if __name__ == "__main__": unittest.main()
