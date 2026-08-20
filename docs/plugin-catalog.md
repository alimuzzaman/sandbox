# WPDeveloper plugin catalog (reference)

A reference list of WPDeveloper plugins, their WP slugs, GitHub repos, and
free/Pro split. **This is documentation, not config.** The live instance model is
per-project — each plugin checkout self-describes via its own
`sandbox.config.json` (constitution Principle I). This catalog exists so per-plugin
skills/schemas and onboarding have a single place to look up the slug↔repo mapping.

Source: the old `sandbox.yml` `projects:` catalog, removed by the per-project
rewrite. Recoverable from git: `git show f3f3633:sandbox.yml` (or
`git show 25fc409:sb` for the old `./sb projects|pick|use|add` commands).

GitHub org: `WPDevelopers`. Repos default to `WPDevelopers/<slug>` unless the
**Repo** column says otherwise (slug↔repo mismatches are called out — they bite
`"."`-in-`plugins` and clone logic).

## Catalog

| Project | Plugin slug | Repo (`WPDevelopers/…`) | Tier |
|---------|-------------|--------------------------|------|
| **EmbedPress** | `embedpress` | `embedpress` | Free |
| | `embedpress-pro` | `embedpress-pro` | Pro |
| **Essential Blocks** (Gutenberg) | `essential-blocks` | `essential-blocks` | Free |
| | `essential-blocks-pro` | `essential-blocks-pro` | Pro |
| **Essential Addons** (Elementor) | `essential-addons-for-elementor-lite` | `essential-addons-for-elementor-lite` | Free (Lite) |
| | `essential-addons-elementor` | `essential-addons-elementor` (a.k.a. **`eael-pro`**) | Pro |
| **BetterDocs** | `betterdocs` | `betterdocs` | Free |
| | `betterdocs-pro` | `betterdocs-pro` | Pro |
| | `betterdocs-ai-chatbot` | `betterdocs-ai-chatbot` | Addon |
| | `betterdocs-oauth` | `betterdocs-oauth` | Addon |
| **BetterLinks** | `betterlinks` | `betterlinks` | Free |
| | `betterlinks-pro` | `betterlinks-pro` | Pro |
| **NotificationX** | `notificationx` | `notificationx` | Free |
| | `notificationx-pro` | `notificationx-pro` | Pro |
| **SchedulePress** | `wp-scheduled-posts` | `wp-scheduled-posts` | Free |
| **Disable Comments** | `disable-comments` | `disable-comments` | Free |
| | `disable-comments-mu` | `disable-comments-mu` | Variant |
| | `disable-feeds` | `disable-feeds` | Free |
| **XSpeed** | `xspeed` | `xspeed` | Free |
| | `xspeed-pro` | `xspeed-pro` | Pro |

Other known slug↔repo mismatches from the old catalog/notes (verify before use):
`wowstore` repo → **`productx`** slug.

## wp.org dependencies per project

Projects that need extra wp.org plugins installed alongside (from the old
`install_from_wporg`):

- **EmbedPress**: `elementor`, `safe-svg` (sanitizes SVG uploads).
- **Essential Addons**: `elementor`, `safe-svg`.

## Pro plugins — shared declaration

Per CLAUDE.md, declare a shared Pro plugin once in the user-global config
(`~/.config/sandbox/config.json`) — typically as `mappings_inactive` with an
absolute/`~` host path — rather than copying it into each repo's override. Lists
and dicts union with the project config.

## Notes

- This catalog is **not** loaded by `sb` or the MCP server. To work on a plugin,
  `cd` into its checkout (or pass `project_dir`); the tools route via the on-disk
  registry. See CLAUDE.md → "Instances — one per project directory".
- Local checkouts on this machine live under `/Users/alim/Sites/git/<slug>` (and
  some Pro zips/checkouts under `/Users/alim/Sites/plugins-pro/`).
- The Pro store (`defaults.pro_plugins_home`) is mirrored to a remote host by
  `./sb remote plugins <name>` (and automatically by `./sb deploy`), which registers
  the same slugs on-demand in the REMOTE user-global catalog. See
  [`docs/remote-hosting.md`](remote-hosting.md) → "Pro plugins on the remote host".
