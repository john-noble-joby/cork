# Cork

**Cork** — **C**ode **Or**chestrator **R**eview **K**ickoff.

A small toolkit of Claude Code skills that take a Linear ticket to a reviewed PR:
the active Claude session implements, then several independent models review the
diff — each seeing only the current code, never prior reviewers' notes — so every
model hunts for issues with fresh eyes.

It ships three user-facing skills (plus `cork-setup` and the auto-loaded
`coding-standards` rubric):

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
   Copies `coding-standards`, `cork`, `copilot-review-loop`, `devit`, and `cork-setup` into
   `~/.claude/skills/` and `statusline.py` into `~/.claude/`, and verifies every version stamp matches `VERSION`.
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

## Commands

| Command | Purpose |
|---------|---------|
| `python3 orchestrate.py auth status [--json]` | Show the active Copilot token source, expiry, refreshability, and one cheap probe result. |
| `python3 orchestrate.py auth print-token [--json]` | Print the resolved Copilot token for a command-backed credential consumer. |
| `python3 orchestrate.py login` | Give cork its own refreshable Copilot token through GitHub's device flow. |
| `python3 orchestrate.py preflight` | Probe the configured model rotation and select usable reviewers. |
| `python3 orchestrate.py config init\|show\|get\|set` | Initialize, inspect, or update cork configuration. |
| `python3 orchestrate.py standards status\|init` | Inspect or initialize the effective review standards. |
| `python3 orchestrate.py <TICKET> <repo> [options]` | Run the headless implementation and review pipeline. |

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

cork's fuller **coding & review rubric** lives at `skills/coding-standards/` and is
installed by `install.sh`. `standards/AGENTS.md` is the condensed copy injected into blind
reviewer models and followed by devit's implementer; the two are kept in step. Other
harnesses share the installed skill:

- **Codex:** symlink `~/.codex/skills/coding-standards` to
  `~/.claude/skills/coding-standards`.
- **Pi:** add `"skills": ["~/.claude/skills"]` to `~/.pi/agent/settings.json`.

The **effective** rubric for a repo is:

  cork's universal default  +  the repo's own standards file (first match of
  `code-review/AGENTS.md`, `code-review/agent.md`, `AGENTS.md`, `agent.md`,
  `.github/AGENTS.md`)

- **Use the default** (on by default): nothing to do — every review/implementation carries
  the baseline.
- **Add project specifics:** `python3 orchestrate.py standards init <repo>` scaffolds a
  `code-review/AGENTS.md` that **extends** the default baseline (your specifics take
  precedence); fill in your stack's conventions.
- **Opt a repo out:** `standards init <repo> --opt-out` (writes `code-review/.cork-standards-off`).
- **Opt out everywhere:** `python3 orchestrate.py config set default_standards false`.
- **Scope of the opt-out:** these toggles control what `orchestrate.py` injects into API
  reviewers and the devit implementer prompt. The installed `coding-standards` skill is a
  harness-level default and stays loaded in interactive sessions; uninstall it (delete
  `~/.claude/skills/coding-standards`) if you don't want it at all.
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
`count` survivors (errors only if none survive). Before probing, preflight names the active
Copilot credential source. Environment overrides are informational; warnings are reserved for
a non-refreshable cork file or the opencode fallback. Auth failures (401/403) are fatal and name
the rejected source plus the `login` recovery command. `gpt-5.x`/codex are reached via
Copilot's `/responses` endpoint automatically; everything else uses `/chat/completions`.

**Providers:** Copilot is the default and recommended path (one flat-rate seat). `openai`
and `anthropic` are supported but disabled by default; enable a provider in `config.json`
and supply its token via `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` (or keys `"openai"` /
`"anthropic"` in `~/.config/cork/auth.json`). Secrets never go in `config.json`.

### Harness reviewers (`claude`, `codex`, `opencode`, `pi`)

Besides API providers, cork can drive a **locally installed coding-agent CLI** as an
independent, read-only reviewer. It receives the same review inputs as an API model —
the standards, the story, the changed files and the diff (delivery per harness, see below) —
and its stdout is consumed as the findings. Same `--review-model provider/model` syntax,
same rotation/preflight/consolidation. Harnesses are disabled by default; enable one in
`config.json` and add rotation entries:

