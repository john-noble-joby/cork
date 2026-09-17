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

Python 3.10+ stdlib only — no `pip install`. **`install.sh` does not fetch tokens**
(it deploys the skills + status line, checks versions, and — only if you accept its
prompt — writes `CORK_HOME` into `~/.claude/settings.json`), so the token and MCP
steps below are manual.

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
   run it straight from `$CORK_HOME`, so `git pull` updates the engine.) `install.sh` can also
   write `CORK_HOME` into `~/.claude/settings.json` if you clone to a non-default path.

3. **Get a Copilot token** (required — this is what unlocks the review models):
   ```bash
   python3 ~/dev/cork/orchestrate.py login
   ```
   GitHub device flow → writes `~/.config/cork/auth.json` (chmod 600). cork **refreshes this
   token automatically** (it persists the refresh token, good ~6 months), so you rarely need
   to re-run `login` — only if the refresh token expires or is revoked.

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
   python3 ~/dev/cork/orchestrate.py config init   # write a starter config you can edit
   python3 ~/dev/cork/orchestrate.py preflight      # show which models your seat can use
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
Commits after each fix give a clear audit trail. Reviewers and devit's implementer use
the **effective standards** — cork's universal default plus the repo's own
`code-review/AGENTS.md` (or root `AGENTS.md` / `.github/AGENTS.md`) if present — unless
opted out (see below).

### Coding & review standards (layering)

cork ships a generalized **coding & review rubric** (`standards/AGENTS.md`) used by both
devit's implementer and the blind review models. The **effective** rubric for a repo is:

  cork's universal default  +  that repo's own `code-review/AGENTS.md` (if present)

- **Use the default** (on by default): nothing to do — every review/implementation carries
  the baseline.
- **Add project specifics:** `python3 orchestrate.py standards init <repo>` scaffolds a
  `code-review/AGENTS.md` that **extends** the default baseline (your specifics take
  precedence); fill in your stack's conventions.
- **Opt a repo out:** `standards init <repo> --opt-out` (writes `code-review/.cork-standards-off`).
- **Opt out everywhere:** `python3 orchestrate.py config set default_standards false`.
- **See what applies:** `python3 orchestrate.py standards status <repo>`.

Two ways to run it:
- **Session-driven (the skills):** the active Claude session implements/fixes and calls
  `orchestrate.py --review-model <provider/model>` once per model for a stateless blind
  review. This is what `cork`/`devit` use.
- **Headless (legacy/unattended):** `python3 orchestrate.py <TICKET> <repo-path>
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

### Harness reviewers (`claude`, `codex`)

Besides API providers, cork can drive a **locally installed coding-agent CLI** as an
independent, read-only reviewer. It receives the same review inputs as an API model —
the standards, the story, the changed files and the diff (delivery per harness, see below) —
and its stdout is consumed as the findings. Same `--review-model provider/model` syntax,
same rotation/preflight/consolidation. Harnesses are disabled by default; enable one in
`config.json` and add rotation entries:

```json
"providers": { "codex": {"enabled": true}, "claude": {"enabled": true, "extra_args": ["--max-budget-usd", "3"]} },
"rotation":  [ {"provider": "codex", "model": "gpt-5.6-sol"}, {"provider": "claude", "model": "claude-opus-4.7"} ]
```

Invocations (flags verified against `claude --help` 2.1.x and `codex exec --help` 0.146.x):

```bash
claude -p --no-session-persistence --output-format text --model <m> --system-prompt <standards> \
       --safe-mode --restricted --tools Read,Grep,Glob --permission-mode plan          # prompt on stdin
codex exec -m <m> --ephemeral --skip-git-repo-check -C <repo> --color never - -s read-only \
       --ignore-user-config --disable shell_tool --disable unified_exec \
       --disable code_mode_host --disable apps                                          # prompt on stdin
