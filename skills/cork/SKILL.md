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
- Blind-review property is preserved: each `--review-model` call is stateless — the prompt carries the story + required context + diff + changed files + AGENTS.md and never prior review text. API and prompt-only lanes see nothing else; tree-capable harnesses (`claude`, `opencode`) can additionally read the repo from their working directory, still read-only.

## When invoked, do this

### Step 0 — Gather context & pick mode

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
python3 "$CORK_HOME/orchestrate.py" --version            # cork version — announce it (see below)
git rev-parse --verify --quiet "{BASE}^{commit}" >/dev/null || { echo "base {BASE} does not resolve"; exit 1; }
git merge-base "{BASE}" HEAD >/dev/null      || { echo "no merge base with {BASE}"; exit 1; }
python3 "$CORK_HOME/orchestrate.py" preflight || { echo "preflight failed — fix auth/config before anything else"; exit 1; }   # probe & select models for this seat (for the confirmation line only; nothing is persisted before the lock)
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

**Rotation** — `preflight` probes each `provider/model` entry in the ranked config rotation and prints the ones that succeed (e.g. `copilot/gpt-5.5`, `copilot/gpt-4.1`, `copilot/claude-opus-4.7`). Those lines are what you show the user in the confirmation line; the rotation the run actually uses is probed again in Step 1 (or review-only block 1) after the worktree lock is claimed and persisted as `$RUN_DIR/models.txt`, so no shared file exists before the lock. If a model later errors mid-run, drop it and continue with the rest. The rotation comes from `~/.config/cork/config.json` (`CORK_CONFIG_FILE` overrides the path) — `config`/`config init` manage that file.

**Mode** — from the user's phrasing: "review only" / "review this branch" / "don't fix" → **review-only mode** (gather context here, then jump to the *Review-only mode* section). Otherwise → **full mode** (the Step 1–6 flow below).

**Base branch** — defaults to `develop` (edge-fmt). Someone else's branch often targets `main` instead; confirm the base with the user before diffing.

**Ticket ID** — required for full mode (used in commit/PR messages). Optional for review-only: if the branch doesn't match `feature/MXE-…`, proceed without one (the report doesn't need it).

Confirm with the user before running (lead with the captured `{VERSION}` and the preflight rotation):
- Full mode: `Cork {VERSION}: {TICKET} | {PATH} | N commits vs {BASE} — implement/fix + PR. Run? (rotation: {PREFLIGHT_MODELS})`
- Review-only: `Cork {VERSION} review-only: {BRANCH} | {PATH} | N commits vs {BASE} — parallel reviews → consolidated report, no fixes. Run?`

## Full mode — implement → fix → PR

### Step 1 — Lock the worktree, pin the base, then implement

Before any write-capable work — including the implementation itself — take this worktree's run
lock and pin the trusted base. Implementation is a tool-capable step: it (or a concurrent fetch)
could move `{BASE}` after Step 0 validated it, and a second full-mode session in the same
worktree would race the implementation commit. Both are closed here, not in Step 2.

