# Cork

**Cork** — **C**ode **Or**chestrator **R**eview **K**ickoff.

A small toolkit of Claude Code skills that take a Linear ticket to a reviewed PR:
the active Claude session implements, then several independent models review the
diff — each seeing only the current code, never prior reviewers' notes — so every
model hunts for issues with fresh eyes.

It ships three skills:

| Skill | Say | What it does |
|-------|-----|--------------|
| **devit** | `devit MXE-123` | The full dev loop: verify the story → size-gate (split if too big) → worktree + branch → implement → cork review → PR → Copilot review loop → surface pushbacks. The top-level entry point. |
| **cork** | `cork` / `cork review` | Multi-model review of a branch. *Full*: implement + apply each model's fixes + PR. *Review-only*: run all models in parallel, print a consolidated findings report, change nothing. |
| **copilot-review-loop** | `run the copilot review loop` | Iterative GitHub Copilot PR review: request → fix/push-back each comment → reply + resolve → re-request, up to N passes. |

`devit` orchestrates the other two — you'll mostly just run `devit`.

---

## Setup

Python 3.10+ stdlib only — no `pip install`. **`install.sh` does not set env vars or
fetch tokens** (it only deploys the skills + status line and checks versions), so the
token and MCP steps below are manual.

1. **Clone** to the default location (`CORK_HOME` defaults to `~/dev/cork`):
   ```bash
   git clone git@github.com:john-noble-joby/cork.git ~/dev/cork
   ```
   If you clone elsewhere, set `CORK_HOME` to that path in your shell profile.

2. **Install the skills + status line:**
   ```bash
   cd ~/dev/cork && ./install.sh
   ```
   Copies `cork`, `copilot-review-loop`, `devit`, and `cork-setup` into `~/.claude/skills/` and
   `statusline.py` into `~/.claude/`, and verifies every version stamp matches `VERSION`.
   Re-run after a `git pull` to update. (`orchestrate.py` itself isn't copied — the skills
   run it straight from `$CORK_HOME`, so `git pull` updates the engine.)

3. **Get a Copilot token** (required — this is what unlocks the review models):
   ```bash
   python ~/dev/cork/orchestrate.py login
   ```
   GitHub device flow → writes `~/.config/cork/auth.json` (chmod 600). Re-run if it expires
   (a 401 in a review means expired).

4. **Connect Linear + mem0 in Claude Code** (MCP): `devit` fetches the story from Linear
   (and files split sub-stories there); cork pulls codebase context from mem0. Configure
   these as MCP servers in Claude Code — they're not part of cork's install.

5. **(Optional) Enable the status line** so a session shows its active ticket/branch — add
   to `~/.claude/settings.json` and restart Claude Code:
   ```json
   { "statusLine": { "type": "command", "command": "~/.claude/statusline.py" } }
   ```

6. **(Optional) Customize the review models** — without a config, a built-in default is
   used:
   ```bash
   python ~/dev/cork/orchestrate.py config init   # write a starter config you can edit
   python ~/dev/cork/orchestrate.py preflight      # show which models your seat can use
   ```

7. **Restart Claude Code** so it loads the new skills (and the status line).

Then, in any repo: **`devit MXE-123`**.

Most of steps 3–6 are handled for you by the **`cork-setup` skill** — after `install.sh` and a
restart, just say **"set up cork"** and it walks you through the token, models, the
pause-between-reviews preference, and the status line.

---

## Write detailed Linear tickets — it matters a lot

Cork is only as good as the ticket you point it at. **Well-fleshed-out tickets — clear
scope, explicit acceptance criteria, context, links, known edge cases — make a real
difference:**

- **Implementation:** `devit` verifies the story before writing code and will *stop and
  ask* if scope or acceptance criteria are vague. A detailed ticket lets it proceed
  confidently and build what you actually meant — fewer clarifying stops, less rework.
- **Review quality:** the cork models and the Copilot PR reviewer judge the diff *against
  the story*. A rich ticket gives them the intent to check the code against, so they catch
  "this doesn't actually satisfy the AC" — not just generic nits. It also yields a sharper
  "In plain terms" PR description.

Thin one-liner tickets → more interruptions and weaker reviews. Spend the five minutes on
the ticket.

---

## How it works

Lower-level detail and the underlying `orchestrate.py` engine.

### The review pipeline (`3 + 2×N` steps)

`N` = the number of models `preflight` selects for your seat:

