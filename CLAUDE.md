# code-orchestrator

## Purpose

Serial multi-model coding pipeline that takes a Linear ticket and produces reviewed, fixed code:

1. **Claude Code** fetches the story via Linear MCP, searches mem0 for codebase context, creates a feature branch, implements the story
2. **GPT-4o** (GitHub Copilot API) reviews the diff and changed files
3. **Claude Code** applies the review findings
4. **Gemini** (GitHub Copilot API) reviews the updated diff
5. **Claude Code** applies final fixes and saves decisions to mem0

mem0 and Linear are accessed through Claude Code's existing MCP connections — the Python script never calls those APIs directly.

## Architecture

```
orchestrate.py          # single entry point, ~130 lines
docs/plan.md            # design decisions and rationale
README.md               # usage and setup
```

No frameworks. No classes. No abstractions beyond what the task requires.

## Python Conventions

- **Python 3.10+** — use `match`, `|` union types, `pathlib.Path` throughout
- **Type hints on all functions** — parameters and return types, no exceptions
- **No classes** — this is a script; module-level functions and constants only
- **No logging framework** — `print()` with `flush=True` for progress, `fail()` helper for errors
- **Explicit over implicit** — if context needs to be passed somewhere, pass it; don't use globals
- **No unnecessary error handling** — only catch at true system boundaries (subprocess calls, HTTP calls, file reads on user-supplied paths)
- **`pathlib.Path` not `os.path`** — for all file operations
- **`subprocess.run()` with `capture_output=True, text=True`** — not `os.system`, not `Popen` unless streaming is needed
- **Short functions** — if a function doesn't fit on screen, split it
- **No docstrings** — function names and type hints are the documentation; add a comment only when the WHY is non-obvious

## Key Files in Target Repos

When the orchestrator runs against a repo, it looks for:
- `AGENTS.md`, `agent.md`, or `.github/AGENTS.md` — injected as system prompt for reviewer models
- Standard git history — `git diff HEAD` is the source of truth for what Claude Code changed

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLAUDE_BIN` | `~/.local/bin/claude` | Claude Code CLI path |

Copilot auth comes from `gh auth token` (OS keyring). Claude Code auth comes from `~/.claude/`.

## Running

```bash
pip install openai
python orchestrate.py ENG-123 ~/dev/target-repo
```

## What NOT to Do

- Don't add a `--dry-run` flag, config file loading, retry logic, or plugin system until there's a real need
- Don't abstract the Copilot client into a class — it's used in one place
- Don't add `__init__.py` or turn this into a package — it's a script
- Don't commit secrets, tokens, or API keys
