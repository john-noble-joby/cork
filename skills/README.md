# Claude Code Skills

Skills that drive the orchestrator from an interactive Claude Code session.

## Install

Run the installer from the repo root — it copies the skills into
`~/.claude/skills/`, prints the version, and warns on drift:

```bash
./install.sh
```

`orchestrate.py` itself isn't installed: the skills call it via `$CORK_HOME`
(default `~/dev/cork`), so it runs from this clone directly — `git pull` updates
it. Only the skill directories (each `SKILL.md` plus any `references/`) are copies, which is
what `install.sh` keeps in sync. Check what's installed any time with
`python3 orchestrate.py --version`
(also surfaced in the cork skill's Step 0 confirmation line).

Then invoke by phrase in any session:
- **coding-standards** — auto-loaded when writing or reviewing code; no phrase needed. Not
  governed by cork's standards opt-out (that gates orchestrator-injected prompts only).
- **cork** — "cork" / "run cork on this branch"
- **copilot-review-loop** — "run the copilot review loop on this branch"
- **devit** — "devit <TICKET>"
- **cork-setup** — "set up cork"
- **cork-cross-review** — "cross review PR <n>" / "cork cross-review"

## Skills

### coding-standards
The canonical coding & review rubric: priorities, recurring defect classes, Fowler smells,
and a separate spec-conformance axis. `standards/AGENTS.md` is its condensed,
reviewer-injected copy. Codex shares it via symlink, and Pi via its `skills` path.

### cork
Session-driven multi-model review pipeline. The active Claude session implements and
applies fixes; `orchestrate.py --review-model MODEL` is called once per model
(rotation: gpt-5.5, gpt-4.1, claude-sonnet-4.5, claude-opus-4.7) to fetch blind review
findings between fix passes. Each review call is stateless — the prompt carries the story
(the acceptance contract from `--story-file`/`--story`, else the checkpoint summary, else the
built-in "Review the branch changes for <ticket>." fallback), the diff,
changed files and the repo's standards file, never prior review text; API and prompt-only lanes
(`codex`, `pi`) see nothing else, while tree-capable harnesses (`claude`, `opencode`) can also
read the repo from their working directory. Two modes: **full** ("cork" — implement
+ iterative fixes + PR, sequential passes) and **review-only** ("cork review" — all
reviewers run in parallel over the same diff, producing one consolidated findings report
with nothing applied; for reviewing someone else's branch).

### copilot-review-loop
Iterative GitHub Copilot PR review: request review → poll → fix/push-back each comment
→ reply + resolve → re-request → repeat up to N passes, stopping when Copilot has no
comments or the max is reached. Request and filter with the bot login
`copilot-pull-request-reviewer[bot]` (the display name `Copilot` silently assigns nobody).

### devit
Linear-story dev loop. `devit <TICKET>` verifies the story (asking for clarity if
needed), gates on size (proposes a split for too-big stories — you verify, then it
files the sub-stories in Linear), cuts a worktree + `feature/` or `bugfix/` branch from
`develop`, implements (parallel `subagent-driven-development` when decomposable — falls
back to inline if the `superpowers` plugin isn't installed), sweeps the long-tail review
classes before any reviewer runs (Phase 3.5: surface inventory, input-domain table, contract
probes, upstream-drift check, platform matrix, and a docs & wording sweep from
`references/docs-sweep.md` — one agent by default, up to two split by audience over a shared
claim inventory the session reconciles — each pasted into the PR body), runs the lens gate
(`$CORK_HOME/lenses/`, Phase 3.75), runs cork review+fix with `--story-file` and `--context-file`
for the blast radius, opens a PR (`<TICKET>:` title + "In plain terms" body + the sweep
artifacts), runs the `copilot-review-loop` with a ~4-pass budget, re-reviews the final diff
with cork (Phase 6.5), and surfaces all pushbacks. Orchestrates the other skills; does not
auto-merge.

### cork-setup
Guided, interactive first-time setup. Say "set up cork" and it walks through the Copilot
token (`login`), review models (`config init`/`preflight`), the pause-between-reviews
preference (`interactive_review`), the status line, and Linear/mem0 MCP checks. Run it after
`install.sh` + a restart.

### cork-cross-review
Top-level, cross-vendor PR verification. The session acts as tech lead, never as reviewer: it
fetches the PR diff + acceptance contract, stands up a detached scratch worktree at the PR head,
runs the repo's gates there, then fans the review out to independent lanes from vendors other
than the author's — agentic harness reviewers (`codex/…`, `claude/…`, `opencode/…`, `pi/…`) run read-only with the scratch tree as cwd; the tree-capable ones (`claude`, `opencode`) can verify their claims there, while `codex` and `pi` are prompt-only like the Copilot API models —
consolidates the reports into one verdict with a `Reviewer | Vendor | Model | Slice` table, routes
blocking findings back to the author, and re-reviews after each fix round (the full PR diff, with the previous blockers and the delta called out in the story). Never merges.

## Configuration

`cork` resolves the orchestrator location from the `CORK_HOME` environment variable,
defaulting to `~/dev/cork`. If your clone lives elsewhere, set it once —
in your shell profile, or in `~/.claude/settings.json`:

```json
{ "env": { "CORK_HOME": "/path/to/cork" } }
```

## Copilot token

`--review-model` calls the Copilot chat API and resolves its token in priority order:

1. **`CORK_COPILOT_TOKEN`** env var — used directly. Best for CI or a dedicated
   token; fully decoupled from opencode.
2. **cork's own auth file** — `CORK_AUTH_FILE` (default `~/.config/cork/auth.json`),
   JSON of either `{"token": "..."}` or `{"github-copilot": {"refresh": "..."}}`.
3. **opencode** `~/.local/share/opencode/auth.json` — legacy fallback.

### Easiest: `cork login` (device flow)

Run the built-in GitHub device-authorization flow — it mints a Copilot token and
writes `~/.config/cork/auth.json` (chmod 600) for you, with no token copying and no
dependency on opencode:

```bash
python3 "$CORK_HOME/orchestrate.py" login
#   Open:  https://github.com/login/device
#   Code:  FD65-A4B3
# (authorize in the browser; cork polls and writes the token automatically)
```

Re-run `login` any time the token expires. The OAuth client id is the public
Copilot one (overridable via `CORK_COPILOT_CLIENT_ID`).

### Manual alternatives

Or export `CORK_COPILOT_TOKEN`, or hand-write the auth file:

```bash
mkdir -p ~/.config/cork
echo '{"token": "<your-copilot-token>"}' > ~/.config/cork/auth.json
chmod 600 ~/.config/cork/auth.json
```

A 401 means the token expired — re-run `cork login`. This Copilot auth is entirely
separate from the `gh` CLI auth that `copilot-review-loop` uses for GitHub's hosted
PR reviewer.
