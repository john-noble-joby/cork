# Multi-Model Coding Orchestration Pipeline

## Context

John wants a serial multi-model coding workflow triggered by a Linear ticket ID. Claude Code does the implementation (with mem0 + Linear context via its existing MCP connections), then two different Copilot-hosted models independently review the code, and Claude Code applies fixes after each review. No orchestration framework (LangGraph, CrewAI, opencode) is needed — the workflow is a simple serial pipeline that doesn't benefit from that complexity.

---

## Project Location

```
~/dev/code-orchestrator/          ← new git repo
├── docs/
│   └── plan.md                   ← this document (copied here at impl time)
├── orchestrate.py                ← the orchestrator script
└── README.md
```

---

## Pipeline

```
$ python orchestrate.py ENG-123 /path/to/repo

1. [Claude Code CLI]        fetch ENG-123 via Linear MCP
                            search mem0 for codebase context + past decisions
                            create branch feature/eng-123
                            implement the story + tests
                            → stdout: summary_1
                            → git diff HEAD: diff_1
                            → changed file list: files_1

2. [Copilot gpt-4o]         system: AGENTS.md code-review section (from repo)
                            user:   story summary + diff_1 + full content of files_1
                            → review_1  (structured: FILE | LINE | ISSUE | FIX)

3. [Claude Code CLI]        context: summary_1 + diff_1 + review_1
                            fix each FILE|LINE finding
                            search mem0 if pattern context needed
                            → stdout: summary_2
                            → git diff HEAD: diff_2
                            → changed file list: files_2

4. [Copilot gemini-2.0-flash] system: AGENTS.md code-review section
                               user:  story summary + diff_2 + full content of files_2
                               → review_2

5. [Claude Code CLI]        context: summary_1 + diff_2 + review_2
                            fix each finding
                            save architectural decisions/patterns to mem0 via MCP
                            → done
```

---

## Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Orchestration framework | None — plain Python | Serial pipeline; frameworks add complexity without capability |
| Copilot API client | `openai` SDK, `base_url="https://api.githubcopilot.com"` | Same endpoint opencode/aider use; 3 lines of code |
| Copilot auth | `gh auth token` subprocess | Token lives in OS keyring, never in plaintext |
| mem0 access | Claude Code MCP (not REST) | Claude already has it wired; no new code needed |
| Linear access | Claude Code MCP (not REST) | Same — just pass ticket ID in prompt |
| Codebase context for reviewers | diff + full changed file contents, read from disk | Reviewers are stateless HTTP calls; context must be explicit |
| AGENT.md injection | Read from repo root at startup, pass as system prompt | Reviewer models need repo-specific instructions |
| No opencode/similar | Raw `openai` SDK for review steps | Review is a single stateless call; extra tool adds no value |

---

## File: `~/dev/code-orchestrator/orchestrate.py`

### Module structure

```python
# ── Config ───────────────────────────────────────────────────────
CLAUDE       = os.environ.get("CLAUDE_BIN", "/home/john.noble/.local/bin/claude")
GH_TOKEN     = subprocess.check_output(["gh", "auth", "token"]).decode().strip()
COPILOT_BASE = "https://api.githubcopilot.com"
MODELS       = ["gpt-4o", "gemini-2.0-flash"]   # review pass 1, pass 2

# ── Helpers ──────────────────────────────────────────────────────
def step(n, msg)                                  # "── Step N/5 — msg"
def fail(msg)                                     # print to stderr, sys.exit(1)

def run_claude(prompt: str, cwd: str) -> str
    # subprocess: [CLAUDE, "--print", prompt], cwd=cwd
    # returns stdout; raises on non-zero exit

def git_diff(cwd: str) -> str
    # git diff HEAD

def changed_files(cwd: str) -> dict[str, str]
    # git diff HEAD --name-only → read each file → {path: content}
    # skip files > 500 lines (include path in diff-only note)

def load_agent_instructions(repo: str) -> str
    # look for AGENTS.md, agent.md, .github/AGENTS.md (in that order)
    # extract code-review section if present, else use full file
    # returns "" if not found

def copilot_review(model: str, instructions: str,
                   story: str, diff: str,
                   files: dict[str, str]) -> str
    # openai.OpenAI(base_url=COPILOT_BASE, api_key=GH_TOKEN)
    # system: instructions (AGENTS.md)
    # user:   story + formatted file contents + diff
    # returns review text

# ── Prompt builders ──────────────────────────────────────────────
def prompt_initial(ticket_id: str) -> str
    # "Fetch {ticket_id} via Linear MCP, search mem0 for context,
    #  create branch feature/{ticket_id.lower()}, implement fully.
    #  Output a one-paragraph summary of what you changed and why."

def prompt_fix(summary: str, diff: str, review: str,
               is_final: bool = False) -> str
    # STORY SUMMARY / CHANGES MADE / CODE REVIEW FINDINGS sections
    # if is_final: add "save key decisions to mem0 via MCP tools"

# ── main() ───────────────────────────────────────────────────────
# argparse: ticket_id, repo_path
# load agent_instructions once at startup
# run 5 steps with step() markers
# print branch name + total lines changed on completion
```

