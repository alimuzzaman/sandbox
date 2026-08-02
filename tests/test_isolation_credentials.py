from pathlib import Path
import tempfile
import unittest


class TestIsolationCredentials(unittest.TestCase):

    def test_machine_local_store_is_owner_only_stable_and_reference_hashed(self):
        from sandbox.isolation.credentials import NativeCredentialStore
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "credentials"
            store = NativeCredentialStore(root)
            reference = "native/sb-0123456789ab/db-credential"
            first = store(reference); second = store(reference)
            self.assertEqual(first, second)
            self.assertNotIn(reference, [path.name for path in root.iterdir()])
            entries = [path for path in root.iterdir() if path.name != ".lock"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
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
        for reference in ("password=leak", "native/../password", "native//password",
                          "native/password\n", "UPPERCASE"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                sanitize_execution_context({}, (reference,))

    def test_default_staging_is_owner_only_no_follow_and_always_cleans_up(self):
        from sandbox.isolation.credentials import CredentialInjector
        secret = b"secret-bytes"
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "injected"
            injector = CredentialInjector(
                secret_provider=lambda _reference: secret,
                installer=lambda machine, digest, name, path: calls.append(
                    (machine, digest, name, path, Path(path).read_bytes())),
                injected_root=root,
            )
            result = injector.install(
                machine_id="sb-0123456789ab", policy_digest="a" * 64,
                references=("native/sb/db-credential",),
            )
            staged = root / "sb-0123456789ab" / "db-credential"
            self.assertFalse(staged.exists())
            self.assertEqual((root.stat().st_mode & 0o777, (root / "sb-0123456789ab").stat().st_mode & 0o777),
                             (0o700, 0o700))
        self.assertEqual(calls[0][4], secret)
        self.assertNotIn(secret.decode(), repr(calls[0][:4]))
        self.assertEqual(result[0]["container_path"], "/run/credentials/sandbox/db-credential")

    def test_cleanup_runs_when_the_installer_fails_without_secret_transport_leakage(self):
        from sandbox.isolation.credentials import CredentialInjector
        secret = b"secret-bytes"
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "injected"

            def failing_installer(*argv):
                calls.append(argv)
                raise RuntimeError("installer failed")

            injector = CredentialInjector(
                secret_provider=lambda _reference: secret, installer=failing_installer,
                injected_root=root,
            )
            with self.assertRaisesRegex(RuntimeError, "installer failed"):
                injector.install(
                    machine_id="sb-0123456789ab", policy_digest="a" * 64,
                    references=("native/sb/db-credential",),
                )
            self.assertFalse((root / "sb-0123456789ab" / "db-credential").exists())
        self.assertNotIn(secret.decode(), repr(calls))

    def test_invalid_identity_or_duplicate_name_is_rejected_before_provider_or_writer(self):
        from sandbox.isolation.credentials import CredentialInjector
        calls = []
        injector = CredentialInjector(
            secret_provider=lambda reference: calls.append(("provider", reference)) or b"secret",
            staging_writer=lambda *args: calls.append(("writer", args)) or "/unused",
            installer=lambda *args: calls.append(("installer", args)),
            staging_cleanup=lambda *args: calls.append(("cleanup", args)),
        )
        invalid = (
            {"machine_id": "sb-not-hex", "policy_digest": "a" * 64,
             "references": ("native/db",)},
            {"machine_id": "sb-0123456789ab", "policy_digest": "invalid",
             "references": ("native/db",)},
            {"machine_id": "sb-0123456789ab", "policy_digest": "a" * 64,
             "references": ("one/db", "two/db")},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                injector.install(**kwargs)
        self.assertEqual(calls, [])

    def test_helper_installer_uses_only_fixed_credential_verb_without_staged_path(self):
        from sandbox.isolation.credentials import HelperCredentialInstaller

        calls = []

        class Process:
            def run(self, argv, **kwargs):
                calls.append((argv, kwargs))
                return type("Result", (), {"returncode": 0, "stdout": "secret-bytes"})()

        machine = "sb-0123456789ab"
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "injected"
            path = root / machine / "db-credential"
            installer = HelperCredentialInstaller(
                process=Process(), helper="/fixed/native-helper", injected_root=root,
            )
            installer.install(machine, digest, "db-credential", path)

        self.assertEqual(calls, [(
            ("sudo", "-n", "/fixed/native-helper", "credential-install", machine,
             digest, "db-credential"),
            {"timeout": 120},
        )])
        self.assertNotIn("secret-bytes", repr(calls))
        self.assertNotIn(str(path), repr(calls))

    def test_helper_installer_rejects_unfixed_path_and_discards_helper_output_on_failure(self):
        from sandbox.isolation.credentials import HelperCredentialInstaller

        calls = []

        class Process:
            def run(self, argv, **kwargs):
                calls.append((argv, kwargs))
                return type("Result", (), {"returncode": 17, "stdout": "secret-bytes"})()

        machine = "sb-0123456789ab"
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "injected"
            installer = HelperCredentialInstaller(
                process=Process(), helper="/fixed/native-helper", injected_root=root,
            )
            with self.assertRaises(ValueError):
                installer.install(machine, digest, "db-credential", "/tmp/credential")
            self.assertEqual(calls, [])
            with self.assertRaisesRegex(RuntimeError, "managed credential installation failed"):
                installer.install(machine, digest, "db-credential",
                                  root / machine / "db-credential")
        self.assertNotIn("secret-bytes", repr(calls))


if __name__ == "__main__":
    unittest.main()
