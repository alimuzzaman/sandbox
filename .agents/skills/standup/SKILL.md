---
name: standup
description: Show Akash's My Day cards from FluentBoards (xSpeed board 57) as a daily standup, confirm, then post to Slack. Use when the user says "standup", "/standup", "post standup", or asks to share today's plan.
---

# Daily Standup (chat flow)

Source: FluentBoards stage `My Day(Akash)` on board **57** (xSpeed Development),
cards assigned to user **35** (Akash). Each card's OPEN sub-tasks are today's plan.

## Flow

1. **Preview** — fetch + format without posting:
   ```bash
   bash /Applications/Workspace/GitHub/xspeed/scripts/standup.sh --preview
   ```
   Show the formatted block (the `Daily_Standup : [...]` section between the `━━━` lines) to the user in chat.

2. **Ask** — "OK to post? Or want to add more cards to My Day first?"

3. **If user says ok / yes / post:**
   ```bash
   bash /Applications/Workspace/GitHub/xspeed/scripts/standup.sh --auto
   ```
   Confirm to the user that it posted.

4. **If user wants more cards:** they drag cards into the `My Day(Akash)` stage on the board
   (https://projects.startise.com/wp-admin/admin.php?page=fluent-boards#/boards/57). After they say
   "done" / "added", re-run from step 1.

## Notes

- Don't reimplement the fetch/format logic — always shell out to the script.
- The `--auto` mode skips the script's own y/n prompt; the user's chat confirmation replaces it.
- If preview reports "No cards", tell the user to add some to My Day first.
- Requires `SLACK_USER_TOKEN` (and optionally `SLACK_CHANNEL`) in `xspeed/.env` — see `.env.example`.
- FluentBoards auth comes from the sandbox `fluentboards` skill's `request.sh`; no FB creds needed in `.env`.