```bash
# Shell variables do not survive between tool calls: everything later blocks need is a FILE under a
# per-run directory. The directory is unique per run (mktemp), so two runs on the same branch —
# or a review-only fan-out beside a full-mode run — can never overwrite each other's story,
# standards or context; its PATH is persisted in this worktree's git dir (never committed, never
# shared with another checkout) so every later block finds it without a variable.
cd {WORKTREE} || exit 1                          # every git/pointer/standards command below is about THIS checkout
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"       # Step 0's assignment did not survive to this block
RUN_PTR="$(git rev-parse --git-dir)/cork-run"
# One full-mode run per worktree at a time, held from before implementation until Step 6 clears it:
# full mode commits to this checkout's branch, so a second run here would race those commits.
[ -e "$RUN_PTR" ] && { echo "a cork run is already in progress in this worktree ($(cat "$RUN_PTR")) — finish it (Step 6 clears the pointer) or remove $RUN_PTR"; exit 1; }
CACHE_HOME="${XDG_CACHE_HOME:-}"; case "$CACHE_HOME" in /*) ;; *) CACHE_HOME="$HOME/.cache";; esac   # XDG: empty or relative = default, never a path inside this worktree
RUN_ROOT="$CACHE_HOME/cork/run"; { mkdir -p "$RUN_ROOT" && chmod 700 "$RUN_ROOT"; } || { echo "cannot prepare $RUN_ROOT"; exit 1; }   # mktemp does not create parents; -m would not fix an existing dir
RUN_DIR=$(mktemp -d "$RUN_ROOT/$(git rev-parse --abbrev-ref HEAD | tr '/' '-').XXXXXX") || { echo "mktemp failed under $RUN_ROOT"; exit 1; }   # fail here, not with an empty RUN_DIR later
# Atomic claim: noclobber makes the pointer write fail if another session created it between the
# check above and here; the loser removes the directory it just made and stops.
( set -o noclobber; printf '%s\n' "$RUN_DIR" > "$RUN_PTR" ) 2>/dev/null \
  || { rm -rf "$RUN_DIR"; echo "another cork run claimed this worktree first ($(cat "$RUN_PTR" 2>/dev/null)) — wait for it or remove $RUN_PTR"; exit 1; }
# Until this block completes, any failure below (base pin, preflight) must give back what it just
# claimed, or every retry reports an active run until someone removes the pointer by hand. The trap
# releases the pointer only while it still names THIS directory (never another run's claim), then
# removes the half-built directory; it is disarmed once the run state is complete.
trap '[ "$(cat "$RUN_PTR" 2>/dev/null)" = "$RUN_DIR" ] && rm -f "$RUN_PTR"; rm -rf "$RUN_DIR"' EXIT
# Pin the trusted base to a commit id before anything can move {BASE}: the standards, the lenses
# and every review diff use this id, and {BASE} survives only as the PR target.
BASE_SHA=$(git rev-parse --verify "{BASE}^{commit}") && printf '%s\n' "$BASE_SHA" > "$RUN_DIR/base-sha" || { echo "cannot pin {BASE} into $RUN_DIR"; exit 1; }
# The rotation is probed again HERE, into this run's own directory: a file shared per worktree and
# written in Step 0 (before the lock) could be truncated or replaced by a second session between
# the confirmation and this claim, persisting another run's roster. One ≤16-token probe per model.
python3 "$CORK_HOME/orchestrate.py" preflight > "$RUN_DIR/preflight.txt" || { cat "$RUN_DIR/preflight.txt"; echo "preflight failed"; exit 1; }
grep -E '^[a-z]+/' "$RUN_DIR/preflight.txt" > "$RUN_DIR/models.txt"   # the selected rotation, one provider/model per line — this is the roster the run uses
[ -s "$RUN_DIR/models.txt" ] || { echo "no models selected by preflight — fix auth/config"; exit 1; }
trap - EXIT   # run state complete: from here the lock is held on purpose, until Step 6 clears it
printf 'RUN_DIR=%s  BASE_SHA=%s  rotation:\n' "$RUN_DIR" "$BASE_SHA"; cat "$RUN_DIR/models.txt"   # if it differs from Step 0's roster, say so before continuing
```

Then, if the branch has no commits vs `$BASE_SHA`, implement the story now (in-session) and
commit. If implementation is already committed, go straight on to Step 2. The lock stays held
either way.

### Step 2 — Self-review

```bash
cd {WORKTREE} || exit 1
RUN_PTR="$(git rev-parse --git-dir)/cork-run"; [ -s "$RUN_PTR" ] || { echo "no cork run in this worktree — run Step 1 first"; exit 1; }
RUN_DIR=$(cat "$RUN_PTR"); BASE_SHA=$(cat "$RUN_DIR/base-sha")
[ -n "$(git diff "$BASE_SHA"...HEAD)" ] || { echo "empty diff vs $BASE_SHA — nothing was implemented"; exit 1; }
```

After the implementation commit, stop here if the diff is still empty; do not fan out
reviewers for a branch that implemented nothing.

