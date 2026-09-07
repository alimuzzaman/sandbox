---
name: share-build
description: Close out a FluentBoards card AFTER the user confirms the fix/feature — move to Done, mark complete, write the branch into the custom field, build the dist zip (dist-archive / .distignore), attach it, and post one close-out comment. Works for all projects (xspeed, embedpress, …). Use when the user says "share the build", "/share-build", "close this card", or confirms a fix is done and wants the zip shared. NEVER run before the user's explicit confirmation.
---

# Share Build — post-approval card close-out

Bundles the four close-out steps into one command, for any plugin project.

## ⛔ Hard gate — never skip

**Do NOT run this until the user has explicitly confirmed the fix/feature is correct.**
The flow is always:

1. I fix / implement.
2. **The user confirms it works.** ← this skill does NOT run before this point.
3. Only then: close out (this skill).

This skill performs no verification — it assumes the user already approved. Posting a
"✅ Closed" comment or attaching a build before approval is the exact mistake to avoid.
If the user hasn't clearly said it's good, ask — don't run.

## What it does (in order)

1. **Move** the card to `Done / Fixed` + mark it `closed`.
2. **Branch → custom field**: writes the branch into the board's branch field
   (Free by default, `--pro` for the Pro field), chosen by the card's tier label.
3. **Build** the dist zip: `npm run dist-archive` (xspeed) or a `.distignore`-aware
   `rsync`+`zip` (embedpress, betterdocs). Version comes from the plugin header.
   `./sb zip --project-dir <repo>` does the same job for any project with a
   `.distignore` and needs no per-project build script — plus branch-tagged
   naming and a build-number version, which is what a card attachment wants so
   the tester's WordPress treats it as an upgrade (see `docs/plugin-zip.md`).
4. **Attach** the zip + post **one** close-out comment ("✅ Closed. Build attached …").

## Usage

```bash
SB=/Applications/Workspace/GitHub/sandbox/skills/share-build/share-build.sh

# xspeed — full close-out (Free branch)
bash "$SB" xspeed 82111 --branch feature/object-cache-automation

# Pro-tier card → Pro branch field
bash "$SB" xspeed 82106 --branch feature/ai-collapse --pro

# embedpress — board 17 has NO branch custom field; the field write auto-skips,
# branch still goes in the close-out comment. No special flag needed.
bash "$SB" embedpress 82016 --branch fix/fbs-82016-fb-embed-gutenberg

# Always preview first on anything non-routine:
bash "$SB" xspeed 82111 --branch feature/x --dry-run
```

Project can be omitted when run from inside the repo (`$PWD` git root infers it):
`cd /Applications/Workspace/GitHub/embedpress && bash "$SB" 82016 --no-field`

### Flags
`--branch <name>` record branch · `--pro` Pro field · `--no-field` skip field write ·
`--no-build` attach existing zip · `--zip <path>` exact file · `--no-move` skip move/complete ·
`--note "<html>"` extra comment line · `--dry-run` preview, no changes.

## Project / board registry (edit the script to extend)

| Project | Repo | Build | Board | Done stage | Free field | Pro field |
|---|---|---|---|---|---|---|
| xspeed | `/…/xspeed` | `npm run dist-archive` | 57 | 1960 | 2038 | 2039 |
| embedpress | `/…/embedpress` | `.distignore` zip | 17 | 145 | — | — |
| betterdocs | `/…/betterdocs` | `.distignore` zip | 35 | ? | — | — |

**EmbedPress (board 17) has no branch custom fields, by design** — the field write
auto-skips and the branch is recorded in the close-out comment instead. No flag needed.
(If a board ever gets fields, add their ids to `board_config()`.)

## Notes

- The board is auto-resolved from the card id; stage/field ids are per-board (registry).
- Descriptions/comments are HTML (TinyMCE), not Markdown — see the xspeed memory on
  FluentBoards custom fields + HTML descriptions.
- Idempotent: re-running overwrites the field value and re-attaches; it does not
  de-duplicate the comment, so don't run twice.
