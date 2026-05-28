---
name: cork
description: Use when the user says "cork" or "run cork" on a branch — runs a session-driven multi-model review pipeline where the active Claude session implements and applies fixes, calling out to several Copilot-hosted models (GPT-4o, GPT-4.1, Claude Sonnet, Claude Opus via the orchestrate.py --review-model mode) for blind review passes between fixes.
---

# Cork — Session-Driven Multi-Model Review Pipeline

"Cork" = **C**ode **Or**chestrator **R**eview **K**ickoff.

**The active Claude session is the coding agent.** Unlike the legacy headless mode (where `orchestrate.py` spawned `claude --print` subprocesses), here *you* — the session with full codebase + conversation context — do the implementing and fixing. The orchestrator script is used only as a stateless review tool: `--review-model MODEL` returns one outside model's findings on the current branch diff.

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

### Step 0 — Gather context

```bash
git rev-parse --abbrev-ref HEAD | grep -oP 'MXE-\d+'   # ticket ID from branch
pwd                                                     # worktree path
git log origin/develop..HEAD --oneline                  # commits vs base
```

If the branch name has no ticket ID, ask for it. Confirm with the user:
`Cork: {TICKET} | worktree: {PATH} | N commits vs develop — run? (model rotation: gpt-4o, gpt-4.1, claude-sonnet-4.5, claude-opus-4.7)`

### Step 1 — Implement (only if not already done)

If the branch has no commits vs develop, implement the story now (in-session), then commit. If implementation is already committed, skip to Step 2.

### Step 2 — Self-review

Review your own diff with subagents (dispatch parallel reviewers), apply fixes, commit.

### Steps 3+ — One blind pass per model

**Division of labour (do not blur):** each Copilot model is a *read-only reviewer* — it only returns findings on the current diff. It never edits the worktree, never commits, never applies its own suggestions. **You — the active Claude Code session — are the only thing that writes code.** You read each model's findings, decide what's valid, apply the fixes yourself, run tests, and commit. The `--review-model` call is a one-shot, stateless "give me your review of this diff" — nothing more.

Default rotation, one review→fix cycle per model in order: `gpt-4o`, `gpt-4.1`, `claude-sonnet-4.5`, `claude-opus-4.7`. Each cycle is: (1) the model reviews the diff, (2) you apply/reject its findings and commit. Opus last — it's the strongest, so it reviews after the others' fixes have landed.

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
python "$CORK_HOME/orchestrate.py" {TICKET} {WORKTREE} --review-model {MODEL} --base-branch develop
```

This command **only prints the model's review to stdout** — it makes no changes. Applying the findings is your job (next paragraph).

**Model availability (Copilot integrator catalog, as of 2026-05):** Gemini is no longer served to cork's integrator identity (`gemini-3.1-pro-preview`/`gemini-2.5-pro` both fail validation), and `gpt-5.x`/codex use an endpoint cork can't reach. If a model errors with "not found in your Copilot account" or "not accessible", drop it and continue with the rest of the rotation — don't block the run. The other confirmed-available substitutes are `claude-opus-4.5` and `claude-haiku-4.5`. The catalog is gated by the `Copilot-Integration-Id` header, not the token, so it can shift; the rotation above is the current known-good set.

Read the findings from stdout. For each: apply the fix in the worktree (run tests before committing), or push back with reasoning if wrong. Commit after each model's fixes with message `fix: apply {MODEL} review [{TICKET}]`.

### Step 6 — Push + PR

Push the branch and open a PR with `gh`, summarizing what each pass caught.

## Notes

- **Base branch** is `develop` for edge-fmt. Pass `--base-branch develop` (local and origin are kept in sync; if in doubt `git fetch origin && git merge --ff-only origin/develop`).
- **Run tests** after each fix before committing — don't commit a broken build.
- **Copilot token**: `--review-model` resolves a token in priority order — `CORK_COPILOT_TOKEN` env var → cork's own `~/.config/cork/auth.json` (`CORK_AUTH_FILE`) → opencode (`~/.local/share/opencode/auth.json`). To give cork its own token, run `python "$CORK_HOME/orchestrate.py" login` (GitHub device flow, writes the auth file automatically). A 401 means the token expired — re-run `login`.
- **Worktree**: all edits go in the PR's worktree, not the main checkout.
- **Legacy headless mode** still exists: `$CORK_HOME/orchestrate.py {TICKET} {WORKTREE} --start-from 2` runs the full pipeline with `claude --print` subprocesses. Use that only for unattended/background runs.
- **Path config:** the orchestrator location comes from `$CORK_HOME` (default `~/dev/cork`). Set it in your shell profile or `~/.claude/settings.json` `env` block if your clone lives elsewhere.