**Persist the reviewer inputs** — every lens and every model pass reads the same two files,
written once, outside the repository, under the run directory Step 1 locked:

```bash
cd {WORKTREE} || exit 1
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
RUN_PTR="$(git rev-parse --git-dir)/cork-run"; [ -s "$RUN_PTR" ] || { echo "no cork run in this worktree — run Step 1 first"; exit 1; }
RUN_DIR=$(cat "$RUN_PTR"); BASE_SHA=$(cat "$RUN_DIR/base-sha")
STORY_FILE="$RUN_DIR/story.md"; printf 'story file: %s\n' "$STORY_FILE"   # Write the ticket (Linear MCP) or the user's stated contract to this literal path next
STANDARDS_FILE="$RUN_DIR/standards.md"
python3 "$CORK_HOME/orchestrate.py" standards show . --base-ref "$BASE_SHA" > "$STANDARDS_FILE" || { echo "standards show failed — no rubric, refusing to dispatch"; exit 1; }
# Blast radius the diff does not show: callers of changed symbols, DI/registration wiring, the
# covering tests, docs that restate the behaviour — plus any changed file the manifest later lists
# as diff-only (over budget or over 500 lines) that the story depends on. Repo-relative paths, one
# per line, persisted so every later block (and a re-run) rebuilds the same list.
printf '%s\n' path/to/caller.py tests/path/covering_test.py > "$RUN_DIR/context.txt"   # <- from grep/LSP over the changed symbols; `: > "$RUN_DIR/context.txt"` when there is genuinely none
```

Every later command block starts by reading the persisted run path and rebuilding the array —
copy this preamble verbatim rather than trusting a variable from an earlier call:

```bash
cd {WORKTREE} || exit 1
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
RUN_PTR="$(git rev-parse --git-dir)/cork-run"; [ -s "$RUN_PTR" ] || { echo "no cork run in this worktree — run Step 1 first"; exit 1; }
RUN_DIR=$(cat "$RUN_PTR")   # the run Step 1 locked in this worktree
STORY_FILE="$RUN_DIR/story.md"; STANDARDS_FILE="$RUN_DIR/standards.md"; BASE_SHA=$(cat "$RUN_DIR/base-sha")
[ -s "$STORY_FILE" ] || { echo "no story at $STORY_FILE — write it first"; exit 1; }
CONTEXT_ARGS=(); while IFS= read -r f; do [ -n "$f" ] && CONTEXT_ARGS+=(--context-file "$f"); done < "$RUN_DIR/context.txt"   # bash 3.2-safe; an empty file means no context
```

Do not dispatch a lens until `$STORY_FILE` has content: the spec-and-test-coverage lens has
nothing to classify without it, and an unexpanded `{STORY_FILE}` placeholder is a silent no-spec
review. The same `$STORY_FILE` goes to the model rotation in Steps 3+.

Review your own diff with subagents, apply fixes, commit. Use the **lenses** in
`$CORK_HOME/lenses/` (plus any under the repo's `code-review/lenses/`, read from the pinned
base with `git show "$BASE_SHA:code-review/lenses/<name>.md"`, never from the checkout): one
read-only subagent per applicable lens, placeholders filled (`{BASE}` = `$BASE_SHA`), run in
parallel over `git diff "$BASE_SHA"...HEAD`. `$BASE_SHA` is the commit id Step 2 pinned from
`{BASE}` (the ref exactly as selected in Step 0, a remote-tracking ref such as `origin/develop`
or a local branch — never prefixed again); the name is kept only for the PR target.
**When the repository under review is cork itself** — compare **absolute** common dirs,
`git rev-parse --path-format=absolute --git-common-dir` here and in `$CORK_HOME` (the plain
form prints a relative `.git` in both, which matches for unrelated repositories) — the shipped
lenses are branch material too: read them with `git -C "$CORK_HOME" show "$BASE_SHA:lenses/<name>.md"`,
the same exception the engine applies to `standards/AGENTS.md`. Fill `{STORY_FILE}` with
`$STORY_FILE` and `{STANDARDS}` with `$STANDARDS_FILE` from the block above (the rubric from the
trusted ref through the engine's loader, never the checkout's standards files). Skip a lens
whose concern the diff does not touch and say so;
never skip spec-and-test-coverage.

