---
name: cork
description: Use when the user says "cork" / "run cork" on a branch (full mode — implement, iteratively apply each model's fixes, open a PR) or "cork review" / "review only" / "review this branch without fixing" (review-only mode — run every model's review in parallel and print a consolidated findings report, applying nothing). Session-driven multi-model pipeline where the active Claude session drives models selected by `preflight` (copilot/openai/anthropic, ranked by config) for blind reviews.
---

# Cork — Session-Driven Multi-Model Review Pipeline

"Cork" = **C**ode **Or**chestrator **R**eview **K**ickoff.

**Version:** 0.8.1 — keep in sync with the repo `VERSION` file (`install.sh` checks this). Confirm the live version in Step 0 with `orchestrate.py --version`.

**The active Claude session is the coding agent.** Unlike the legacy headless mode (where `orchestrate.py` spawned `claude --print` subprocesses), here *you* — the session with full codebase + conversation context — do the implementing and fixing. The orchestrator script is used only as a stateless review tool: `--review-model MODEL` returns one outside model's findings on the current branch diff.

## Two modes

- **Full mode** (default — "cork", "run cork"): implement the story if needed, then for each model run a blind review → you apply its fixes → commit, in sequence; finally push and open a PR. Reviews are **sequential** because fixes land between passes.
- **Review-only mode** ("cork review", "review only", "review this branch without fixing"): run every reviewer over the *same* diff **in parallel**, then print one consolidated findings report. You apply nothing — no edits, commits, push, PR, or mem0/Linear writes. Use this to review someone else's branch.

Pick the mode in Step 0 from how the user phrased the request.

## Configuration

Resolve the orchestrator location from the `CORK_HOME` environment variable, falling back to `~/dev/cork`. Every command below uses:

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
```

If `$CORK_HOME/orchestrate.py` does not exist, tell the user to set `CORK_HOME` to their clone of the cork repo and stop.

## Why session-driven

- Fix steps run with full context (worktree state, prior decisions, the whole conversation) — a cold `claude --print` had none of that.
- The user sees the work happen live and can interject.
- Blind-review property is preserved: each `--review-model` call is stateless — the reviewer sees only the diff + changed files + AGENTS.md, never prior review text.

## When invoked, do this

### Step 0 — Gather context & pick mode

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
python3 "$CORK_HOME/orchestrate.py" --version            # cork version — announce it (see below)
python3 "$CORK_HOME/orchestrate.py" preflight            # probe & select models for this seat
python3 "$CORK_HOME/orchestrate.py" standards status .   # show the active review-standards layers
git rev-parse --abbrev-ref HEAD                         # current branch
git rev-parse --abbrev-ref HEAD | grep -oP 'MXE-\d+'    # ticket ID, if branch follows convention
pwd                                                     # worktree path
git log {BASE}..HEAD --oneline                          # commits vs base
```

If `standards status` shows *no project standards* and the default is on, mention once (non-blocking): the repo has no project standards layer — `standards init` adds one, `--opt-out` skips the default. Proceed regardless.

Capture the `--version` output (e.g. `cork 0.5.0 (a1b2c3d)`) and lead the confirmation line with it, so every run announces exactly which cork the agent is using.

**Rotation** — `preflight` probes each `provider/model` entry in the ranked config rotation and prints the ones that succeed (e.g. `copilot/gpt-5.5`, `copilot/gpt-4.1`, `copilot/claude-opus-4.7`). Use those printed lines, in order, as the reviewer rotation for this run. If a model later errors mid-run, drop it and continue with the rest. The rotation comes from `~/.config/cork/config.json` (`CORK_CONFIG_FILE` overrides the path) — `config`/`config init` manage that file.

**Mode** — from the user's phrasing: "review only" / "review this branch" / "don't fix" → **review-only mode** (gather context here, then jump to the *Review-only mode* section). Otherwise → **full mode** (the Step 1–6 flow below).

**Base branch** — defaults to `develop` (edge-fmt). Someone else's branch often targets `main` instead; confirm the base with the user before diffing.

