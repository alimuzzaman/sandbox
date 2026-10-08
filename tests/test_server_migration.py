"""Offline tests for tools/server-migration/*.sh.

Nothing here touches a server. Every script runs with a PATH whose front directory
holds recording fakes for ssh, curl, rclone and caffeinate, and with SB pointing at a
fake `sb`. The fakes append their argv to a log, so a test can prove which commands
ran, and that nothing ran at all when a destructive step lacked --confirm.

Run: ../sandbox/.cli-venv/bin/python -m unittest tests.test_server_migration
"""
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.subprocess_support import run_test_process

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools" / "server-migration"
SCRIPTS = ["prepare-host.sh", "deploy-project.sh", "retire-failed.sh", "stage-volume.sh",
           "copy-volume.sh", "pg-transfer.sh", "cutover-compose-data.sh", "wait-and-cutover.sh",
           "verify-site.sh", "backup-to-drive.sh"]
OLD, NEW = "u@old.example.invalid", "u@new.example.invalid"

RECORDER = textwrap.dedent("""\
    #!/usr/bin/env bash
    {{ printf '%s' {name}; for a in "$@"; do printf '\\x1f%s' "$a"; done; printf '\\n'; }} >> "$FAKE_LOG"
    {body}
    """)

FAKE_CURL_BODY = textwrap.dedent("""\
    printf 'HTTP/2 %s\\r\\n' "${FAKE_CURL_CODE:-200}"
    [ -z "${FAKE_CURL_REV:-}" ] || printf 'x-revision: %s\\r\\n' "$FAKE_CURL_REV"
    printf '\\r\\n%s\\n' "${FAKE_CURL_CODE:-200}"
    """)

FAKE_SB_BODY = textwrap.dedent("""\
    case "$1 $2" in
      "host plan") echo "plan ok" ;;
      "host apply")
        case "${FAKE_APPLY:-context}" in
          ok) echo applied ;;
          mismatch) echo "error: remote_runtime_revision_mismatch" >&2; exit 1 ;;
          busy) echo "error: operation_busy" >&2; exit 1 ;;
          context)
            echo "error: recovery_context_required; prepare with: $0 job-start --local --project-dir $4 --request-id deploy-dev-abc123 --source-commit deadbeef --timeout 900 -- $0 host apply --project-dir $4 --remote new-remote --environment dev --confirm" >&2
            exit 1 ;;
        esac ;;
      "host retire-delivery") echo '{"ok": true}' ;;
      "job-status "*)
        echo "{\\"lifecycle\\": \\"${FAKE_LIFECYCLE:-succeeded}\\", \\"request_id\\": \\"deploy-dev-abc123\\", \\"job_id\\": \\"$2\\"}" ;;
      "job-output "*) echo "fake job output" ;;
      "secrets run")
        shift 2
        while [ "$1" != "--" ]; do shift; done; shift
        RECOVERY_PASSPHRASE=fake-test-passphrase-not-a-secret exec "$@" ;;
      *)
        if [ "$1" = job-start ]; then echo '{"job_id": "job-42", "ok": true}'; exit 0; fi
        echo "fake sb: unhandled $*" >&2; exit 9 ;;
    esac
    """)


class MigrationScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="server-migration-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "calls.log"
        self.log.touch()
        for name, body in {"ssh": "exit 0", "rclone": "exit 0", "caffeinate": "exit 0",
                           "curl": FAKE_CURL_BODY, "sb": FAKE_SB_BODY}.items():
            path = self.bin / name
            path.write_text(RECORDER.format(name=name, body=body))
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_script(self, script, *args, env=None, stdin=subprocess.DEVNULL):
        full_env = {"PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
                    "HOME": str(self.tmp), "FAKE_LOG": str(self.log), "SB": str(self.bin / "sb"),
                    "LANG": "C"}
        full_env.update(env or {})
        return subprocess.run([str(TOOLS / script), *args], capture_output=True, text=True,
                              env=full_env, stdin=stdin, timeout=60)

    def calls(self, name=None):
        rows = [line.split("\x1f") for line in self.log.read_text().splitlines() if line]
        return [r for r in rows if name is None or r[0] == name]

    def assertNoRemoteCalls(self):
        self.assertEqual(self.calls("ssh"), [], "ssh must not run")
        self.assertEqual(self.calls("rclone"), [], "rclone must not run")


