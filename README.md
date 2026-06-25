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

## Pipeline

The pipeline is `3 + 2×N` steps, where N is the number of models selected by
`preflight` (see below):

| Steps | Who | What |
|-------|-----|-------|
| 1 | Claude Code | Fetch Linear story via MCP, search mem0, implement, **commit** |
| 2 | Claude Code | Multi-agent self-review |
| 3 | Claude Code | Apply self-review findings, **commit** |
| 4, 6, … | Reviewer model (×N) | Blind review — sees current code, not prior findings |
| 5, 7, … | Claude Code (×N) | Apply findings, **commit** |

Finally, Claude Code pushes the branch and opens a PR summarizing what each
review pass caught. Commits after each fix step give a clear audit trail.

Review models use `code-review/AGENTS.md` if present, falling back to root
`AGENTS.md` or `.github/AGENTS.md`. `gpt-5.x`/codex models are reached via
Copilot's `/responses` endpoint (cork routes them there automatically —
`/chat/completions` returns 400 for them); everything else uses
`/chat/completions`.

> **Session-driven mode:** the `cork` skill runs a richer, interactive variant
> where the active Claude Code session does the implementing and fixing and
> calls `orchestrate.py --review-model MODEL` once per model for a stateless
> blind review. See `skills/cork/SKILL.md`.

## Model configuration

Cork selects reviewers at runtime via `preflight`. The ranked candidate list and
desired count live in `~/.config/cork/config.json` (override path with
`CORK_CONFIG_FILE`):

```json
{
  "version": 1,
  "count": 3,
  "rotation": [
    {"provider": "copilot",   "model": "gpt-5.5"},
    {"provider": "copilot",   "model": "claude-opus-4.7"},
    {"provider": "copilot",   "model": "gpt-4.1"}
  ]
}
```

`rotation` is the ranked preference list; `count` is how many to select.
`preflight` probes each entry in order and picks the first `count` that respond,
skipping unreachable models. Auth failures (401/403) are fatal — fix the token
and retry.

```bash
# Create a starter config (safe to re-run — won't overwrite)
python orchestrate.py config init

# Show the active config
python orchestrate.py config

# Probe and print the models that will be used for this seat
python orchestrate.py preflight
```

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `CLAUDE_BIN` | `~/.local/bin/claude` | Path to Claude Code CLI |
| `CORK_HOME` | `~/dev/cork` | Location of this repo (used by the cork skill) |
| `CORK_CONFIG_FILE` | `~/.config/cork/config.json` | Per-seat model config (ranked `rotation` + `count`) |
| `CORK_COPILOT_TOKEN` | — | Copilot token, used directly (highest priority) |
| `CORK_AUTH_FILE` | `~/.config/cork/auth.json` | Cork's own Copilot token store |
| `CORK_COPILOT_CLIENT_ID` | `Iv1.b507a08c87ecfe98` | GitHub OAuth client id for `login` |

Review models are configured via `config.json` — run `python orchestrate.py config init` to
create a starter file, then edit `rotation` and `count` to taste. Use `preflight` to
confirm what's reachable on your seat.
