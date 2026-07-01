import sys
import subprocess
import time
import datetime
import re
import os
import json
import shutil
import uuid
from collections import Counter

OPTION_ALIASES = {
    "-chat": "chat",
    "--chat": "chat",
    "-prompt": "prompt",
    "--prompt": "prompt",
    "-goal": "goal",
    "--goal": "goal",
    "-plan": "plan",
    "--plan": "plan",
}

USAGE = """Usage:
  schedule <prompt>
  schedule goal <prompt>
  schedule plan <prompt>
  schedule -Chat "chat name" -Prompt "prompt"
  schedule -Chat "chat name" -Goal "goal prompt here"
  schedule -Chat "chat name" -Plan "plan prompt here"
  schedule -Chats [count | search text]
"""

CHATS_ALIASES = {"-chats", "--chats", "chats"}

def configure_console_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="backslashreplace")

def get_wait_time(output_text):
    # Pattern for "try again at 3:17 PM" / "reset at 3:17 PM" / "resets at 15:17"
    m = re.search(r'(?:try again|resets?)\s*(?:at)?\s*(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?', output_text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = m.group(3)
        now = datetime.datetime.now()
        if ampm:
            ampm = ampm.upper()
            if ampm == 'PM' and hour < 12:
                hour += 12
            elif ampm == 'AM' and hour == 12:
                hour = 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target < now:
            target += datetime.timedelta(days=1)
        # Add a 15-second buffer to ensure the rate limit has actually cleared
        return int((target - now).total_seconds()) + 15

    # Pattern for "resets 3pm" / "reset at 11 am" (hour only, no minutes)
    m = re.search(r'(?:try again|resets?)\s*(?:at)?\s*(\d{1,2})\s*(AM|PM|am|pm)', output_text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        ampm = m.group(2).upper()
        now = datetime.datetime.now()
        if ampm == 'PM' and hour < 12:
            hour += 12
        elif ampm == 'AM' and hour == 12:
            hour = 0
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target < now:
            target += datetime.timedelta(days=1)
        return int((target - now).total_seconds()) + 15

    # Pattern for "try again in X minutes"
    m = re.search(r'try again in\s*(\d+)\s*minutes?', output_text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 60 + 15

    # Pattern for "try again in X seconds"
    m = re.search(r'try again in\s*(\d+)\s*seconds?', output_text, re.IGNORECASE)
    if m:
        return int(m.group(1)) + 5

    # Pattern for "try again in X hours"
    m = re.search(r'try again in\s*(\d+)\s*hours?', output_text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 3600 + 15

    # Claude sometimes reports the reset moment as a unix epoch, e.g.
    # "Claude AI usage limit reached|1735750800"
    m = re.search(r'limit reached\|(\d{10,})', output_text, re.IGNORECASE)
    if m:
        target = datetime.datetime.fromtimestamp(int(m.group(1)))
        delta = int((target - datetime.datetime.now()).total_seconds())
        if delta > 0:
            return delta + 15

    # General usage limit error fallback
    if any(x in output_text.lower() for x in ["usage limit", "rate limit", "limit reached", "too many requests"]):
        return 300
    return None

def wait_countdown(seconds):
    end_time = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
    while datetime.datetime.now() < end_time:
        remaining = int((end_time - datetime.datetime.now()).total_seconds())
        if remaining <= 0:
            break
        mins = remaining // 60
        secs = remaining % 60
        sys.stdout.write(f"\r[Schedule] Rate limit active. Retrying in {mins}m {secs:02d}s...   ")
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r[Schedule] Retrying now...                                   \n")
    sys.stdout.flush()

def print_non_rate_limit_hint(output_text):
    lowered = output_text.lower()
    if "no conversation found" in lowered or "session not found" in lowered or "could not find session" in lowered:
        print(
            "[Schedule] Claude could not resume that session. It may have been "
            "started in a different directory or removed. Run `claude --resume` "
            "to see available sessions, then retry with the exact chat name or id."
        )

def parse_args(args):
    if not args:
        raise ValueError(USAGE)

    values = {}
    positional = []
    i = 0

    while i < len(args):
        token = args[i]
        option = OPTION_ALIASES.get(token.lower())

        if not option:
            positional.append(token)
            i += 1
            continue

        if option in values:
            raise ValueError(f"[Schedule] {token} was provided more than once.\n\n{USAGE}")

        i += 1
        collected = []
        while i < len(args) and args[i].lower() not in OPTION_ALIASES:
            collected.append(args[i])
            i += 1

        if not collected:
            raise ValueError(f"[Schedule] {token} needs a value.\n\n{USAGE}")

        values[option] = " ".join(collected)

    explicit_prompt_options = [name for name in ("prompt", "goal", "plan") if name in values]
    if len(explicit_prompt_options) > 1:
        raise ValueError(f"[Schedule] Use only one of -Prompt, -Goal, or -Plan.\n\n{USAGE}")

    chat = values.get("chat")

    if explicit_prompt_options:
        if positional:
            raise ValueError(f"[Schedule] Extra text after option parsing: {' '.join(positional)!r}\n\n{USAGE}")
        mode = explicit_prompt_options[0]
        text = values[mode].strip()
    elif positional:
        if positional[0].lower() in ("goal", "plan"):
            mode = positional[0].lower()
            text = " ".join(positional[1:]).strip()
        else:
            mode = "prompt"
            text = " ".join(positional).strip()
    else:
        raise ValueError(f"[Schedule] Missing prompt text.\n\n{USAGE}")

    if not text:
        raise ValueError(f"[Schedule] Missing prompt text.\n\n{USAGE}")

    if mode == "goal":
        prompt = f"/goal {text}"
    elif mode == "plan":
        prompt = f"/plan {text}"
    else:
        prompt = text

    return chat, prompt

def claude_home():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")

def parse_datetime(value):
    if not value:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed
    except ValueError:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

def is_uuid(value):
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False

def _slug_from_text(text, limit=70):
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text

def read_session_summary(path):
    """Pull a display name, cwd, and update time out of one Claude session file.

    Claude stores each conversation as ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl.
    The human-readable title is emitted as an "ai-title" record; the working
    directory shows up on user-message records. We read just enough of the file
    to recover both without loading giant transcripts fully.
    """
    session_id = os.path.splitext(os.path.basename(path))[0]
    ai_title = None
    first_user_text = None
    cwd = None

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")

                if etype == "ai-title" and event.get("aiTitle"):
                    ai_title = event["aiTitle"]

                if cwd is None:
                    candidate = event.get("cwd")
                    if candidate and os.path.isdir(candidate):
                        cwd = candidate

                if first_user_text is None and etype == "user":
                    message = event.get("message") or {}
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        first_user_text = content.strip()

                # Title lives at the top and cwd/first prompt in the first turn;
                # once we have all three there is no need to keep scanning.
                if ai_title and cwd and first_user_text:
                    break
    except OSError:
        return None

    thread_name = ai_title or (_slug_from_text(first_user_text) if first_user_text else None)
    if not thread_name:
        return None

    try:
        updated_at = datetime.datetime.fromtimestamp(
            os.path.getmtime(path), tz=datetime.timezone.utc
        ).isoformat()
    except OSError:
        updated_at = None

    return {
        "id": session_id,
        "thread_name": thread_name,
        "updated_at": updated_at,
        "cwd": cwd,
    }

def load_session_index():
    projects_dir = os.path.join(claude_home(), "projects")
    if not os.path.isdir(projects_dir):
        raise FileNotFoundError(
            f"[Schedule] Could not find Claude sessions directory at {projects_dir}"
        )

    entries = []
    for root, _dirs, files in os.walk(projects_dir):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            summary = read_session_summary(os.path.join(root, name))
            if summary:
                entries.append(summary)

    if not entries:
        raise FileNotFoundError(
            f"[Schedule] No resumable Claude sessions found under {projects_dir}"
        )

    return entries

def newest(entries):
    return max(entries, key=lambda entry: parse_datetime(entry.get("updated_at")))

def format_updated(value):
    parsed = parse_datetime(value)
    if parsed == datetime.datetime.min.replace(tzinfo=datetime.timezone.utc):
        return "unknown"
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")

def list_chats(limit=20, search=None):
    """Print recent Claude project chats, most recently updated first."""
    try:
        entries = load_session_index()
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)

    if search:
        wanted = search.casefold()
        entries = [
            entry for entry in entries
            if wanted in entry.get("thread_name", "").casefold()
        ]
        if not entries:
            print(f"[Schedule] No chats matched {search!r}.")
            return

    entries.sort(key=lambda entry: parse_datetime(entry.get("updated_at")), reverse=True)
    shown = entries[:limit]

    header = f"[Schedule] Recent Claude chats (showing {len(shown)} of {len(entries)})"
    if search:
        header += f" matching {search!r}"
    print(header)
    print("")

    for entry in shown:
        name = entry.get("thread_name") or "(untitled)"
        print(f"  {name}")
        print(f"    id:      {entry.get('id')}")
        print(f"    updated: {format_updated(entry.get('updated_at'))}")
        if entry.get("cwd"):
            print(f"    cwd:     {entry['cwd']}")
        print("")

    print("Resume one with:  schedule -Chat \"<name or id>\" -Prompt \"<prompt>\"")

def resolve_chat(chat):
    chat = chat.strip()

    if not chat:
        raise ValueError("[Schedule] -Chat needs a non-empty chat name.")

    if is_uuid(chat):
        return chat, chat, None

    entries = load_session_index()
    wanted = chat.casefold()

    exact_matches = [
        entry for entry in entries
        if entry.get("thread_name", "").casefold() == wanted
    ]
    if exact_matches:
        match = newest(exact_matches)
        return match["id"], match["thread_name"], match.get("cwd")

    contains_matches = [
        entry for entry in entries
        if wanted in entry.get("thread_name", "").casefold()
    ]

    unique_names = sorted({entry["thread_name"] for entry in contains_matches})
    if len(unique_names) == 1:
        match = newest(contains_matches)
        return match["id"], match["thread_name"], match.get("cwd")

    if contains_matches:
        candidates = "\n".join(f"  - {name}" for name in unique_names[:10])
        raise ValueError(
            f"[Schedule] Chat name {chat!r} is ambiguous. Use the exact name or session id.\n\n"
            f"Matches:\n{candidates}"
        )

    recent = sorted(entries, key=lambda entry: parse_datetime(entry.get("updated_at")), reverse=True)[:10]
    recent_text = "\n".join(
        f"  - {entry.get('thread_name')} ({entry.get('id')})"
        for entry in recent
    )
    raise ValueError(
        f"[Schedule] Could not find a chat named {chat!r}.\n\n"
        f"Recent chats:\n{recent_text}"
    )

def build_claude_command(chat, prompt):
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude:
        raise FileNotFoundError("[Schedule] Could not find the claude command on PATH.")

    if chat:
        session_id, matched_name, cwd = resolve_chat(chat)
        print(f"[Schedule] Target chat: {matched_name} ({session_id})")
        if cwd:
            print(f"[Schedule] Target cwd: {cwd}")
        command = [claude, "-p", "--resume", session_id, prompt]
        return command, cwd

    return [claude, "-p", prompt], None

def main():
    configure_console_output()
    args = sys.argv[1:]

    if args and args[0].lower() in CHATS_ALIASES:
        rest = " ".join(args[1:]).strip()
        if rest.isdigit():
            list_chats(limit=int(rest))
        elif rest:
            list_chats(search=rest)
        else:
            list_chats()
        return

    try:
        chat, prompt = parse_args(args)
    except ValueError as e:
        print(e)
        sys.exit(1)

    try:
        command, cwd = build_claude_command(chat, prompt)
    except (FileNotFoundError, ValueError) as e:
        print(e)
        sys.exit(1)

    print(f"[Schedule] Starting prompt: {repr(prompt)}")

    while True:
        print("[Schedule] Sending prompt now...")
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            bufsize=1,
            shell=False
        )

        output_lines = []
        for line in iter(process.stdout.readline, ''):
            sys.stdout.write(line)
            sys.stdout.flush()
            output_lines.append(line)

        process.wait()
        output_text = "".join(output_lines)

        if process.returncode == 0:
            print("[Schedule] Prompt sent right now because Claude did not report a rate limit.")
            break
        else:
            wait_seconds = get_wait_time(output_text)
            if wait_seconds is not None:
                wait_countdown(wait_seconds)
            else:
                print_non_rate_limit_hint(output_text)
                print(f"[Schedule] Claude exited with error code {process.returncode} but no rate limit was detected. Exiting.")
                sys.exit(process.returncode)

if __name__ == "__main__":
    main()
