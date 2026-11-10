# Quickstart: validating controller auto-update

Everything here runs in temporary directories. Never point it at a real remote,
the installed checkout, or GitHub.

## 1. Automated tests

```sh
.cli-venv/bin/python -m unittest discover -s tests -p 'test_controller_update*.py' -v
```

Expected: all pass. They cover trust refusals, every skip reason, the
fast-forward, rollback after a failed refresh, the refusal memo, gating,
notices and the re-exec guard.

## 2. End-to-end through a real `./sb`

`tests/test_controller_update_e2e.py` builds this, and can be followed by hand:

1. Copy `sb`, `sandbox/`, `sandbox.yml` and `requirements-cli.txt` from the
   working tree into `$T/src`, commit (C1), add a marker commit (C2).
2. `git clone --bare $T/src $T/origin.git`.
3. `git clone $T/origin.git $T/ctl`, then in `$T/ctl`:
   `git remote set-url origin https://github.com/example/sandbox.git`,
   `git config url.file://$T/origin.git.insteadOf https://github.com/example/sandbox.git`,
   `git checkout -q -B installed C1`, `git branch -q -u origin/main installed`.
4. Write `$T/home/runtime/controller-update/state.json` with a hint
   `{"remote": "demo", "revision": "<C2>"}`.
5. Run `SANDBOX_HOME=$T/home $T/ctl/sb remote list`.

Expected:

- stderr has `sb: controller updated <C1:7> -> <C2:7> from origin/main; re-running the command.`
- `git -C $T/ctl rev-parse HEAD` is C2.
- The `remote list` output appears once.
- Repeating step 5 prints nothing extra.
- Repeating with `SANDBOX_AUTO_UPDATE=0`, `--json`, or a dirty file prints no
  update and leaves `HEAD` unchanged (dirty prints its notice).

## 3. Owner's one-time move of the installed checkout onto `latest`

See `docs/controller-auto-update.md`, section "Follow latest". The installed
checkout gets a local branch `installed` whose upstream is `origin/latest`,
because `latest` itself is checked out in another worktree.
