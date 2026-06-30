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
"""

def configure_console_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="backslashreplace")

def get_wait_time(output_text):
    # Pattern for "try again at 3:17 PM" or "try again at 15:17"
    m = re.search(r'try again at\s*(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?', output_text, re.IGNORECASE)
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

    # General usage limit error fallback
    if any(x in output_text.lower() for x in ["usage limit", "rate limit", "too many requests"]):
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
    if "does not start with session metadata" in lowered or "thread-store internal error" in lowered:
        print(
            "[Schedule] This usually means your terminal Codex CLI is older than the "
            "Codex app that created the target chat. Run `codex update`, restart "
            "Codex, then retry the schedule command."
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

def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")

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

def load_session_index():
    path = os.path.join(codex_home(), "session_index.jsonl")
    entries = []

    if not os.path.exists(path):
        raise FileNotFoundError(f"[Schedule] Could not find Codex session index at {path}")

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("id") and entry.get("thread_name"):
                entries.append(entry)

    return entries

def newest(entries):
    return max(entries, key=lambda entry: parse_datetime(entry.get("updated_at")))

def resolve_chat(chat):
    chat = chat.strip()

    if not chat:
        raise ValueError("[Schedule] -Chat needs a non-empty chat name.")

    if is_uuid(chat):
        return chat, chat

    entries = load_session_index()
    wanted = chat.casefold()

    exact_matches = [
        entry for entry in entries
        if entry.get("thread_name", "").casefold() == wanted
    ]
    if exact_matches:
        match = newest(exact_matches)
        return match["id"], match["thread_name"]

    contains_matches = [
        entry for entry in entries
        if wanted in entry.get("thread_name", "").casefold()
    ]

    unique_names = sorted({entry["thread_name"] for entry in contains_matches})
    if len(unique_names) == 1:
        match = newest(contains_matches)
        return match["id"], match["thread_name"]

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

def find_session_files(session_id):
    sessions_dir = os.path.join(codex_home(), "sessions")
    if not os.path.isdir(sessions_dir):
        return []

    matches = []
    needle = f"{session_id}.jsonl"
    for root, _dirs, files in os.walk(sessions_dir):
        for name in files:
            if name.endswith(needle):
                matches.append(os.path.join(root, name))

    return matches

def get_thread_cwd(session_id):
    home = os.path.normcase(os.path.normpath(os.path.expanduser("~")))
    cwd_counts = Counter()
    latest_cwd = None

    for path in find_session_files(session_id):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if event.get("type") != "turn_context":
                        continue

                    cwd = event.get("payload", {}).get("cwd")
                    if not cwd or not os.path.isdir(cwd):
                        continue

                    latest_cwd = cwd
                    cwd_counts[cwd] += 1
        except OSError:
            continue

    if not cwd_counts:
        return None

    non_home = {
        cwd: count for cwd, count in cwd_counts.items()
        if os.path.normcase(os.path.normpath(cwd)) != home
    }

    if non_home:
        return max(non_home.items(), key=lambda item: item[1])[0]

    return latest_cwd

def build_codex_command(chat, prompt):
    codex = shutil.which("codex") or shutil.which("codex.cmd")
    if not codex:
        raise FileNotFoundError("[Schedule] Could not find the codex command on PATH.")

    if chat:
        session_id, matched_name = resolve_chat(chat)
        print(f"[Schedule] Target chat: {matched_name} ({session_id})")
        cwd = get_thread_cwd(session_id)
        if cwd:
            print(f"[Schedule] Target cwd: {cwd}")
            return [codex, "exec", "-C", cwd, "resume", session_id, prompt]
        return [codex, "exec", "resume", session_id, prompt]

    return [codex, "exec", prompt]

def main():
    configure_console_output()
    args = sys.argv[1:]
    try:
        chat, prompt = parse_args(args)
    except ValueError as e:
        print(e)
        sys.exit(1)

    try:
        command = build_codex_command(chat, prompt)
    except (FileNotFoundError, ValueError) as e:
        print(e)
        sys.exit(1)

    print(f"[Schedule] Starting prompt: {repr(prompt)}")

    while True:
        print("[Schedule] Sending prompt now...")
        process = subprocess.Popen(
            command,
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
            print("[Schedule] Prompt sent right now because Codex did not report a rate limit.")
            break
        else:
            wait_seconds = get_wait_time(output_text)
            if wait_seconds is not None:
                wait_countdown(wait_seconds)
            else:
                print_non_rate_limit_hint(output_text)
                print(f"[Schedule] Codex exited with error code {process.returncode} but no rate limit was detected. Exiting.")
                sys.exit(process.returncode)

if __name__ == "__main__":
    main()
