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
        from sandbox.commands.manifest import BUILTIN_COMMAND_MODULES, load_builtin_commands
        from sandbox.registry import COMMANDS, COMMAND_SPECS

        load_builtin_commands()
        self.assertEqual(len(BUILTIN_COMMAND_MODULES), len(set(BUILTIN_COMMAND_MODULES)))
        self.assertEqual(set(COMMANDS), set(COMMAND_SPECS.names()))
        self.assertEqual(len(COMMANDS), 68)


if __name__ == "__main__":
    unittest.main()
