"""Unit tests for remote VPS hosting (specs/014-remote-vps-hosting/).

Stdlib `unittest` only, no docker, no real SSH/VPS -- pure config-read/write,
SSH/git command-construction, and deploy-mechanism logic. Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v

Per Constitution Principle IV, this unit coverage is NOT proof of done on its
own -- see specs/014-remote-vps-hosting/quickstart.md for the required
live-verification pass against a real VPS.
"""
import json
import importlib.util
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._remote as sr  # noqa: E402
import sandbox.core._config as _cfgmod  # noqa: E402
import sandbox.commands.remote as remote_cmd  # noqa: E402
import sandbox.commands.deploy as deploy_cmd  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                        stdout=stdout, stderr=stderr)


class _patched_config_local:
    """sandbox/core/__init__.py back-fills CONFIG_LOCAL into every submodule's
    OWN namespace (see its module docstring) -- _remote.py's write path and
    _config.py's _local_yaml() read path each resolve their OWN separate
    binding, even though both started out pointing at the same object. A
    single patch.object on just one module leaves the other reading/writing
    the REAL sandbox.local.yml. Patch both together so reads and writes in a
    test agree on the same temp path."""
    def __init__(self, path):
        self._patches = [
            patch.object(sr, "CONFIG_LOCAL", path),
            patch.object(_cfgmod, "CONFIG_LOCAL", path),
        ]

    def __enter__(self):
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


