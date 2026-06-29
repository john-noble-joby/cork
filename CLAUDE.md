# Cork (Code Orchestrator Review Kickoff)

## Purpose

Serial multi-model coding pipeline that takes a Linear ticket and produces reviewed, fixed code:

1. **Claude Code** fetches the story via Linear MCP, searches mem0 for codebase context, creates a feature branch, implements the story
2. **Claude Code** reviews its own diff (multi-agent), then applies the findings
3. Each blind reviewer from the **preflight-selected ranked rotation** (copilot/openai/anthropic, up to `count` models from `~/.config/cork/config.json`) reviews the current code state in turn — never prior review text — and **Claude Code** applies each model's findings before the next reviewer runs
4. **Claude Code** applies the final model's findings and saves decisions to mem0

The pipeline is `3 + 2×N` committed steps (N = number of preflight-selected models). mem0 and Linear are accessed through Claude Code's existing MCP connections — the Python script never calls those APIs directly.

## Architecture

```
orchestrate.py          # single entry point
docs/plan.md            # design decisions and rationale (historical record)
README.md               # usage and setup
skills/                 # Claude Code skills: cork (session-driven), copilot-review-loop
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
| `CORK_HOME` | `~/dev/cork` | Location of this repo (used by the cork skill) |
| `CORK_CONFIG_FILE` | `~/.config/cork/config.json` | Per-seat model config (ranked `rotation` + `count`) |
| `CORK_COPILOT_TOKEN` | — | Copilot token, used directly (highest priority) |
| `CORK_AUTH_FILE` | `~/.config/cork/auth.json` | Cork's own Copilot token store |
| `CORK_COPILOT_CLIENT_ID` | `Iv1.b507a08c87ecfe98` | GitHub OAuth client id for `login` |

Copilot auth resolves in priority order: `CORK_COPILOT_TOKEN` → `CORK_AUTH_FILE` (`~/.config/cork/auth.json`, written by `orchestrate.py login`) → opencode's `~/.local/share/opencode/auth.json`. Claude Code auth comes from `~/.claude/`.

## Running

```bash
python3 orchestrate.py ENG-123 ~/dev/target-repo
```

No third-party dependencies — Python 3.10+ stdlib only (Copilot API calls go
through `urllib`).

## What NOT to Do

- Don't add a `--dry-run` flag, config file loading, retry logic, or plugin system until there's a real need
- Don't abstract the Copilot client into a class — it's used in one place
- Don't add `__init__.py` or turn this into a package — it's a script
- Don't commit secrets, tokens, or API keys
