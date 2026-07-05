---
name: daily-update
description: Build today's work update FROM today's git commits (the ground truth), map each to its board card, summarize cleanly, then post to Slack. Use when the user says "daily update", "work update for <project>", "post my update", or asks to share today's work for xspeed or embedpress.
---

# Commit-driven Daily Update

The accurate method: today's **commits** are the ground truth. Map them to real
board cards where a `fbs-<id>` ref exists; for cardless work, write an honest
title. YOU do the summarization — don't dump raw commit lists.

Projects are configured in `scripts/daily_update.config.json` (board, repos,
channel, env, slack_post path). Currently: `xspeed` (board 57 → #speedpress),
`embedpress` (board 17 → #embedpress).

## Flow

1. **Gather raw structured data** (deterministic — script does git + card lookup):
   ```bash
   bash /Applications/Workspace/GitHub/xspeed/scripts/daily-update.sh <project> --preview --raw 2>/dev/null
   ```
   Wait — `daily-update.sh` doesn't take `--raw`. Instead call the python directly with
   the project's board/repos (read them from the config first), e.g. for embedpress:
   ```bash
   python3 /Applications/Workspace/GitHub/xspeed/scripts/daily_update.py \
     --board 17 --author Akash --date "$(date +%d-%m-%Y)" --raw \
     --out /tmp/<project>-raw.json \
     --repos /Applications/Workspace/GitHub/embedpress \
             /Applications/Workspace/GitHub/embedpress-pro \
             /Applications/Workspace/GitHub/embedpress-docker
   ```
   This prints `{date, carded:[{card_id,card_title,url,commits[]}], cardless:[...], commit_count}`.

2. **Synthesize the payload (this is the model's job).** From the raw data, write an
   embedpress-format payload to `/tmp/<project>-daily-update.json`:
   ```json
   {"title": "Daily_Report : [DD-MM-YYYY]",
    "items": [{"text": "Work on: <clean title>", "url": "<card url if carded>",
               "subtasks": ["<summarized point>", ...]}]}
   ```
   Rules:
   - One item per **card** (use its real `card_title` + `url`). Fold that card's
     commits into 1–4 *summarized* sub-bullets — group related commits, don't
     transcribe every one. (16 e2e commits → "Stabilized the full E2E suite on
     real Apache + mod_php", not 16 lines.)
   - Cardless commits: cluster by theme into a few honest items with a written
     title (e.g. "Work on: CI pipeline — real Apache E2E host + Slack notifications").
     Never emit a title that's just a commit prefix like "Add", "Update", "Ci".
   - If a cardless cluster clearly belongs to a carded feature, fold it in there.
   - Keep it tight: a daily update is a digest, not a changelog.

3. **Preview** the rendered block to the user (title + bullets + sub-bullets) and ask
   "Post <project> update to #<channel>?".

4. **On yes**, post:
   ```bash
   set -a; source <project env>; set +a
   python3 <project slack_post> --token "$SLACK_USER_TOKEN" --channel <channel> \
     < /tmp/<project>-daily-update.json
   ```
   Confirm the `✓ Posted to Slack` result.

## Notes
- `scripts/daily-update.sh <project> --preview` (no `--raw`) is the **non-interactive
  fallback** — it auto-groups without model synthesis. Fine for cron; for a quality
  update driven from chat, prefer the raw → synthesize flow above.
- Card lookup uses the sandbox fluentboards `request.sh` (its own auth). Read-only.
- Channel names: xspeed → `speedpress` (NOT `xspeed`), embedpress → `embedpress`.
- Add a new project by adding a block to `daily_update.config.json` — no code change.
