---
name: cork
description: "Use when the user says \"cork\" / \"run cork\" on a branch (full mode — implement, iteratively apply each model's fixes, open a PR) or \"cork review\" / \"review only\" / \"review this branch without fixing\" (review-only mode — run every model's review in parallel and print a consolidated findings report, applying nothing). Session-driven multi-model pipeline where the active Claude session drives models selected by `preflight` (configured API and CLI-harness models, ranked by config) for blind reviews."
---

# Cork — Session-Driven Multi-Model Review Pipeline

"Cork" = **C**ode **Or**chestrator **R**eview **K**ickoff.

**Version:** 0.18.0 — keep in sync with the repo `VERSION` file (`install.sh` checks this). Confirm the live version in Step 0 with `orchestrate.py --version`.

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
- Blind-review property is preserved: each `--review-model` call is stateless — the prompt carries the story + diff + changed files + AGENTS.md and never prior review text. API and prompt-only lanes see nothing else; tree-capable harnesses (`claude`, `opencode`) can additionally read the repo from their working directory, still read-only.

## When invoked, do this

### Step 0 — Gather context & pick mode

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
python3 "$CORK_HOME/orchestrate.py" --version            # cork version — announce it (see below)
git rev-parse --verify --quiet "{BASE}^{commit}" >/dev/null || { echo "base {BASE} does not resolve"; exit 1; }
git merge-base "{BASE}" HEAD >/dev/null      || { echo "no merge base with {BASE}"; exit 1; }
python3 "$CORK_HOME/orchestrate.py" preflight            # probe & select models for this seat
python3 "$CORK_HOME/orchestrate.py" standards status .   # show the active review-standards layers
git rev-parse --abbrev-ref HEAD                         # current branch
git rev-parse --abbrev-ref HEAD | grep -oP 'MXE-\d+'    # ticket ID, if branch follows convention
pwd                                                     # worktree path
git log {BASE}..HEAD --oneline                          # commits vs base
```

Stop here if the base is unresolvable or unrelated — fail once, locally, before probing
providers or starting any review process.

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

```bash
[ -n "$(git diff {BASE}...HEAD)" ] || { echo "empty diff vs {BASE} — nothing was implemented"; exit 1; }
```

After the implementation commit, stop here if the diff is still empty; do not fan out
reviewers for a branch that implemented nothing.

Review your own diff with subagents, apply fixes, commit. Use the **lenses** in
`$CORK_HOME/lenses/` (plus any under the repo's `code-review/lenses/`, read from the trusted
base ref with `git show {BASE}:code-review/lenses/<name>.md`, never from the checkout): one
read-only subagent per applicable lens, placeholders filled, run in parallel over
`git diff {BASE}...HEAD`. `{BASE}` is the trusted ref exactly as selected in Step 0 (a
remote-tracking ref such as `origin/develop`, or a local branch) — never prefix it again.
**When the repository under review is cork itself** (its `git rev-parse --git-common-dir`
equals `$CORK_HOME`'s), the shipped lenses are branch material too: read them with
`git -C "$CORK_HOME" show {BASE}:lenses/<name>.md`, the same exception the engine applies
to `standards/AGENTS.md`. Fill `{STANDARDS}` with the path of a file written by
`python3 "$CORK_HOME/orchestrate.py" standards show . --base-ref {BASE} > /tmp/cork-standards-{BRANCH}.md`
(outside the repo; the rubric from the trusted ref through the engine's loader, never the
checkout's standards files). Skip a lens whose concern the diff does not touch and say so;
never skip spec-and-test-coverage.

### Steps 3+ — One blind pass per model

**Division of labour (do not blur):** each reviewer model is a *read-only reviewer* — it only returns findings on the current diff. It never edits the worktree, never commits, never applies its own suggestions. **You — the active Claude Code session — are the only thing that writes code.** You read each model's findings, decide what's valid, apply the fixes yourself, run tests, and commit. The `--review-model` call is a one-shot, stateless "give me your review of this diff" — nothing more.

Rotation — use the `provider/model` lines printed by `preflight` in Step 0, in order. One review→fix cycle per model: (1) the model reviews the diff, (2) you apply/reject its findings and commit. Save the strongest model for last so it reviews after the others' fixes have landed. (For Copilot API lanes, `gpt-5.x`/`gpt-6.x`/codex use `/responses` automatically; harness lanes use their configured CLI instead.)

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
python3 "$CORK_HOME/orchestrate.py" {TICKET} {WORKTREE} --review-model {MODEL} --base-branch {BASE} --story-file {STORY_FILE}
```