### Steps 3+ — One blind pass per model

**Division of labour (do not blur):** each reviewer model is a *read-only reviewer* — it only returns findings on the current diff. It never edits the worktree, never commits, never applies its own suggestions. **You — the active Claude Code session — are the only thing that writes code.** You read each model's findings, decide what's valid, apply the fixes yourself, run tests, and commit. The `--review-model` call is a one-shot, stateless "give me your review of this diff" — nothing more.

Rotation — use the entries of `$RUN_DIR/models.txt`, in order: the roster Step 1 probed and persisted after the lock, which is what the run reviews with even when it differs from Step 0's confirmation line. One review→fix cycle per model: (1) the model reviews the diff, (2) you apply/reject its findings and commit. Save the strongest model for last so it reviews after the others' fixes have landed. (For Copilot API lanes, `gpt-5.x`/`gpt-6.x`/codex use `/responses` automatically; harness lanes use their configured CLI instead.)

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
cd {WORKTREE} || exit 1
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
RUN_PTR="$(git rev-parse --git-dir)/cork-run"; [ -s "$RUN_PTR" ] || { echo "no cork run in this worktree — run Step 1 first"; exit 1; }
RUN_DIR=$(cat "$RUN_PTR")   # Step 1's lock: the persisted per-run path
STORY_FILE="$RUN_DIR/story.md"; [ -s "$STORY_FILE" ] || { echo "no story at $STORY_FILE — write it first"; exit 1; }
BASE_SHA=$(cat "$RUN_DIR/base-sha")                     # the pinned base, not the movable {BASE}
CONTEXT_ARGS=(); while IFS= read -r f; do [ -n "$f" ] && CONTEXT_ARGS+=(--context-file "$f"); done < "$RUN_DIR/context.txt"   # bash 3.2-safe; an empty file means no context
grep -qxF "{MODEL}" "$RUN_DIR/models.txt" || { echo "{MODEL} is not in this run's roster:"; cat "$RUN_DIR/models.txt"; exit 1; }   # the persisted rotation, not Step 0's output
python3 "$CORK_HOME/orchestrate.py" {TICKET} {WORKTREE} --review-model {MODEL} --base-branch "$BASE_SHA" --story-file "$STORY_FILE" "${CONTEXT_ARGS[@]}"
```

The preamble is not decoration: this command runs in a fresh shell, so without it `--story-file`
expands to an empty path and every context argument silently disappears. `context.txt` is how
callers, wiring, tests and docs reach a blind lane — prose cannot deliver them. Append to it after
each pass when the manifest shows a file the story depends on was seen diff-only.

`--story-file` is **required on every call**: pass the `$STORY_FILE` written in Step 2 (the
ticket or the user's stated contract, outside the repository). devit passes
the Linear story plus its Phase 3.5 `## Pre-review sweep` artifacts this way. The file lives
outside the repository, so no lane can reach it through the tree: not the API models or the
harnesses without tree access (`codex`, `pi`), and not the tree-capable ones (`claude`,
`opencode`) either, because it is not in the tree they can read. The story file is the only
way the contract reaches any reviewer. Without the flag `orchestrate.py` falls back to the
story devit persisted for the ticket, then a generic fallback (never the implementer's
checkpoint summary) — a call whose output says `Story: fallback` reviewed with no spec and must be re-run.

`{MODEL}` is one line of `$RUN_DIR/models.txt` — a full `provider/model` ref (e.g. `copilot/gpt-5.5`); `orchestrate.py` splits it (a bare id defaults to `copilot`). Walk the file top to bottom; the guard above refuses a model the run did not select.

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

