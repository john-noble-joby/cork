#!/usr/bin/env python3
"""
orchestrate.py — Multi-model coding pipeline with independent sequential reviews.

Pipeline (7 steps):
  1. Claude Code: implement story (branch + commit)
  2. Claude Code: parallel multi-agent review of own work → findings
  3. Claude Code: apply Claude findings → commit
  4. GPT-5.3-Codex: blind review of current branch state → findings
  5. Claude Code: apply GPT findings → commit
  6. Gemini 3.1 Pro: blind review of current branch state → findings
  7. Claude Code: apply Gemini findings → commit + save to mem0

Each Copilot reviewer sees only the current code state, never prior review text.
Commits after each fix step create a clear audit trail of what each model caught.

Resume after failure:
  The orchestrator writes a checkpoint to
  ~/.local/share/code-orchestrator/<TICKET>.json after each completed step.
  Re-running the same command resumes automatically. To force a specific step:
    python orchestrate.py ENG-123 ~/dev/edge-fmt --start-from 4
  To reset and start over, delete the checkpoint file.

Usage:
    python orchestrate.py <TICKET-ID> <repo-path> [options]
    python orchestrate.py ENG-123 ~/dev/edge-fmt --base-branch develop

Requirements:
    pip install openai
    opencode authenticated with GitHub Copilot
    (token read from ~/.local/share/opencode/auth.json)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

# ── Config ────────────────────────────────────────────────────────────────────

CLAUDE         = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
COPILOT_BASE   = "https://api.githubcopilot.com"
MODELS         = ["gpt-4.1", "gemini-3.1-pro-preview"]  # review pass 1, pass 2
MAX_FILE_LINES = 500
TOTAL_STEPS    = 7
STATE_DIR      = Path.home() / ".local/share/code-orchestrator"
_OPENCODE_AUTH = Path.home() / ".local/share/opencode/auth.json"
_DEFAULT_CHAR_BUDGET = 192_000  # fallback if /models fetch fails

REVIEW_SYSTEM = """\
You are a senior code reviewer. For each issue output exactly:
FILE: <path> | LINE: <n> | ISSUE: <description> | FIX: <suggestion>
Be specific. Reference exact file paths and line numbers.
Cover: correctness, error handling, edge cases,
style consistency with surrounding code, test coverage.\
"""

# ── Auth ──────────────────────────────────────────────────────────────────────

def _copilot_token() -> str:
    try:
        data = json.loads(_OPENCODE_AUTH.read_text())
        return data["github-copilot"]["refresh"]
    except (KeyError, FileNotFoundError) as e:
        fail(
            f"Cannot read opencode Copilot token from {_OPENCODE_AUTH}: {e}\n"
            "  → Run opencode and authenticate with GitHub Copilot first."
        )

# ── Startup checks ───────────────────────────────────────────────────────────

def startup_checks(models: list[str]) -> int:
    """
    1. Fetch /models from Copilot API.
    2. Verify each model in `models` is listed.
    3. Test each model with a 1-token call to confirm /chat/completions works.
    4. Return a char budget derived from the minimum max_prompt_tokens across models.
    Fails fast with a clear message if any model is misconfigured.
    """
    token = _copilot_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "x-initiator": "user",
        "Openai-Intent": "conversation-edits",
        "User-Agent": "opencode/0.1.0",
    }

    print("Validating review models…")
    try:
        req = urllib.request.Request(f"{COPILOT_BASE}/models", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            model_list = json.loads(r.read()).get("data", [])
    except Exception as e:
        print(f"  ⚠ Could not fetch model list ({e}) — skipping limit detection")
        model_list = []

    model_map = {m["id"]: m for m in model_list}

    client = OpenAI(
        base_url=COPILOT_BASE,
        api_key=token,
        default_headers={k: v for k, v in headers.items()
                         if k != "Authorization"},
    )

    min_prompt_tokens = 48_000  # conservative fallback
    for model in models:
        if model_map and model not in model_map:
            available = ", ".join(
                m for m in sorted(model_map)
                if not m.startswith("text-embedding")
            )
            fail(
                f"Model '{model}' not found in your Copilot account.\n"
                f"  Available: {available}\n"
                f"  → Update MODELS in orchestrate.py"
            )
        try:
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=1,
            )
        except APIStatusError as e:
            if e.status_code == 400 and "not accessible" in str(e):
                fail(
                    f"Model '{model}' does not support /chat/completions.\n"
                    f"  → gpt-5.x and codex models use a different endpoint.\n"
                    f"  → Working alternatives: gpt-4.1, gpt-4o, "
                    f"gemini-3.1-pro-preview, claude-sonnet-4.6"
                )
            raise

        limit = (
            model_map.get(model, {})
            .get("capabilities", {})
            .get("limits", {})
            .get("max_prompt_tokens")
        )
        if limit:
            min_prompt_tokens = min(min_prompt_tokens, limit)
            print(f"  ✓ {model}  ({limit:,} token limit)")
        else:
            print(f"  ✓ {model}  (limit unknown — using default)")

    # Reserve 8k tokens for response, take 90% of remainder, 4 chars/token
    budget = int((min_prompt_tokens - 8_000) * 0.9) * 4
    print(f"  Char budget: {budget:,} chars (~{budget // 4:,} tokens)")
    return budget


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _state_path(ticket_id: str) -> Path:
    return STATE_DIR / f"{ticket_id}.json"


def load_state(ticket_id: str) -> dict:
    p = _state_path(ticket_id)
    if p.exists():
        return json.loads(p.read_text())
    return {"ticket_id": ticket_id, "completed": []}


def mark_done(ticket_id: str, step_n: int, **extras) -> None:
    state = load_state(ticket_id)
    if step_n not in state["completed"]:
        state["completed"].append(step_n)
    state.update(extras)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(ticket_id).write_text(json.dumps(state, indent=2))


def clear_state(ticket_id: str) -> None:
    p = _state_path(ticket_id)
    if p.exists():
        p.unlink()
        print(f"  → cleared checkpoint {p}")

# ── Helpers ───────────────────────────────────────────────────────────────────

def step(n: int, msg: str) -> None:
    print(f"\n── Step {n}/{TOTAL_STEPS} — {msg}", flush=True)


def skip(n: int, msg: str) -> None:
    print(f"\n── Step {n}/{TOTAL_STEPS} — {msg} [skipped — already done]", flush=True)


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


def git_diff_branch(cwd: str, base: str) -> str:
    return subprocess.check_output(
        ["git", "diff", f"{base}..HEAD"], cwd=cwd, text=True
    )


def changed_files_branch(cwd: str, base: str) -> dict[str, str]:
    names = subprocess.check_output(
        ["git", "diff", f"{base}..HEAD", "--name-only"], cwd=cwd, text=True
    ).strip().splitlines()
    contents: dict[str, str] = {}
    for name in names:
        path = Path(cwd) / name
        if not path.exists():
            continue
        lines = path.read_text(errors="replace").splitlines()
        contents[name] = (
            "\n".join(lines) if len(lines) <= MAX_FILE_LINES
            else f"[file too large ({len(lines)} lines) — see diff]"
        )
    return contents


def git_commit_all(cwd: str, message: str) -> bool:
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=cwd, capture_output=True, text=True
    )
    if result.returncode == 0:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=cwd, text=True
        ).strip()
        print(f"  → committed {sha}: {message}")
        return True
    if "nothing to commit" in (result.stdout + result.stderr):
        print("  → nothing to commit")
        return False
    fail(f"git commit failed:\n{result.stderr}")
    return False


def load_agent_instructions(repo: str) -> tuple[str, str]:
    candidates = [
        Path(repo) / "code-review" / "AGENTS.md",
        Path(repo) / "code-review" / "agent.md",
        Path(repo) / "AGENTS.md",
        Path(repo) / "agent.md",
        Path(repo) / ".github" / "AGENTS.md",
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(errors="replace"), str(p)
    return "", ""


def _budget_files(files: dict[str, str], budget_chars: int) -> tuple[str, int]:
    """
    Pack as many file contents as fit within budget_chars.
    Returns (file_block_str, included_count).
    Sorts by size ascending so small files always get in.
    """
    sorted_files = sorted(files.items(), key=lambda x: len(x[1]))
    included, used = [], 0
    for name, content in sorted_files:
        entry = f"### {name}\n```\n{content}\n```"
        if used + len(entry) > budget_chars:
            break
        included.append(entry)
        used += len(entry)
    if not included:
        return "(files omitted — diff too large; see diff section)", 0
    block = "\n\n".join(included)
    if len(included) < len(files):
        block += f"\n\n_(+{len(files) - len(included)} files omitted for token budget — see diff)_"
    return block, len(included)


def copilot_review(model: str, instructions: str,
                   story: str, diff: str, files: dict[str, str],
                   char_budget: int = _DEFAULT_CHAR_BUDGET,
                   max_attempts: int = 3) -> str:
    client = OpenAI(
        base_url=COPILOT_BASE,
        api_key=_copilot_token(),
        default_headers={
            "x-initiator": "user",
            "Openai-Intent": "conversation-edits",
            "User-Agent": "opencode/0.1.0",
        },
    )

    system = (
        instructions
        + "\n\n---\n"
        "Note: you are a single-pass API reviewer — you cannot spawn "
        "sub-agents or invoke skills. Apply §3–§7 in one pass and produce "
        "the §8 output format. Do NOT apply fixes; report findings only."
        if instructions
        else REVIEW_SYSTEM
    )

    fixed_chars = len(system) + len(story) + len(diff) + 500  # headers + overhead
    file_budget  = max(0, char_budget - fixed_chars)

    file_block, n_included = _budget_files(files, file_budget)
    if n_included < len(files):
        print(f"  → token budget: included {n_included}/{len(files)} files "
              f"(diff-only for the rest)")

    user_msg = (
        f"## Story / Task\n{story}\n\n"
        f"## Changed Files (current state)\n{file_block}\n\n"
        f"## Branch Diff\n```diff\n{diff}\n```"
    )

    for attempt in range(max_attempts):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_msg},
                ],
            )
            return resp.choices[0].message.content.strip()

        except APITimeoutError:
            _retry_wait(attempt, max_attempts, "timeout")
        except APIConnectionError:
            _retry_wait(attempt, max_attempts, "connection error")
        except APIStatusError as e:
            if e.status_code in (429, 500, 502, 503, 504):
                _retry_wait(attempt, max_attempts, f"HTTP {e.status_code}", long=e.status_code == 429)
            else:
                fail(f"Copilot API error {e.status_code}: {e.message}")

    fail(f"Copilot API failed after {max_attempts} attempts")


def _retry_wait(attempt: int, max_attempts: int, reason: str, long: bool = False) -> None:
    if attempt == max_attempts - 1:
        fail(f"Copilot API: {reason} — giving up after {max_attempts} attempts")
    wait = (2 ** attempt) * (5 if long else 1)
    print(f"  → {reason}, retrying in {wait}s (attempt {attempt + 1}/{max_attempts})")
    time.sleep(wait)


def extract_uncertain(review: str) -> str:
    """
    Pull out the 'Uncertain / needs human judgment' section from a review.
    Returns the section body, or "" if not present.
    """
    match = re.search(
        r"#+\s*(?:Uncertain|needs human|human judgment)[^\n]*\n(.*?)(?=\n#+\s|\Z)",
        review, re.IGNORECASE | re.DOTALL
    )
    if not match:
        return ""
    body = match.group(1).strip()
    # Skip if the section is empty or just says "none" / "n/a"
    if not body or re.match(r"^(none|n/?a|—|-)\s*$", body, re.IGNORECASE):
        return ""
    return body


def print_human_summary(
    uncertain: list[tuple[str, str]],
    notes: list[tuple[str, str]],
) -> None:
    """
    Print items needing human attention after the pipeline completes.
    uncertain: [(reviewer_label, uncertain_section_text), ...]
    notes:     [(step_label, claude_fix_response), ...]
    """
    has_uncertain = any(text for _, text in uncertain)
    has_notes = any(text for _, text in notes)
    if not has_uncertain and not has_notes:
        return

    print("\n── Human attention needed ────────────────────────────────")

    if has_uncertain:
        print("\nUncertain items requiring your judgment:")
        for label, text in uncertain:
            if text:
                print(f"\n  [{label}]")
                for line in text.splitlines():
                    print(f"    {line}")

    if has_notes:
        print("\nClaude Code notes from fix steps (pushbacks / partial applies):")
        for label, text in notes:
            if text:
                # Show first 600 chars — enough to see reasoning without flooding terminal
                preview = text[:600].strip()
                if len(text) > 600:
                    preview += "\n    … (truncated — full text in checkpoint)"
                print(f"\n  [{label}]")
                for line in preview.splitlines():
                    print(f"    {line}")


# ── Prompt builders ───────────────────────────────────────────────────────────

def prompt_initial(ticket_id: str) -> str:
    return (
        f"Use your Linear MCP tools to fetch ticket {ticket_id}. "
        "Search mem0 for relevant context about this codebase — architecture, "
        "patterns, past decisions. "
        f"Create a git branch named feature/{ticket_id.lower()}. "
        "Implement the story fully. Write or update tests if the codebase has them. "
        "When done, output a concise paragraph summarising what you changed and why. "
        "Do NOT commit — the orchestrator will commit after this step."
    )


def prompt_claude_review(base: str, instructions_path: str) -> str:
    review_src = (
        f"Read and follow the review instructions in {instructions_path}."
        if instructions_path
        else "Perform a thorough multi-agent code review."
    )
    return (
        f"Review the current feature branch against {base}. "
        f"The full branch diff is available via: git diff {base}..HEAD\n\n"
        f"{review_src}\n\n"
        "Output ONLY a structured findings report. "
        "Do NOT apply any fixes. Do NOT edit any files."
    )


def prompt_fix(summary: str, base: str, review: str, ticket_id: str,
               is_final: bool = False) -> str:
    save_note = (
        "\n\nAfter making fixes, use your mem0 MCP tools to save any non-obvious "
        "architectural decisions, patterns, or gotchas from this implementation."
        if is_final else ""
    )
    return (
        f"## Story Summary\n{summary}\n\n"
        "## Current Branch State\n"
        f"Run `git diff {base}..HEAD` to see all changes on this branch.\n\n"
        f"## Code Review Findings\n{review}\n\n"
        "Address findings in the Critical, Important, Minor, Cross-cutting, and "
        "Promotion candidates sections. Make targeted fixes — don't rewrite what works. "
        "Search mem0 if you need context about patterns or past decisions.\n\n"
        "DO NOT attempt to resolve items in 'Uncertain', 'needs human judgment', or "
        "'Out of scope' sections — those are flagged for human review, not automated fixing.\n\n"
        "If you choose not to apply a finding (because it conflicts with established patterns, "
        "would break something, or is genuinely wrong for this codebase), explain your "
        "reasoning clearly in your response. Your response is captured and shown to the human.\n\n"
        f"If a finding requires effort too large to address inline (a significant refactor, "
        f"a new service, a cross-cutting change), use your Linear MCP tools to create a new "
        f"story for it, linked to {ticket_id}. Include the created story ID in your response.\n\n"
        f"If you discover something during fixes that materially changes the scope or approach "
        f"of the current story (a pivot, a learned constraint, a design correction), update "
        f"ticket {ticket_id} via your Linear MCP tools to reflect it.\n\n"
        f"Do NOT commit — the orchestrator will commit after this step.{save_note}"
    )

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Linear story → Claude review → GPT review → Gemini review"
    )
    parser.add_argument("ticket_id",  help="Linear ticket ID, e.g. ENG-123")
    parser.add_argument("repo_path",  help="Absolute path to target git repo")
    parser.add_argument("--base-branch", default="develop",
                        help="Branch to diff against (default: develop)")
    parser.add_argument("--start-from", type=int, metavar="N",
                        help="Force resume from step N (auto-detected from checkpoint if omitted)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete checkpoint and start from scratch")
    parser.add_argument("--seed-only", action="store_true",
                        help="Seed checkpoint from existing branch commits and exit. "
                             "Use when implementation is already done — then re-run "
                             "with --start-from 2 to begin reviews.")
    args = parser.parse_args()

    tid  = args.ticket_id
    repo = str(Path(args.repo_path).expanduser().resolve())
    base = args.base_branch

    if not Path(repo).is_dir():
        fail(f"repo_path does not exist: {repo}")

    if args.reset:
        clear_state(tid)

    # ── --seed-only: checkpoint an existing branch and exit ──────────────────
    if args.seed_only:
        commits = subprocess.check_output(
            ["git", "log", f"{base}..HEAD", "--oneline"], cwd=repo, text=True
        ).strip()
        if not commits:
            fail(f"No commits found on branch vs {base}. Is the branch checked out?")
        summary = f"Implementation already complete on branch. Commits:\n{commits}"
        mark_done(tid, 1, summary=summary, base=base, repo=repo)
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, text=True
        ).strip()
        print(f"Seeded checkpoint for {tid}")
        print(f"Branch:  {branch}")
        print(f"Commits: {len(commits.splitlines())} commits vs {base}")
        print(f"Run:     python orchestrate.py {tid} {repo} --start-from 2 --base-branch {base}")
        return

    char_budget = startup_checks(MODELS)

    state = load_state(tid)
    completed = set(state.get("completed", []))

    start_from = args.start_from or (
        max(completed) + 1 if completed else 1
    )

    if start_from > 1:
        summary = state.get("summary")
        if not summary:
            fail(
                f"Resuming from step {start_from} but no checkpoint found for {tid}.\n"
                f"  → Run without --start-from, or delete {_state_path(tid)} to reset."
            )
        print(f"Resuming {tid} from step {start_from} "
              f"(completed: {sorted(completed)})")
    else:
        summary = ""

    instructions, instructions_path = load_agent_instructions(repo)
    if instructions_path:
        print(f"Review instructions: {instructions_path} ({len(instructions)} chars)")
    else:
        print("No AGENTS.md found — using default review format")

    # Accumulators for human-attention summary printed at the end
    uncertain_items: list[tuple[str, str]] = []
    fix_notes: list[tuple[str, str]] = []

    # ── Step 1: Implement ────────────────────────────────────────────────────
    if start_from <= 1:
        step(1, f"Claude Code: implement {tid}")
        summary = run_claude(prompt_initial(tid), cwd=repo)
        print(f"  Summary: {summary[:200]}…")
        git_commit_all(repo, f"feat: implement {tid}")
        mark_done(tid, 1, summary=summary, base=base, repo=repo)
    else:
        skip(1, f"Claude Code: implement {tid}")

    diff  = git_diff_branch(repo, base)
    files = changed_files_branch(repo, base)
    if not diff.strip():
        fail("No diff vs base branch — nothing to review.")
    print(f"  {len(files)} files, {len(diff.splitlines())} diff lines vs {base}")

    # ── Step 2: Claude multi-agent review ────────────────────────────────────
    if start_from <= 2:
        step(2, "Claude Code: multi-agent review")
        claude_review = run_claude(prompt_claude_review(base, instructions_path), cwd=repo)
        print(f"  {claude_review[:300]}…")
        mark_done(tid, 2, claude_review=claude_review)
    else:
        skip(2, "Claude Code: multi-agent review")
        claude_review = state.get("claude_review", "")

    uncertain_items.append(("Claude agent review", extract_uncertain(claude_review)))

    # ── Step 3: Apply Claude findings ────────────────────────────────────────
    if start_from <= 3:
        step(3, "Claude Code: apply Claude review findings")
        fix_out = run_claude(prompt_fix(summary, base, claude_review, tid), cwd=repo)
        fix_notes.append(("Step 3 — Claude review fixes", fix_out))
        git_commit_all(repo, f"fix: apply Claude agent review [{tid}]")
        mark_done(tid, 3, fix_note_3=fix_out)
    else:
        skip(3, "Claude Code: apply Claude review findings")
        fix_notes.append(("Step 3 — Claude review fixes", state.get("fix_note_3", "")))

    # ── Step 4: GPT blind review ─────────────────────────────────────────────
    if start_from <= 4:
        step(4, f"Blind review: {MODELS[0]} via Copilot")
        diff  = git_diff_branch(repo, base)
        files = changed_files_branch(repo, base)
        print(f"  Sending {len(files)} files, {len(diff.splitlines())} lines to {MODELS[0]}")
        gpt_review = copilot_review(MODELS[0], instructions, summary, diff, files, char_budget)
        print(f"  {gpt_review[:300]}…")
        mark_done(tid, 4, gpt_review=gpt_review)
    else:
        skip(4, f"Blind review: {MODELS[0]}")
        gpt_review = state.get("gpt_review", "")

    uncertain_items.append((MODELS[0], extract_uncertain(gpt_review)))

    # ── Step 5: Apply GPT findings ───────────────────────────────────────────
    if start_from <= 5:
        step(5, f"Claude Code: apply {MODELS[0]} findings")
        fix_out = run_claude(prompt_fix(summary, base, gpt_review, tid), cwd=repo)
        fix_notes.append((f"Step 5 — {MODELS[0]} fixes", fix_out))
        git_commit_all(repo, f"fix: apply {MODELS[0]} review [{tid}]")
        mark_done(tid, 5, fix_note_5=fix_out)
    else:
        skip(5, f"Claude Code: apply {MODELS[0]} findings")
        fix_notes.append((f"Step 5 — {MODELS[0]} fixes", state.get("fix_note_5", "")))

    # ── Step 6: Gemini blind review ──────────────────────────────────────────
    if start_from <= 6:
        step(6, f"Blind review: {MODELS[1]} via Copilot")
        diff  = git_diff_branch(repo, base)
        files = changed_files_branch(repo, base)
        print(f"  Sending {len(files)} files, {len(diff.splitlines())} lines to {MODELS[1]}")
        gemini_review = copilot_review(MODELS[1], instructions, summary, diff, files, char_budget)
        print(f"  {gemini_review[:300]}…")
        mark_done(tid, 6, gemini_review=gemini_review)
    else:
        skip(6, f"Blind review: {MODELS[1]}")
        gemini_review = state.get("gemini_review", "")

    uncertain_items.append((MODELS[1], extract_uncertain(gemini_review)))

    # ── Step 7: Apply Gemini findings + save to mem0 ─────────────────────────
    if start_from <= 7:
        step(7, f"Claude Code: apply {MODELS[1]} findings + save to mem0")
        fix_out = run_claude(prompt_fix(summary, base, gemini_review, tid, is_final=True), cwd=repo)
        fix_notes.append((f"Step 7 — {MODELS[1]} fixes", fix_out))
        git_commit_all(repo, f"fix: apply {MODELS[1]} review [{tid}]")
        mark_done(tid, 7, fix_note_7=fix_out)
    else:
        skip(7, f"Claude Code: apply {MODELS[1]} findings")
        fix_notes.append((f"Step 7 — {MODELS[1]} fixes", state.get("fix_note_7", "")))

    final_diff = git_diff_branch(repo, base)
    clear_state(tid)

    print(f"\n── Done ──────────────────────────────────────────────────")
    print(f"Branch:     feature/{tid.lower()}")
    print(f"Base:       {base}")
    print(f"Total diff: {len(final_diff.splitlines())} lines vs {base}")
    print(f"Commits:    git log --oneline {base}..HEAD")

    print_human_summary(uncertain_items, fix_notes)


if __name__ == "__main__":
    main()