```

**Read-only guarantees and their limits.** `claude` runs with `--safe-mode` (no CLAUDE.md,
hooks, MCP or plugins — auth is kept, unlike `--bare`, which refuses OAuth logins) and
`--restricted` (no code-running tools; file tools confined to the repo), with only
`Read,Grep,Glob` under `--permission-mode plan`: it can read the repo but cannot run
commands or reach outside it. `codex` runs under its `read-only` sandbox with no session
persisted — but that sandbox only blocks writes; codex's shell would still run read-only
commands and read anywhere the user can. So cork also passes `--disable shell_tool
--disable unified_exec --disable code_mode_host --disable apps` and `--ignore-user-config`
(features listed by `codex features list`, flags by `codex exec --help`): the codex
reviewer then has **no command execution, no file access and none of your MCP servers**
and reviews from the prompt alone, like an API model (verified with a tool-inventory probe
on codex-cli 0.146.0; it still has web search, image tools and sub-agent tools). Neither
lane can modify the repo (the manual check in the 0.11.0 PR shows `git status --porcelain`
identical before and after). Codex has no system-prompt flag, so the standards are prepended to the prompt
body under a `=== END OF REVIEW STANDARDS ===` separator. Claude's standards travel as one
`--system-prompt` argument, so a standards layer over ~128 KiB hits the Linux per-argument
limit and the lane is skipped with `Argument list too long`. **Trust boundary:** the
reviewer follows instructions from the branch under review (`code-review/AGENTS.md`, file
contents) with your local login, so a hostile branch could steer it into reading and quoting
files it can reach (`--restricted` limits claude to the repo; codex has no file access,
but does have web search).
Run harness lanes only on branches you would run the repo's own hooks or tests from — the
same trust you already extend to the implementer step. A timeout kills the CLI process
itself; tool subprocesses it spawned are not tracked.

Per-harness config keys — the only ones read: `bin` (or env `CORK_CLAUDE_BIN` /
`CORK_CODEX_BIN`, which wins), `extra_args` (appended verbatim, *before* the read-only
flags; treated as trusted — it is your own config), `timeout` (seconds, default 900). The
argv template and read-only flags are not configurable. `preflight` selects a
harness iff its binary is on PATH — no spend. A harness that exits non-zero, times out,
or prints nothing is reported and skipped (`[codex/<m> returned no usable content — skipped]`);
there is no retry.

### Interactive review (`interactive_review`, default on)

When on, cork (full mode) and the Copilot review loop **pause after each reviewer**: the
session presents that reviewer's findings plus its own recommendation and waits for you to
choose — **fix all**, **pick specific**, **push back** (with a reason), or **proceed with no
changes**. devit inherits this. Turn it off for fully autonomous runs:
`python3 orchestrate.py config set interactive_review false` (or via `cork-setup`). It does
not affect cork *review-only* or the headless pipeline.

### Status line

`statusline.py` (deployed by `install.sh`) reads Claude Code's status JSON and prints the
active ticket/branch + model, e.g. `⎇ MXE-123 (feature/mxe-123-foo) · Opus`. It's
branch-driven, so a `devit` run shows its ticket automatically once it's in the worktree —
no per-run action. Outside a git branch it falls back to the directory name; it never
blocks or errors to blank. Enable it via the `settings.json` snippet in Setup step 5.

### Environment variables

Tokens live in **`~/.config/cork/auth.json`** (written by `orchestrate.py login`; chmod 600).
`login` writes the Copilot token plus its refresh metadata —
`{"token": "<copilot>", "refresh_token": "<refresh>", "expires_at": <unix-ts>}` — and cork
refreshes it in place; native-provider keys live alongside and are preserved across refreshes:
`{"token": …, "refresh_token": …, "expires_at": …, "openai": "<key>", "anthropic": "<key>"}`.
(For backward compat a bare `{"token": "<copilot>"}` and the legacy opencode shape
`{"github-copilot": {"access": "…"}}` are also accepted.) The env vars below are **overrides**
(resolved first), not required. None are needed if you clone to `~/dev/cork` and run `login`.

| Env var | Default | Purpose |
|---------|---------|---------|
| `CORK_HOME` | `~/dev/cork` | Where the skills find the repo/engine |
| `CORK_CONFIG_FILE` | `~/.config/cork/config.json` | Model config (ranked `rotation` + `count`) |
| `CORK_AUTH_FILE` | `~/.config/cork/auth.json` | Cork's token store (written by `login`) |
| `CORK_COPILOT_TOKEN` | — | Copilot token used directly (highest priority) |
| `CORK_COPILOT_CLIENT_ID` | `Iv1.b507a08c87ecfe98` | GitHub OAuth client id for `login` |
| `CLAUDE_BIN` | `~/.local/bin/claude` | Path to Claude Code CLI (headless mode) |
| `CORK_CLAUDE_BIN` / `CORK_CODEX_BIN` | `claude` / `codex` (PATH) | Harness reviewer binaries (see *Harness reviewers*) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | Native-provider tokens (only if you enable those providers) |

### Error recovery (headless)

The headless pipeline checkpoints after every step (model-keyed, under
`~/.local/share/code-orchestrator/`). Re-run the same command to resume; `--reset` discards
the checkpoint. Copilot API calls retry 3× with exponential backoff on timeouts/connection
errors/5xx; 429s wait 5× longer.

### Versioning

cork follows [Semantic Versioning](https://semver.org/); the `VERSION` file is the source of
truth and every skill stamp + `orchestrate.py --version` tracks it (`install.sh` warns on
drift). See [`CHANGELOG.md`](CHANGELOG.md) for the release history and the bump policy.
