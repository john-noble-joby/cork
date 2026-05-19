# code-orchestrator

Serial multi-model coding pipeline: fetch a Linear story → Claude Code implements → GPT-4o reviews → Claude fixes → Gemini reviews → Claude final fix + mem0 save.

## Usage

```bash
python orchestrate.py <TICKET-ID> <repo-path>
python orchestrate.py ENG-123 ~/dev/my-repo
```

## Requirements

```bash
pip install openai
```

- **Claude Code CLI** — authenticated via `~/.claude/` (no extra setup)
- **GitHub CLI** — `gh auth login` with a Copilot-enabled account
- **mem0** running locally at `http://localhost:8888` (for Claude's MCP context)

## How it works

1. Claude Code fetches the Linear story via MCP, searches mem0 for codebase context, creates `feature/<ticket>` branch, implements the story
2. GPT-4o (via GitHub Copilot API) reviews the diff + changed files
3. Claude Code applies the review findings
4. Gemini (via GitHub Copilot API) reviews the updated diff
5. Claude Code applies final fixes and saves architectural decisions to mem0

Review models read `AGENTS.md` (or `agent.md` / `.github/AGENTS.md`) from the target repo as their system prompt.

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `CLAUDE_BIN` | `~/.local/bin/claude` | Path to Claude Code CLI |

Review models can be changed by editing `MODELS` at the top of `orchestrate.py`.
