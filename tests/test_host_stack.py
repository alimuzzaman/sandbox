"""Spec 071: `sb host stack` templates, ports, hostname ownership and commands."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sandbox.core._hosting as hosting
from sandbox.commands import host_stack
from sandbox.commands import hosting as hosting_cmd
from sandbox.hosting import hostname_ownership, stack_registry, stack_template
from sandbox.hosting.stack_template import StackRef, StackTemplateError
from sandbox.hosting.lifetime import client as lifetime_client, hoststore
from tests.subprocess_support import run_test_process

OWNER_A = {"label": "alice", "controller": "a" * 32}
OWNER_B = {"label": "bob", "controller": "b" * 32}

COMMIT = "a" * 40

TEMPLATE = """\
version: 1
project: xspeed-hub-{slug}
stack:
  slug: '^[a-z0-9][a-z0-9-]{1,30}$'
  ports: [mailpit]
environments:
  sandbox:
    compose:
      files: [compose.yml]
      service: web
      container_port: 80
      background_services: [mailpit]
    healthcheck: {path: /, statuses: [200]}
    host:
      primary: hub-{slug}.example.test
      aliases:
        - hostname: admin-{slug}.example.test
          mode: serve
        - hostname: renderer-{slug}.example.test
          mode: serve
    deploy:
      allowed_branches: ['{ref_branch}']
      require_clean: true
    cloudflare: {proxied: true, tls: origin-ca, ssl_mode: strict}
    secrets:
      values:
        BRANCH_SLUG: '{slug}'
        HUB_HOST: 'hub-{slug}.example.test'
        MAILPIT_PORT: '{port:mailpit}'
      generated:
        PG_PASSWORD: 'XSPEED_HUB_SBX_{SLUG}_PG_PASSWORD'
"""
COMPOSE = """\
services:
  web:
    image: nginx:alpine
  mailpit:
    image: axllent/mailpit
    ports:
      - "127.0.0.1:${MAILPIT_PORT:-8025}:8025"
