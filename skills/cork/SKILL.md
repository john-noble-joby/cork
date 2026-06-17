---
name: cork
description: Use when the user says "cork" / "run cork" on a branch (full mode — implement, iteratively apply each model's fixes, open a PR) or "cork review" / "review only" / "review this branch without fixing" (review-only mode — run every model's review in parallel and print a consolidated findings report, applying nothing). Session-driven multi-model pipeline where the active Claude session drives several Copilot-hosted models (GPT-5.5, GPT-4.1, Claude Sonnet, Claude Opus via orchestrate.py --review-model) for blind reviews.
---

# Cork — Session-Driven Multi-Model Review Pipeline

"Cork" = **C**ode **Or**chestrator **R**eview **K**ickoff.

**Version:** 0.4.0 — keep in sync with the repo `VERSION` file (`install.sh` checks this). Confirm the live version in Step 0 with `orchestrate.py --version`.

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
python "$CORK_HOME/orchestrate.py" --version            # cork version — announce it (see below)
git rev-parse --abbrev-ref HEAD                         # current branch
git rev-parse --abbrev-ref HEAD | grep -oP 'MXE-\d+'    # ticket ID, if branch follows convention
pwd                                                     # worktree path
git log {BASE}..HEAD --oneline                          # commits vs base
```

Capture the `--version` output (e.g. `cork 0.4.0 (a1b2c3d)`) and lead the confirmation line with it, so every run announces exactly which cork the agent is using.

**Mode** — from the user's phrasing: "review only" / "review this branch" / "don't fix" → **review-only mode** (gather context here, then jump to the *Review-only mode* section). Otherwise → **full mode** (the Step 1–6 flow below).

**Base branch** — defaults to `develop` (edge-fmt). Someone else's branch often targets `main` instead; confirm the base with the user before diffing.

**Ticket ID** — required for full mode (used in commit/PR messages). Optional for review-only: if the branch doesn't match `feature/MXE-…`, proceed without one (the report doesn't need it).

Confirm with the user before running (lead with the captured `{VERSION}`):
- Full mode: `Cork {VERSION}: {TICKET} | {PATH} | N commits vs {BASE} — implement/fix + PR. Run? (rotation: gpt-5.5, gpt-4.1, claude-sonnet-4.5, claude-opus-4.7)`
- Review-only: `Cork {VERSION} review-only: {BRANCH} | {PATH} | N commits vs {BASE} — parallel reviews → consolidated report, no fixes. Run?`

## Full mode — implement → fix → PR

### Step 1 — Implement (only if not already done)

If the branch has no commits vs develop, implement the story now (in-session), then commit. If implementation is already committed, skip to Step 2.

### Step 2 — Self-review

Review your own diff with subagents (dispatch parallel reviewers), apply fixes, commit.

### Steps 3+ — One blind pass per model

**Division of labour (do not blur):** each Copilot model is a *read-only reviewer* — it only returns findings on the current diff. It never edits the worktree, never commits, never applies its own suggestions. **You — the active Claude Code session — are the only thing that writes code.** You read each model's findings, decide what's valid, apply the fixes yourself, run tests, and commit. The `--review-model` call is a one-shot, stateless "give me your review of this diff" — nothing more.

Default rotation, one review→fix cycle per model in order: `gpt-5.5`, `gpt-4.1`, `claude-sonnet-4.5`, `claude-opus-4.7`. Each cycle is: (1) the model reviews the diff, (2) you apply/reject its findings and commit. Opus last — it's the strongest, so it reviews after the others' fixes have landed. (`gpt-5.5` is reached via Copilot's `/responses` endpoint; `orchestrate.py` routes it there automatically — nothing to configure.)

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
python "$CORK_HOME/orchestrate.py" {TICKET} {WORKTREE} --review-model {MODEL} --base-branch develop
```

This command **only prints the model's review to stdout** — it makes no changes. Applying the findings is your job (next paragraph).

**Model availability (Copilot catalog, as of 2026-06):** `gpt-5.x`/codex are reachable but only via the `/responses` endpoint (`/chat/completions` returns 400) — `orchestrate.py` routes them there automatically by id, so they work in the rotation with no special handling. Gemini is no longer served to this integrator (`gemini-3.1-pro-preview`/`gemini-2.5-pro` fail validation). If a model errors with "not found in your Copilot account" or "not accessible", drop it and continue with the rest of the rotation — don't block the run. Other confirmed-available substitutes: `claude-opus-4.5`, `claude-haiku-4.5`. The catalog is gated by the authenticated **seat's** Copilot plan (see `docs/personal-vs-work-seat.md`), so it can differ per account; hit `/models` for the seat you're on if in doubt.

Read the findings from stdout. For each: apply the fix in the worktree (run tests before committing), or push back with reasoning if wrong. Commit after each model's fixes with message `fix: apply {MODEL} review [{TICKET}]`.

### Step 6 — Push + PR

Push the branch and open a PR with `gh`, summarizing what each pass caught.

## Review-only mode — parallel reviews → consolidated report

You apply **nothing** in this mode: no edits, no commits, no push, no PR, no mem0/Linear writes. The deliverable is one findings report printed in-session.

Because no fixes land between passes, **every reviewer sees the identical diff** — so the reviews are independent and you run them **in parallel** (the opposite of full mode, where fixes between passes force sequencing).

### R1 — Fan out all reviewers at once

Dispatch concurrently, then collect when all return:

- **Self-review:** dispatch your own parallel review subagents over `git diff {BASE}..HEAD`. Gather findings only — apply nothing.
- **Each Copilot model**, all launched together (background processes, then `wait`):

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
for M in gpt-5.5 gpt-4.1 claude-sonnet-4.5 claude-opus-4.7; do
  python "$CORK_HOME/orchestrate.py" "${TICKET:-REVIEW}" {WORKTREE} \
    --review-model "$M" --base-branch {BASE} --skip-validation \
    > "/tmp/cork-review-$M.txt" 2>&1 &
done
wait
```

Each `--review-model` call is stateless and read-only — it only prints findings. Pass `--skip-validation` here: every reviewer otherwise fires a per-model validation call (one Copilot premium request each), so skipping it across the parallel fan-out saves ~one request per model. The positional ticket arg isn't used by review output, so any placeholder is fine when there's no ticket. `gpt-5.5` is auto-routed to Copilot's `/responses` endpoint. If a model errors with "not found in your Copilot account" or "not accessible", drop it and keep the rest (see *Model availability* under full mode).

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
- **Copilot token**: `--review-model` resolves a token in priority order — `CORK_COPILOT_TOKEN` env var → cork's own `~/.config/cork/auth.json` (`CORK_AUTH_FILE`) → opencode (`~/.local/share/opencode/auth.json`). To give cork its own token, run `python "$CORK_HOME/orchestrate.py" login` (GitHub device flow, writes the auth file automatically). A 401 means the token expired — re-run `login`.
- **Worktree**: all edits go in the PR's worktree, not the main checkout.
- **Legacy headless mode** still exists: `$CORK_HOME/orchestrate.py {TICKET} {WORKTREE} --start-from 2` runs the full pipeline with `claude --print` subprocesses. Use that only for unattended/background runs.
- **Path config:** the orchestrator location comes from `$CORK_HOME` (default `~/dev/cork`). Set it in your shell profile or `~/.claude/settings.json` `env` block if your clone lives elsewhere.