**Ticket ID** — required for full mode (used in commit/PR messages). Optional for review-only: if the branch doesn't match `feature/MXE-…`, proceed without one (the report doesn't need it).

Confirm with the user before running (lead with the captured `{VERSION}` and the preflight rotation):
- Full mode: `Cork {VERSION}: {TICKET} | {PATH} | N commits vs {BASE} — implement/fix + PR. Run? (rotation: {PREFLIGHT_MODELS})`
- Review-only: `Cork {VERSION} review-only: {BRANCH} | {PATH} | N commits vs {BASE} — parallel reviews → consolidated report, no fixes. Run?`

## Full mode — implement → fix → PR

### Step 1 — Implement (only if not already done)

If the branch has no commits vs develop, implement the story now (in-session), then commit. If implementation is already committed, skip to Step 2.

### Step 2 — Self-review

Review your own diff with subagents (dispatch parallel reviewers), apply fixes, commit.

### Steps 3+ — One blind pass per model

**Division of labour (do not blur):** each Copilot model is a *read-only reviewer* — it only returns findings on the current diff. It never edits the worktree, never commits, never applies its own suggestions. **You — the active Claude Code session — are the only thing that writes code.** You read each model's findings, decide what's valid, apply the fixes yourself, run tests, and commit. The `--review-model` call is a one-shot, stateless "give me your review of this diff" — nothing more.

Rotation — use the `provider/model` lines printed by `preflight` in Step 0, in order. One review→fix cycle per model: (1) the model reviews the diff, (2) you apply/reject its findings and commit. Save the strongest model for last so it reviews after the others' fixes have landed. (`gpt-5.5`/`gpt-5.x` models are reached via Copilot's `/responses` endpoint; `orchestrate.py` routes them there automatically — nothing to configure.)

**Interactive review (default on).** Read the preference once before the rotation:

Run `python3 "$CORK_HOME/orchestrate.py" config get interactive_review`. If it prints `true` (the default), pause as below; if `false`, behave autonomously.

- **`true` (default):** after fetching **each** model's review, apply NOTHING yet.
  (1) **Pre-pass:** read the findings and form your recommendation — which you'd fix, which
  you'd push back on (with a reason), which are out of scope. (2) **Present** the model's
  findings *and* your recommendation, numbered. (3) **Wait** for the user to choose:
    - **Fix all** — apply every finding, run tests, commit, continue.
    - **Pick specific** (e.g. "1, 3, 4") — apply those, commit; leave/push back the rest as they say.
    - **Push back** — record won't-fix items + reasons for the final pushback summary.
    - **Proceed (no changes)** — apply nothing from this model; make **zero edits/commits**; move on.
  Do not apply anything or advance to the next model until they answer.
- **`false`:** behave autonomously (you apply the valid findings, push back with reasoning
  where wrong, and commit) — the flow described below.

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
python3 "$CORK_HOME/orchestrate.py" {TICKET} {WORKTREE} --review-model {MODEL} --base-branch develop
```

`{MODEL}` is the full `provider/model` ref printed by `preflight` (e.g. `copilot/gpt-5.5`); `orchestrate.py` splits it (a bare id defaults to `copilot`).

This command **only prints the model's review to stdout** — it makes no changes. Applying the findings is your job (next paragraph).

**Model availability** is seat-dependent — that's exactly what `preflight` checks. If a model errors mid-run with "not found in your Copilot account" or "not accessible", drop it and continue. `gpt-5.x`/codex are reachable via Copilot but only via the `/responses` endpoint — `orchestrate.py` routes them there automatically. Gemini is no longer served to this integrator. For openai/anthropic models, `preflight` needs the matching provider token (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` env vars, or keys `"openai"` / `"anthropic"` in `~/.config/cork/auth.json` — chmod 600; tokens never go in `config.json`).