Push the branch and open a PR with `gh`, summarizing what each pass caught. Then clear the run
pointer so the next run in this worktree can start: `cd {WORKTREE} && rm -f "$(git rev-parse --git-dir)/cork-run"` (a fresh shell may be in another checkout; never resolve the pointer from wherever the session happens to be)
(the run directory itself stays for the record). If a run was abandoned, the same `rm` unblocks Step 1.

## Review-only mode — parallel reviews → consolidated report

You apply **nothing** in this mode: no edits, no commits, no push, no PR, no mem0/Linear writes. The deliverable is one findings report printed in-session.

Because no fixes land between passes, **every reviewer sees the identical diff** — so the reviews are independent and you run them **in parallel** (the opposite of full mode, where fixes between passes force sequencing).

```bash
[ -n "$(git diff {BASE}...HEAD)" ] || { echo "empty diff vs {BASE} — nothing to review"; exit 1; }
```

### R1 — Fan out all reviewers at once

Dispatch concurrently, then collect when all return:

- **Inputs first (block 1):** allocate this fan-out's directory and persist everything the reviewers need — the pinned base, the rotation, the standards, the context list — then **Write** the story file at the path block 1 prints, and carry the printed `RUN_DIR` into block 2 and the lens prompts verbatim (not via the pointer: it only records the last fan-out). Nothing in a shell variable survives to the next tool call, so block 2 reads all of it back from files.
- **Self-review:** dispatch the lenses in `$CORK_HOME/lenses/` (and the repo's `code-review/lenses/`, read from the pinned base with `git show "$BASE_SHA:…"`; cork's own lenses too when the repo under review is cork, detected with absolute common dirs as in Step 2) as parallel read-only subagents over `git diff "$BASE_SHA"...HEAD`, with `{BASE}` = `$BASE_SHA`, `{STORY_FILE}` = the story file and `{STANDARDS}` = the standards file from block 1. Gather findings only — apply nothing.
- **Each model from the persisted rotation (block 2),** all launched together (background processes, then `wait`).

Block 1 — inputs:

```bash
cd {WORKTREE} || exit 1
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
# Unique per fan-out: never the full-mode run's directory, and two review-only runs on the same
# branch never share story, context or review-<model>.txt. The pointer below records the LAST
# fan-out for a human to find the report afterwards; block 2 receives this run's path explicitly.
CACHE_HOME="${XDG_CACHE_HOME:-}"; case "$CACHE_HOME" in /*) ;; *) CACHE_HOME="$HOME/.cache";; esac   # XDG: empty or relative = default
RUN_ROOT="$CACHE_HOME/cork/run"; { mkdir -p "$RUN_ROOT" && chmod 700 "$RUN_ROOT"; } || { echo "cannot prepare $RUN_ROOT"; exit 1; }
RUN_DIR=$(mktemp -d "$RUN_ROOT/$(git rev-parse --abbrev-ref HEAD | tr '/' '-')-review.XXXXXX") || { echo "mktemp failed under $RUN_ROOT"; exit 1; }   # fail here, not with an empty RUN_DIR later
REVIEW_PTR="$(git rev-parse --git-dir)/cork-review-run"; printf '%s\n' "$RUN_DIR" > "$REVIEW_PTR"
# Same rule as Step 1: a failure before this block completes removes the half-built directory and
# clears the pointer only while it still names this directory, so "find the last report" never
# leads to a run that was never dispatched. Disarmed below once the inputs are all persisted.
trap '[ "$(cat "$REVIEW_PTR" 2>/dev/null)" = "$RUN_DIR" ] && rm -f "$REVIEW_PTR"; rm -rf "$RUN_DIR"' EXIT
BASE_SHA=$(git rev-parse --verify "{BASE}^{commit}") && printf '%s\n' "$BASE_SHA" > "$RUN_DIR/base-sha" || { echo "cannot pin {BASE} into $RUN_DIR"; exit 1; }   # pinned: standards, lenses and diffs all use it
python3 "$CORK_HOME/orchestrate.py" preflight > "$RUN_DIR/preflight.txt" || { cat "$RUN_DIR/preflight.txt"; echo "preflight failed"; exit 1; }   # probed into THIS fan-out's directory, never a shared per-worktree file (see Step 1)
grep -E '^[a-z]+/' "$RUN_DIR/preflight.txt" > "$RUN_DIR/models.txt"
[ -s "$RUN_DIR/models.txt" ] || { echo "no models selected by preflight — fix auth/config"; exit 1; }
python3 "$CORK_HOME/orchestrate.py" standards show . --base-ref "$BASE_SHA" > "$RUN_DIR/standards.md" || { echo "standards show failed — no rubric, refusing to dispatch"; exit 1; }   # for the lenses
# Blast radius the diff does not show — callers of changed symbols, DI/registration wiring, covering
# tests, restating docs, and any changed file the manifest lists as diff-only. Repo-relative paths,
# one per line; populate it here (this directory is this fan-out's alone), or the lanes get no context.
printf '%s\n' path/to/caller.py tests/path/covering_test.py > "$RUN_DIR/context.txt"   # <- from grep/LSP over the changed symbols; `: > …` when there is none
trap - EXIT                                      # inputs complete: the directory and pointer now outlive this block on purpose
printf 'RUN_DIR=%s\n' "$RUN_DIR"                  # carry THIS literal path into block 2 (it is unique to this fan-out)
printf 'story file: %s\n' "$RUN_DIR/story.md"   # now Write it: the PR body's acceptance section, the Linear story the branch names, or the user's contract
```

