# Cork

**Cork** — **C**ode **Or**chestrator **R**eview **K**ickoff.

Multi-model coding pipeline with independent sequential reviews. Each reviewer
sees only the current code state — never what prior reviewers found — so every
model hunts for issues with genuinely fresh eyes.

## Usage

```bash
python orchestrate.py <TICKET-ID> <repo-path> [--base-branch <branch>]
python orchestrate.py ENG-123 ~/dev/edge-fmt --base-branch develop
```

## Error recovery

The orchestrator checkpoints after every completed step to
`~/.local/share/code-orchestrator/<TICKET>.json`. If anything fails mid-run,
re-run the same command and it resumes automatically from where it left off.

```bash
# Resume automatically (reads checkpoint)
python orchestrate.py ENG-123 ~/dev/edge-fmt

# Force a specific step (e.g. retry step 4 after a token expiry)
python orchestrate.py ENG-123 ~/dev/edge-fmt --start-from 4

# Discard checkpoint and start over
python orchestrate.py ENG-123 ~/dev/edge-fmt --reset
```

Copilot API calls retry 3× with exponential backoff on timeouts, connection
errors, and 5xx responses. Rate-limit (429) responses wait 5× longer.

## Requirements

No third-party Python packages — Python 3.10+ stdlib only.

- **Claude Code CLI** — authenticated via `~/.claude/` (no extra setup)
- **A GitHub Copilot token** — unlocks the GPT and Claude review models not available via the `gh` CLI token. Resolved in priority order: `CORK_COPILOT_TOKEN` env var → cork's own `~/.config/cork/auth.json` (`CORK_AUTH_FILE`) → opencode's `~/.local/share/opencode/auth.json`. The easiest way to get one: run `python orchestrate.py login` (GitHub device flow — writes `~/.config/cork/auth.json` for you).
- **mem0** running locally at `http://localhost:8888` (for Claude's MCP context)

## Pipeline (9 steps)

| Step | Who | What |
|------|-----|-------|
| 1 | Claude Code | Fetch Linear story via MCP, search mem0, implement, **commit** |
| 2 | Claude Code | Multi-agent review of own work using `code-review/AGENTS.md` |
| 3 | Claude Code | Apply Claude findings, **commit** |
| 4 | GPT-4o | Blind review — sees current code, not Claude's findings |
| 5 | Claude Code | Apply GPT-4o findings, **commit** |
| 6 | GPT-4.1 | Blind review — sees current code, not prior findings |
| 7 | Claude Code | Apply GPT-4.1 findings, **commit** |
| 8 | Claude Opus 4.7 | Blind review — sees current code, not prior findings |
| 9 | Claude Code | Apply Opus findings, save to mem0, **commit** |

Finally, Claude Code pushes the branch and opens a PR summarizing what each
review pass caught.

Each Copilot reviewer gets the full `git diff base..HEAD` plus current file
contents — enough context to review thoroughly without knowing what prior
reviewers found. Commits after each fix step give a clear audit trail.

Review models use `code-review/AGENTS.md` if present, falling back to root
`AGENTS.md` or `.github/AGENTS.md`. The three blind-review models are the
`MODELS` list at the top of `orchestrate.py`; they're the set confirmed
available to cork's Copilot integrator identity (Gemini is no longer served to
it, and `gpt-5.x`/codex models use an endpoint cork can't reach).

> **Session-driven mode:** the `cork` skill runs a richer, interactive variant
> where the active Claude Code session does the implementing and fixing and
> calls `orchestrate.py --review-model MODEL` once per model for a stateless
> blind review. See `skills/cork/SKILL.md`.

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `CLAUDE_BIN` | `~/.local/bin/claude` | Path to Claude Code CLI |
| `CORK_HOME` | `~/dev/cork` | Location of this repo (used by the cork skill) |
| `CORK_COPILOT_TOKEN` | — | Copilot token, used directly (highest priority) |
| `CORK_AUTH_FILE` | `~/.config/cork/auth.json` | Cork's own Copilot token store |
| `CORK_COPILOT_CLIENT_ID` | `Iv1.b507a08c87ecfe98` | GitHub OAuth client id for `login` |

Review models can be changed by editing `MODELS` at the top of `orchestrate.py`.