| Harness | Read-only boundary | Auth probe |
|---------|--------------------|------------|
| `claude` | Safe/restricted plan mode; only `Read,Grep,Glob` | `claude auth status --text` |
| `codex` | Read-only sandbox; ephemeral session | `codex login status` |
| `opencode` | Env-denied write/shell/network/task tools; project config disabled; global config (MCP servers, plugins) isolated | `opencode auth list` shows a credential for the model's provider |
| `pi` | No tools at all (prompt-only); no session, extensions, skills, templates, themes, context files, or project approval | `pi auth check … --no-refresh` |

```json
"providers": { "codex": {"enabled": true}, "opencode": {"enabled": true}, "pi": {"enabled": true} },
"rotation":  [ {"provider": "codex", "model": "gpt-5.6-sol"}, {"provider": "opencode", "model": "github-copilot/gpt-5.5"}, {"provider": "pi", "model": "glm-internal/glm-5.3-onprem"} ]
```

Invocations (flags verified against `claude --help` 2.1.x, `codex exec --help` 0.146.x,
`opencode run --help` 1.17.x, and `pi --help` 0.85.x):

```bash
claude -p --no-session-persistence --output-format text --model <m> --system-prompt <standards> \
       --safe-mode --restricted --tools Read,Grep,Glob --permission-mode plan          # prompt on stdin
codex exec -m <m> --ephemeral --skip-git-repo-check -C <repo> --color never - -s read-only \
       --ignore-user-config --disable shell_tool --disable unified_exec \
       --disable code_mode_host --disable apps                                          # prompt on stdin
opencode run -m <provider/model> --agent plan --format default --dir <repo> --pure -- <prompt>
pi -p --model <provider/model> --system-prompt <standards> --no-tools --no-extensions --no-skills \
   --no-prompt-templates --no-themes --no-context-files --no-approve --no-session \
   --append-system-prompt "" -- <prompt> </dev/null
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
on codex-cli 0.146.0; it still has web search, image tools and sub-agent tools). OpenCode's stock `plan` agent still allows shell and plan-file
writes, so cork injects `OPENCODE_PERMISSION` denies for `bash`; `edit` (which governs both
write and patch tools); `task`; `webfetch`; `websearch`; `external_directory` access; and the
experimental `lsp` tool (it starts language-server processes; the `OPENCODE_EXPERIMENTAL*`
switches that enable it are cleared from the lane's environment). External skill discovery
(`.claude/skills`, `.agents/skills`) and Claude Code compatibility (`CLAUDE.md`, `.claude/*`)
are disabled with `OPENCODE_DISABLE_EXTERNAL_SKILLS=1` / `OPENCODE_DISABLE_CLAUDE_CODE=1`, since
a branch could otherwise inject instructions through them.
`OPENCODE_DISABLE_PROJECT_CONFIG=1` prevents a branch's
`.opencode/` configuration or project instructions from weakening that policy; `--pure` also
disables external plugins. Neither stops OpenCode loading your **global**
`~/.config/opencode/opencode.json`, whose MCP servers have dynamic tool names the `plan` agent's
wildcard allow admits, so cork also points `XDG_CONFIG_HOME` at an empty directory it owns for
the run: no global config, while your login (`~/.local/share/opencode/auth.json`) and the models
cache (`~/.cache/opencode/`) live elsewhere and stay available (verified on 1.17.3: `mcp list`
shows none, `auth list` unchanged); any inherited `OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR` or
`OPENCODE_CONFIG_CONTENT` is cleared from the lane's environment for the same reason. One gap those flags do not close: OpenCode still imports
and runs a project's `.opencode/plugins/*.{ts,js}` — and the singular `.opencode/plugin/`,
which its loader scans too (upstream anomalyco/opencode#49836, open). That would be
branch-controlled code executing outside the permission layer, so cork refuses to run the
OpenCode lane at all when either directory exists in the tree under review or in any
directory above it (OpenCode walks every ancestor of its cwd), and reports it as a skipped
reviewer — the message says whether the hit is the branch's or your own environment's. The
auth probe applies the same refusal before it launches the CLI. Pi runs with **no tools at all**: its `read` and `find` accept
absolute paths, so a read allowlist would still let a prompt-injected review reach other
reviewers' `/tmp/cork-review-*` files. Its extensions, skills, prompt templates, themes,
context files and ambient `APPEND_SYSTEM.md` are disabled too; it keeps no session, ignores
project-local `.pi/` resources with `--no-approve`, and reads stdin from
`/dev/null` so `-p` cannot wait forever for EOF on a held-open pipe. None can modify the repo (the manual checks in the
release PRs show `git status --porcelain` identical before and after). Codex and OpenCode have
no system-prompt flag, so the standards are prepended to the prompt body under a
`=== END OF REVIEW STANDARDS ===` separator. Claude and Pi receive the standards through
`--system-prompt`; for Claude this is one
`--system-prompt` argument. Linux caps a single argument at 128 KiB, so a standards layer
that large — or an OpenCode/Pi prompt that large, since those lanes pass the prompt as an
argument — is refused by cork before the CLI runs and the lane is skipped with an explicit
size message. Codex and claude take the prompt itself on stdin, so for them only the
`--system-prompt` standards argument (claude's) is subject to the limit. **Trust boundary:** the
reviewer follows instructions from the branch under review (`code-review/AGENTS.md`, file
contents) with your local login, so a hostile branch could steer it into reading and quoting
files it can reach (`--restricted` limits claude to the repo; codex has no file access,
but does have web search).
Run harness lanes only on branches you would run the repo's own hooks or tests from — the
same trust you already extend to the implementer step. A timeout kills the CLI process
itself; tool subprocesses it spawned are not tracked.

Preflight checks live login state for every enabled harness without opening a browser or making
a model request. Logged-out lanes print the exact recovery action: `claude auth login`,
`codex login`, `opencode auth login`, or launch `pi` and run `/login`. Pi's probe always uses
`--no-refresh`; alternatively, set the selected Pi provider's API-key environment variable.
The other probes are status/list commands and do not write credentials. OpenCode's probe
checks that `opencode auth list` shows a credential — stored, or environment-backed such as
`OpenAI OPENAI_API_KEY` — for the model's *own* provider (the `github-copilot` in
`github-copilot/gpt-5`), not merely that some provider is logged in. Display names are resolved
to provider ids through OpenCode's own models.dev cache (`~/.cache/opencode/models.json`), so
`Vertex` means `google-vertex` and never `google`. If
`ANTHROPIC_API_KEY` is set, the Claude probe reports that fact without validating the key,
because an invalid value can make `claude -p` hang silently until cork's timeout. Some Pi
installations are wrapped in a provider-policy shim; cork passes the model through unchanged,
so such a shim may refuse unsupported providers. OpenCode's `github-copilot` provider uses the
same Copilot seat as cork's own Copilot API lane; it does not require a second subscription.

Per-harness config keys — the only ones read:

- `bin` (or env `CORK_CLAUDE_BIN` / `CORK_CODEX_BIN` / `CORK_OPENCODE_BIN` / `CORK_PI_BIN`, which wins): a bare command name
  resolved on `PATH`, or an absolute path (`~` is expanded). A relative path is rejected:
  preflight would resolve it from cork's cwd while the review runs from the target repo.
- `extra_args`: appended verbatim, *before* the read-only flags; treated as trusted — it is
  your own config.
- `timeout`: seconds, default 900.

The argv template and read-only flags are not configurable. `preflight` selects a harness
iff its binary is found (on `PATH`, or at the configured absolute `bin` path) and its auth probe
succeeds — no model spend. A
harness that exits non-zero, times out, or prints nothing is reported and skipped
(`[codex/<m> returned no usable content — skipped]`); there is no retry.

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
`{"github-copilot": {"refresh": "…"}}` are also accepted.) The env vars below are **overrides**
(resolved first), not required. None are needed if you clone to `~/dev/cork` and run `login`.

Run `python3 orchestrate.py auth status` to see which source actually won, whether it can
refresh, its expiry, and a one-request probe result. Add `--json` for scripting; its `probe`
field is `{"status": "ok|fail", "reason": "<verdict>"}` so callers can distinguish auth
failures from model, integrator, timeout, and connection failures. The reason is `missing`,
`expired`, or `refresh_failed` (GitHub rejected the stored refresh token) when no probe can
run, otherwise it is the probe verdict (`ok`, `auth`,
`model_not_supported`, `integrator_mismatch`, `timeout`, `connection`, or `other`). A
token-only cork file and the read-only opencode fallback are not refreshable; run
`python3 "$CORK_HOME/orchestrate.py" login` to replace either with cork's own credential.

| Env var | Default | Purpose |
|---------|---------|---------|
| `CORK_HOME` | `~/dev/cork` | Where the skills find the repo/engine |
| `CORK_CONFIG_FILE` | `~/.config/cork/config.json` | Model config (ranked `rotation` + `count`) |
| `CORK_AUTH_FILE` | `~/.config/cork/auth.json` | Cork's token store (written by `login`) |
| `CORK_COPILOT_TOKEN` | — | Copilot token used directly (highest priority) |
| `CORK_COPILOT_CLIENT_ID` | `Iv1.b507a08c87ecfe98` | GitHub OAuth client id for `login` |
| `CLAUDE_BIN` | `~/.local/bin/claude` | Path to Claude Code CLI (headless mode) |
| `CORK_CLAUDE_BIN` / `CORK_CODEX_BIN` / `CORK_OPENCODE_BIN` / `CORK_PI_BIN` | CLI name (PATH) | Harness reviewer binaries (see *Harness reviewers*) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | Native-provider tokens (only if you enable those providers) |

### Using your Copilot seat for the Codex lane

Codex CLI (verified on 0.146 and 0.157) can use cork's resolved Copilot credential through a
custom Responses provider whose auth is a command: Codex runs `auth print-token` and uses the
stdout as the bearer token, re-running it every `refresh_interval_ms`. cork's resolver applies
the same `CORK_COPILOT_TOKEN` → cork auth file → opencode fallback precedence as reviews and
refreshes an expired, refreshable cork token before printing it. Verify the source first with
`python3 "${CORK_HOME:-$HOME/dev/cork}/orchestrate.py" auth status`.

**For cork's Codex harness lane, the provider must be defined in `extra_args`.** The lane
always passes `--ignore-user-config` (see *Harness reviewers*), which tells Codex not to load
`~/.codex/config.toml` — so a provider defined only there is invisible to cork and the lane
fails with `Model provider \`copilot\` not found`. Codex's `-c key=value` overrides are read
regardless, and the value is parsed as TOML, so the whole definition travels as trusted config
(this needs the harness lanes, cork 0.11+; on 0.10.x it fails validation):

```json
{
  "providers": {
    "codex": {
      "enabled": true,
      "extra_args": [
        "-c", "model_provider=copilot",
        "-c", "model_providers.copilot.name=\"GitHub Copilot\"",
        "-c", "model_providers.copilot.base_url=\"https://api.githubcopilot.com\"",
        "-c", "model_providers.copilot.wire_api=\"responses\"",
        "-c", "model_providers.copilot.http_headers={ \"x-initiator\" = \"user\", \"Openai-Intent\" = \"conversation-edits\", \"User-Agent\" = \"opencode/0.1.0\" }",
        "-c", "model_providers.copilot.auth.command=\"sh\"",
        "-c", "model_providers.copilot.auth.args=[\"-c\", 'python3 \"${CORK_HOME:-$HOME/dev/cork}/orchestrate.py\" auth print-token']",
        "-c", "model_providers.copilot.auth.refresh_interval_ms=300000"
      ]
    }
  },
  "rotation": [{"provider": "codex", "model": "gpt-5.5"}]
}
```

For **interactive Codex use outside cork**, the same definition can live in
`~/.codex/config.toml` instead (cork's lane does not read it). Registering the provider is
not enough — select it too, or Codex keeps using its default provider:

```toml
model_provider = "copilot"

[model_providers.copilot]
name = "GitHub Copilot"
base_url = "https://api.githubcopilot.com"
wire_api = "responses"
http_headers = { "x-initiator" = "user", "Openai-Intent" = "conversation-edits", "User-Agent" = "opencode/0.1.0" }

[model_providers.copilot.auth]
command = "sh"
args = ["-c", 'python3 "${CORK_HOME:-$HOME/dev/cork}/orchestrate.py" auth print-token']
refresh_interval_ms = 300000
```

This passthrough is unsupported by GitHub and may stop working. Codex receives the token from
the helper's stdout through a subprocess pipe, so do not add any other stdout output to
`auth print-token` and remember that a process with access to that pipe can read the token.
Claude Code cannot use this provider: its model API integration uses Anthropic's wire format,
not the OpenAI Responses format exposed here.

### Error recovery (headless)

The headless pipeline checkpoints after every step (model-keyed, under
`~/.local/share/code-orchestrator/`). Re-run the same command to resume; `--reset` discards
the checkpoint. Copilot API calls retry 3× with exponential backoff on timeouts/connection
errors/5xx; 429s wait 5× longer.

### Versioning

cork follows [Semantic Versioning](https://semver.org/); the `VERSION` file is the source of
truth and every skill stamp + `orchestrate.py --version` tracks it (`install.sh` warns on
drift). See [`CHANGELOG.md`](CHANGELOG.md) for the release history and the bump policy.