Block 2 — fan-out (a fresh shell). Fill `{RUN_DIR}` with the literal path block 1 printed —
never re-read the `cork-review-run` pointer here: it names the *last* fan-out started in this
worktree, so two overlapping review-only runs would otherwise read each other's story and
context and launch reviews into the wrong directory. The pointer exists only so a human can
find the latest report afterwards.

```bash
cd {WORKTREE} || exit 1
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
RUN_DIR="{RUN_DIR}"; [ -d "$RUN_DIR" ] && [ -s "$RUN_DIR/base-sha" ] || { echo "no fan-out at $RUN_DIR — run block 1 and copy the RUN_DIR it printed"; exit 1; }
STORY_FILE="$RUN_DIR/story.md"; [ -s "$STORY_FILE" ] || { echo "no story at $STORY_FILE — Write it first (block 1)"; exit 1; }
BASE_SHA=$(cat "$RUN_DIR/base-sha")
TICKET="$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)"; TICKET="${TICKET:-REVIEW}"   # a label only: --story-file carries the contract
CONTEXT_ARGS=(); while IFS= read -r f; do [ -n "$f" ] && CONTEXT_ARGS+=(--context-file "$f"); done < "$RUN_DIR/context.txt"   # bash 3.2-safe; an empty file means no context
while read -r M; do
  [ -n "$M" ] || continue
  safe="${M//%/%25}"; safe="${safe//\//%2F}"   # injective: opencode/a-b/c and opencode/a/b-c must not share a file
  ( python3 "$CORK_HOME/orchestrate.py" "$TICKET" . \
      --review-model "$M" --story-file "$STORY_FILE" --base-branch "$BASE_SHA" "${CONTEXT_ARGS[@]}" --skip-validation \
      > "$RUN_DIR/review-${safe}.txt" 2>&1; echo $? > "$RUN_DIR/review-${safe}.status" ) &   # a bare `wait` discards exit statuses
done < "$RUN_DIR/models.txt"
wait
grep -L '^0$' "$RUN_DIR"/review-*.status 2>/dev/null | sed 's/\.status$//; s/^/failed lane: /'   # R2 counts only lanes with status 0
```

