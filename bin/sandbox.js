#!/usr/bin/env node
// npm/global entry point for the Sandbox CLI.
//
// `sb` is a polyglot shell+python file: its leading shell block is a Python
// docstring, so running `python3 sb …` works directly and cross-platform.
// This shim finds a Python 3 interpreter and execs the bundled `sb` with the
// same args + inherited stdio, exiting with sb's status. Keep `./sb` as the
// in-repo alias; this is just the installed-bin path (incl. Windows, where the
// `#!/bin/sh` shebang on `sb` would not run).
'use strict';

const { spawnSync } = require('child_process');
const path = require('path');

const SB = path.join(__dirname, '..', 'sb');

function findPython() {
  for (const candidate of ['python3', 'python']) {
    const probe = spawnSync(candidate, ['-c', 'import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)'],
      { stdio: 'ignore' });
    if (probe.status === 0) return candidate;
  }
  return null;
}

const python = findPython();
if (!python) {
  process.stderr.write(
    'sandbox requires Python 3, which was not found on PATH.\n' +
    '  • macOS:         brew install python\n' +
    '  • Ubuntu/Debian: sudo apt install python3 python3-venv\n' +
    '  • Fedora/RHEL:   sudo dnf install python3 python3-virtualenv\n');
  process.exit(1);
}

const result = spawnSync(python, [SB, ...process.argv.slice(2)], { stdio: 'inherit' });
if (result.error) {
  process.stderr.write(`sandbox: failed to launch ${python}: ${result.error.message}\n`);
  process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