Read the findings from stdout. For each: apply the fix in the worktree (run tests before committing), or push back with reasoning if wrong. Commit after each model's fixes with message `fix: apply {MODEL} review [{TICKET}]`.

### Step 6 — Push + PR

Push the branch and open a PR with `gh`, summarizing what each pass caught.

## Review-only mode — parallel reviews → consolidated report

You apply **nothing** in this mode: no edits, no commits, no push, no PR, no mem0/Linear writes. The deliverable is one findings report printed in-session.

Because no fixes land between passes, **every reviewer sees the identical diff** — so the reviews are independent and you run them **in parallel** (the opposite of full mode, where fixes between passes force sequencing).

### R1 — Fan out all reviewers at once

Dispatch concurrently, then collect when all return:

- **Self-review:** dispatch your own parallel review subagents over `git diff {BASE}..HEAD`. Gather findings only — apply nothing.
- **Each model from the `preflight` rotation** (captured in Step 0), all launched together (background processes, then `wait`):

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
# PREFLIGHT_MODELS is the space-separated list of "provider/model" lines from Step 0 preflight
for M in $PREFLIGHT_MODELS; do
  safe="${M//\//-}"
  python3 "$CORK_HOME/orchestrate.py" "${TICKET:-REVIEW}" {WORKTREE} \
    --review-model "$M" --base-branch {BASE} --skip-validation \
    > "/tmp/cork-review-${safe}.txt" 2>&1 &
done
wait
```

Each `--review-model` call is stateless and read-only — it only prints findings. Pass `--skip-validation` here: every reviewer otherwise fires a per-model validation call (one premium request each), so skipping it across the parallel fan-out saves ~one request per model. The positional ticket arg isn't used by review output, so any placeholder is fine when there's no ticket. `gpt-5.5`/`gpt-5.x` models are auto-routed to Copilot's `/responses` endpoint. If a model errors, drop it and keep the rest (see *Model availability* under full mode).

### R2 — Consolidate into one report

Merge the self-review and every model's findings into a single markdown report:

- **Group by severity:** Critical / Important / Minor / Nits.
- **Per finding:** `path:line` · description · suggested fix · **flagged by** (which reviewers — e.g. `gpt-4.1, opus, self`). Keep overlap as a confidence signal: something 4/5 reviewers caught is high-confidence; a lone flag is weaker.
- **Dedupe:** merge near-identical findings across models into one entry rather than repeating them.
- **Uncertain / needs human judgment:** a trailing section aggregating items reviewers flagged as judgment calls or out of scope.

Print the report and stop. If the user then wants fixes applied, that's a separate full-mode (or manual) pass.

## Notes

- **Base branch** is `develop` for edge-fmt. Pass `--base-branch develop` (local and origin are kept in sync; if in doubt `git fetch origin && git merge --ff-only origin/develop`).
- **Run tests** after each fix before committing — don't commit a broken build. (Full mode only — review-only never writes code.)
- **Review-only mode** is side-effect-free: parallel reviews → one consolidated report, nothing applied. Reach for it to review someone else's branch.
- **Copilot token**: `--review-model` resolves a token in priority order — `CORK_COPILOT_TOKEN` env var → cork's own `~/.config/cork/auth.json` (`CORK_AUTH_FILE`) → opencode (`~/.local/share/opencode/auth.json`). To give cork its own token, run `python3 "$CORK_HOME/orchestrate.py" login` (GitHub device flow, writes the auth file automatically). A 401 means the token expired — re-run `login`.
- **Worktree**: all edits go in the PR's worktree, not the main checkout.
- **Headless mode** still exists: `$CORK_HOME/orchestrate.py {TICKET} {WORKTREE}` runs the full `3 + 2×N` pipeline with `claude --print` subprocesses (N = preflight-selected count from config). Use that only for unattended/background runs; it resumes automatically from the checkpoint on re-run.
- **Path config:** the orchestrator location comes from `$CORK_HOME` (default `~/dev/cork`). Set it in your shell profile or `~/.claude/settings.json` `env` block if your clone lives elsewhere.