class HelpAndUsageTests(MigrationScriptTestCase):
    def test_every_script_has_help(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                result = self.run_script(script, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage", result.stdout)
                self.assertIn("--dry-run", result.stdout)

    def test_missing_arguments_is_a_usage_error(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                result = self.run_script(script)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("see --help", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_unknown_flag_rejected(self):
        result = self.run_script("pg-transfer.sh", "--old", OLD, "--new", NEW,
                                 "--container", "db", "--bogus")
        self.assertEqual(result.returncode, 2)

    def test_scripts_are_valid_bash(self):
        for script in [*SCRIPTS, "lib.sh"]:
            with self.subTest(script=script):
                result = subprocess.run(["bash", "-n", str(TOOLS / script)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck not installed")
    def test_shellcheck_clean(self):
        result = subprocess.run(["shellcheck", "-x", "-s", "bash", *SCRIPTS, "lib.sh"],
                                cwd=TOOLS, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)


class PgTransferTests(MigrationScriptTestCase):
    ARGS = ("--old", OLD, "--new", NEW, "--container", "sandbox-host-p-dev-lenzora-db-1")

    def test_dry_run_pipes_pg_dump_into_ssh_and_runs_nothing(self):
        result = self.run_script("pg-transfer.sh", "--dry-run", *self.ARGS, "--check-table", "users")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        dump_line = next(l for l in out.splitlines() if "pg_dump" in l)
        self.assertTrue(dump_line.startswith(f"+ ssh -A -o BatchMode=yes"))
        self.assertIn(OLD, dump_line)
        self.assertIn('pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z 6', dump_line)
        self.assertRegex(dump_line, r"pg_dump .*\| ssh .*" + NEW.replace(".", r"\."))
        self.assertIn("--no-owner --no-acl", out)
        self.assertIn("# destructive step (needs --confirm)", out)
        self.assertIn("select count(*) from users", out)
        self.assertNoRemoteCalls()

    def test_restore_without_confirm_refuses_before_connecting(self):
        result = self.run_script("pg-transfer.sh", *self.ARGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm", result.stderr)
        # The dump is non-destructive and runs; the restore never does.
        ssh_calls = self.calls("ssh")
        self.assertEqual(len(ssh_calls), 1)
        self.assertIn("pg_dump", ssh_calls[0][-1])
        self.assertNotIn("pg_restore", "".join(ssh_calls[0]))

    def test_restore_only_without_confirm_runs_nothing(self):
        result = self.run_script("pg-transfer.sh", *self.ARGS, "--restore-only")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing", result.stderr)
        self.assertNoRemoteCalls()

    def test_real_ssh_invocation_is_agent_forwarded_batch_and_parses(self):
        result = self.run_script("pg-transfer.sh", *self.ARGS, "--dump-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        (call,) = self.calls("ssh")
        self.assertEqual(call[1], "-A")
        self.assertIn("BatchMode=yes", call)
        self.assertEqual(call[-2], OLD)
        words = shlex.split(call[-1])
        self.assertEqual(words[:4], ["bash", "-o", "pipefail", "-c"])
        inner = words[4]
        self.assertIn("docker exec sandbox-host-p-dev-lenzora-db-1 sh -c", inner)
        hop = shlex.split(inner.split("| ", 1)[1])
        self.assertEqual(hop[0], "ssh")
        self.assertIn(NEW, hop)
        self.assertEqual(shlex.split(hop[-1])[:4], ["bash", "-o", "pipefail", "-c"])
        self.assertIn("cat > migration/sandbox-host-p-dev-lenzora-db-1.dump.partial", hop[-1])

    def test_recreate_mode_drops_and_creates(self):
        result = self.run_script("pg-transfer.sh", "--dry-run", *self.ARGS, "--restore-only",
                                 "--restore-mode", "recreate")
        self.assertIn("dropdb", result.stdout)
        self.assertIn("createdb", result.stdout)
        self.assertNotIn("--clean", result.stdout)

    def test_bad_table_name_rejected(self):
        for bad in ("x;drop", '"x;drop"', '"Snapshot', 'Snapshot"', '"a" or 1', "1abc", '""', "a..b", "a."):
            with self.subTest(table=bad):
                result = self.run_script("pg-transfer.sh", "--dry-run", *self.ARGS, "--check-table", bad)
                self.assertEqual(result.returncode, 2, result.stdout)

    def test_quoted_table_names_keep_their_case(self):
        result = self.run_script("pg-transfer.sh", "--dry-run", *self.ARGS, "--check-only",
                                 "--check-table", '"Snapshot"', "--check-table", 'public."User"',
                                 "--check-table", "users")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('select count(*) from "Snapshot"', result.stdout)
        self.assertIn('select count(*) from public."User"', result.stdout)
        self.assertIn("select count(*) from users", result.stdout)


class VolumeTests(MigrationScriptTestCase):
    def test_stage_volume_dry_run(self):
        result = self.run_script("stage-volume.sh", "--dry-run", "--old", OLD, "--new", NEW,
                                 "--volume", "proj_storage")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"sudo -n tar --numeric-owner -C \"\$\(sudo -n docker volume inspect .*proj_storage\)\" -cf - \. \| ssh ")
        self.assertNotIn("running=$(", result.stdout, "a pre-stage may copy a live volume")
        self.assertIn("migration/proj_storage.tar", result.stdout)
        self.assertNoRemoteCalls()

    def test_copy_volume_dry_run_extracts_with_ownership(self):
        result = self.run_script("copy-volume.sh", "--dry-run", "--old", OLD, "--new", NEW,
                                 "--volume", "proj_storage")
        out = result.stdout
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--numeric-owner -xpf", out)
        self.assertIn("docker ps -q --filter volume=proj_storage", out)
        self.assertLess(out.index("# destructive step"), out.index("find \"$mp\" -mindepth 1 -delete"))
        self.assertNoRemoteCalls()

    def test_copy_volume_extract_requires_confirm(self):
        result = self.run_script("copy-volume.sh", "--new", NEW, "--volume", "proj_storage", "--extract-only")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing", result.stderr)
        self.assertNoRemoteCalls()

    def test_copy_volume_with_confirm_runs_both_steps(self):
        result = self.run_script("copy-volume.sh", "--confirm", "--old", OLD, "--new", NEW,
                                 "--volume", "proj_storage")
        self.assertEqual(result.returncode, 0, result.stderr)
        targets = [c[-2] for c in self.calls("ssh")]
        self.assertEqual(targets, [OLD, NEW])


class CutoverTests(MigrationScriptTestCase):
    ARGS = ("--old", OLD, "--new", NEW, "--compose-project", "sandbox-host-lenzora-dev",
            "--db-service", "lenzora-db", "--keep-service", "lenzora-job-queue",
            "--volume", "lenzora-storage", "--health-url", "https://dev.example.invalid/api/health",
            "--revision-header", "X-Revision", "--expect-revision", "abc123", "--check-table", "users")

    def test_dry_run_order(self):
        result = self.run_script("cutover-compose-data.sh", "--dry-run", *self.ARGS)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        steps = [
            out.index(f"{OLD} -- [ \"$(sudo -n docker inspect"),          # preflight
            out.index("/stopped.txt.now"),                                    # stop new first
            out.index("stopped-old.txt.now"),                                 # then old
            out.index("pg_dump"),                                             # final dump
            out.index("sandbox-host-lenzora-dev_lenzora-storage)\" -cf -"),   # final tar
            out.index("dropdb"),                                              # restore
            out.index("--numeric-owner -xpf"),                                # extract
            out.index("xargs sudo -n docker start < migration/sandbox-host-lenzora-dev/stopped.txt"),
            out.index("+ curl"),                                              # verify
            out.index("select count(*) from users"),
        ]
        self.assertEqual(steps, sorted(steps))
        self.assertIn("sandbox-host-lenzora-dev-lenzora-db-1", out)
        # The database and kept services are excluded from the stop set.
        self.assertIn("case ' lenzora-db lenzora-job-queue ' in", out)
        self.assertIn("Rollback", out)
        self.assertIn("stopped-old.txt", out[out.index("Rollback"):])
        self.assertNoRemoteCalls()

    def test_final_volume_copy_requires_quiescent_source(self):
        result = self.run_script("cutover-compose-data.sh", "--dry-run", *self.ARGS,
                                 "--volume", "lenzora-production-job-queue-data")
        fetch = [l for l in result.stdout.splitlines()
                 if l.startswith(f"+ ssh") and "running=$(" in l and OLD in l]
        self.assertEqual(len(fetch), 2, result.stdout)
        self.assertIn("filter volume=sandbox-host-lenzora-dev_lenzora-production-job-queue-data", fetch[1])

    def test_only_selected_services(self):
        result = self.run_script("cutover-compose-data.sh", "--dry-run", *self.ARGS,
                                 "--service", "web", "--service", "worker")
        self.assertIn("case ' web worker ' in", result.stdout)

    def test_without_confirm_nothing_runs(self):
        result = self.run_script("cutover-compose-data.sh", *self.ARGS)
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_bad_check_table_refused_before_anything_moves(self):
        result = self.run_script("cutover-compose-data.sh", "--confirm", *self.ARGS, "--check-table", "x;drop")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])
        ok = self.run_script("cutover-compose-data.sh", "--dry-run", *self.ARGS, "--check-table", '"Snapshot"')
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn('from "Snapshot"', ok.stdout)

    def test_db_service_cannot_be_stopped(self):
        for flag in ("--service", "--keep-service"):
            with self.subTest(flag=flag):
                result = self.run_script("cutover-compose-data.sh", "--dry-run", *self.ARGS, flag, "lenzora-db")
                self.assertEqual(result.returncode, 2)

    def test_failure_after_stop_prints_rollback(self):
        failing_ssh = self.bin / "ssh"
        failing_ssh.write_text(RECORDER.format(
            name="ssh", body='case "${@: -1}" in *pg_dump*) exit 1 ;; esac; exit 0'))
        result = self.run_script("cutover-compose-data.sh", "--confirm", *self.ARGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("To roll back", result.stderr)
        self.assertIn("docker start < migration/sandbox-host-lenzora-dev/stopped-old.txt", result.stderr)
        self.assertFalse(any("pg_restore" in c[-1] for c in self.calls("ssh")))


    def test_first_run_records_and_stops_without_a_prior_list(self):
        # Run the real stop step under bash -o pipefail against a fake docker, on a
        # server where the record files do not exist yet (the 2026-10-08 dev round trip).
        remote = self.tmp / "remote"
        remote.mkdir()
        docker = self.bin / "fakedocker"
        docker.write_text(RECORDER.format(name="docker", body=textwrap.dedent("""\
            case "$1" in
              ps) echo c1 ;;
              inspect) case "$3" in *service*) echo web ;; *) echo /proj-web-1 ;; esac ;;
            esac
            exit 0""")))
        docker.chmod(0o755)
        ssh = self.bin / "ssh"
        ssh.write_text(RECORDER.format(name="ssh", body=textwrap.dedent(f"""\
            case "${{@: -1}}" in
              *stopped*.txt.now*) cd {shlex.quote(str(remote))} && eval "${{@: -1}}" ;;
              *pg_dump*) exit 1 ;;  # end the run here; the stop steps are what is under test
            esac""")))
        result = self.run_script("cutover-compose-data.sh", "--confirm", "--yes", *self.ARGS,
                                 env={"MIGRATION_DOCKER": str(docker)})
        listed = remote / "migration" / "sandbox-host-lenzora-dev"
        self.assertEqual((listed / "stopped.txt").read_text(), "proj-web-1\n", result.stderr)
        self.assertEqual((listed / "stopped-old.txt").read_text(), "proj-web-1\n", result.stderr)
        self.assertEqual(len([c for c in self.calls("docker") if c[1] == "stop"]), 2)
        self.assertTrue(any("pg_dump" in c[-1] for c in self.calls("ssh")), result.stderr)


    MAINT = ("--maintenance-service", "lenzora-monitor-worker")

    def test_maintenance_mode_wraps_the_cutover(self):
        result = self.run_script("cutover-compose-data.sh", "--dry-run", *self.ARGS, *self.MAINT)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        steps = [
            out.index("maintenance:status\n"),                               # CLI present
            out.index("maintenance:enable --reason 'server migration'"),      # NEW read_only
            out.index("maintenance:drain --reason 'server migration' --wait --timeout 15"),
            out.index("grep -q '\"mode\":\"read_only\"'"),                   # OLD confirmed
            out.index("/stopped.txt.now"),                                    # then stop NEW
            out.index("stopped-old.txt.now"),
            out.index("pg_dump"),
            out.index("+ curl"),
            out.index("grep -q '\"mode\":\"read_write\"'"),                  # NEW writable
        ]
        self.assertEqual(steps, sorted(steps))
        enable = [l for l in out.splitlines() if "maintenance:enable" in l]
        self.assertEqual(len(enable), 1)
        before_enable = out[:out.index("maintenance:enable")]
        self.assertIn(f"{NEW} -- set -e", before_enable[before_enable.rindex("+ ssh"):])
        drain_ssh = out[:out.index("maintenance:drain")]
        self.assertIn(f"{OLD} -- set -e", drain_ssh[drain_ssh.rindex("+ ssh"):])
        self.assertIn("com.docker.compose.service=lenzora-monitor-worker", out)
        self.assertIn("maintenance:disable", out[out.index("Rollback"):])
        self.assertNoRemoteCalls()

    def test_maintenance_old_only_leaves_new_alone(self):
        result = self.run_script("cutover-compose-data.sh", "--dry-run", *self.ARGS, *self.MAINT,
                                 "--maintenance-old-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("maintenance:enable", result.stdout)
        self.assertIn("maintenance:drain", result.stdout)

    def test_maintenance_timeout_must_cover_a_drain(self):
        result = self.run_script("cutover-compose-data.sh", "--dry-run", *self.ARGS, *self.MAINT,
                                 "--maintenance-timeout", "5")
        self.assertEqual(result.returncode, 2)
        self.assertIn("at least 6", result.stderr)

    def test_drain_failure_stops_nothing_and_prints_disable(self):
        failing_ssh = self.bin / "ssh"
        failing_ssh.write_text(RECORDER.format(
            name="ssh", body='case "${@: -1}" in *maintenance:drain*) exit 4 ;; esac; exit 0'))
        result = self.run_script("cutover-compose-data.sh", "--confirm", *self.ARGS, *self.MAINT)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("stopped" in c[-1] for c in self.calls("ssh")))
        self.assertIn("To leave maintenance mode", result.stderr)
        tail = result.stderr[result.stderr.index("To leave maintenance mode"):]
        self.assertIn(f"ssh {OLD} ", tail)
        self.assertIn(f"ssh {NEW} ", tail)
        self.assertIn("maintenance:disable", tail)


class WaitAndCutoverTests(MigrationScriptTestCase):
    CUT = ("--old", OLD, "--new", NEW, "--compose-project", "sandbox-host-p-dev", "--db-service", "db",
           "--health-url", "https://p.example.invalid/api/health")

    def wait(self, *extra, env=None):
        return self.run_script("wait-and-cutover.sh", "--job-id", "job-7", "--poll-interval", "1",
                               "--poll-timeout", "3", "--no-caffeinate", *extra, "--", *self.CUT, env=env)

    def test_runs_cutover_only_after_success(self):
        result = self.wait("--confirm", env={"FAKE_LIFECYCLE": "succeeded"})
        self.assertIn("starting the data cutover", result.stderr)
        self.assertTrue(any("pg_dump" in c[-1] for c in self.calls("ssh")))

    def test_failed_job_never_touches_data(self):
        result = self.wait("--confirm", env={"FAKE_LIFECYCLE": "failed"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("no cutover", result.stderr)
        self.assertEqual(self.calls("ssh"), [])

    def test_running_job_is_bounded_and_never_touches_data(self):
        result = self.wait("--confirm", env={"FAKE_LIFECYCLE": "running"})
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.calls("ssh"), [])

    def test_confirm_checked_before_waiting(self):
        result = self.wait(env={"FAKE_LIFECYCLE": "succeeded"})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_dry_run(self):
        result = self.wait("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("job-status job-7 --json", result.stdout)
        self.assertIn("only on lifecycle=succeeded", result.stdout)
        self.assertIn("pg_dump", result.stdout)
        self.assertEqual(self.calls(), [])


class VerifySiteTests(MigrationScriptTestCase):
    def test_dry_run_with_resolve(self):
        result = self.run_script("verify-site.sh", "--dry-run", "--url", "https://a.example.invalid/api/health",
                                 "--resolve-ip", "192.0.2.10")
        self.assertIn("--resolve a.example.invalid:443:192.0.2.10", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_passes_on_status_and_revision(self):
        result = self.run_script("verify-site.sh", "--url", "https://a.example.invalid/h",
                                 "--revision-header", "X-Revision", "--expect-revision", "abc",
                                 "--attempts", "1", env={"FAKE_CURL_REV": "abc123def"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fails_on_wrong_revision_and_status(self):
        for env in ({"FAKE_CURL_REV": "zzz"}, {"FAKE_CURL_CODE": "502", "FAKE_CURL_REV": "abc"}):
            with self.subTest(env=env):
                result = self.run_script("verify-site.sh", "--url", "https://a.example.invalid/h",
                                         "--revision-header", "X-Revision", "--expect-revision", "abc",
                                         "--attempts", "1", env=env)
                self.assertEqual(result.returncode, 1)

    def test_basic_auth_value_never_in_argv(self):
        result = self.run_script("verify-site.sh", "--url", "https://a.example.invalid/h", "--attempts", "1",
                                 "--basic-auth-env", "SITE_AUTH", env={"SITE_AUTH": "user:hunter2"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("hunter2", self.log.read_text())
        self.assertNotIn("hunter2", result.stdout + result.stderr)


class DeployProjectTests(MigrationScriptTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.tmp / "project"
        self.project.mkdir()
        git = ["git", "-C", str(self.project), "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
        subprocess.run([*git[:3], "init", "-q"], check=True)
        (self.project / "README").write_text("x\n")
        subprocess.run([*git, "add", "README"], check=True)
        subprocess.run([*git, "commit", "-qm", "init"], check=True)

    def deploy(self, *extra, env=None):
        return self.run_script("deploy-project.sh", "--project-dir", str(self.project), "--environment", "dev",
                               "--remote", "new-remote", "--no-caffeinate", "--poll-interval", "1",
                               "--poll-timeout", "5", *extra, env=env)

    def test_runs_printed_job_start_with_raised_timeout(self):
        result = self.deploy("--confirm", "--job-timeout", "5400")
        self.assertEqual(result.returncode, 0, result.stderr)
        sb = [c[1:] for c in self.calls("sb")]
        self.assertEqual(sb[0][:2], ["host", "plan"])
        self.assertEqual(sb[1][:2], ["host", "apply"])
        job = next(c for c in sb if c[0] == "job-start")
        self.assertEqual(job[1], "--json")
        self.assertEqual(job[job.index("--timeout") + 1], "5400")
        self.assertEqual(job[job.index("--request-id") + 1], "deploy-dev-abc123")
        self.assertEqual(job[job.index("--") + 1:job.index("--") + 3], [str(self.bin / "sb"), "host"])
        self.assertIn(["job-status", "job-42", "--json"], sb)

    def test_failed_job_prints_retire_command(self):
        result = self.deploy("--confirm", env={"FAKE_LIFECYCLE": "failed"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("retire-failed.sh", result.stderr)
        self.assertIn("--original-request-id deploy-dev-abc123", result.stderr)
        self.assertNotIn("retire-delivery", [c[2] for c in self.calls("sb") if len(c) > 2])

    def test_poll_budget_is_bounded(self):
        result = self.deploy("--confirm", env={"FAKE_LIFECYCLE": "running"})
        self.assertEqual(result.returncode, 3)
        self.assertIn("still running", result.stderr)

    def test_without_confirm_only_plans(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c[1:3] for c in self.calls("sb")], [["host", "plan"]])

    def test_revision_mismatch_names_remote_up(self):
        result = self.deploy("--confirm", env={"FAKE_APPLY": "mismatch"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("remote up new-remote --confirm", result.stderr)

    def test_busy_names_retire(self):
        result = self.deploy("--confirm", env={"FAKE_APPLY": "busy"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("retire-failed.sh", result.stdout + result.stderr)

    def test_dirty_checkout_refused(self):
        (self.project / "dirty").write_text("x")
        result = self.deploy("--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("uncommitted", result.stderr)
        self.assertEqual(self.calls("sb"), [])


class RetireTests(MigrationScriptTestCase):
    BASE = ("--project-dir", "/tmp/p", "--environment", "dev", "--remote", "new-remote")

    def test_dry_run(self):
        result = self.run_script("retire-failed.sh", "--dry-run", *self.BASE, "--original-request-id", "req-1")
        self.assertIn("host retire-delivery --project-dir /tmp/p --environment dev --remote new-remote "
                      "--original-request-id req-1 --confirm --json", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_live_job_is_not_retired(self):
        result = self.run_script("retire-failed.sh", "--confirm", *self.BASE, "--job-id", "job-1",
                                 env={"FAKE_LIFECYCLE": "running"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("not ended", result.stderr)
        self.assertNotIn("retire-delivery", self.log.read_text())

    def test_job_id_resolves_request_id(self):
        result = self.run_script("retire-failed.sh", "--confirm", *self.BASE, "--job-id", "job-1",
                                 env={"FAKE_LIFECYCLE": "failed"})
        self.assertEqual(result.returncode, 0, result.stderr)
        retire = next(c for c in self.calls("sb") if c[1:3] == ["host", "retire-delivery"])
        self.assertEqual(retire[retire.index("--original-request-id") + 1], "deploy-dev-abc123")


class PrepareHostTests(MigrationScriptTestCase):
    ARGS = ("--root-ssh", "root@new.example.invalid", "--user", "deployer", "--remote-name", "new-remote",
            "--ssh-url", "deployer@new.example.invalid", "--control-host", "control.example.invalid")

    def test_dry_run(self):
        result = self.run_script("prepare-host.sh", "--dry-run", *self.ARGS)
        out = result.stdout
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('visudo -cf "$tmp"', out)
        self.assertIn("/etc/sudoers.d/90-sandbox-deployer", out)
        self.assertIn("usermod -aG docker deployer", out)
        self.assertIn("/swapfile-sandbox", out)
        self.assertIn("vm.swappiness=10", out)
        self.assertNotRegex(out, r"(mkswap|swapon|fallocate -l 8G) /swapfile(\s|$)")
        self.assertIn("remote add new-remote deployer@new.example.invalid --front-door nginx", out)
        self.assertIn("remote provision new-remote --control https --control-host control.example.invalid "
                      "--front-door nginx --confirm", out)
        self.assertEqual(self.calls(), [])

    def test_without_confirm_refuses(self):
        result = self.run_script("prepare-host.sh", *self.ARGS)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])


class BackupTests(MigrationScriptTestCase):
    def test_repo_dry_run(self):
        result = self.run_script("backup-to-drive.sh", "repo", "--dry-run", "--repo", "/tmp/r",
                                 "--name", "hermes", "--secret-source", "drive-src")
        out = result.stdout
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bundle create", out)
        self.assertIn("--all", out)
        self.assertIn("secrets run --source drive-src --key RECOVERY_PASSPHRASE --destination RECOVERY_PASSPHRASE", out)
        self.assertIn("--internal-crypt", out)
        self.assertRegex(out, r"rclone copy .* gdrive:hermes-full-recovery/manual/\d{8}T\d{6}Z-hermes")
        self.assertIn("rclone check", out)
        self.assertEqual(self.calls(), [])

    def test_recovery_dry_run(self):
        result = self.run_script("backup-to-drive.sh", "recovery", "--dry-run", "--remote", "old-remote",
                                 "--profile", "lenzora-dev", "--backup-id", "b1", "--secret-source", "drive-src")
        self.assertIn("-- " + str(self.bin / "sb") + " recovery create --remote old-remote --profile lenzora-dev "
                      "--backup-id b1 --destination gdrive:hermes-full-recovery --confirm --json", result.stdout)
        self.assertIn("recovery verify", result.stdout)

    def test_requires_confirm(self):
        result = self.run_script("backup-to-drive.sh", "repo", "--repo", "/tmp/r", "--name", "hermes",
                                 "--secret-source", "drive-src")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_internal_crypt_refuses_without_brokered_passphrase(self):
        result = self.run_script("backup-to-drive.sh", "--internal-crypt", "/tmp/a", "/tmp/b")
        self.assertEqual(result.returncode, 1)
        self.assertIn("sb secrets run", result.stderr)

    @unittest.skipUnless(shutil.which("gpg"), "gpg not installed")
    def test_repo_round_trip_with_fake_broker(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        git = ["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "f").write_text("data\n")
        subprocess.run([*git, "add", "f"], check=True)
        subprocess.run([*git, "commit", "-qm", "c"], check=True)
        # gpg-agent's socket path must stay short, so this home lives directly in /tmp.
        gnupg = Path(tempfile.mkdtemp(prefix="smg-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, gnupg, ignore_errors=True)
        self.addCleanup(run_test_process, ["gpgconf", "--kill", "gpg-agent"], capture_output=True,
                        env={"GNUPGHOME": str(gnupg)})
        work = self.tmp / "work"
        result = self.run_script("backup-to-drive.sh", "repo", "--confirm", "--repo", str(repo), "--name", "r",
                                 "--secret-source", "drive-src", "--work-dir", str(work),
                                 env={"GNUPGHOME": str(gnupg)})
        self.assertEqual(result.returncode, 0, result.stderr)
        rclone = self.calls("rclone")
        self.assertEqual([c[1] for c in rclone], ["copy", "check"])
        self.assertFalse(work.exists(), "work dir is removed after upload")
        self.assertNotIn("fake-test-passphrase", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