class TestRemoteBlockConfig(unittest.TestCase):
    def test_round_trip_preserves_unrelated_config(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            local_yml.write_text("licensing:\n  elementor_pro_key: keep-me\n")
            with _patched_config_local(local_yml):
                sr._write_remote_block({"myvps": {"ssh": "ubuntu@1.2.3.4"}})
                block = sr._remote_block()
                self.assertEqual(block, {"myvps": {"ssh": "ubuntu@1.2.3.4"}})
                # unrelated section untouched
                import yaml
                raw = yaml.safe_load(local_yml.read_text())
                self.assertEqual(raw["licensing"]["elementor_pro_key"], "keep-me")

    def test_empty_block_removes_the_key_entirely(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            with _patched_config_local(local_yml):
                sr._write_remote_block({"myvps": {"ssh": "x"}})
                sr._write_remote_block({})
                import yaml
                raw = yaml.safe_load(local_yml.read_text()) or {}
                self.assertNotIn("remotes", raw)

    def test_put_remote_is_idempotent_on_reregister(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            with _patched_config_local(local_yml):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=False)
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=False)
                self.assertEqual(len(sr.list_remotes()), 1)
                self.assertEqual(sr.get_remote("myvps")["ssh"], "ubuntu@1.2.3.4")

    def test_put_remote_updates_only_given_fields(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            with _patched_config_local(local_yml):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=False)
                sr.put_remote("myvps", provisioned=True)
                entry = sr.get_remote("myvps")
                self.assertEqual(entry["ssh"], "ubuntu@1.2.3.4")
                self.assertTrue(entry["provisioned"])

    def test_remove_remote_is_local_only(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            with _patched_config_local(local_yml):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                existed = sr.remove_remote("myvps")
                self.assertTrue(existed)
                self.assertIsNone(sr.get_remote("myvps"))
                # removing again is a no-op, not an error
                self.assertFalse(sr.remove_remote("myvps"))


class TestValidateRemoteName(unittest.TestCase):
    def test_valid_names_pass(self):
        for name in ["myvps", "my-vps", "my_vps", "vps1"]:
            self.assertEqual(sr.validate_remote_name(name), name)

    def test_invalid_names_raise(self):
        for name in ["My VPS", "vps!", "", "  ", "VPS"]:
            with self.assertRaises(ValueError):
                sr.validate_remote_name(name)


class TestSshRun(unittest.TestCase):
    def test_raises_when_no_ssh_configured(self):
        with self.assertRaises(ValueError):
            sr.ssh_run({}, "true")

    @patch("subprocess.run")
    def test_builds_expected_ssh_command(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        sr.ssh_run({"ssh": "ubuntu@1.2.3.4"}, "true", timeout=10)
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "ssh")
        self.assertIn("ubuntu@1.2.3.4", args)
        self.assertIn("true", args)

    @patch("subprocess.run")
    def test_builds_expected_ssh_command_with_custom_port(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        sr.ssh_run({"ssh": "ubuntu@1.2.3.4:2222"}, "true", timeout=10)
        args = mock_run.call_args[0][0]
        self.assertIn("-p", args)
        self.assertIn("2222", args)
        self.assertIn("ubuntu@1.2.3.4", args)

    def test_parses_ssh_url_with_custom_port(self):
        parts = sr.remote_ssh_parts("ssh://ubuntu@1.2.3.4:2222")
        self.assertEqual(parts["target"], "ubuntu@1.2.3.4")
        self.assertEqual(parts["host"], "1.2.3.4")
        self.assertEqual(parts["port"], 2222)

    def test_redacts_ssh_target_from_user_visible_error(self):
        error = sr.redact_ssh_connection(
            "ssh://ubuntu@1.2.3.4:2222: connection refused",
            {"ssh": "ubuntu@1.2.3.4:2222"},
        )
        self.assertNotIn("ubuntu@1.2.3.4", error)
        self.assertIn("[redacted SSH target]", error)


class TestCheckReachable(unittest.TestCase):
    @patch("subprocess.run")
    def test_true_on_zero_exit(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        self.assertTrue(sr.check_reachable({"ssh": "ubuntu@1.2.3.4"}))

    @patch("subprocess.run")
    def test_false_on_nonzero_exit(self, mock_run):
        mock_run.return_value = _completed(returncode=255, stderr="Connection refused")
        self.assertFalse(sr.check_reachable({"ssh": "ubuntu@1.2.3.4"}))

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10))
    def test_false_on_timeout(self, mock_run):
        self.assertFalse(sr.check_reachable({"ssh": "ubuntu@1.2.3.4"}))

    def test_false_on_missing_ssh_config(self):
        self.assertFalse(sr.check_reachable({}))


class TestDeployTargetPath(unittest.TestCase):
    @patch("subprocess.run")
    def test_resolves_using_project_slug_and_remote_sandbox_home(self, mock_run):
        mock_run.return_value = _completed(returncode=0, stdout="/home/ubuntu/sandbox\n")
        path = sr.deploy_target_path({"ssh": "ubuntu@1.2.3.4"}, "/local/path/my-plugin")
        self.assertEqual(path, "/home/ubuntu/sandbox/deploy-src/my-plugin")

    @patch("subprocess.run")
    def test_raises_when_sandbox_home_unresolvable(self, mock_run):
        mock_run.return_value = _completed(returncode=1, stderr="connection refused")
        with self.assertRaises(RuntimeError):
            sr.deploy_target_path({"ssh": "ubuntu@1.2.3.4"}, "/local/path/my-plugin")


class TestPushCommits(unittest.TestCase):
    @patch("subprocess.run")
    def test_pushes_head_to_the_correct_branch_and_url(self, mock_run):
        # first call: git push; second call: git rev-parse HEAD
        mock_run.side_effect = [
            _completed(returncode=0),
            _completed(returncode=0, stdout="abc1234\n"),
        ]
        sha = sr.push_commits({"ssh": "ubuntu@1.2.3.4"}, "/local/proj",
                               "/home/ubuntu/sandbox/deploy-src/proj", "main")
        self.assertEqual(sha, "abc1234")
        push_args = mock_run.call_args_list[0][0][0]
        self.assertEqual(push_args[0], "git")
        self.assertEqual(push_args[1], "push")
        self.assertIn("ssh://ubuntu@1.2.3.4/home/ubuntu/sandbox/deploy-src/proj", push_args)
        self.assertIn("HEAD:refs/heads/main", push_args)

    @patch("subprocess.run")
    def test_push_url_preserves_custom_ssh_port(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0),
            _completed(returncode=0, stdout="abc1234\n"),
        ]
        sr.push_commits({"ssh": "ubuntu@1.2.3.4:2222"}, "/local/proj",
                         "/home/ubuntu/sandbox/deploy-src/proj", "main")
        push_args = mock_run.call_args_list[0][0][0]
        self.assertIn("ssh://ubuntu@1.2.3.4:2222/home/ubuntu/sandbox/deploy-src/proj",
                      push_args)

    @patch("subprocess.run")
    def test_raises_on_push_failure(self, mock_run):
        mock_run.return_value = _completed(returncode=1, stderr="rejected")
        with self.assertRaises(RuntimeError):
            sr.push_commits({"ssh": "ubuntu@1.2.3.4"}, "/local/proj",
                             "/home/ubuntu/sandbox/deploy-src/proj", "main")

    @patch("subprocess.run")
    def test_never_references_origin_or_any_other_remote(self, mock_run):
        # Spec FR-008: deploy must succeed even for a branch never pushed to
        # GitHub/origin. Guards against a future change accidentally routing
        # through the project's OWN git remotes instead of pushing straight
        # to the VPS's deploy-target path.
        mock_run.side_effect = [
            _completed(returncode=0),
            _completed(returncode=0, stdout="abc1234\n"),
        ]
        sr.push_commits({"ssh": "ubuntu@1.2.3.4"}, "/local/proj",
                         "/home/ubuntu/sandbox/deploy-src/proj", "wip-branch")
        push_args = mock_run.call_args_list[0][0][0]
        self.assertNotIn("origin", push_args)


class TestCaptureAndApplyUncommitted(unittest.TestCase):
    @patch("subprocess.run")
    def test_capture_returns_diff_and_untracked_files(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout="diff --git a/f.php b/f.php\n+x\n"),
            _completed(returncode=0, stdout="M  f.php\n?? new-file.txt\n?? sub/new2.txt\n"),
        ]
        diff_text, untracked = sr.capture_uncommitted("/local/proj")
        self.assertIn("diff --git", diff_text)
        self.assertEqual(untracked, ["new-file.txt", "sub/new2.txt"])

    @patch("subprocess.run")
    def test_uses_untracked_files_all_so_nested_new_files_are_not_collapsed(self, mock_run):
        # Real bug caught only by live-verifying against an actual remote (a
        # mocked porcelain string can't reveal this -- the mock has to assume
        # the shape it's testing): plain `git status --porcelain` collapses a
        # brand-new untracked DIRECTORY to just its directory name (`subdir/`)
        # rather than listing files inside it. Without `--untracked-files=all`,
        # apply_uncommitted's `local_path.is_file()` check would silently skip
        # every file inside a new untracked directory (a directory is never
        # a file), and it would never transfer.
        mock_run.side_effect = [
            _completed(returncode=0, stdout=""),
            _completed(returncode=0, stdout="?? subdir/nested.txt\n"),
        ]
        sr.capture_uncommitted("/local/proj")
        status_call_args = mock_run.call_args_list[1][0][0]
        self.assertIn("--untracked-files=all", status_call_args)

    @patch("subprocess.run")
    def test_replace_not_stack_resets_before_applying(self, mock_run):
        # Verifies the ORDER: reset_target_to must run (and succeed) before
        # apply_uncommitted's diff-apply step -- this is what makes a second
        # deploy replace rather than stack (spec FR-007).
        mock_run.return_value = _completed(returncode=0)
        calls = []

        def record(*args, **kwargs):
            calls.append(args[0])
            return _completed(returncode=0)

        mock_run.side_effect = record
        entry = {"ssh": "ubuntu@1.2.3.4"}
        sr.reset_target_to(entry, "/home/ubuntu/sandbox/deploy-src/proj", "abc1234")
        with patch("sandbox.core._remote.ssh_run", return_value=_completed(returncode=0)):
            sr.apply_uncommitted(entry, "/home/ubuntu/sandbox/deploy-src/proj",
                                  "/local/proj", "diff --git a/f b/f\n+x\n", [])
        # reset happened via ssh_run (subprocess.run under the hood) before apply
        reset_call = [c for c in calls if "git reset --hard abc1234" in " ".join(c)]
        self.assertTrue(reset_call, "expected a git reset --hard call")

    @patch("subprocess.run")
    def test_reset_also_removes_untracked_files_left_by_a_prior_deploy(self, mock_run):
        # Real bug caught by /speckit-analyze: `git reset --hard` alone only
        # rewinds TRACKED files -- it does nothing about an untracked file a
        # PREVIOUS deploy transferred. Without also cleaning those, a file
        # added in deploy #1 and later deleted locally would survive on the
        # VPS forever, breaking the "replace, not stack" guarantee (FR-007)
        # for exactly that class of file.
        mock_run.return_value = _completed(returncode=0)
        sr.reset_target_to({"ssh": "ubuntu@1.2.3.4"},
                            "/home/ubuntu/sandbox/deploy-src/proj", "abc1234")
        cmd_arg = mock_run.call_args[0][0]
        joined = " ".join(cmd_arg)
        self.assertIn("git reset --hard abc1234", joined)
        self.assertIn("git clean -fd", joined)
        # order matters: reset must come before clean
        self.assertLess(joined.index("git reset --hard"), joined.index("git clean -fd"))

    @patch("sandbox.core._remote.ssh_run")
    @patch("subprocess.run")
    def test_apply_counts_tracked_and_untracked_files(self, mock_run, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        # subprocess.run is used for: git diff --name-only, git diff deleted,
        # and scp for each dirty file. mkdir-parent goes through ssh_run,
        # mocked separately.
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "a.php").write_text("x")
            (proj / "b.php").write_text("y")
            mock_run.side_effect = [
                _completed(returncode=0, stdout="a.php\n"),  # git diff --name-only
                _completed(returncode=0, stdout=""),           # git diff deleted
                _completed(returncode=0),                      # scp a.php
                _completed(returncode=0),                      # scp b.php
            ]
            applied = sr.apply_uncommitted(
                {"ssh": "ubuntu@1.2.3.4"}, "/home/ubuntu/sandbox/deploy-src/proj",
                str(proj), "diff --git a/x b/x\n+y\n", ["b.php"],
            )
            self.assertEqual(applied, 2)  # 1 tracked-diff file + 1 untracked file
            self.assertIn("a.php", mock_run.call_args_list[2][0][0][-1])
            self.assertIn("b.php", mock_run.call_args_list[3][0][0][-1])

    @patch("sandbox.core._remote.ssh_run")
    @patch("subprocess.run")
    def test_scp_uses_custom_ssh_port(self, mock_run, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "b.php").write_text("y")
            mock_run.return_value = _completed(returncode=0)
            sr.apply_uncommitted(
                {"ssh": "ubuntu@1.2.3.4:2222"},
                "/home/ubuntu/sandbox/deploy-src/proj",
                str(proj), "", ["b.php"],
            )
            scp_args = mock_run.call_args[0][0]
            self.assertIn("-P", scp_args)
            self.assertIn("2222", scp_args)
            self.assertIn("ubuntu@1.2.3.4:/home/ubuntu/sandbox/deploy-src/proj/b.php",
                          scp_args)

    @patch("sandbox.core._remote.ssh_run")
    @patch("subprocess.run")
    def test_apply_removes_deleted_tracked_files(self, mock_run, mock_ssh_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout="gone.php\n"),  # git diff --name-only
            _completed(returncode=0, stdout="gone.php\n"),  # git diff deleted
        ]
        mock_ssh_run.return_value = _completed(returncode=0)
        applied = sr.apply_uncommitted(
            {"ssh": "ubuntu@1.2.3.4"}, "/home/ubuntu/sandbox/deploy-src/proj",
            "/local/proj", "diff --git a/gone.php b/gone.php\n", [],
        )
        self.assertEqual(applied, 1)
        rm_cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("rm -f --", rm_cmd)
        self.assertIn("gone.php", rm_cmd)

    @patch("sandbox.core._remote.ssh_run")
    @patch("subprocess.run")
    def test_missing_untracked_file_is_skipped_not_erroring(self, mock_run, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        mock_run.return_value = _completed(returncode=0)
        applied = sr.apply_uncommitted(
            {"ssh": "ubuntu@1.2.3.4"}, "/home/ubuntu/sandbox/deploy-src/proj",
            "/nonexistent/local/proj", "", ["does-not-exist.txt"],
        )
        self.assertEqual(applied, 0)


class TestCurrentBranch(unittest.TestCase):
    @patch("subprocess.run")
    def test_returns_branch_name(self, mock_run):
        mock_run.return_value = _completed(returncode=0, stdout="main\n")
        self.assertEqual(sr.current_branch("/local/proj"), "main")

    @patch("subprocess.run")
    def test_raises_on_detached_head(self, mock_run):
        mock_run.return_value = _completed(returncode=0, stdout="HEAD\n")
        with self.assertRaises(RuntimeError):
            sr.current_branch("/local/proj")


class TestCmdRemoteAdd(unittest.TestCase):
    def test_add_requires_ssh_url(self):
        args = MagicMock(name="myvps", ssh_url=None, json=False)
        args.name = "myvps"
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                with self.assertRaises(SystemExit):
                    remote_cmd._cmd_add(args, as_json=False)

    def test_add_registers_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock()
                args.name = "myvps"
                args.ssh_url = "ssh://ubuntu@1.2.3.4"
                remote_cmd._cmd_add(args, as_json=False)
                remote_cmd._cmd_add(args, as_json=False)
                self.assertEqual(len(sr.list_remotes()), 1)
                self.assertEqual(sr.get_remote("myvps")["ssh"], "ubuntu@1.2.3.4")

    def test_add_preserves_custom_ssh_port_in_normalized_form(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock()
                args.name = "myvps"
                args.ssh_url = "ssh://ubuntu@1.2.3.4:2222"
                remote_cmd._cmd_add(args, as_json=False)
                self.assertEqual(sr.get_remote("myvps")["ssh"], "ubuntu@1.2.3.4:2222")

    def test_add_json_does_not_return_ssh_target(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock(ssh_url="ssh://ubuntu@1.2.3.4", json=True)
                args.name = "myvps"
                with patch("builtins.print") as mock_print:
                    remote_cmd._cmd_add(args, as_json=True)
                output = mock_print.call_args[0][0]
                self.assertNotIn("ubuntu@1.2.3.4", output)
                self.assertTrue(json.loads(output)["ssh_configured"])

    def test_list_never_returns_ssh_target(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                with patch.object(sr, "check_reachable", return_value=True), \
                     patch("builtins.print") as mock_print:
                    remote_cmd._cmd_list(args, as_json=True)
                output = mock_print.call_args[0][0]
                self.assertNotIn("ubuntu@1.2.3.4", output)
                self.assertTrue(json.loads(output)["remotes"][0]["ssh_configured"])


class TestCmdRemoteRemove(unittest.TestCase):
    def test_remove_never_calls_ssh(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = MagicMock()
                args.name = "myvps"
                with patch("subprocess.run") as mock_run:
                    remote_cmd._cmd_remove(args, as_json=False)
                    mock_run.assert_not_called()
                self.assertIsNone(sr.get_remote("myvps"))


class TestUploadRuntimeSource(unittest.TestCase):
    @patch("subprocess.run")
    def test_streams_this_checkout_to_remote_sandbox_home(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout=b"tarball", stderr=b""),
            _completed(returncode=0, stdout=b"", stderr=b""),
        ]
        remote_cmd._upload_runtime_source("ubuntu@1.2.3.4")

        tar_args = mock_run.call_args_list[0][0][0]
        self.assertEqual(tar_args[0], "tar")
        self.assertIn("--exclude", tar_args)
        self.assertIn(".git", tar_args)
        self.assertIn(".cli-venv", tar_args)
        self.assertIn("mcp/wp-server/.venv", tar_args)
        self.assertIn("runtime", tar_args)
        self.assertEqual(mock_run.call_args_list[0][1]["cwd"], str(ROOT))

        ssh_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(ssh_args[0], "ssh")
        self.assertIn("ubuntu@1.2.3.4", ssh_args)
        self.assertIn("sb-src", ssh_args[-1])
        self.assertEqual(mock_run.call_args_list[1][1]["input"], b"tarball")
        self.assertFalse(mock_run.call_args_list[1][1]["text"])

    @patch("subprocess.run")
    def test_upload_runtime_source_uses_custom_ssh_port(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout=b"tarball", stderr=b""),
            _completed(returncode=0, stdout=b"", stderr=b""),
        ]
        remote_cmd._upload_runtime_source("ubuntu@1.2.3.4:2222")
        ssh_args = mock_run.call_args_list[1][0][0]
        self.assertIn("-p", ssh_args)
        self.assertIn("2222", ssh_args)

    @patch("subprocess.run")
    def test_raises_when_tar_fails_before_ssh(self, mock_run):
        mock_run.return_value = _completed(
            returncode=2, stdout=b"", stderr=b"tar failed"
        )
        with self.assertRaisesRegex(RuntimeError, "could not package"):
            remote_cmd._upload_runtime_source("ubuntu@1.2.3.4")
        self.assertEqual(mock_run.call_count, 1)


class TestCmdRemoteProvisionSurfacesTheToken(unittest.TestCase):
    # Real bug caught by /speckit-analyze: cmd_remote_provision's own
    # success message claimed "bearer token minted above" while never
    # actually printing it anywhere -- there was no way to complete the
    # second-MCP-server registration described in docs/remote-hosting.md.
    def test_provision_result_includes_the_minted_token(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = MagicMock()
                args.name = "myvps"
                args.control = "https"
                args.control_host = "sandbox.example.com"
                with patch("subprocess.run", return_value=_completed(returncode=0)), \
                     patch.object(sr, "configure_https_proxy"), \
                     patch.object(sr, "start_remote_mcp_server"), \
                     patch("builtins.print") as mock_print:
                    remote_cmd._cmd_provision(args, as_json=True)
                printed = mock_print.call_args[0][0]
                result = json.loads(printed)
                self.assertTrue(result["bearer_token"])
                self.assertEqual(len(result["bearer_token"]), 64)  # secrets.token_hex(32)
                self.assertEqual(result["control_transport"], "https")
                self.assertEqual(result["control_url"], "https://sandbox.example.com")

    def test_provision_can_explicitly_use_tailscale(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = MagicMock()
                args.name = "myvps"
                args.control = "tailscale"
                args.control_host = None
                with patch("subprocess.run", return_value=_completed(returncode=0)) as mock_run, \
                     patch.object(sr, "resolve_tailscale_ip", return_value="100.64.1.2"), \
                     patch.object(sr, "start_remote_mcp_server"), \
                     patch("builtins.print") as mock_print:
                    remote_cmd._cmd_provision(args, as_json=True)
                provision_ssh_cmd = mock_run.call_args_list[2][0][0][-1]
                self.assertIn("SANDBOX_CONTROL_TRANSPORT=tailscale", provision_ssh_cmd)
                result = json.loads(mock_print.call_args[0][0])
                self.assertEqual(result["control_transport"], "tailscale")
                self.assertEqual(result["control_url"], "http://100.64.1.2:9174")

    def test_https_json_provision_requires_control_host(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = MagicMock()
                args.name = "myvps"
                args.control = "https"
                args.control_host = None
                with self.assertRaises(SystemExit):
                    remote_cmd._cmd_provision(args, as_json=True)


class TestStartRemoteMcpServer(unittest.TestCase):
    @patch("sandbox.core._remote.ssh_run")
    def test_start_remote_mcp_server_defaults_sandbox_home(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        entry = {"ssh": "ubuntu@1.2.3.4"}
        sr.start_remote_mcp_server(entry, "100.64.1.2", 9174, "token123")
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("sandbox_home=${SANDBOX_HOME:-$HOME/sandbox}", cmd)
        self.assertIn("mkdir -p \"$sandbox_home/sb-src\"", cmd)
        self.assertIn("cd \"$sandbox_home/sb-src\"", cmd)

    @patch("sandbox.core._remote.ssh_run")
    def test_start_remote_mcp_server_passes_public_url(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        entry = {"ssh": "ubuntu@1.2.3.4"}
        sr.start_remote_mcp_server(
            entry, "127.0.0.1", 9174, "token123",
            public_url="https://sandbox.example.com",
        )
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("--bind 127.0.0.1", cmd)
        self.assertIn("--public-url https://sandbox.example.com", cmd)
        self.assertIn("</dev/null", cmd)

    @patch("sandbox.core._remote.ssh_run")
    def test_start_remote_mcp_server_timeout_is_redacted(self, mock_ssh_run):
        mock_ssh_run.side_effect = [
            _completed(returncode=0),
            subprocess.TimeoutExpired(cmd="ssh", timeout=30),
        ]
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            sr.start_remote_mcp_server(
                {"ssh": "ubuntu@1.2.3.4"}, "127.0.0.1", 9174,
                "secret-token", public_url="https://sandbox.example.com",
            )


class TestConfigureHttpsProxy(unittest.TestCase):
    @patch("sandbox.core._remote.ssh_run")
    def test_configures_caddy_virtual_host(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        sr.configure_https_proxy({"ssh": "ubuntu@1.2.3.4"}, "sandbox.example.com", 9174)
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("apt-get install -y caddy", cmd)
        self.assertIn("reverse_proxy 127.0.0.1:9174", cmd)
        self.assertIn("/etc/caddy/conf.d/sandbox-mcp-sandbox.example.com.caddy", cmd)
        self.assertIn("import /etc/caddy/conf.d/*.caddy", cmd)

    def test_rejects_non_hostname(self):
        with self.assertRaises(ValueError):
            sr.configure_https_proxy({"ssh": "ubuntu@1.2.3.4"}, "bad/host", 9174)

    @patch("sandbox.core._remote.ssh_run")
    def test_instance_route_bootstraps_caddy_like_control_proxy(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        sr.configure_instance_https_route(
            {"ssh": "ubuntu@1.2.3.4"}, "default-demo.sandbox.asb.bd", 8188
        )
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("apt-get install -y caddy", cmd)
        self.assertIn("import /etc/caddy/conf.d/*.caddy", cmd)
        self.assertIn("systemctl enable --now caddy", cmd)
        self.assertIn("reverse_proxy 127.0.0.1:8188", cmd)
        self.assertIn(
            "/etc/caddy/conf.d/sandbox-instance-default-demo.sandbox.asb.bd.caddy",
            cmd,
        )

    @patch("sandbox.core._remote.ssh_run")
    def test_removes_only_the_named_instance_route(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        sr.remove_instance_https_route(
            {"ssh": "ubuntu@1.2.3.4"}, "preview-demo.sandbox.asb.bd"
        )
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("rm -f /etc/caddy/conf.d/sandbox-instance-preview-demo.sandbox.asb.bd.caddy", cmd)
        self.assertIn("caddy validate", cmd)


class TestRemotePreviewInstances(unittest.TestCase):
    @patch("sandbox.core._remote.ssh_run")
    def test_ensure_remote_instance_uses_new_label(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(stdout='{"instance":"preview-a","wordpress_port":8123}\n')
        result = sr.ensure_remote_instance({"ssh": "ubuntu@1.2.3.4"}, "/srv/project", "preview-a")
        self.assertEqual(result["instance"], "preview-a")
        self.assertIn("--label preview-a --create", mock_ssh_run.call_args[0][1])

    @patch("sandbox.core._remote.ssh_run")
    def test_delete_remote_instance_is_scoped_to_name(self, mock_ssh_run):
        mock_ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(returncode=0)]
        sr.delete_remote_instance({"ssh": "ubuntu@1.2.3.4"}, "preview-a")
        self.assertIn("instance delete preview-a --yes", mock_ssh_run.call_args[0][1])


class TestStopRemoteMcpServer(unittest.TestCase):
    @patch("sandbox.core._remote.ssh_run")
    def test_stop_kills_pidfile_and_stale_streamable_http_processes(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        sr.stop_remote_mcp_server({"ssh": "ubuntu@1.2.3.4"})
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("/tmp/sandbox-mcp-remote.pid", cmd)
        self.assertIn("/proc", cmd)
        self.assertIn("streamable-http", cmd)
        self.assertIn("--token", cmd)


class TestDeployRequiresProvisionedRemote(unittest.TestCase):
    def test_deploy_to_unprovisioned_remote_dies(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=False)
                args = MagicMock()
                args.project_dir = d
                args.remote = "myvps"
                args.json = False
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                   return_value={"root": d, "slug": "proj"}):
                    with self.assertRaises(SystemExit):
                        deploy_cmd.cmd_deploy(None, args)

    def test_deploy_to_unregistered_remote_dies(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock()
                args.project_dir = d
                args.remote = "does-not-exist"
                args.json = False
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                   return_value={"root": d, "slug": "proj"}):
                    with self.assertRaises(SystemExit):
                        deploy_cmd.cmd_deploy(None, args)

    def test_json_deploy_to_unregistered_remote_returns_json(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock()
                args.project_dir = d
                args.remote = "does-not-exist"
                args.json = True
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                  return_value={"root": d, "slug": "proj"}), \
                     patch("builtins.print") as mock_print:
                    with self.assertRaises(SystemExit):
                        deploy_cmd.cmd_deploy(None, args)
                result = json.loads(mock_print.call_args[0][0])
                self.assertFalse(result["ok"])
                self.assertEqual(result["remote"], "does-not-exist")
                self.assertIn("no remote named", result["error"])


class TestRejectHerdProjects(unittest.TestCase):
    def test_herd_configured_project_raises(self):
        with self.assertRaises(ValueError):
            sr.reject_herd_projects({"server": "herd"})

    def test_non_herd_project_is_fine(self):
        sr.reject_herd_projects({"server": "nginx"})  # does not raise
        sr.reject_herd_projects({})  # missing server key does not raise

    def test_deploy_to_herd_project_dies_before_touching_the_remote(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                args.project_dir = d
                args.remote = "myvps"
                args.json = False
                sc = deploy_cmd._core()
                with patch.object(
                    sc, "load_project_config",
                    return_value={"root": d, "slug": "proj", "server": "herd"},
                ):
                    with patch("subprocess.run") as mock_run:
                        with self.assertRaises(SystemExit):
                            deploy_cmd.cmd_deploy(None, args)
                        mock_run.assert_not_called()


class TestDeployEnsureExpose(unittest.TestCase):
    def test_default_instance_domain_uses_hyphenated_label_and_slug(self):
        self.assertEqual(
            sr.default_instance_domain("default", "templately.ai.builder"),
            "default-templately-ai-builder.sandbox.asb.bd",
        )
        self.assertEqual(
            sr.default_instance_domain("!!!", "!!!"),
            "default-project.sandbox.asb.bd",
        )

    def test_rewrite_instance_url_preserves_autologin_query(self):
        self.assertEqual(
            sr.rewrite_instance_url(
                "http://localhost:8188/?sandbox_autologin=abc123",
                "https://default-demo.sandbox.asb.bd",
            ),
            "https://default-demo.sandbox.asb.bd/?sandbox_autologin=abc123",
        )

    def test_deploy_can_ensure_activate_and_expose_remote_instance(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".git").mkdir()
            with _patched_config_local(root / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                args.project_dir = str(root)
                args.remote = "myvps"
                args.json = True
                args.ensure = True
                args.expose = True
                args.domain = "default-demo.sandbox.asb.bd"
                args.plugin_slug = "demo"
                inst = {
                    "instance": "demo",
                    "label": "default",
                    "wordpress_port": 8188,
                    "url": "http://localhost:8188",
                    "login_url": "http://localhost:8188/?sandbox_autologin=abc123",
                }
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                  return_value={"root": str(root), "slug": "demo"}), \
                     patch.object(sr, "ensure_deploy_repo", return_value="/remote/demo"), \
                     patch.object(sr, "current_branch", return_value="main"), \
                     patch.object(sr, "push_commits", return_value="abc123"), \
                     patch.object(sr, "reset_target_to"), \
                     patch.object(sr, "capture_uncommitted", return_value=("", [])), \
                     patch.object(sr, "apply_uncommitted", return_value=0), \
                     patch.object(sr, "ensure_remote_instance", return_value=inst) as mock_ensure, \
                     patch.object(sr, "activate_remote_plugin") as mock_activate, \
                     patch.object(sr, "configure_instance_https_route") as mock_route, \
                     patch.object(sr, "set_remote_instance_url") as mock_url, \
                     patch("builtins.print") as mock_print:
                    deploy_cmd.cmd_deploy(None, args)
                result = json.loads(mock_print.call_args[0][0])
                self.assertTrue(result["ok"])
                self.assertEqual(result["url"], "https://default-demo.sandbox.asb.bd")
                self.assertEqual(result["instance"]["admin_url"],
                                 "https://default-demo.sandbox.asb.bd/wp-admin/")
                self.assertEqual(
                    result["instance"]["login_url"],
                    "https://default-demo.sandbox.asb.bd/?sandbox_autologin=abc123",
                )
                mock_ensure.assert_called_once_with(sr.get_remote("myvps"), "/remote/demo")
                mock_activate.assert_called_once_with(
                    sr.get_remote("myvps"), "/remote/demo", "demo", "demo"
                )
                mock_route.assert_called_once_with(
                    sr.get_remote("myvps"), "default-demo.sandbox.asb.bd", 8188
                )
                mock_url.assert_called_once_with(
                    sr.get_remote("myvps"), "/remote/demo",
                    "https://default-demo.sandbox.asb.bd"
                )

    def test_malformed_ensure_result_returns_actionable_json_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".git").mkdir()
            with _patched_config_local(root / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                args.project_dir = str(root)
                args.remote = "myvps"
                args.json = True
                args.ensure = True
                args.expose = True
                args.domain = "default-demo.sandbox.asb.bd"
                args.plugin_slug = "demo"
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                  return_value={"root": str(root), "slug": "demo"}), \
                     patch.object(sr, "ensure_deploy_repo", return_value="/remote/demo"), \
                     patch.object(sr, "current_branch", return_value="main"), \
                     patch.object(sr, "push_commits", return_value="abc123"), \
                     patch.object(sr, "reset_target_to"), \
                     patch.object(sr, "capture_uncommitted", return_value=("", [])), \
                     patch.object(sr, "apply_uncommitted", return_value=0), \
                     patch.object(sr, "ensure_remote_instance", return_value={"status": "ready"}), \
                     patch("builtins.print") as mock_print:
                    with self.assertRaises(SystemExit):
                        deploy_cmd.cmd_deploy(None, args)
                result = json.loads(mock_print.call_args[0][0])
                self.assertFalse(result["ok"])
                self.assertIn("remote ensure returned no 'instance'", result["error"])


class TestRemoteDeployMcpWrapper(unittest.TestCase):
    def _load_tool_module(self):
        class _Mcp:
            def tool(self):
                def decorator(fn):
                    return fn
                return decorator

        fake_app = types.ModuleType("app")
        fake_app.mcp = _Mcp()
        fake_app.SANDBOX_ROOT = ROOT
        fake_app._safe_json = json.loads
        old_app = sys.modules.get("app")
        sys.modules["app"] = fake_app
        try:
            path = ROOT / "mcp" / "wp-server" / "tools" / "remote.py"
            spec = importlib.util.spec_from_file_location("remote_tool_under_test", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        finally:
            if old_app is None:
                sys.modules.pop("app", None)
            else:
                sys.modules["app"] = old_app

    def test_remote_deploy_defaults_to_ensure_and_expose(self):
        module = self._load_tool_module()
        payload = {
            "ok": True,
            "remote": "myvps",
            "pushed_commit": "abc123",
            "uncommitted_files_applied": 0,
            "instance": {"instance": "demo"},
            "url": "https://default-demo.sandbox.asb.bd",
            "error": None,
        }
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload) + "\n", stderr=""
        )
        with patch.object(module.subprocess, "run", return_value=fake) as mock_run:
            result = module.remote_deploy("/tmp/project", "myvps")
        self.assertTrue(result["ok"])
        cmd = mock_run.call_args[0][0]
        self.assertIn("--ensure", cmd)
        self.assertIn("--expose", cmd)

    def test_remote_deploy_forwards_domain_and_plugin_slug(self):
        module = self._load_tool_module()
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"ok": True, "remote": "myvps"}) + "\n",
            stderr="",
        )
        with patch.object(module.subprocess, "run", return_value=fake) as mock_run:
            module.remote_deploy(
                "/tmp/project", "myvps",
                domain="default-demo.sandbox.asb.bd",
                plugin_slug="demo",
            )
        cmd = mock_run.call_args[0][0]
        self.assertIn("--domain", cmd)
        self.assertIn("default-demo.sandbox.asb.bd", cmd)
        self.assertIn("--plugin-slug", cmd)
        self.assertIn("demo", cmd)

    def test_remote_deploy_redacts_ssh_target_in_error(self):
        module = self._load_tool_module()
        fake = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ssh: ubuntu@1.2.3.4 refused"
        )
        with patch.object(module.subprocess, "run", return_value=fake):
            result = module.remote_deploy("/tmp/project", "myvps")
        self.assertNotIn("ubuntu@1.2.3.4", result["error"])
        self.assertIn("[redacted SSH target]", result["error"])


if __name__ == "__main__":
    unittest.main()
