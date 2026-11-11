"""Disabled dispatch boundaries; stateless refusals, never admission/replay proof."""
from argparse import Namespace
from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
import io
import json
import unittest
from unittest.mock import patch

from sandbox.commands import hosting as command, host_stack
from sandbox.hosting.credential_selection import INVALID, UNSUPPORTED, INVENTORY_UNAVAILABLE
from tests.test_host_credential_selection import server_fields


def arguments(**overrides):
    return Namespace(**{**server_fields(), "action": "apply", "json": True, **overrides})


@contextmanager
def forbidden_boundaries():
    """Patch the owners actually called by legacy paths, never the new guard."""
    targets = (
        "sandbox.commands.hosting._secret_status",
        "sandbox.commands.hosting._acquire_host_apply_lease",
        "sandbox.commands.hosting._prepare_host_apply",
        "sandbox.commands.hosting._validate_apply_source",
        "sandbox.commands.hosting._host_edge",
        "sandbox.core._secrets.read_secret_file", "sandbox.core._secrets.resolve_secret",
        "sandbox.core._secrets.write_secret", "sandbox.core._secrets.generate_secret",
        "sandbox.core._secrets.read_hosting_binding_metadata",
        "sandbox.core._remote.get_remote", "sandbox.core._remote.ssh_run",
        "sandbox.core._hosting.load_host_state", "sandbox.core._hosting.save_host_state",
        "sandbox.core._hosting.load_manifest", "sandbox.core._hosting.validate_manifest",
        "sandbox.commands.host_stack._entry", "sandbox.commands.host_stack._store",
        "sandbox.commands.host_stack._caller", "sandbox.commands.host_stack.template_checkout",
        "sandbox.commands.host_stack._add_worktree", "sandbox.commands.host_stack._export_compose_files",
        "sandbox.commands.host_stack._submit", "sandbox.commands.host_stack.job_view",
        "sandbox.hosting.stack_registry.read", "sandbox.hosting.stack_registry.locked",
        "sandbox.hosting.lifetime.client.StoreClient.call",
        "sandbox.core._cloudflare.Client", "subprocess.Popen", "socket.socket",
        "pathlib.Path.write_text", "pathlib.Path.write_bytes",
    )
    with ExitStack() as stack:
        spies = [stack.enter_context(patch(target, side_effect=AssertionError("forbidden boundary: " + target)))
                 for target in targets]
        yield spies
        for spy in spies:
            spy.assert_not_called()


class DisabledServerCredentialTests(unittest.TestCase):
    def refusal(self, call, code=UNSUPPORTED, *, text=False):
        output, errors = io.StringIO(), io.StringIO()
        with forbidden_boundaries(), redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as caught:
                call()
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(errors.getvalue(), "")
        if text:
            self.assertEqual(output.getvalue(), code + ": execution disabled; credential availability unverified."
                             + (" Inventory unavailable." if code == INVENTORY_UNAVAILABLE else "") + "\n")
        else:
            expected = dict(ok=False, code=code, execution="disabled", availability="unverified")
            if code == INVENTORY_UNAVAILABLE:
                expected.update(scope="server", inventory="unavailable")
            self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertNotIn("SYNTHETIC_SENTINEL", output.getvalue())
        return output.getvalue()

    def test_public_host_actions_refuse_before_legacy_work(self):
        for action in ("apply", "stage", "image", "sync", "plan", "stack"):
            for mode in ("plan", "confirm"):
                args = arguments(action=action, image_action="activate", **{mode: True})
                with self.subTest(action=action, mode=mode):
                    self.refusal(lambda: command.cmd_host({}, args))

    def test_direct_handlers_do_not_trust_validate_action_or_context(self):
        # A copied action/context cannot convert an effectful entry into static validation.
        args = arguments(action="validate", confirm=True, plan=True,
                         owner_context="SYNTHETIC_SENTINEL", admission="fake", capability=True)
        calls = [lambda: command._cmd_host_stage(args),
                 lambda: command._cmd_host_stage_discover(args),
                 lambda: command._cmd_host_image_build_local(args),
                 lambda: command._cmd_host_image_push_local(args),
                 lambda: command._cmd_host_image_storage_admission(args),
                 lambda: command._cmd_host_image_verify(args),
                 lambda: command._cmd_host_image({}, args),
                 lambda: command._cmd_host_sync({}, {}, "fixture-server", args),
                 lambda: command._cmd_host_secrets({}, args)]
        for index, call in enumerate(calls):
            with self.subTest(handler=index):
                self.refusal(call)

    def test_stack_direct_and_dispatch_modes_refuse_before_store_claim_source_or_job(self):
        for action in host_stack.STACK_ACTIONS:
            for mode in ("plan", "confirm"):
                args = arguments(action="stack", stack_action=action, **{mode: True})
                for handler in (host_stack.cmd_host_stack, getattr(host_stack, "_" + action)):
                    with self.subTest(action=action, mode=mode, handler=handler.__name__):
                        self.refusal(lambda: handler(args))

    def test_validated_dictionary_guards_precede_apply_lease_and_effects(self):
        for action in ("apply", "validate"):
            validated = {**server_fields(), "action": action, "json": True,
                         "owner_context": "SYNTHETIC_SENTINEL", "capability": True}
            calls = [lambda: command._apply_host(validated, {}, "fixture-server", {}),
                     lambda: command._apply_host_unleased(validated, {}, "fixture-server", {}, {}, False, "fixture"),
                     lambda: command._continue_host_edge_only(validated, {}, "fixture-server", None, {})]
            for index, call in enumerate(calls):
                with self.subTest(action=action, engine=index):
                    self.refusal(call)

    def test_inventory_is_unavailable_without_presence_or_validity_probe(self):
        for text in (False, True):
            args = arguments(action="secrets", json=not text)
            self.refusal(lambda: command.host_preconfiguration_dispatch(args), INVENTORY_UNAVAILABLE, text=text)

    def test_generate_and_set_refuse_even_for_static_or_inventory_actions(self):
        for action in ("validate", "secrets", "apply"):
            for option in ({"generate_secrets": True}, {"set_secret": "SYNTHETIC_SENTINEL"}):
                args = arguments(action=action, **option)
                with self.subTest(action=action, option=option):
                    self.refusal(lambda: command.host_preconfiguration_dispatch(args))

    def test_repeated_valid_and_invalid_requests_emit_fixed_stateless_refusals(self):
        for text in (False, True):
            for remote, code in (("fixture-server", UNSUPPORTED), ("SYNTHETIC_SENTINEL/invalid", INVALID)):
                args = arguments(remote=remote, json=not text, request_id="SYNTHETIC_SENTINEL")
                outputs = [self.refusal(lambda: command.cmd_host({}, args), code, text=text) for _ in range(3)]
                self.assertEqual(outputs, [outputs[0]] * 3)
