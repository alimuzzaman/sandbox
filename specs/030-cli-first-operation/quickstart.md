# Quickstart validation: CLI-first Sandbox operation

1. Inspect the generic fixture catalog:

   ```bash
   ./sb guide --project-dir tests/fixtures/generic-compose --json
   ```

   Expect `project_kind` to be `compose`, `exec` to be included, and WordPress
   execution commands to be absent.

2. Start the generic fixture:

   ```bash
   ./sb ensure --project-dir tests/fixtures/generic-compose --json
   ```

3. From that fixture directory, use explicit runtime execution:

   ```bash
   ../../../sb exec -- sh -lc 'echo cli-first'
   ```

   Expect the exact output and no raw Docker command.

4. Inspect the current WordPress project catalog:

   ```bash
   ./sb guide --project-dir . --json
   ```

   Expect `project_kind` to be `wordpress`, `wp` to be included, and `exec` to
   be absent.

5. Verify the shipped skill and tests:

   ```bash
   ./sb skill show sandbox-cli
   .cli-venv/bin/python -m unittest tests.test_cli tests.test_command_composition
   ```
