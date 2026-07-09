"""Unit tests for remote VPS hosting (specs/014-remote-vps-hosting/).

Stdlib `unittest` only, no docker, no real SSH/VPS -- pure config-read/write,
SSH/git command-construction, and deploy-mechanism logic. Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v

Per Constitution Principle IV, this unit coverage is NOT proof of done on its
own -- see specs/014-remote-vps-hosting/quickstart.md for the required
live-verification pass against a real VPS.
"""
import json
import subprocess
import sys
import tempfile
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
        # subprocess.run is used for: git diff --name-only, the ssh apply call,
        # the mkdir-parent ssh call (goes through ssh_run, mocked separately),
        # and scp for each untracked file.
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "a.php").write_text("x")
            mock_run.side_effect = [
                _completed(returncode=0, stdout="a.php\n"),  # git diff --name-only
                _completed(returncode=0),                      # ssh | git apply
                _completed(returncode=0),                      # scp a.php
            ]
            applied = sr.apply_uncommitted(
                {"ssh": "ubuntu@1.2.3.4"}, "/home/ubuntu/sandbox/deploy-src/proj",
                str(proj), "diff --git a/x b/x\n+y\n", ["a.php"],
            )
            self.assertEqual(applied, 2)  # 1 tracked-diff file + 1 untracked file

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
                with patch("subprocess.run", return_value=_completed(returncode=0)), \
                     patch.object(sr, "resolve_tailscale_ip", return_value="100.64.1.2"), \
                     patch.object(sr, "start_remote_mcp_server"), \
                     patch("builtins.print") as mock_print:
                    remote_cmd._cmd_provision(args, as_json=True)
                printed = mock_print.call_args[0][0]
                result = json.loads(printed)
                self.assertTrue(result["bearer_token"])
                self.assertEqual(len(result["bearer_token"]), 64)  # secrets.token_hex(32)


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


if __name__ == "__main__":
    unittest.main()