Each `--review-model` call is stateless and read-only — the prompt carries the story + required context + diff + changed files + AGENTS.md (never prior review text), tree-capable harnesses may also read the worktree, and the call only prints findings. Pass `--skip-validation` here to bypass both API availability requests and harness login probes already performed by preflight (one premium request saved per API model); without this flag, harness validation re-runs the CLI's login probe (no model turn is spent) but still does not check model access. With `--story-file` the positional ticket id is only a label; without a story flag the engine looks for devit's persisted story for that id and otherwise uses the generic fallback (never the checkpoint summary), so never rely on a placeholder to carry the contract. Copilot and OpenAI API lanes auto-route `gpt-5.x`/`gpt-6.x`/codex to `/responses`; CLI harnesses retain their own provider routing. If a model errors, drop it and keep the rest (see *Model availability* under full mode).

### R2 — Consolidate into one report

Merge the self-review and every model's findings into a single markdown report:

- **Group by severity:** Critical / Important / Minor / Nits.
- **Promotion candidates:** its own section — what should move to central/shared
  configuration or version management, with scope and one-time migration cost S/M/L.
- **Spec conformance:** its own section, per reviewer, never merged into the severity groups; "no spec available" if every reviewer said so.
- **Per finding:** `path:line` · description · suggested fix · **flagged by** (which reviewers — e.g. `gpt-4.1, opus, self`). Keep overlap as a confidence signal: something 4/5 reviewers caught is high-confidence; a lone flag is weaker.
- **Dedupe:** merge near-identical findings across models into one entry rather than repeating them.
- **Uncertain / needs human judgment:** a trailing section aggregating items reviewers flagged as judgment calls or out of scope.
- **Lanes and inputs — first, not last:** the rotation that **actually completed** — a lane counts
  only when its `.status` file holds `0` **and** its output is neither empty nor the
  `[… — skipped]` sentinel; a lane that printed a manifest and then exited non-zero reviewed
  nothing (preflight's probe can pass hours before the seat drops a model) — and, per lane, the
  review-input manifest's omissions. A
  "no findings" from a lane that saw 15 of 29 files diff-only is evidence about 15 files.
  Say what was inspected versus executed, and keep coverage gaps apart from current defects.

Print the report and stop. If the user then wants fixes applied, that's a separate full-mode (or manual) pass.

## Notes

- **Base branch** is `develop` for edge-fmt. Prefer `--base-branch origin/develop` after a `git fetch`: cork warns when a local base differs from its remote — behind, ahead or diverged (the diff would then cover the base's own catch-up — 49 files instead of 10 on one run).
- **Budget and context:** API lanes get `review_budget_chars` of prompt (default 192k chars ≈ 48k tokens) and nothing outside the changed set; the manifest shows what fell off, `--context-file` adds what must not, and tree-capable harness lanes (`claude`, `opencode`) read the rest themselves — prefer one of those on the roster when the change's blast radius is the question.
- **Run tests** after each fix before committing — don't commit a broken build. (Full mode only — review-only never writes code.)
- **Review-only mode** is side-effect-free: parallel reviews → one consolidated report, nothing applied. Reach for it to review someone else's branch.
- **Copilot token**: `--review-model` resolves a token in priority order — `CORK_COPILOT_TOKEN` env var → cork's own `~/.config/cork/auth.json` (`CORK_AUTH_FILE`) → opencode (`~/.local/share/opencode/auth.json`). Run `python3 "$CORK_HOME/orchestrate.py" auth status` to see the source, expiry, refreshability, and probe result. Preflight warns when it is using the non-refreshable opencode fallback or a token-only credential. To give cork its own refreshable token, run `python3 "$CORK_HOME/orchestrate.py" login` (GitHub device flow, writes the auth file automatically). Re-run `login` only if the refresh token itself expires (~6 months), is revoked, or status reports a non-refreshable source.
- **Worktree**: all edits go in the PR's worktree, not the main checkout.
- **Headless mode** still exists: `$CORK_HOME/orchestrate.py {TICKET} {WORKTREE}` runs the full `3 + 2×N` pipeline with `claude --print` subprocesses (N = preflight-selected count from config). Use that only for unattended/background runs; it resumes automatically from the checkpoint on re-run.
- **Path config:** the orchestrator location comes from `$CORK_HOME` (default `~/dev/cork`). Set it in your shell profile or `~/.claude/settings.json` `env` block if your clone lives elsewhere.
