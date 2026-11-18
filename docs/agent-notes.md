# Agent Notes from the CLI: `sb notes` and `sb mockups`

`sb notes` publishes markdown or HTML files to an
[Agent Notes](https://github.com/alimuzzaman/agent-notes) service and manages them.
`sb mockups` publishes a folder with an `index.html` as one HTML note. Any shell,
script, durable job or remote session that can run `./sb` can use them, with no
MCP client setup.

## How the token is held

The commands call the service's MCP endpoint (`<url>/mcp`) with a bearer token.
The token is a secret held by the Sandbox secrets broker:

- It is stored as one key in a broker source, by default the `personal` source
  (`~/.zshrc.secrets`, or `$SANDBOX_SECRETS_FILE`), under
  `SANDBOX_NOTES_TOKEN_<REMOTE>` (for `xspeed-dev`: `SANDBOX_NOTES_TOKEN_XSPEED_DEV`).
- Every command runs the HTTP calls in a broker child process
  (`sandbox/notes/child.py`, the `sandbox-notes-mcp` use profile). The token
  is in that child's environment only. The broker redacts it from the child's
  output and audits each use in `$SANDBOX_HOME/runtime/secrets/audit.jsonl`
  without the value.
- The token never goes in argv, a log, command output or config. Requests and
  responses pass through a `0700` work directory under
  `$SANDBOX_HOME/runtime/notes/work/`, which is deleted after each batch.

## First-time setup (the owner runs this once per machine)

```sh
./sb notes login --remote xspeed-dev --url https://notes.xspeedcdn.online
```

`login` works only from a machine on the tailnet, because it relies on the
service's tailnet trust: opening the dashboard there signs you in with no
password. It then:

1. records `xspeed-dev -> https://notes.xspeedcdn.online` in
   `$SANDBOX_HOME/sandbox.local.yml` (and makes it the default remote if none is set);
2. reads the dashboard's CSRF field, revokes every live token named
   `sb-notes-<host>` (`--name` picks another name), and creates a new one
   through the dashboard form;
3. stores the new token with the broker (`SecretService.set`; it creates an
   empty `0600` personal file if there is none), then makes one `list_notes`
   call to check it (`--no-verify` skips the check).

Running `login` again rotates the token: the old one with the same name is
revoked. `login` refuses inside a durable job.

The broker does not require a human approval for this store, but minting a
credential is the owner's call. Agents should not run `login` unless the owner
asked for it.

### Where the remote name comes from

`--remote NAME` selects a service from the `notes` block. With no `--remote`,
`notes.default_remote` is used. The name is only a label for the service; it
need not be a registered `sb remote`.

```yaml
# $SANDBOX_HOME/sandbox.local.yml (no secrets here)
notes:
  default_remote: xspeed-dev
  services:
    xspeed-dev:
      url: https://notes.xspeedcdn.online
      # token: {source: personal, key: SANDBOX_NOTES_TOKEN_XSPEED_DEV}   # the default
```

`sb notes config` shows the resolved service and token location.
`sb notes config --remote R --url U [--default]` records a service without minting
a token.

### On xspeed-dev itself (jobs and remote shells)

Commands that run on the server (a durable job, `sb host exec`, an SSH shell)
use the server's own broker and config. Give the server its own token once,
in either of two ways:

- If the server can reach the service over the tailnet listener, run
  `./sb notes login --remote xspeed-dev --url https://notes.xspeedcdn.online`
  on the server. That mints a separate token named `sb-notes-<server host>`.
- Otherwise copy the controller's token over SSH stdin and record the service:

  ```sh
  ./sb secrets copy --source personal --key SANDBOX_NOTES_TOKEN_XSPEED_DEV --remote xspeed-dev
  # then, on the server:
  ./sb notes config --remote xspeed-dev --url https://notes.xspeedcdn.online --default
  ```

## `sb notes`

Every action takes `--remote NAME` and `--json` (one JSON object on stdout;
`"ok": false` with an `error.code` and exit 1 on failure).

| Command | What it does |
|---|---|
| `create --file F [--title T] [--html] [--public]` | New note. Prints the slug and URL. The title defaults to the first heading (`<title>`/`<h1>` with `--html`), else the file name. `--html` stores one complete HTML document. `--public` serves it on the public host: only for notes with nothing private. |
| `update <slug> --file F [--title T]` | Replaces the content. Same slug, same URL. |
| `get <slug>` | Prints the content (`--json`: the note, its assets included). |
| `list` | All your notes, newest first. |
| `delete <slug>` | Deletes the note and its assets. |
| `sync <dir> [--prune]` | One note per `*.md` file in `dir` (not recursive; dotfiles skipped). See below. |
| `upload <slug> <file>` | Attaches a PNG, JPEG, GIF or WebP image. Prints the path to embed. |
| `share <slug> --ttl 24h` | Creates an expiring read-only link on the public host and prints it. `--ttl` takes hours (`24`, `24h`) or days (`7d`). The service caps it. |
| `share <slug> --revoke` | Revokes every active link on the note. |

A share link is a secret: the service shows it once. `share --ttl` refuses inside
a durable job, because job output is retained.

### `sync`

`sync` keeps a folder of markdown files published:

- The map `<dir>/.notes.json` records `file -> {slug, url, title, sha256}` and
  the remote it belongs to. A map for another remote is refused.
- A file with no map entry is created. A file whose content or first heading
  changed is updated (same URL). An unchanged file makes no call.
- If a mapped note was deleted on the service, the file is published again as
  a new note (`recreated`, new URL).
- A map entry whose file is gone is reported as `orphaned`. Only `--prune`
  deletes its note.
- Calls go in batches of 25, and the map is saved after each batch. Ctrl-C or
  a client that dies part way keeps every note that finished. Only if `sb`
  itself is killed (SIGKILL) can the notes of the unfinished batch be
  published without a map entry; the next run then publishes them again.
  Running it again is otherwise always safe.
- Exit 1 if any file failed. Failures are listed with their error code.

## `sb mockups`

A mockup is a folder with an `index.html` and the files it uses. It is stored
as one HTML note plus image assets. Its name (`[a-z0-9-]`, up to 53) is kept as
the note's download filename `mockup-<name>`, which is how the other commands
find it.

| Command | What it does |
|---|---|
| `publish <dir> --name N [--title T] [--public]` | New mockup. Refuses if `N` exists and points to `update`. |
| `update N <dir> [--title T]` | Republishes: same note, same URL. Images no longer used are deleted. |
| `list` / `get N` / `delete N` | List, show (`--json` includes the HTML), delete. |
| `pull N <dir> [--force]` | Rebuilds the folder. Refuses a non-empty folder without `--force`. |
| `share N --ttl 24h` / `share N --revoke` | As for notes. |

How the folder is packed:

- Local stylesheets (`<link rel="stylesheet">`) and scripts (`<script src>`)
  are inlined as `<style data-sb-src="path">` and `<script data-sb-src="path">`.
- Local PNG, JPEG, GIF and WebP files referenced from `src`, `href`, `poster`,
  `srcset` or CSS `url()` are uploaded as note assets, and their references
  are rewritten to the asset paths. The service accepts up to 5 MB per image
  by default and dedups identical images.
- Any other local file (SVG, fonts) becomes a `data:` URI.
- URLs with a scheme, `//`, `/` or `#` are left alone, and so are page links
  (`<a href>`, `<area href>`). A local reference to a missing file, or to one
  outside the folder (symlinks included), is refused before anything is sent.
- Only `index.html` is packed. `import` statements inside scripts and other
  pages of the folder are not followed.
- A JSON manifest (`<script type="application/json" id="sb-mockup">`) maps each
  asset path back to its file. `pull` uses it to write the images, stylesheets
  and scripts back to their paths. SVGs and fonts come back inline.

The note content limit is 1 MB, so large scripts or many data URIs can exceed it.
The service allows 60 MCP calls a minute per token. The client waits and
retries when it is rate limited, so a mockup with many images publishes slowly
rather than failing.

The broker child runs with a minimal environment (`PATH`, `HOME`, `TMPDIR`,
locale). It uses Python's default CA store; `SSL_CERT_FILE` and `SSL_CERT_DIR`
are not passed through.

## Errors

| Code | Meaning |
|---|---|
| `notes_remote_missing` / `notes_remote_unknown` | No `--remote` and no default, or no service for that name. Run `login --url` or `config --url`. |
| `notes_token_missing` | The broker has no token at the configured key. Run `login`. |
| `notes_unauthorized` | The service refused the token (revoked or wrong). Run `login`. |
| `notes_unreachable` | No connection, e.g. not on the tailnet. |
| `notes_bad_response` | The service answered with something other than a tool result, including any redirect (never followed, so the token cannot leave). |
| `notes_incomplete` | The client stopped before a call (killed or timed out); that call did not run. |
| `notes_tool_error` | The service refused the call; the message is the service's own. |
| `notes_login_untrusted` | The dashboard did not sign this machine in by tailnet trust. |
| `notes_refused_in_job` | `login` or `share --ttl` inside a durable job. |
| `mockup_exists` / `mockup_not_found` | `publish` over an existing name; any other action on a missing one. |
