# Quickstart: Verify Hermes Worker Routing

1. Install Hermes and run setup on a configured remote:

   ```bash
   ./sb hermes install --remote <name> --json
   ./sb hermes setup --remote <name> --json
   ```

2. Complete provider authentication separately, then inspect profiles:

   ```bash
   hermes profile list
   ```

   Expected: default Spark plus Luna, Terra, and Sol profiles.

3. Activate the task-board dispatcher only through the existing allowlisted gateway workflow:

   ```bash
   ./sb hermes gateway setup --remote <name> --allow <operator>
   ./sb hermes gateway install --remote <name>
   ./sb hermes gateway start --remote <name>
   ```

4. Run the focused Sandbox verification:

   ```bash
   python3 -m unittest tests.test_hermes -v
   ```

See [setup contract](contracts/setup.md) for convergence and non-goals.

## Local verification evidence

On 2026-07-12, `python3 -m unittest tests.test_hermes -v` passed 94 tests and
`git diff --check` passed. Fresh-server acceptance remains pending explicit approval
because it requires provider authentication and an operator-selected remote.