"""


def _yaml(text: str) -> dict:
    import yaml
    return yaml.safe_load(text)


def _git(cwd: Path, *arguments: str) -> str:
    result = run_test_process(
        ("git", "-C", str(cwd), *arguments),
        env={"HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"},
        capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip()


class _Runtime:
    """A temporary SANDBOX runtime dir patched into every reader."""

    def __init__(self, test: unittest.TestCase):
        self.base = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.base, True)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        patcher = patch("sandbox.core._paths.RUNTIME_DIR", self.runtime)
        patcher.start()
        test.addCleanup(patcher.stop)

    def checkout(self, template: str = TEMPLATE, *, name: str = "hub", subdir: str = ".") -> Path:
        """A committed checkout with the template and its compose file in ``subdir``."""
        root = self.base / name
        root.mkdir()
        _git(root, "init", "--quiet", "--initial-branch=main")
        folder = root / subdir
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "sandbox.hosting.yml").write_text(template)
        (folder / "compose.yml").write_text(COMPOSE)
        _git(root, "add", "-A")
        _git(root, "commit", "--quiet", "-m", "init")
        _git(root, "branch", "stack/demo")
        return root


class TemplateSchemaTests(unittest.TestCase):
    def test_plain_commands_refuse_a_template(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sandbox.hosting.yml").write_text(TEMPLATE)
            Path(directory, "compose.yml").write_text(COMPOSE)
            with self.assertRaises(hosting.HostingRefusal) as caught:
                hosting.validate_manifest(directory)
        self.assertEqual(caught.exception.code, "stack_template_requires_stack_up")

    def test_placeholder_without_stack_block_is_still_a_template(self):
        self.assertEqual(stack_template.manifest_kind({"project": "hub-{slug}"}), "template")
        self.assertEqual(stack_template.manifest_kind({"project": "hub"}), "plain")
        # Basic-auth route parameters are not stack placeholders.
        self.assertEqual(stack_template.manifest_kind({"x": "/api/{id}"}), "plain")

    def test_placeholder_outside_allowed_fields_is_refused(self):
        data = _yaml(TEMPLATE)
        data["environments"]["sandbox"]["healthcheck"]["path"] = "/{slug}"
        with self.assertRaisesRegex(StackTemplateError, "healthcheck.path") as caught:
            stack_template.parse_template(data)
        self.assertEqual(caught.exception.code, "stack_template_invalid")

    def test_unknown_placeholder_undeclared_port_and_stray_brace(self):
        for field, value, message in (
                ("BRANCH_SLUG", "{branch}", "unknown placeholder"),
                ("BRANCH_SLUG", "{port:smtp}", "does not declare"),
                ("BRANCH_SLUG", "{slug", "unbalanced"),
        ):
            data = _yaml(TEMPLATE)
            data["environments"]["sandbox"]["secrets"]["values"][field] = value
            with self.subTest(value=value), self.assertRaisesRegex(StackTemplateError, message):
                stack_template.parse_template(data)

    def test_basic_auth_subtree_keeps_its_own_brace_syntax(self):
        data = _yaml(TEMPLATE)
        data["environments"]["sandbox"]["basic_auth"] = {
            "bypass_routes": [{"path_template": "/api/{id}", "methods": ["GET"]}]}
        stack_template.parse_template(data)
        data["environments"]["sandbox"]["basic_auth"]["username"] = "{slug}"
        with self.assertRaisesRegex(StackTemplateError, "basic_auth.username"):
            stack_template.parse_template(data)

    def test_slug_must_match_template_pattern(self):
        template = stack_template.parse_template(_yaml(TEMPLATE))
        self.assertEqual(stack_template.validate_slug(template, "demo-1"), "demo-1")
        for slug in ("d", "Demo", "-demo", "a" * 40, "de_mo"):
            with self.subTest(slug=slug), self.assertRaises(StackTemplateError) as caught:
                stack_template.validate_slug(template, slug)
            self.assertEqual(caught.exception.code, "stack_slug_invalid")

    def test_render_substitutes_only_allowed_fields(self):
        template = stack_template.parse_template(_yaml(TEMPLATE))
        ref = StackRef(ref="stack/demo", commit=COMMIT, ref_branch="stack/demo")
        rendered = stack_template.render(template, slug="pr-12", ref=ref, ports={"mailpit": 18142},
                                         source_root="/abs/src", template_path="/abs/hub/sandbox.hosting.yml")
        env = rendered["environments"]["sandbox"]
        self.assertEqual(rendered["project"], "xspeed-hub-pr-12")
        self.assertEqual(env["host"]["primary"], "hub-pr-12.example.test")
        self.assertEqual(env["host"]["aliases"][1]["hostname"], "renderer-pr-12.example.test")
        self.assertEqual(env["deploy"]["allowed_branches"], ["stack/demo"])
        self.assertEqual(env["secrets"]["values"]["MAILPIT_PORT"], "18142")
        self.assertEqual(env["secrets"]["generated"]["PG_PASSWORD"], "XSPEED_HUB_SBX_PR_12_PG_PASSWORD")
        self.assertNotIn("stack", rendered)
        self.assertEqual(rendered["source_root"], "/abs/src")
        self.assertEqual(rendered["stack_instance"]["ports"], {"mailpit": 18142})
        self.assertEqual(rendered["stack_instance"]["commit"], COMMIT)

    def test_detached_commit_cannot_fill_ref_branch(self):
        template = stack_template.parse_template(_yaml(TEMPLATE))
        ref = StackRef(ref=COMMIT, commit=COMMIT, ref_branch="")
        with self.assertRaises(StackTemplateError) as caught:
            stack_template.render(template, slug="demo", ref=ref, ports={"mailpit": 18142},
                                  source_root="/abs", template_path="/abs/t.yml")
        self.assertEqual(caught.exception.code, "stack_branch_unavailable")

    def test_source_root_follows_template_location(self):
        data = _yaml(TEMPLATE)
        data["source_root"] = ".."
        template = stack_template.parse_template(data)
        self.assertEqual(str(stack_template.template_source_root(template, "config/sandbox.hosting.yml")), ".")
        with self.assertRaises(StackTemplateError):
            stack_template.template_source_root(template, "sandbox.hosting.yml")


class BranchPolicyTests(unittest.TestCase):
    def test_globs_exact_names_and_star(self):
        allowed = stack_template.branch_allowed
        self.assertTrue(allowed("feat/x", ["feat/*"]))
        self.assertFalse(allowed("main", ["feat/*"]))
        self.assertTrue(allowed("main", ["main"]))
        self.assertTrue(allowed("anything", ["*"]))
        self.assertTrue(allowed("", ["*"]))
        self.assertFalse(allowed("", ["feat/*", "main"]))
        self.assertFalse(allowed("Feat/x", ["feat/*"]))

    def _validated(self, stack, allowed):
        return {"project_root": "/src", "environment": "sandbox", "stack": stack,
                "deploy": {"allowed_branches": allowed, "require_clean": False}}

    def test_apply_source_uses_stack_ref_branch_on_detached_worktree(self):
        stack = {"commit": COMMIT, "ref_branch": "stack/demo"}
        with patch.object(hosting_cmd.remote, "current_branch") as current, \
                patch.object(hosting_cmd, "_resolve_host_source_commit", return_value=COMMIT):
            branch = hosting_cmd._validate_apply_source(self._validated(stack, ["stack/*"]))
        self.assertEqual(branch, "stack/demo")
        current.assert_not_called()

    def test_apply_source_refuses_detached_head_at_another_commit(self):
        stack = {"commit": COMMIT, "ref_branch": "stack/demo"}
        with patch.object(hosting_cmd.remote, "current_branch", return_value=None), \
                patch.object(hosting_cmd, "_resolve_host_source_commit", return_value="b" * 40):
            with self.assertRaises(hosting.HostingRefusal) as caught:
                hosting_cmd._validate_apply_source(self._validated(stack, ["*"]))
        self.assertEqual(caught.exception.code, "stack_source_mismatch")

    def test_detached_stack_commit_needs_star(self):
        stack = {"commit": COMMIT, "ref_branch": ""}
        with patch.object(hosting_cmd.remote, "current_branch", return_value=None), \
                patch.object(hosting_cmd, "_resolve_host_source_commit", return_value=COMMIT):
            self.assertEqual(hosting_cmd._validate_apply_source(self._validated(stack, ["*"])), "")
            with self.assertRaisesRegex(hosting.HostingError, "detached commit"):
                hosting_cmd._validate_apply_source(self._validated(stack, ["feat/*"]))

    def test_plain_manifest_branch_glob(self):
        with patch.object(hosting_cmd.remote, "current_branch", return_value="feat/one"):
            self.assertEqual(hosting_cmd._validate_apply_source(self._validated(None, ["feat/*"])),
                             "feat/one")


class DeliveredCommitTests(unittest.TestCase):
    def test_a_succeeded_last_apply_is_delivered(self):
        record = {"commit": "a" * 40, "last_apply": {"commit": "b" * 40}}
        for status, expected in (("succeeded", "b" * 40), ("failed", "a" * 40), ("running", "a" * 40),
                                 ("submitted", "a" * 40), (None, "a" * 40)):
            with self.subTest(status=status):
                self.assertEqual(stack_registry.delivered_commit(record, status), expected)
        self.assertIsNone(stack_registry.delivered_commit({"last_apply": {"commit": "b" * 40}}, "failed"))
        # A record written before last_apply.commit existed keeps its commit.
        self.assertEqual(stack_registry.delivered_commit({"commit": "c" * 40, "last_apply": {}}, "succeeded"),
                         "c" * 40)


class RecordDeliveryTests(unittest.TestCase):
    def test_only_the_awaited_apply_moves_the_commit(self):
        path = Path(tempfile.mkdtemp()) / "registry.json"
        self.addCleanup(shutil.rmtree, path.parent, True)
        with stack_registry.locked(path) as registry:
            registry.put({"remote": "r", "slug": "demo", "commit": "a" * 40,
                          "last_apply": {"request_id": "req-2", "commit": "b" * 40}})

        def deliver(**values):
            return stack_registry.record_delivery("r", "demo", path=path, now=7, **values)

        for values in ({"request_id": "req-1", "commit": "b" * 40},   # an older apply
                       {"request_id": "req-2", "commit": "c" * 40},   # another commit
                       {"request_id": None, "commit": "b" * 40}):     # no durable request
            with self.subTest(values=values):
                self.assertFalse(deliver(**values))
                self.assertEqual(stack_registry.read(path)["stacks"]["r/demo"]["commit"], "a" * 40)
        self.assertFalse(stack_registry.record_delivery("r", "other", request_id="req-2",
                                                        commit="b" * 40, path=path))
        self.assertTrue(deliver(request_id="req-2", commit="b" * 40))
        record = stack_registry.read(path)["stacks"]["r/demo"]
        self.assertEqual((record["commit"], record["updated_at"]), ("b" * 40, 7))
        self.assertFalse(deliver(request_id="req-2", commit="b" * 40))


class PortAllocationTests(unittest.TestCase):
    def allocate(self, **overrides):
        options = dict(remote_name="r", slug="demo", names=("mailpit",), existing={}, pins={},
                       other_stacks={}, reserved=set(), bound={},
                       own_compose_project="sandbox-host-xspeed-hub-demo-sandbox")
        options.update(overrides)
        return stack_registry.allocate_ports(**options)

    def test_allocates_deterministically_in_range(self):
        first = self.allocate()["mailpit"]
        self.assertIn(first, stack_registry.STACK_PORT_RANGE)
        self.assertEqual(self.allocate()["mailpit"], first)

    def test_skips_other_stacks_reserved_and_foreign_bound_ports(self):
        first = self.allocate()["mailpit"]
        taken = {first: "other"}
        second = self.allocate(other_stacks=taken)["mailpit"]
        self.assertNotEqual(second, first)
        self.assertNotEqual(self.allocate(reserved={first})["mailpit"], first)
        self.assertNotEqual(self.allocate(bound={first: set()})["mailpit"], first)

    def test_own_binding_and_recorded_port_are_kept(self):
        own = {18150: {"sandbox-host-xspeed-hub-demo-sandbox"}}
        self.assertEqual(self.allocate(existing={"mailpit": 18150}, bound=own)["mailpit"], 18150)

    def test_pins(self):
        self.assertEqual(self.allocate(pins={"mailpit": 8025})["mailpit"], 8025)
        for overrides in ({"other_stacks": {8025: "pr-1"}},
                          {"bound": {8025: {"another-project"}}},
                          {"bound": {8025: set()}}):
            with self.subTest(overrides=overrides), self.assertRaises(stack_registry.StackRegistryError) as caught:
                self.allocate(pins={"mailpit": 8025}, **overrides)
            self.assertEqual(caught.exception.code, "stack_port_unavailable")

    def test_recorded_port_taken_by_foreign_process_is_refused(self):
        with self.assertRaises(stack_registry.StackRegistryError):
            self.allocate(existing={"mailpit": 18150}, bound={18150: set()})

    def test_exhausted_range(self):
        everything = {port: "x" for port in stack_registry.STACK_PORT_RANGE}
        with self.assertRaisesRegex(stack_registry.StackRegistryError, "no free stack port"):
            self.allocate(other_stacks=everything)

    def test_parse_pins(self):
        self.assertEqual(stack_registry.parse_pins(["mailpit=8025"], ("mailpit",)), {"mailpit": 8025})
        for values in (["mailpit"], ["smtp=1025"], ["mailpit=80"], ["mailpit=1", "mailpit=2"]):
            with self.subTest(values=values), self.assertRaises(stack_registry.StackRegistryError):
                stack_registry.parse_pins(values, ("mailpit",))

    def test_web_loopback_allocation_skips_stack_range(self):
        key = next(f"r/p{index}/e" for index in range(10_000)
                   if 100 <= int(hashlib.sha256(f"r/p{index}/e".encode()).hexdigest()[:8], 16) % 1000 < 200)
        port = hosting.allocate_loopback_port({"version": 1, "hosts": {}}, key)
        self.assertNotIn(port, range(hosting.STACK_PORT_START, hosting.STACK_PORT_END))
        recorded = {"version": 1, "hosts": {key: {"loopback_port": 18150}}}
        self.assertEqual(hosting.allocate_loopback_port(recorded, key), 18150)

    def test_parse_port_probe(self):
        output = ("--listen--\n127.0.0.1:18150\n[::]:22\n*:443\n--docker--\n"
                  "sandbox-host-a-b|127.0.0.1:18150->8025/tcp\n|0.0.0.0:9000->9000/tcp\n")
        bound = host_stack.parse_port_probe(output)
        self.assertEqual(bound[18150], {"sandbox-host-a-b"})
        self.assertEqual(bound[22], set())
        self.assertEqual(bound[9000], {"a non-Compose container"})


class HostnameOwnershipTests(unittest.TestCase):
    STATE = {"version": 1, "hosts": {
        "r/alpha/production": {"hostnames": ["hub-a.example.test"]},
        "r/beta/production": {"records": [{"applied": {"name": "beta.example.test"}}],
                              "dns_coverage": {"hostname": "cov.example.test"}},
        "other/gamma/production": {"hostnames": ["hub-g.example.test"]},
    }}

    def test_other_project_on_same_remote_is_refused(self):
        for hostname, owner in (("hub-a.example.test", "alpha"), ("beta.example.test", "beta"),
                                ("COV.example.test.", "beta")):
            with self.subTest(hostname=hostname), self.assertRaises(hostname_ownership.HostnameOwnedError) as caught:
                hostname_ownership.assert_available(self.STATE, "r", "new", [hostname])
            self.assertEqual(caught.exception.code, "hostname_owned_by_other_project")
            self.assertIn(owner, str(caught.exception))

    def test_same_project_and_other_remote_pass(self):
        hostname_ownership.assert_available(self.STATE, "r", "alpha", ["hub-a.example.test"])
        hostname_ownership.assert_available(self.STATE, "r", "new", ["hub-g.example.test"])

    def test_stack_records_count_as_holdings(self):
        stacks = [{"remote": "r", "project": "xspeed-hub-pr-1", "hostnames": ["hub-pr-1.example.test"]}]
        with self.assertRaises(hostname_ownership.HostnameOwnedError):
            hostname_ownership.assert_available({"hosts": {}}, "r", "new", ["hub-pr-1.example.test"], stacks)


class RenderedManifestTests(unittest.TestCase):
    def setUp(self):
        self.runtime = _Runtime(self)
        self.hub = self.runtime.checkout()
        self.template = stack_template.parse_template(_yaml(TEMPLATE))
        self.ref = StackRef(ref="stack/demo", commit=_git(self.hub, "rev-parse", "HEAD"), ref_branch="stack/demo")

    def _render(self, source_root: Path) -> dict:
        return stack_template.render(self.template, slug="demo", ref=self.ref, ports={"mailpit": 18142},
                                     source_root=source_root, template_path=self.hub / "sandbox.hosting.yml")

    def _worktree(self) -> Path:
        path = self.runtime.runtime / "stacks" / "demo" / "source" / "wt"
        path.parent.mkdir(parents=True)
        _git(self.hub, "worktree", "add", "--detach", "--quiet", str(path), self.ref.commit)
        return path

    def _write(self, rendered: dict, path: Path) -> Path:
        import yaml
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(rendered, sort_keys=False))
        return path

    def test_rendered_manifest_is_valid_only_at_its_runtime_path(self):
        worktree = self._worktree()
        rendered = self._render(worktree)
        target = self._write(rendered, self.runtime.runtime / "stacks" / "demo" / "sandbox.hosting.yml")
        validated = hosting.validate_manifest(target)
        self.assertEqual(validated["project"], "xspeed-hub-demo")
        self.assertEqual(validated["source_root"], str(worktree.resolve()))
        self.assertEqual(validated["stack"]["ref_branch"], "stack/demo")
        self.assertEqual(validated["secrets"]["values"]["MAILPIT_PORT"], "18142")
        copy = self._write(rendered, self.runtime.base / "copy" / "sandbox.hosting.yml")
        with self.assertRaisesRegex(hosting.HostingRefusal, "runtime path"):
            hosting.validate_manifest(copy)

    def test_rendered_source_root_must_be_a_stack_worktree_checkout(self):
        loose = self.runtime.runtime / "stacks" / "demo" / "source" / "loose"
        loose.mkdir(parents=True)
        (loose / "compose.yml").write_text(COMPOSE)
        target = self._write(self._render(loose), self.runtime.runtime / "stacks" / "demo" / "sandbox.hosting.yml")
        with self.assertRaisesRegex(hosting.HostingError, "Git checkout"):
            hosting.validate_manifest(target)
        # Any other checkout, even a clean one, is not the stack's source.
        self._write(self._render(self.hub), target)
        with self.assertRaisesRegex(hosting.HostingError, "worktree"):
            hosting.validate_manifest(target)

    def test_env_file_reaches_compose_ports(self):
        target = self._write(self._render(self._worktree()), self.runtime.runtime / "stacks" / "demo" / "sandbox.hosting.yml")
        validated = hosting.validate_manifest(target)
        env_text = hosting.render_env_file(
            validated, {"XSPEED_HUB_SBX_DEMO_PG_PASSWORD": "synthetic-value"})
        self.assertIn("MAILPIT_PORT=18142\n", env_text)
        env_path = self.runtime.base / "environment.env"
        env_path.write_text(env_text)
        docker = shutil.which("docker")
        if not docker:
            self.skipTest("docker is not installed; env-file content asserted above")
        result = run_test_process(
            (docker, "compose", "-p", "stackcheck", "--env-file", str(env_path),
             "-f", str(self.hub / "compose.yml"), "config", "--format", "json"),
            env={"HOME": str(self.runtime.base), "PATH": os.path.dirname(docker)},
            capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            self.skipTest("docker compose config is unavailable here")
        import json
        ports = json.loads(result.stdout)["services"]["mailpit"]["ports"]
        self.assertEqual(str(ports[0]["published"]), "18142")
        self.assertEqual(ports[0]["host_ip"], "127.0.0.1")


class StackCommandTests(unittest.TestCase):
    def setUp(self):
        self.runtime = _Runtime(self)
        self.hub = self.runtime.checkout()
        self.head = _git(self.hub, "rev-parse", "HEAD")
        self.entry = {"name": "r", "front_door": "nginx"}
        for target, value in ((host_stack.remote, "get_remote"),):
            patcher = patch.object(target, value, return_value=self.entry)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.probe = patch.object(host_stack, "probe_ports", return_value={})
        self.probe.start()
        self.addCleanup(self.probe.stop)
        state = patch.object(host_stack.hosting, "load_host_state",
                             return_value={"version": 1, "hosts": {}})
        state.start()
        self.addCleanup(state.stop)
        secrets_status = patch.object(hosting_cmd, "_secret_status",
                                      return_value=({"XSPEED_HUB_SBX_DEMO_PG_PASSWORD": "x"}, []))
        secrets_status.start()
        self.addCleanup(secrets_status.stop)
        # Spec 077: the remote lifetime store runs in-process against a temp home.
        self.remote_home = self.runtime.base / "remote-home"
        self.owner = dict(OWNER_A)
        store = patch.object(host_stack, "_store",
                             side_effect=lambda entry, caller: lifetime_client.StoreClient(
                                 self._store_runner, caller))
        store.start()
        self.addCleanup(store.stop)
        owner = patch.object(host_stack, "_caller", side_effect=lambda checkout, args: dict(self.owner))
        owner.start()
        self.addCleanup(owner.stop)

    def _store_runner(self, command, payload):
        response = hoststore.handle(json.loads(payload), str(self.remote_home), free_paths=())
        return SimpleNamespace(returncode=0, stdout=json.dumps(response) + "\n", stderr="")

    def remote_records(self):
        records, _invalid = hoststore.Store(self.remote_home).load()
        return records

    def second_controller(self):
        """A fresh controller runtime against the same remote."""
        runtime = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, runtime, True)
        patcher = patch("sandbox.core._paths.RUNTIME_DIR", runtime)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.owner = dict(OWNER_B)
        return runtime

    def args(self, action, **values):
        base = dict(stack_action=action, template=str(self.hub / "sandbox.hosting.yml"), name="demo",
                    ref="stack/demo", port=None, remote="r", plan=False, confirm=False, json=True,
                    environment=None, request_id=None, generate_secrets=False, lock_wait=600,
                    lifetime=None, ttl=None, owner=None, copy=None, force_owner=False)
        base.update(values)
        return SimpleNamespace(**base)

    def test_resolve_ref_kinds(self):
        self.assertEqual(host_stack.resolve_ref(self.hub, "stack/demo").ref_branch, "stack/demo")
        self.assertEqual(host_stack.resolve_ref(self.hub, None).ref_branch, "main")
        detached = host_stack.resolve_ref(self.hub, self.head[:10])
        self.assertEqual((detached.commit, detached.ref_branch), (self.head, ""))
        _git(self.hub, "update-ref", "refs/remotes/origin/feat/x", self.head)
        self.assertEqual(host_stack.resolve_ref(self.hub, "origin/feat/x").ref_branch, "feat/x")
        for ref in ("nosuch", "-x"):
            with self.subTest(ref=ref), self.assertRaises(host_stack.StackCommandError) as caught:
                host_stack.resolve_ref(self.hub, ref)
            self.assertEqual(caught.exception.code, "stack_ref_unresolved")

    def test_plan_changes_nothing(self):
        before = _git(self.hub, "worktree", "list", "--porcelain")
        result = host_stack._up(self.args("up", plan=True))
        self.assertEqual(result["mode"], "plan")
        self.assertEqual(result["project"], "xspeed-hub-demo")
        self.assertIn(result["ports"]["mailpit"], stack_registry.STACK_PORT_RANGE)
        self.assertEqual(result["commit"], self.head)
        self.assertIn("job-start", result["submission"])
        self.assertFalse((self.runtime.runtime / "stacks").exists())
        self.assertEqual(_git(self.hub, "worktree", "list", "--porcelain"), before)

    def test_up_without_mode_and_with_both_modes_is_refused(self):
        for values in ({}, {"plan": True, "confirm": True}):
            with self.subTest(values=values), self.assertRaises(host_stack.StackCommandError) as caught:
                host_stack._up(self.args("up", **values))
            self.assertEqual(caught.exception.code, "stack_confirmation_required")

    def test_confirm_writes_manifest_worktree_record_and_submits_once(self):
        with patch.object(host_stack, "_submit", return_value="job-1") as submit:
            result = host_stack._up(self.args("up", confirm=True))
        submit.assert_called_once()
        argv = submit.call_args.args[0]
        self.assertEqual(argv[1:3], ["job-start", "--local"])
        self.assertEqual(argv[argv.index("--source-commit") + 1], self.head)
        self.assertEqual(argv[argv.index("--") + 2:argv.index("--") + 4], ["host", "apply"])
        manifest = self.runtime.runtime / "stacks" / "demo" / "sandbox.hosting.yml"
        self.assertTrue(manifest.is_file())
        worktree = Path(result["source_root"])
        # A template at the checkout root: the job binds the worktree root itself.
        self.assertEqual(argv[argv.index("--project-dir") + 1], str(worktree))
        self.assertEqual(Path(_git(worktree, "rev-parse", "--show-toplevel")).resolve(), worktree)
        self.assertEqual(_git(worktree, "rev-parse", "HEAD"), self.head)
        self.assertEqual(_git(worktree, "status", "--porcelain"), "")
        record = stack_registry.read()["stacks"]["r/demo"]
        self.assertEqual(record["ports"], result["ports"])
        self.assertEqual(record["last_apply"]["job_id"], "job-1")
        # A second up reuses the port and replaces the earlier worktree.
        with patch.object(host_stack, "job_view", return_value={"lifecycle": "failed"}), \
                patch.object(host_stack, "_submit", return_value="job-2"):
            again = host_stack._up(self.args("up", confirm=True))
        self.assertEqual(again["ports"], result["ports"])
        self.assertFalse(worktree.exists())
        self.assertEqual(stack_registry.read()["stacks"]["r/demo"]["worktrees"], [again["source_root"]])

    def test_subfolder_template_job_binds_the_source_root_its_apply_admits(self):
        """Feedback 20153592: a template in a checkout subfolder (`stacks/wp-site/`)."""
        hub = self.runtime.checkout(name="setup", subdir="stacks/wp-site")
        template = str(hub / "stacks" / "wp-site" / "sandbox.hosting.yml")
        plan = host_stack._up(self.args("up", plan=True, template=template))["submission"]
        planned = plan[:plan.index("--")]
        self.assertTrue(planned[planned.index("--project-dir") + 1].endswith("<token>/stacks/wp-site"))
        self.assertIn("--exact-project-dir", planned)
        with patch.object(host_stack, "_submit", return_value="job-1") as submit:
            result = host_stack._up(self.args("up", confirm=True, template=template))
        argv = submit.call_args.args[0]
        job, child = argv[:argv.index("--")], argv[argv.index("--") + 1:]
        source_root = Path(result["source_root"])
        worktree = Path(_git(source_root, "rev-parse", "--show-toplevel")).resolve()
        self.assertEqual(source_root, worktree / "stacks" / "wp-site")
        # host apply admits the job against the rendered manifest's source root,
        # so job-start must record exactly that folder, not the worktree root.
        manifest_root = self.runtime.runtime / "stacks" / "demo"
        validated = hosting.validate_manifest(str(manifest_root), "sandbox")
        self.assertEqual(validated["project_root"], str(source_root))
        self.assertEqual(job[job.index("--project-dir") + 1], validated["project_root"])
        self.assertIn("--exact-project-dir", job)
        self.assertEqual(child[child.index("--project-dir") + 1], str(manifest_root))
        self.assertEqual(stack_registry.read()["stacks"]["r/demo"]["template"], template)

    def test_record_commit_moves_only_when_its_apply_proves_delivery(self):
        """Feedback 20153592: `stack list` must not run ahead of the server."""
        manifest_root = self.runtime.runtime / "stacks" / "demo"

        def apply_finished(succeeded, request_id=None):
            """What `sb host apply` does for the rendered manifest once it finishes."""
            validated = hosting.validate_manifest(str(manifest_root), "sandbox")
            last = stack_registry.read()["stacks"]["r/demo"]["last_apply"]
            hosting_cmd._record_stack_delivery(validated, "r", {
                "delivery_succeeded": succeeded, "delivery_state": "succeeded" if succeeded else "failed",
                "request_id": request_id or last["request_id"]})

        def listed():
            payload = host_stack._list(self.args("list"))
            return payload["stacks"][0], host_stack._render_text(payload)

        with patch.object(host_stack, "job_view", return_value=None):
            with patch.object(host_stack, "_submit", return_value="job-1"):
                host_stack._up(self.args("up", confirm=True))
            record = stack_registry.read()["stacks"]["r/demo"]
            self.assertIsNone(record["commit"])
            self.assertEqual(record["last_apply"]["commit"], self.head)
            # The remote lifetime record holds the claimed commit; the row does not.
            self.assertEqual(self.remote_records()["sandbox-host-xspeed-hub-demo-sandbox"].stack.commit,
                             self.head)
            apply_finished(False)
            row, text = listed()
            self.assertIsNone(row["commit"])
            self.assertEqual(row["last_apply"]["commit"], self.head)
            self.assertIn("@not delivered", text)
            apply_finished(True, request_id="another-request")
            self.assertIsNone(listed()[0]["commit"])
            apply_finished(True)
            self.assertEqual(listed()[0]["commit"], self.head)
            # A newer commit on the branch whose apply fails.
            tree = _git(self.hub, "rev-parse", "HEAD^{tree}")
            newer = _git(self.hub, "commit-tree", tree, "-p", self.head, "-m", "next")
            _git(self.hub, "update-ref", "refs/heads/stack/demo", newer)
            with patch.object(host_stack, "_submit", return_value="job-2"):
                again = host_stack._up(self.args("up", confirm=True))
            self.assertEqual(again["commit"], newer)
            record = stack_registry.read()["stacks"]["r/demo"]
            self.assertEqual((record["commit"], record["last_apply"]["commit"]), (self.head, newer))
            apply_finished(False)
            row, text = listed()
            self.assertEqual((row["commit"], row["last_apply"]["commit"]), (self.head, newer))
            self.assertIn(f"@{self.head[:12]}", text)
            self.assertIn(f"commit {newer[:12]}", text)
            apply_finished(True)
            self.assertEqual(listed()[0]["commit"], newer)

    def test_live_sequence_failed_then_completed_apply_moves_the_commit(self):
        """Feedback 94bf4610: a pre-fix record held the commit of an apply that failed;
        the next apply completed with an `incomplete` delivery summary (exit 0,
        required_evidence_missing) and `commit` stayed at the undelivered commit."""
        manifest_root = self.runtime.runtime / "stacks" / "demo"
        jobs: dict[str, str] = {}

        def view(job_id):
            return {"lifecycle": jobs[job_id], "finished_at": None} if job_id in jobs else None

        def row():
            return host_stack._list(self.args("list"))["stacks"][0]

        with patch.object(host_stack, "job_view", side_effect=view):
            with patch.object(host_stack, "_submit", return_value="job-1"):
                host_stack._up(self.args("up", confirm=True))
            # Rewrite it the way the pre-fix `up` did: commit = requested, no last_apply.commit.
            with stack_registry.locked() as registry:
                legacy = registry.get("r", "demo")
                legacy["commit"] = self.head
                legacy["last_apply"].pop("commit")
                registry.put(legacy)
            jobs["job-1"] = "failed"
            tree = _git(self.hub, "rev-parse", "HEAD^{tree}")
            newer = _git(self.hub, "commit-tree", tree, "-p", self.head, "-m", "next")
            _git(self.hub, "update-ref", "refs/heads/stack/demo", newer)
            with patch.object(host_stack, "_submit", return_value="job-2"):
                host_stack._up(self.args("up", confirm=True))
            jobs["job-2"] = "running"
            self.assertEqual(row()["commit"], self.head)  # a pre-fix value cannot be told apart
            validated = hosting.validate_manifest(str(manifest_root), "sandbox")
            request_id = stack_registry.read()["stacks"]["r/demo"]["last_apply"]["request_id"]
            hosting_cmd._record_stack_delivery(validated, "r", {
                "request_id": request_id, "delivery_state": "incomplete", "delivery_succeeded": False})
            jobs["job-2"] = "succeeded"
            self.assertEqual(stack_registry.read()["stacks"]["r/demo"]["commit"], newer)
            self.assertEqual(row()["commit"], newer)

    def test_a_succeeded_apply_counts_even_when_its_record_write_was_missed(self):
        jobs = {"job-1": "running"}
        with patch.object(host_stack, "job_view",
                          side_effect=lambda job_id: {"lifecycle": jobs[job_id]} if job_id in jobs else None):
            with patch.object(host_stack, "_submit", return_value="job-1"):
                host_stack._up(self.args("up", confirm=True))
            self.assertIsNone(host_stack._list(self.args("list"))["stacks"][0]["commit"])
            jobs["job-1"] = "succeeded"  # the apply finished; record_delivery never ran
            self.assertIsNone(stack_registry.read()["stacks"]["r/demo"]["commit"])
            self.assertEqual(host_stack._list(self.args("list"))["stacks"][0]["commit"], self.head)
            # The next `up` carries the delivered commit, not the stale stored one.
            with patch.object(host_stack, "_submit", return_value="job-2"):
                host_stack._up(self.args("up", confirm=True))
            self.assertEqual(stack_registry.read()["stacks"]["r/demo"]["commit"], self.head)

    def test_a_failed_delivery_never_moves_the_commit(self):
        manifest_root = self.runtime.runtime / "stacks" / "demo"
        with patch.object(host_stack, "job_view", return_value=None):
            with patch.object(host_stack, "_submit", return_value="job-1"):
                host_stack._up(self.args("up", confirm=True))
            validated = hosting.validate_manifest(str(manifest_root), "sandbox")
            request_id = stack_registry.read()["stacks"]["r/demo"]["last_apply"]["request_id"]
            for state in ("failed", "unknown", None):
                with self.subTest(state=state):
                    hosting_cmd._record_stack_delivery(validated, "r", {
                        "request_id": request_id, "delivery_state": state, "delivery_succeeded": False})
                    self.assertIsNone(stack_registry.read()["stacks"]["r/demo"]["commit"])
            hosting_cmd._record_stack_delivery(validated, "r", None)
            self.assertIsNone(stack_registry.read()["stacks"]["r/demo"]["commit"])

    def test_failed_submission_keeps_the_delivered_commit(self):
        with patch.object(host_stack, "job_view", return_value={"lifecycle": "succeeded"}):
            with patch.object(host_stack, "_submit", return_value="job-1"):
                host_stack._up(self.args("up", confirm=True))
            last = stack_registry.read()["stacks"]["r/demo"]["last_apply"]
            self.assertTrue(stack_registry.record_delivery(
                "r", "demo", request_id=last["request_id"], commit=self.head))
            failure = host_stack.StackCommandError("stack_submission_failed", "refused")
            with patch.object(host_stack, "_submit", side_effect=failure), \
                    self.assertRaises(host_stack.StackCommandError):
                host_stack._up(self.args("up", confirm=True))
        record = stack_registry.read()["stacks"]["r/demo"]
        self.assertEqual(record["commit"], self.head)
        self.assertEqual(record["last_apply"]["status"], "submission_failed")

    def test_up_refused_while_previous_job_is_active(self):
        with patch.object(host_stack, "_submit", return_value="job-1"):
            host_stack._up(self.args("up", confirm=True))
        with patch.object(host_stack, "job_view", return_value={"lifecycle": "running"}), \
                self.assertRaises(host_stack.StackCommandError) as caught:
            host_stack._up(self.args("up", confirm=True))
        self.assertEqual(caught.exception.code, "stack_apply_in_progress")

    def test_failed_submission_keeps_ports(self):
        failure = host_stack.StackCommandError("stack_submission_failed", "refused")
        with patch.object(host_stack, "_submit", side_effect=failure), \
                self.assertRaises(host_stack.StackCommandError):
            host_stack._up(self.args("up", confirm=True))
        record = stack_registry.read()["stacks"]["r/demo"]
        self.assertEqual(record["last_apply"]["status"], "submission_failed")
        self.assertIn("mailpit", record["ports"])

    def test_hostname_owned_by_other_project_refuses_before_any_change(self):
        state = {"version": 1, "hosts": {"r/other/production": {"hostnames": ["admin-demo.example.test"]}}}
        with patch.object(host_stack.hosting, "load_host_state", return_value=state), \
                patch.object(host_stack, "_submit") as submit, \
                self.assertRaises(hostname_ownership.HostnameOwnedError):
            host_stack._up(self.args("up", confirm=True))
        submit.assert_not_called()
        self.assertFalse((self.runtime.runtime / "stacks" / "demo" / "source").exists())

    def test_missing_generated_secret_needs_generate(self):
        with patch.object(hosting_cmd, "_secret_status",
                          return_value=({}, ["XSPEED_HUB_SBX_DEMO_PG_PASSWORD"])), \
                self.assertRaises(host_stack.StackCommandError) as caught:
            host_stack._up(self.args("up", confirm=True))
        self.assertEqual(caught.exception.code, "stack_secrets_missing")
        with patch.object(hosting_cmd, "_secret_status",
                          return_value=({}, ["XSPEED_HUB_SBX_DEMO_PG_PASSWORD"])), \
                patch.object(hosting_cmd.personal_secrets, "write_secret") as write, \
                patch.object(hosting_cmd.personal_secrets, "generate_secret", return_value="generated"), \
                patch.object(host_stack, "_submit", return_value="job-1"):
            host_stack._up(self.args("up", confirm=True, generate_secrets=True))
        write.assert_called_once_with("XSPEED_HUB_SBX_DEMO_PG_PASSWORD", "generated")

    def test_list_and_down(self):
        with patch.object(host_stack, "_submit", return_value="job-1"):
            created = host_stack._up(self.args("up", confirm=True))
        listed = host_stack._list(self.args("list"))
        self.assertEqual([item["slug"] for item in listed["stacks"]], ["demo"])
        self.assertEqual(listed["stacks"][0]["ports"], created["ports"])
        with self.assertRaises(host_stack.StackCommandError) as caught:
            host_stack._down(self.args("down"))
        self.assertEqual(caught.exception.code, "stack_confirmation_required")
        from sandbox.hosting import destroy
        with patch.object(destroy, "execute", side_effect=destroy.DestroyError("boom", "remote_probe_failed")), \
                self.assertRaises(host_stack.StackCommandError) as caught:
            host_stack._down(self.args("down", confirm=True))
        self.assertEqual(caught.exception.code, "stack_destroy_failed")
        self.assertIn("r/demo", stack_registry.read()["stacks"])
        with patch.object(destroy, "execute", return_value={"state": "destroyed"}) as execute:
            down = host_stack._down(self.args("down", confirm=True))
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[2:4], ("xspeed-hub-demo", "sandbox"))
        self.assertEqual(down["released_ports"], created["ports"])
        self.assertNotIn("r/demo", stack_registry.read()["stacks"])
        self.assertFalse(Path(created["source_root"]).exists())
        with self.assertRaises(host_stack.StackCommandError) as caught:
            host_stack._down(self.args("down", confirm=True))
        self.assertEqual(caught.exception.code, "stack_not_found")

    def test_plan_checks_the_remote_without_claiming(self):
        result = host_stack._up(self.args("up", plan=True))
        self.assertEqual(result["lifetime"], "temporary")
        self.assertEqual(result["ttl_seconds"], 14 * 86400)
        self.assertEqual(result["owner"]["label"], "alice")
        self.assertEqual(self.remote_records(), {})

    def test_confirm_claims_the_remote_record_and_mirrors_it(self):
        with patch.object(host_stack, "_submit", return_value="job-1"):
            result = host_stack._up(self.args("up", confirm=True))
        self.assertRegex(result["copy_id"], r"^[0-9a-f]{8}$")
        record = self.remote_records()["sandbox-host-xspeed-hub-demo-sandbox"]
        self.assertEqual((record.stack.slug, record.stack.copy_id), ("demo", result["copy_id"]))
        self.assertEqual(record.stack.ports, result["ports"])
        self.assertEqual(sorted(record.hostnames), sorted(result["hostnames"]))
        local = stack_registry.read()["stacks"]["r/demo"]
        self.assertEqual((local["copy_id"], local["lifetime"]), (result["copy_id"], "temporary"))
        with patch.object(host_stack, "_submit", return_value="job-2"), \
                patch.object(host_stack, "job_view", return_value={"lifecycle": "succeeded"}):
            again = host_stack._up(self.args("up", confirm=True))
        self.assertEqual(again["copy_id"], result["copy_id"])
        self.assertEqual(again["expires_at"], result["expires_at"])

    def test_another_owner_cannot_take_the_name_over(self):
        with patch.object(host_stack, "_submit", return_value="job-1"):
            host_stack._up(self.args("up", confirm=True))
        before = self.remote_records()
        other_runtime = self.second_controller()
        with patch.object(host_stack, "_submit") as submit, \
                self.assertRaises(host_stack.hosting.HostingRefusal) as caught:
            host_stack._up(self.args("up", confirm=True))
        self.assertEqual(caught.exception.code, "stack_slug_taken")
        self.assertIn("alice", caught.exception.detail)
        submit.assert_not_called()
        self.assertFalse((other_runtime / "stacks" / "demo").exists())
        self.assertEqual(self.remote_records(), before)

    def test_unnamed_copies_get_distinct_names_and_ports(self):
        with patch.object(host_stack, "_submit", return_value="job-1"):
            first = host_stack._up(self.args("up", confirm=True, name=None))
            second = host_stack._up(self.args("up", confirm=True, name=None))
        self.assertEqual((first["slug"], second["slug"]), ("demo-alice-1", "demo-alice-2"))
        self.assertTrue(first["generated_name"])
        self.assertNotEqual(first["ports"], second["ports"])
        self.assertNotEqual(first["project"], second["project"])
        self.assertFalse(set(first["hostnames"]) & set(second["hostnames"]))

    def test_hosted_needs_the_dev_branch(self):
        with self.assertRaises(host_stack.StackCommandError) as caught:
            host_stack._up(self.args("up", plan=True, lifetime="hosted"))
        self.assertEqual(caught.exception.code, "stack_lifetime_invalid")
        _git(self.hub, "branch", "dev")
        result = host_stack._up(self.args("up", plan=True, lifetime="hosted", ref="dev", name="dev"))
        self.assertTrue(result["canonical"])
        self.assertIsNone(result["expires_at"])
        with self.assertRaises(host_stack.StackCommandError) as caught:
            host_stack._up(self.args("up", plan=True, lifetime="permanent", ttl="3d"))
        self.assertEqual(caught.exception.code, "stack_ttl_invalid")

    def test_ttl_and_owner_checks(self):
        with patch.object(host_stack, "_submit", return_value="job-1"):
            host_stack._up(self.args("up", confirm=True))
        changed = host_stack._ttl(self.args("ttl", ttl="1h", confirm=True))
        self.assertEqual(changed["ttl_seconds"], 3600)
        self.assertEqual(stack_registry.read()["stacks"]["r/demo"]["ttl_seconds"], 3600)
        self.second_controller()
        with self.assertRaises(host_stack.hosting.HostingRefusal) as caught:
            host_stack._ttl(self.args("ttl", ttl="2d", confirm=True))
        self.assertEqual(caught.exception.code, "stack_not_owner")
        forced = host_stack._ttl(self.args("ttl", ttl="2d", confirm=True, force_owner=True))
        self.assertEqual(forced["ttl_seconds"], 2 * 86400)
        record = self.remote_records()["sandbox-host-xspeed-hub-demo-sandbox"]
        self.assertEqual(record.overrides[-1]["by"]["label"], "bob")
        self.assertEqual(record.extended_count, 2)
        from sandbox.hosting import destroy
        with patch.object(destroy, "execute", return_value={"state": "destroyed"}) as execute, \
                self.assertRaises(host_stack.hosting.HostingRefusal) as caught:
            host_stack._down(self.args("down", confirm=True))
        self.assertEqual(caught.exception.code, "stack_not_owner")
        execute.assert_not_called()
        with patch.object(destroy, "execute", return_value={"state": "destroyed"}) as execute:
            down = host_stack._down(self.args("down", confirm=True, force_owner=True))
        execute.assert_called_once()
        self.assertTrue(down["forced_owner"])
        self.assertEqual(self.remote_records(), {})

    def test_list_shows_every_controllers_stacks(self):
        with patch.object(host_stack, "_submit", return_value="job-1"):
            host_stack._up(self.args("up", confirm=True))
        self.second_controller()
        with patch.object(host_stack, "_submit", return_value="job-1"):
            host_stack._up(self.args("up", confirm=True, name="other"))
        listed = host_stack._list(self.args("list"))
        rows = {row["slug"]: row for row in listed["stacks"]}
        self.assertEqual(set(rows), {"demo", "other"})
        self.assertEqual(rows["demo"]["owner"]["label"], "alice")
        self.assertEqual(rows["demo"]["schema_version"], 1)
        self.assertFalse(rows["demo"]["mine"])
        self.assertTrue(rows["other"]["mine"])
        self.assertEqual(rows["demo"]["remote_record"], "present")

    def test_failed_worktree_releases_a_fresh_claim(self):
        with patch.object(host_stack, "_add_worktree", side_effect=RuntimeError("git worktree add failed")), \
                self.assertRaises(RuntimeError):
            host_stack._up(self.args("up", confirm=True))
        self.assertEqual(self.remote_records(), {})

    def test_slug_on_another_remote_is_refused(self):
        with patch.object(host_stack, "_submit", return_value="job-1"):
            host_stack._up(self.args("up", confirm=True))
        with self.assertRaises(host_stack.StackCommandError) as caught:
            host_stack._up(self.args("up", plan=True, remote="r2"))
        self.assertEqual(caught.exception.code, "stack_slug_in_use")


class HostDispatchTests(unittest.TestCase):
    def test_stack_actions_dispatch_and_image_mixups_are_refused(self):
        with patch("sandbox.commands.host_stack.cmd_host_stack") as handler:
            hosting_cmd.cmd_host({}, SimpleNamespace(action="stack", image_action="list", remote="r", json=True))
        self.assertEqual(handler.call_args.args[0].stack_action, "list")
        for action, sub in (("image", "up"), ("stack", "status"), ("stack", None)):
            with self.subTest(action=action, sub=sub), self.assertRaises(SystemExit):
                hosting_cmd.cmd_host({}, SimpleNamespace(action=action, image_action=sub, remote="r", json=True))

    def test_cli_parses_stack_up(self):
        import json
        import sys
        from contextlib import redirect_stdout
        from io import StringIO
        import sandbox.cli as cli
        argv = ["sb", "host", "stack", "up", "--template", "/t/sandbox.hosting.yml", "--name", "demo",
                "--ref", "stack/demo", "--port", "mailpit=8025", "--remote", "r", "--plan", "--json"]
        seen = []
        forbidden = AssertionError("compatibility writer reached host stack")
        with patch.object(sys, "argv", argv), redirect_stdout(StringIO()), \
                patch.object(cli, "load_config", return_value={}), \
                patch.object(cli, "resolve_instances", return_value={}), \
                patch.object(cli, "_cwd_instance", return_value=None), \
                patch.object(cli, "_core", return_value=SimpleNamespace(registry_all=lambda: {})), \
                patch("sandbox.commands.migrate.maybe_auto_migrate", side_effect=forbidden), \
                patch("sandbox.commands.migrate.finalize_auto_migration", side_effect=forbidden), \
                patch.object(cli, "write_compose_files", side_effect=forbidden), \
                patch.object(cli, "write_env_for_compose", side_effect=forbidden), \
                patch("sandbox.commands.host_stack.cmd_host_stack", side_effect=seen.append):
            try:
                cli.main()
            except SystemExit as exc:
                self.assertIn(exc.code, (0, None))
        self.assertEqual(len(seen), 1)
        args = seen[0]
        self.assertEqual((args.action, args.stack_action, args.port, args.plan, args.name, args.ref),
                         ("stack", "up", ["mailpit=8025"], True, "demo", "stack/demo"))

if __name__ == "__main__":
    unittest.main()
