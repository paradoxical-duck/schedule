# schedule

`schedule` is a tiny Windows-friendly terminal command for sending a prompt to Codex now, or automatically retrying after a Codex rate limit clears.

It can send to a new non-interactive Codex exec session, or resume an existing Codex chat by chat name.

## Install

Requirements:

- Python on `PATH`
- Codex CLI on `PATH`
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

Send to an existing Codex chat by name:

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

## Chat Matching

`schedule -Chat` reads Codex's local `session_index.jsonl` from `CODEX_HOME`, or from `~\.codex` if `CODEX_HOME` is not set.

Matching rules:

1. If `-Chat` is a session UUID, it is used directly.
2. Exact chat-name matches win.
3. If there is one unique partial match, that chat is used.
4. Ambiguous or missing names fail before sending, with suggestions.

When multiple index entries share the same chat name, `schedule` uses the most recently updated one.

## Rate Limit Behavior

`schedule` first sends the prompt immediately.

If Codex succeeds, it prints:

```text
[Schedule] Prompt sent right now because Codex did not report a rate limit.
```

If Codex reports a rate limit, `schedule` parses retry text such as:

- `try again at 3:17 PM`
- `try again in 2 minutes`
- `try again in 30 seconds`
- `try again in 1 hour`

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
codex exec <prompt>
```

or, when `-Chat` is provided:

```text
codex exec resume <session-id> <prompt>
```