| Steps | Who | What |
|-------|-----|------|
| 1 | Claude Code | Fetch story, search mem0, implement, **commit** |
| 2 | Claude Code | Multi-agent self-review |
| 3 | Claude Code | Apply self-review findings, **commit** |
| 4, 6, … | Reviewer model (×N) | Blind review — sees current code, not prior findings |
| 5, 7, … | Claude Code (×N) | Apply findings, **commit** |

Then Claude Code pushes the branch and opens a PR summarizing what each pass caught.
Commits after each fix give a clear audit trail. Reviewers use `code-review/AGENTS.md` if
present (else root `AGENTS.md` or `.github/AGENTS.md`).

Two ways to run it:
- **Session-driven (the skills):** the active Claude session implements/fixes and calls
  `orchestrate.py --review-model <provider/model>` once per model for a stateless blind
  review. This is what `cork`/`devit` use.
- **Headless (legacy/unattended):** `python orchestrate.py <TICKET> <repo-path>
  [--base-branch <branch>]` runs the whole loop in subprocesses, checkpointing after each
  step (resume by re-running; `--reset` to start over).

### Model selection (`preflight` + `config.json`)

Cork picks reviewers at runtime. The ranked candidate list and desired count live in
`~/.config/cork/config.json` (override path with `CORK_CONFIG_FILE`):

```json
{
  "version": 1,
  "count": 3,
  "providers": { "copilot": {"enabled": true}, "openai": {"enabled": false}, "anthropic": {"enabled": false} },
  "rotation": [
    {"provider": "copilot", "model": "gpt-5.5"},
    {"provider": "copilot", "model": "claude-opus-4.7"},
    {"provider": "copilot", "model": "gpt-4.1"}
  ]
}
```

`rotation` is the ranked preference list; `count` is how many reviewers to actually run.
`preflight` probes each entry in order, drops the unreachable ones, and selects the first
`count` survivors (errors only if none survive). Auth failures (401/403) are fatal — fix
the token. `gpt-5.x`/codex are reached via Copilot's `/responses` endpoint automatically;
everything else uses `/chat/completions`.

**Providers:** Copilot is the default and recommended path (one flat-rate seat). `openai`
and `anthropic` are supported but disabled by default; enable a provider in `config.json`
and supply its token via `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` (or keys `"openai"` /
`"anthropic"` in `~/.config/cork/auth.json`). Secrets never go in `config.json`.

### Interactive review (`interactive_review`, default on)

When on, cork (full mode) and the Copilot review loop **pause after each reviewer**: the
session presents that reviewer's findings plus its own recommendation and waits for you to
choose — **fix all**, **pick specific**, **push back** (with a reason), or **proceed with no
changes**. devit inherits this. Turn it off for fully autonomous runs:
`python orchestrate.py config set interactive_review false` (or via `cork-setup`). It does
not affect cork *review-only* or the headless pipeline.

### Status line

`statusline.py` (deployed by `install.sh`) reads Claude Code's status JSON and prints the
active ticket/branch + model, e.g. `⎇ MXE-123 (feature/mxe-123-foo) · Opus`. It's
branch-driven, so a `devit` run shows its ticket automatically once it's in the worktree —
no per-run action. Outside a git branch it falls back to the directory name; it never
blocks or errors to blank. Enable it via the `settings.json` snippet in Setup step 5.

### Environment variables

Tokens live in **`~/.config/cork/auth.json`** (written by `orchestrate.py login`; chmod 600) —
`{"token": "<copilot>", "openai": "<key>", "anthropic": "<key>"}`. The env vars below are
**overrides** (resolved first), not required. None are needed if you clone to `~/dev/cork`
and run `login`.

| Env var | Default | Purpose |
|---------|---------|---------|
| `CORK_HOME` | `~/dev/cork` | Where the skills find the repo/engine |
| `CORK_CONFIG_FILE` | `~/.config/cork/config.json` | Model config (ranked `rotation` + `count`) |
| `CORK_AUTH_FILE` | `~/.config/cork/auth.json` | Cork's token store (written by `login`) |
| `CORK_COPILOT_TOKEN` | — | Copilot token used directly (highest priority) |
| `CORK_COPILOT_CLIENT_ID` | `Iv1.b507a08c87ecfe98` | GitHub OAuth client id for `login` |
| `CLAUDE_BIN` | `~/.local/bin/claude` | Path to Claude Code CLI (headless mode) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | Native-provider tokens (only if you enable those providers) |

### Error recovery (headless)

The headless pipeline checkpoints after every step (model-keyed, under
`~/.local/share/code-orchestrator/`). Re-run the same command to resume; `--reset` discards
the checkpoint. Copilot API calls retry 3× with exponential backoff on timeouts/connection
errors/5xx; 429s wait 5× longer.
