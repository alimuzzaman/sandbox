import unittest
import argparse


class TestCommandComposition(unittest.TestCase):
    def test_specs_are_deterministic_and_duplicates_fail(self):
        from sandbox.registry import CommandRegistry, CommandSpec

        registry = CommandRegistry()
        registry.add(CommandSpec("zeta", lambda _: None, owner="tests", order=20))
        registry.add(CommandSpec("alpha", lambda _: None, owner="tests", order=10))
        self.assertEqual([item.name for item in registry.specs()], ["alpha", "zeta"])
        with self.assertRaisesRegex(ValueError, "duplicate command"):
            registry.add(CommandSpec("alpha", lambda _: None, owner="other"))

    def test_alias_collision_fails(self):
        from sandbox.registry import CommandRegistry, CommandSpec

        registry = CommandRegistry()
        registry.add(CommandSpec("first", lambda _: None, aliases=("f",), owner="tests"))
        with self.assertRaisesRegex(ValueError, "duplicate command or alias"):
            registry.add(CommandSpec("second", lambda _: None, aliases=("f",), owner="tests"))

    def test_feature_command_composes_without_central_parser_edit(self):
        from sandbox.registry import CommandSpec, compose_missing_parsers

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")

        def configure(command):
            command.add_argument("--value", required=True)

        spec = CommandSpec(
            "fixture-command", lambda *_: None, owner="tests", order=10,
            configure=configure, scope="global",
        )
        self.assertEqual(compose_missing_parsers(sub, (spec,)), ("fixture-command",))
        self.assertEqual(compose_missing_parsers(sub, (spec,)), ())
        args = parser.parse_args(["fixture-command", "--value", "ok"])
        self.assertEqual((args.cmd, args.value), ("fixture-command", "ok"))

    def test_builtin_manifest_is_explicit_and_complete(self):
        from sandbox.commands.manifest import (
            BUILTIN_COMMAND_MODULES,
            LEGACY_BRIDGE_COMMANDS,
            load_builtin_commands,
            validate_builtin_command_coverage,
        )
        from sandbox.registry import COMMANDS, COMMAND_SPECS

        load_builtin_commands()
        self.assertEqual(len(BUILTIN_COMMAND_MODULES), len(set(BUILTIN_COMMAND_MODULES)))
        self.assertEqual(set(COMMANDS), set(COMMAND_SPECS.names()))
        self.assertEqual(len(COMMANDS), 89)
        self.assertIn("sandbox.commands.activation", BUILTIN_COMMAND_MODULES)
        self.assertIn("sandbox.commands.recovery", BUILTIN_COMMAND_MODULES)
        self.assertIn("sandbox.commands.domains", BUILTIN_COMMAND_MODULES)
        self.assertIn("sandbox.commands.jobs_runtime", BUILTIN_COMMAND_MODULES)
        self.assertIn("sandbox.commands.workspaces", BUILTIN_COMMAND_MODULES)
        self.assertEqual(set(COMMANDS) - {"secrets", "init", "activation"},
                         set(LEGACY_BRIDGE_COMMANDS))
        self.assertEqual(validate_builtin_command_coverage(), ())
        self.assertEqual(COMMAND_SPECS.get("domains").owner, "sandbox.commands.domains")
        self.assertEqual(COMMAND_SPECS.get("secrets").owner, "sandbox.commands.secrets")
        self.assertEqual(COMMAND_SPECS.get("init").owner, "sandbox.commands.instances_cmd")
        self.assertIsNotNone(COMMAND_SPECS.get("init").configure)
        self.assertIsNotNone(COMMAND_SPECS.get("init").predispatch_policy)

    def test_recovery_stays_feature_owned(self):
        from pathlib import Path
        root = Path(__file__).parent.parent
        self.assertIn("CommandSpec(", (root / "sandbox/commands/recovery.py").read_text())
        self.assertNotIn('add_parser("recovery"', (root / "sandbox/cli.py").read_text())

    def test_domains_command_keeps_ingress_transport_in_its_feature_boundary(self):
        from sandbox.commands.domains import DOMAIN_ACTIONS, INGRESS_ACTIONS
        self.assertIn("ingress", DOMAIN_ACTIONS)
        self.assertEqual(
            INGRESS_ACTIONS,
            {"detect", "support", "status", "plan", "apply", "cleanup", "reconcile", "reconsider"},
        )


if __name__ == "__main__":
    unittest.main()
