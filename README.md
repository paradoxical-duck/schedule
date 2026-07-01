# schedule

> **Claude Code variant.** This is a port of `schedule` that drives the
> [Claude Code](https://claude.com/claude-code) CLI (`claude`) instead of the
> Codex CLI. It shells out to `claude -p` and resumes Claude sessions rather
> than Codex chats.

`schedule` is a tiny Windows-friendly terminal command for sending a prompt to Claude Code now, or automatically retrying after a Claude usage/rate limit clears.

It can send to a new non-interactive Claude session, or resume an existing Claude chat by chat name.

## Install

Requirements:

- Python on `PATH`
- Claude Code CLI (`claude`) on `PATH`
- PowerShell on Windows

Clone the repo, then run:

```powershell
.\install.ps1
```

The installer writes:

- `%APPDATA%\npm\schedule.ps1`
- `%APPDATA%\npm\schedule.cmd`
- `%APPDATA%\npm\schedule-src\schedule.py`

Make sure `%APPDATA%\npm` is on your `PATH`. It usually already is if npm global commands work.

## Usage

Send a normal prompt:

```powershell
schedule -Prompt "Summarize the current repo"
```

Send to an existing Claude chat by name:

```powershell
schedule -Chat "chat name" -Prompt "prompt"
```

Create or update a goal in an existing chat:

```powershell
schedule -Chat "chat name" -Goal "goal prompt here"
```

Open planning mode in an existing chat:

```powershell
schedule -Chat "chat name" -Plan "plan prompt here"
```

The older positional forms still work:

```powershell
schedule "prompt"
schedule goal "goal prompt"
schedule plan "plan prompt"
```

## Viewing Recent Chats

List your recent Claude project chats, most recently updated first:

```powershell
schedule -Chats
```

By default it shows the 20 most recent chats. Pass a number to change how many,
or pass text to filter by chat name:

```powershell
schedule -Chats 5          # show the 5 most recent chats
schedule -Chats AFK        # show chats whose name contains "AFK"
```

Each entry lists the chat title, its session id, when it was last updated, and
the directory it ran in — so you can copy a name or id straight into
`schedule -Chat`:

```text
[Schedule] Recent Claude chats (showing 5 of 42)

  Dashboard redesign
    id:      9f1c1e0a-3b7d-4a2e-8f10-1c2b3d4e5f60
    updated: 2026-07-01 15:51
    cwd:     D:\Projects\dashboard
```

## Chat Matching

`schedule -Chat` scans Claude's local session store under
`%USERPROFILE%\.claude\projects` (or `CLAUDE_CONFIG_DIR\projects` when
`CLAUDE_CONFIG_DIR` is set). Each conversation is a `<session-id>.jsonl` file;
`schedule` reads the auto-generated chat title (or falls back to the first user
message) to match against the name you pass.

Matching rules:

1. If `-Chat` is a session UUID, it is used directly.
2. Exact chat-name matches win.
3. If there is one unique partial match, that chat is used.
4. Ambiguous or missing names fail before sending, with suggestions.

When multiple sessions share the same chat name, `schedule` uses the most
recently updated one.

Each session records the working directory it ran in. `schedule` reuses that
directory when resuming (`claude -p --resume <session-id>` launched from the
recorded cwd), so scheduled project work runs in the right place regardless of
where your terminal happens to be.

## Rate Limit Behavior

`schedule` first sends the prompt immediately.

If Claude succeeds, it prints:

```text
[Schedule] Prompt sent right now because Claude did not report a rate limit.
```

If Claude reports a usage/rate limit, `schedule` parses retry text such as:

- `resets at 3:17 PM`
- `try again at 15:17`
- `resets 3pm`
- `try again in 2 minutes`
- `try again in 30 seconds`
- `try again in 1 hour`
- a unix epoch, e.g. `limit reached|1735750800`

It waits with a countdown, then retries the same command automatically.

## Examples

```powershell
schedule -Chat "AFK" -Prompt "Continue from where we left off."
schedule -Chat "Final Project" -Goal "Finish the Firebase deploy checklist."
schedule -Chat "Dashboard redesign" -Plan "Plan the next UI polish pass."
```

## Notes

This is intentionally small. It shells out to:

```text
claude -p <prompt>
```

or, when `-Chat` is provided:

```text
claude -p --resume <session-id> <prompt>
```
