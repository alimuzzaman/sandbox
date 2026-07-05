---
name: report
description: Build Akash's EOD work report from completed sub-tasks across his My Day cards (xSpeed board 57), confirm, then post to Slack. Use when the user says "report", "/report", "EOD report", "work update", or asks to share what they finished today.
---

# Daily Work Report (chat flow)

Source: FluentBoards stage `My Day(Akash)` on board **57**, cards assigned to user **35** (Akash).
**Only checked / completed sub-tasks** are included as the day's done items.

## Flow

1. **Preview** — fetch cards, walk their sub-tasks, keep the completed ones, format:
   ```bash
   bash /Applications/Workspace/GitHub/xspeed/scripts/report.sh --preview
   ```
   Show the formatted `Daily_Report : [...]` block (between the `━━━` lines) to the user.

2. **Ask** — "OK to post?"

3. **If user says ok / yes / post:**
   ```bash
   bash /Applications/Workspace/GitHub/xspeed/scripts/report.sh --auto
   ```
   Confirm posted.

4. **If preview shows "No completed sub-tasks today":** tell the user to check off sub-tasks on
   their My Day cards (in the FluentBoards UI), then ask them to say "done" so you can re-run.

## Notes

- Don't reimplement — always call the script.
- A sub-task counts as completed if its `status` is `closed/completed/done` OR its `last_completed_at` is set.
- The `--auto` mode skips the script's own y/n confirm; the user's chat "ok" replaces it.
- Requires `SLACK_USER_TOKEN` (and optionally `SLACK_CHANNEL`) in `xspeed/.env` — see `.env.example`.
