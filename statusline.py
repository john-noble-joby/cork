#!/usr/bin/env python3
"""cork status line — shows the active ticket/branch + model so you can see, at a
glance, what a session is working on.

Claude Code runs this after each message (and on the optional refreshInterval),
piping its status JSON on stdin. We read the live working dir (which reflects a
git worktree if the session cd'd into one), derive the branch, parse a ticket id
like MXE-123 from it, and print one line. Pairs with devit, which names branches
`feature/<TICKET>-<slug>` / `bugfix/<TICKET>-<slug>` — so the status line tracks
the devit run automatically, no per-run action needed.

Enable by adding to ~/.claude/settings.json:
    "statusLine": { "type": "command", "command": "~/.claude/statusline.py" }
(install.sh deploys this file to ~/.claude/statusline.py.)

stdlib only; must be fast and never error to blank — falls back gracefully.
"""

import json
import os
import re
import subprocess
import sys


def _git_branch(cwd: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd, "branch", "--show-current"],
                           capture_output=True, text=True, timeout=2)
        return r.stdout.strip()
    except Exception:
        return ""


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    cwd = ((data.get("workspace") or {}).get("current_dir")
           or data.get("cwd") or os.getcwd())
    model = (data.get("model") or {}).get("display_name") or "?"

    branch = _git_branch(cwd)
    m = re.search(r"[A-Za-z]+-\d+", branch)
    if m:
        label = f"{m.group(0).upper()} ({branch})"   # ticket + full branch
    elif branch:
        label = branch
    else:
        label = os.path.basename(cwd) or "no-branch"

    # First line of stdout becomes the status line.
    print(f"⎇ {label} · {model}")


if __name__ == "__main__":
    main()