`--story-file` is **required on every call**: write the ticket (or the user's stated contract)
to a file outside the repository before the rotation starts and pass that path. devit passes
the Linear story plus its Phase 3.5 `## Pre-review sweep` artifacts this way. The file lives
outside the repository, so no lane can reach it through the tree: not the API models or the
harnesses without tree access (`codex`, `pi`), and not the tree-capable ones (`claude`,
`opencode`) either, because it is not in the tree they can read. The story file is the only
way the contract reaches any reviewer. Without the flag `orchestrate.py` falls back to the
story devit persisted for the ticket, then the checkpoint summary, then a generic fallback —
a call whose output says `Story: fallback` reviewed with no spec and must be re-run.

`{MODEL}` is the full `provider/model` ref printed by `preflight` (e.g. `copilot/gpt-5.5`); `orchestrate.py` splits it (a bare id defaults to `copilot`).

This command **only prints the model's review to stdout** — it makes no changes. Applying the findings is your job (next paragraph).

**Model availability** is seat-dependent. `preflight` probes API model access; for harnesses it probes the CLI's login state live (binary present, logged in) but not model availability. If a model errors mid-run with "not found in your account" or "not accessible", drop it and continue. Copilot and OpenAI API lanes route `gpt-5.x`/`gpt-6.x`/codex through `/responses`; harness lanes use their own provider routing and login. For openai/anthropic API models, `preflight` needs the matching provider token (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` env vars, or keys `"openai"` / `"anthropic"` in `~/.config/cork/auth.json` — chmod 600; tokens never go in `config.json`).

**Harness reviewers.** `preflight` reports enabled local CLIs with status lines such as `claude: live (…)`, then includes selected harnesses in the trailing `provider/model` list (`claude/<model>`, `codex/<model>`, `opencode/<provider/model>`, or `pi/<provider/model>`). Treat selected harnesses exactly like API models — same `--review-model` ref, output format, and consolidation. Selection requires an enabled provider, installed binary, and live auth probe; a harness that fails or times out prints the usual `… — skipped]` sentinel and the rotation continues.

Pi harness refs retain the inner provider: `pi/openai-codex/gpt-6-sol`. Pi uses its own
login and `--thinking` effort, with no tools, session persistence or ambient resources.
As with other harnesses, preflight verifies the binary and its login (`pi auth check … --no-refresh`), not model availability.

Pass the story on every call: write the ticket (or the user's contract) to a file outside the
repo and add `--story-file PATH`; a call whose output says `Story: fallback` reviewed with no
spec and must be re-run. Read the **review-input manifest** each call prints: when a file the
story depends on was seen diff-only, run a focused packet (`--context-file` for exactly those
files) before trusting the verdict, and name callers, DI wiring, covering tests and restating
docs with `--context-file` from the start.

Read the findings from stdout. For each: apply the fix in the worktree (run tests before committing), or push back with reasoning if wrong. Commit after each model's fixes with message `fix: apply {MODEL} review [{TICKET}]`, plus one line naming the defect class the fixes closed and the mutation check run for each new conditional. If the same area needs fixing a second time in this run, stop and propose a design change instead of a third patch.

### Step 6 — Push + PR

Push the branch and open a PR with `gh`, summarizing what each pass caught.

## Review-only mode — parallel reviews → consolidated report

You apply **nothing** in this mode: no edits, no commits, no push, no PR, no mem0/Linear writes. The deliverable is one findings report printed in-session.

Because no fixes land between passes, **every reviewer sees the identical diff** — so the reviews are independent and you run them **in parallel** (the opposite of full mode, where fixes between passes force sequencing).

```bash
[ -n "$(git diff {BASE}...HEAD)" ] || { echo "empty diff vs {BASE} — nothing to review"; exit 1; }
```

### R1 — Fan out all reviewers at once

Dispatch concurrently, then collect when all return:

- **Self-review:** dispatch the lenses in `$CORK_HOME/lenses/` (and the repo's `code-review/lenses/`, read from the trusted `{BASE}` ref; cork's own lenses too when the repo under review is cork) as parallel read-only subagents over `git diff {BASE}...HEAD`, with `{STANDARDS}` written by `standards show . --base-ref {BASE}` exactly as in Step 2. Gather findings only — apply nothing.
- **Each model from the `preflight` rotation** (captured in Step 0), all launched together (background processes, then `wait`):

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
# The story every reviewer judges against: the PR body's acceptance section, the Linear story the
# branch names, or one the user gives you. Write it once; without it the lanes only get the generic
# "Review the branch changes for <ticket>." fallback (or a stale checkpoint if <ticket> has one).
OUTDIR=$(mktemp -d /tmp/cork-review.XXXXXX)   # per-run dir: concurrent runs never share story or report files
STORY="$OUTDIR/story.md"                      # <- fill from the PR body / ticket / user before fanning out
# PREFLIGHT_MODELS is the space-separated list of "provider/model" lines from Step 0 preflight
CONTEXT_ARGS=(); for f in "${CONTEXT_FILES[@]}"; do CONTEXT_ARGS+=(--context-file "$f"); done   # callers, DI, covering tests, docs
for M in $PREFLIGHT_MODELS; do
  safe="${M//\//-}"
  python3 "$CORK_HOME/orchestrate.py" "${TICKET:-REVIEW}" {WORKTREE} \
    --review-model "$M" --story-file "$STORY" --base-branch {BASE} "${CONTEXT_ARGS[@]}" --skip-validation \
    > "$OUTDIR/review-${safe}.txt" 2>&1 &
done
wait
```

Each `--review-model` call is stateless and read-only — the prompt carries the story + diff + changed files + AGENTS.md (never prior review text), tree-capable harnesses may also read the worktree, and the call only prints findings. Pass `--skip-validation` here to bypass both API availability requests and harness login probes already performed by preflight (one premium request saved per API model); without this flag, harness validation re-runs the CLI's login probe (no model turn is spent) but still does not check model access. With `--story-file` the positional ticket id is only a label; without a story flag it selects the checkpoint story for that id and appears in the generic fallback, so never rely on a placeholder to carry the contract. Copilot and OpenAI API lanes auto-route `gpt-5.x`/`gpt-6.x`/codex to `/responses`; CLI harnesses retain their own provider routing. If a model errors, drop it and keep the rest (see *Model availability* under full mode).

### R2 — Consolidate into one report

Merge the self-review and every model's findings into a single markdown report:

- **Group by severity:** Critical / Important / Minor / Nits.
- **Promotion candidates:** its own section — what should move to central/shared
  configuration or version management, with scope and one-time migration cost S/M/L.
- **Spec conformance:** its own section, per reviewer, never merged into the severity groups; "no spec available" if every reviewer said so.
- **Per finding:** `path:line` · description · suggested fix · **flagged by** (which reviewers — e.g. `gpt-4.1, opus, self`). Keep overlap as a confidence signal: something 4/5 reviewers caught is high-confidence; a lone flag is weaker.
- **Dedupe:** merge near-identical findings across models into one entry rather than repeating them.
- **Uncertain / needs human judgment:** a trailing section aggregating items reviewers flagged as judgment calls or out of scope.
- **Lanes and inputs — first, not last:** the rotation that **actually completed** (a lane whose
  file holds the `[… — skipped]` sentinel reviewed nothing; preflight's probe can pass hours
  before the seat drops a model) and, per lane, the review-input manifest's omissions. A
  "no findings" from a lane that saw 15 of 29 files diff-only is evidence about 15 files.
  Say what was inspected versus executed, and keep coverage gaps apart from current defects.

Print the report and stop. If the user then wants fixes applied, that's a separate full-mode (or manual) pass.

## Notes

- **Base branch** is `develop` for edge-fmt. Prefer `--base-branch origin/develop` after a `git fetch`: cork warns when a local base is behind its remote (the diff would then cover the base's own catch-up — 49 files instead of 10 on one run).
- **Budget and context:** API lanes get `review_budget_chars` of prompt (default 192k chars ≈ 48k tokens) and nothing outside the changed set; the manifest shows what fell off, `--context-file` adds what must not, and tree-capable harness lanes (`claude`, `opencode`) read the rest themselves — prefer one of those on the roster when the change's blast radius is the question.
- **Run tests** after each fix before committing — don't commit a broken build. (Full mode only — review-only never writes code.)
- **Review-only mode** is side-effect-free: parallel reviews → one consolidated report, nothing applied. Reach for it to review someone else's branch.
- **Copilot token**: `--review-model` resolves a token in priority order — `CORK_COPILOT_TOKEN` env var → cork's own `~/.config/cork/auth.json` (`CORK_AUTH_FILE`) → opencode (`~/.local/share/opencode/auth.json`). Run `python3 "$CORK_HOME/orchestrate.py" auth status` to see the source, expiry, refreshability, and probe result. Preflight warns when it is using the non-refreshable opencode fallback or a token-only credential. To give cork its own refreshable token, run `python3 "$CORK_HOME/orchestrate.py" login` (GitHub device flow, writes the auth file automatically). Re-run `login` only if the refresh token itself expires (~6 months), is revoked, or status reports a non-refreshable source.
- **Worktree**: all edits go in the PR's worktree, not the main checkout.
- **Headless mode** still exists: `$CORK_HOME/orchestrate.py {TICKET} {WORKTREE}` runs the full `3 + 2×N` pipeline with `claude --print` subprocesses (N = preflight-selected count from config). Use that only for unattended/background runs; it resumes automatically from the checkpoint on re-run.
- **Path config:** the orchestrator location comes from `$CORK_HOME` (default `~/dev/cork`). Set it in your shell profile or `~/.claude/settings.json` `env` block if your clone lives elsewhere.