### Review system prompt (structured output)

```
You are a senior code reviewer. For each issue output exactly:
FILE: <path> | LINE: <n> | ISSUE: <description> | FIX: <suggestion>
Be specific. Reference exact file paths and line numbers.
Cover: correctness, error handling, edge cases,
       style consistency with surrounding code, test coverage.
```

---

## Dependencies

```
openai>=1.0      # Copilot API (pip install openai)
# everything else is stdlib: subprocess, os, sys, argparse, pathlib
```

Claude Code CLI auth comes from existing `~/.claude/` config — no `ANTHROPIC_API_KEY` needed.

---

## Setup Steps (at implementation time)

1. `mkdir -p ~/dev/code-orchestrator/docs`
2. `git init ~/dev/code-orchestrator`
3. Copy this plan to `~/dev/code-orchestrator/docs/plan.md`
4. Write `orchestrate.py`
5. Write `README.md` (usage, deps, setup)
6. `pip install openai` (or add to a venv)
7. Initial commit

---

## Verification

1. `python orchestrate.py ENG-123 ~/dev/some-repo`
2. After step 1: `git -C ~/dev/some-repo branch` shows `feature/eng-123`; `git diff HEAD` has real changes
3. After step 2: review_1 contains `FILE:` and `LINE:` references
4. After step 5: `git diff HEAD` reflects all three rounds of fixes; mem0 has new entries
5. Re-run on a second ticket to confirm AGENT.md loads, token refreshes cleanly

---

## Learnings from first real run (MXE-204, 2026-05-19)

### Bugs hit and fixed in-flight

**1. Codex/gpt-5.x models don't support `/chat/completions`**
`gpt-5.3-codex` returned HTTP 400 "not accessible via /chat/completions". These models
use a different responses API endpoint. Switched to `gpt-4.1`. Working models confirmed
on this Copilot account: `gpt-4.1`, `gpt-4o`, `gemini-3.1-pro-preview`, `gemini-3-flash-preview`,
`claude-sonnet-4.6`. Fixed: startup validation now tests each model before the pipeline starts.

**2. Token budget overflow on large PRs**
MXE-204 had 33 files; full contents hit 84k tokens against a 64k limit. Fixed via `_budget_files()`.
Hardcoded 192k char cap replaced by fetching the real `max_prompt_tokens` from `/models` at startup.

### What worked well

- **Blind review design validated** — each model found genuinely different things with no meaningful
  overlap. Claude caught critical correctness bugs; GPT-4.1 caught doc/contract clarity; Gemini
  caught heap allocations and LINQ closure costs.
- **Checkpoint resume was essential** — failed twice (wrong model, token overflow), resumed from
  step 4 both times without losing the step 3 commit.
- **Human-attention summary was signal, not noise** — both uncertain items were real judgment calls.
- **Claude updated CLAUDE.md autonomously** — added transport naming convention learned during fixes.
  This is desirable; the convention doc update was included in the review-fix commit (acceptable).

### Improvements implemented after the run

1. **Startup validation** — `startup_checks()` fetches `/models`, verifies each configured model is
   listed and callable on `/chat/completions`, and returns the char budget from the real token limit.
2. **Dynamic token budget** — uses `min(max_prompt_tokens)` across review models from the API,
   not a hardcoded constant.
3. **`--seed-only` flag** — creates a checkpoint from an existing branch's commit log without
   running step 1, enabling the pipeline to start at step 2 for in-flight PRs.
