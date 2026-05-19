# code-orchestrator

Multi-model coding pipeline with independent sequential reviews. Each reviewer
sees only the current code state — never what prior reviewers found — so every
model hunts for issues with genuinely fresh eyes.

## Usage

```bash
python orchestrate.py <TICKET-ID> <repo-path> [--base-branch <branch>]
python orchestrate.py ENG-123 ~/dev/edge-fmt --base-branch develop
```

## Requirements

```bash
pip install openai
```

- **Claude Code CLI** — authenticated via `~/.claude/` (no extra setup)
- **opencode** — must be authenticated with GitHub Copilot (`opencode auth login`); the script reads the token from `~/.local/share/opencode/auth.json`. This token unlocks Gemini and newer GPT models not available via the `gh` CLI token.
- **mem0** running locally at `http://localhost:8888` (for Claude's MCP context)

## Pipeline (7 steps)

| Step | Who | What |
|------|-----|-------|
| 1 | Claude Code | Fetch Linear story via MCP, search mem0, implement, **commit** |
| 2 | Claude Code | Multi-agent review of own work using `code-review/AGENTS.md` |
| 3 | Claude Code | Apply Claude findings, **commit** |
| 4 | GPT-5.3-Codex | Blind review — sees current code, not Claude's findings |
| 5 | Claude Code | Apply GPT findings, **commit** |
| 6 | Gemini 3.1 Pro | Blind review — sees current code, not prior findings |
| 7 | Claude Code | Apply Gemini findings, save to mem0, **commit** |

Each Copilot reviewer gets the full `git diff base..HEAD` plus current file
contents — enough context to review thoroughly without knowing what prior
reviewers found. Commits after each fix step give a clear audit trail.

Review models use `code-review/AGENTS.md` if present, falling back to root
`AGENTS.md` or `.github/AGENTS.md`.

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `CLAUDE_BIN` | `~/.local/bin/claude` | Path to Claude Code CLI |

Review models can be changed by editing `MODELS` at the top of `orchestrate.py`.
