# Tasks: Default Reader.md Bootstrap

**Input**: `spec.md`, `plan.md`, and `research.md`

## Phase 1: Bootstrap behavior

- [x] T001 [US1] Add a fourth Reader.md stage to `scripts/install-macos.sh`.
- [x] T002 [US1] Detect an existing `reader` command and avoid reinstalling it.
- [x] T003 [US1] Add `SANDBOX_SKIP_READER_MD=1` and non-fatal failure handling.

## Phase 2: Documentation and verification

- [x] T004 [P] [US1] Document the macOS default and opt-out in `README.md`.
- [x] T005 [P] [US1] Add `tests/test_install_macos_script.py` regression coverage.
- [x] T006 [US1] Run Bash syntax validation and the focused unittest.
- [x] T007 [US1] Trust only the upstream Reader.md cask before installation,
  as required by current Homebrew releases.
