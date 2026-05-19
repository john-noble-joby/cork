#!/usr/bin/env python3
"""
orchestrate.py — Linear story → Claude Code → GPT-4o review → Claude fix →
                 Gemini review → Claude final fix + mem0 save

Usage:
    python orchestrate.py <TICKET-ID> <repo-path>
    python orchestrate.py ENG-123 ~/dev/my-repo

Requirements:
    pip install openai
    gh auth login  (GitHub CLI, authenticated with Copilot access)
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────

CLAUDE       = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
COPILOT_BASE = "https://api.githubcopilot.com"
MODELS       = ["gpt-4o", "gemini-2.0-flash"]   # review pass 1, pass 2
MAX_FILE_LINES = 500  # files larger than this are included as diff-only

REVIEW_SYSTEM = """\
You are a senior code reviewer. For each issue output exactly:
FILE: <path> | LINE: <n> | ISSUE: <description> | FIX: <suggestion>
Be specific. Reference exact file paths and line numbers.
Cover: correctness, error handling, edge cases, \
style consistency with surrounding code, test coverage.\
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def step(n: int, msg: str) -> None:
    print(f"\n── Step {n}/5 — {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"\nFAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def run_claude(prompt: str, cwd: str) -> str:
    result = subprocess.run(
        [CLAUDE, "--print", prompt],
        cwd=cwd, capture_output=True, text=True
    )
    if result.returncode != 0:
        fail(f"Claude exited {result.returncode}:\n{result.stderr[-2000:]}")
    return result.stdout.strip()


def git_diff(cwd: str) -> str:
    return subprocess.check_output(
        ["git", "diff", "HEAD"], cwd=cwd, text=True
    )


def changed_files(cwd: str) -> dict:
    names = subprocess.check_output(
        ["git", "diff", "HEAD", "--name-only"], cwd=cwd, text=True
    ).strip().splitlines()
    contents = {}
    for name in names:
        path = Path(cwd) / name
        if not path.exists():
            continue
        lines = path.read_text(errors="replace").splitlines()
        if len(lines) <= MAX_FILE_LINES:
            contents[name] = "\n".join(lines)
        else:
            contents[name] = f"[file too large ({len(lines)} lines) — see diff]"
    return contents


def load_agent_instructions(repo: str) -> str:
    candidates = [
        Path(repo) / "AGENTS.md",
        Path(repo) / "agent.md",
        Path(repo) / ".github" / "AGENTS.md",
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(errors="replace")
    return ""


def copilot_review(model: str, instructions: str,
                   story: str, diff: str, files: dict) -> str:
    gh_token = subprocess.check_output(
        ["gh", "auth", "token"], text=True
    ).strip()
    client = OpenAI(base_url=COPILOT_BASE, api_key=gh_token)

    file_block = "\n\n".join(
        f"### {name}\n```\n{content}\n```"
        for name, content in files.items()
    ) if files else "(no changed files read)"

    user_msg = (
        f"## Story / Task\n{story}\n\n"
        f"## Changed Files\n{file_block}\n\n"
        f"## Git Diff\n```diff\n{diff}\n```"
    )

    system = "\n\n".join(filter(None, [instructions, REVIEW_SYSTEM]))

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
    )
    return resp.choices[0].message.content.strip()


# ── Prompt builders ───────────────────────────────────────────────────────────

def prompt_initial(ticket_id: str) -> str:
    return (
        f"Use your Linear MCP tools to fetch ticket {ticket_id}. "
        "Search mem0 for relevant context about this codebase — architecture, "
        "patterns, past decisions. "
        f"Create a git branch named feature/{ticket_id.lower()}. "
        "Implement the story fully. Write or update tests if the codebase has them. "
        "When done, output a concise paragraph summarising what you changed and why."
    )


def prompt_fix(summary: str, diff: str, review: str,
               is_final: bool = False) -> str:
    save_note = (
        "\n\nAfter making fixes, use your mem0 MCP tools to save any non-obvious "
        "architectural decisions, patterns, or gotchas from this implementation."
        if is_final else ""
    )
    return (
        f"## Story Summary\n{summary}\n\n"
        f"## Changes Already Made (git diff)\n```diff\n{diff}\n```\n\n"
        f"## Code Review Findings\n{review}\n\n"
        "Address each finding from the code review. Make targeted fixes — "
        "don't rewrite what works. Search mem0 if you need context about "
        f"patterns or past decisions.{save_note}"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Linear story → Claude Code → dual review loop"
    )
    parser.add_argument("ticket_id",  help="Linear ticket ID, e.g. ENG-123")
    parser.add_argument("repo_path",  help="Absolute path to target git repo")
    args = parser.parse_args()

    tid  = args.ticket_id
    repo = str(Path(args.repo_path).expanduser().resolve())

    if not Path(repo).is_dir():
        fail(f"repo_path does not exist: {repo}")

    instructions = load_agent_instructions(repo)
    if instructions:
        print(f"Loaded agent instructions ({len(instructions)} chars)")

    # Step 1 — implement
    step(1, f"Claude Code: implement {tid}")
    summary_1 = run_claude(prompt_initial(tid), cwd=repo)
    diff_1    = git_diff(repo)
    files_1   = changed_files(repo)
    if not diff_1.strip():
        fail("Claude made no file changes.")
    print(f"  {len(files_1)} files changed, {len(diff_1.splitlines())} diff lines")
    print(f"  Summary: {summary_1[:200]}…")

    # Step 2 — review 1
    step(2, f"Review 1: {MODELS[0]} via Copilot")
    review_1 = copilot_review(MODELS[0], instructions, summary_1, diff_1, files_1)
    print(f"  {review_1[:300]}…")

    # Step 3 — fix review 1
    step(3, "Claude Code: apply review 1 fixes")
    summary_2 = run_claude(prompt_fix(summary_1, diff_1, review_1), cwd=repo)
    diff_2    = git_diff(repo)
    files_2   = changed_files(repo)
    print(f"  {len(files_2)} files changed, {len(diff_2.splitlines())} diff lines")

    # Step 4 — review 2
    step(4, f"Review 2: {MODELS[1]} via Copilot")
    review_2 = copilot_review(MODELS[1], instructions, summary_1, diff_2, files_2)
    print(f"  {review_2[:300]}…")

    # Step 5 — fix review 2 + save to mem0
    step(5, "Claude Code: apply review 2 fixes + save to mem0")
    summary_3 = run_claude(
        prompt_fix(summary_1, diff_2, review_2, is_final=True), cwd=repo
    )
    diff_3 = git_diff(repo)

    print(f"\n── Done ─────────────────────────────────────────────")
    print(f"Branch:      feature/{tid.lower()}")
    print(f"Total diff:  {len(diff_3.splitlines())} lines")
    print(f"Summary:     {summary_3[:400]}")


if __name__ == "__main__":
    main()
