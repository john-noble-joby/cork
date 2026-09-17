---
name: cork-cross-review
description: Use when the user says "cross review PR <n>", "cork cross-review", "cross-vendor review", or "multi-agent review of this PR/branch". The active Claude Code session acts as tech lead — it never reviews the code itself. It fans the PR's diff out to INDEPENDENT reviewers from vendors other than the author's (agentic harness lanes claude/codex/opencode/pi run inside a read-only scratch worktree at the PR head, plus Copilot API models), consolidates their findings into one verdict, turns blocking issues into fix tasks, and loops on the delta until clean. The human merges.
---

# cork-cross-review — independent, cross-vendor PR verification

**Version:** 0.13.0 — keep in sync with the repo `VERSION` file (`install.sh` checks this).

**You are the tech lead, not the reviewer.** The author never signs off on their own work, and
neither do you — a *different vendor's* model does. Your job is to gather the diff and its
contract, stand up a read-only checkout the reviewers can verify against, fan the review out to
independent lanes, reconcile their reports into one verdict, and route blocking findings back to
whoever owns the branch. You do not review the code from your own reading, and you never merge.

This skill is the top-level orchestrator. `cork` (full mode) is the *author's* pipeline —
implement → blind API reviews → fix → PR. `cork review` is a flat parallel API fan-out with no
tree access. `cork-cross-review` sits above both: it adds **agentic harness reviewers** that can
read the repo, the **different-vendor rule**, a **scratch worktree** so claims are verifiable, and
a **loop** that re-reviews only the delta after fixes.

## Principles (non-negotiable)

1. **Different vendor than the author.** Claude wrote it → GPT/GLM review it, and vice versa. A
   human-authored PR makes every lane a valid reviewer. If only one vendor is available that
   differs from the author, say so and stop at the plan gate — do not fake independence.
2. **The diff is the object of review; the checkout is read-only context.** Reviewers get the
   diff text plus a detached scratch worktree at the PR head so they can verify their claims
   (callers, merge-base behaviour, pinned dependencies) instead of reporting "cannot verify".
3. **Reviewers surface, they never fix.** Every lane runs read-only (`orchestrate.py` enforces
   the flags). Only the author's session edits code; only the author's branch gets pushed.
4. **Reports are files, not scrollback.** Every lane writes to `~/.cache/cork/pr<N>/`; the
   consolidated report is the deliverable and is durable across context loss.
5. **Pin and record the model.** Every lane runs an explicit `provider/model` ref and the final
   report carries a `Reviewer | Vendor | Model | Slice` table, so the human knows exactly which
   models judged the PR.

## Configuration

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
```

If `$CORK_HOME/orchestrate.py` does not exist, tell the user to set `CORK_HOME` and stop.

## Step 0 — Roster, auth, and the plan gate

```bash
python3 "$CORK_HOME/orchestrate.py" --version
python3 "$CORK_HOME/orchestrate.py" auth status        # Copilot token source + per-harness login state
python3 "$CORK_HOME/orchestrate.py" preflight          # the lanes that will actually run on this seat
```

`preflight` prints one final `provider/model` line per selected usable lane (up to the configured
`count`). Lanes come in two kinds:

| Kind | Example refs | What it is | Auth it needs |
|---|---|---|---|
| API (Copilot-hosted) | `copilot/gpt-5.6-sol`, `copilot/claude-opus-4.7`, `copilot/gemini-3.1-pro-preview` | Stateless call — sees only diff + changed files + standards | `cork login` (one Copilot seat covers all of these) |
| Harness (agentic) | `codex/gpt-5.6-sol`, `claude/claude-opus-4.7`, `opencode/github-copilot/gpt-5.5`, `pi/glm-internal/glm-5.3-onprem` | Locally installed coding-agent CLI run read-only inside the scratch worktree — can read callers, run `git show`, verify | Each CLI's own vendor login (`codex login`, `claude auth login`, `opencode auth login`, pi `/login` or `GLM_API_KEY`) |

Group the printed lanes by **vendor family** — Claude, GPT, GLM, Gemini — regardless of kind.
Then determine the **author's vendor**: a human → all lanes valid; a `cork`/Claude Code session →
exclude the Claude family from primary review; a codex worker → exclude GPT; and so on. If the
PR body or branch name does not say, ask.

Confirm before running:
`Cork {VERSION} cross-review: PR #{N} ({BRANCH} → {BASE}) | author vendor: {V} | lanes: {LANES} | slices: {K}. Run?`

Stop here — do not proceed — if fewer than two vendor families remain after excluding the author's.

## Step 1 — Diff and contract

```bash
N=<pr-number>; OUT=~/.cache/cork/pr$N; mkdir -p "$OUT"
gh pr view "$N" --json title,body,author,baseRefName,headRefName,headRefOid,additions,deletions,changedFiles > "$OUT/pr.json"
gh pr diff "$N" > "$OUT/diff.patch"
```

The **acceptance contract** is what the reviewers judge against. In order of preference: the PR
body's acceptance section / checklist; the Linear story the branch names (`MXE-\d+`); a contract
the user pastes. Write it to `$OUT/contract.md`. If nothing exists, ask the user for one
sentence per required behaviour — do not let reviewers invent the spec.

## Step 2 — Scratch worktree at the PR head + deterministic gates

```bash
HEAD=$(jq -r .headRefOid "$OUT/pr.json")
git fetch origin "pull/$N/head"
git worktree add --detach "/tmp/cork-pr$N/wt" "$HEAD"
```

Run the repo's own tests / lint / typecheck **inside that worktree first** (use the commands the
repo's `CLAUDE.md` documents). If a gate is red, stop: report the failing gate to the author and
do not spend reviewer time on a red branch. Record the green result — it goes in the report.

Never use the author's checkout, and never let a reviewer share a worktree with anything that
writes. The scratch tree is yours and is discarded at the end.

## Step 3 — Slice large PRs

Under ~1,500 diff lines: one slice, the whole diff. Above that, split by **concern** into
disjoint pathspecs (e.g. `Server/**` vs `WebClient/**`, or migration vs handler vs tests), write a
per-slice contract excerpt, and add one **seams** slice that gets the full *code* diff (no tests,
no docs) with the instruction "review only the interactions between the parts". Above ~5,000
lines, tell the user the PR should be split before review; offer to review the smallest complete
slice.

```bash
git -C "/tmp/cork-pr$N/wt" diff "origin/$(jq -r .baseRefName "$OUT/pr.json")...HEAD" -- <pathspec…> > "$OUT/slice-<name>.patch"
```

## Step 4 — Fan out (all lanes in parallel, one attempt each)

Assign lanes: each slice gets a **primary** reviewer from a vendor ≠ author; the highest-risk
slice (migrations, wire formats, auth, parsing untrusted input) also gets a **second** reviewer
from a *different* vendor than the primary. Never let one vendor be the only voice on any slice.
Prefer a harness lane as primary when the slice's risk is "does this interact correctly with code
outside the diff" — that is exactly what the scratch tree is for. Prefer API lanes for breadth.

```bash
BASE=$(jq -r .baseRefName "$OUT/pr.json")
for LANE in $LANES; do
  safe="${LANE//\//-}"
  python3 "$CORK_HOME/orchestrate.py" "PR$N" "/tmp/cork-pr$N/wt" \
      --review-model "$LANE" --base-branch "origin/$BASE" --skip-validation \
      > "$OUT/review-$safe.txt" 2> "$OUT/review-$safe.err" &
done
wait
```

`--review-model` currently has no pathspec/slice option, so every call receives the full branch
diff. Until one exists, run the loop once per slice, pass the slice contract in the story text,
and tell the reviewer exactly which paths are in scope. Harness lanes run with the scratch
worktree as their working directory — `orchestrate.py` passes it as `cwd` and applies the
read-only flags; you do not need to add prompt text for that. What you **do** add to the story
text for every lane, verbatim:

> The DIFF is the object of review; the checkout is read-only CONTEXT. Verify your claims
> against it — callers, merge-base behaviour via `git show <merge_base>:<path>`, pinned
> dependencies — do not edit, stage, commit or create files inside it. Review ONLY against the
> contract. Report blocking / non-blocking / suggestions, each with file:line and a concrete fix.

Lane-specific rules learned the hard way:

- **`pi` / GLM** — pin `pi/glm-internal/glm-5.3-onprem` (or the current on-prem model from
  `pi --list-models glm`). `orchestrate.py` already closes stdin (pi blocks on an open pipe) and
  passes `--no-session --no-context-files`. GLM is the **tie-breaker**: when a Claude finding and a
  GPT finding disagree, or when you are tempted to overrule a reviewer from your own knowledge,
  run one more `pi` lane on *just that finding* with the evidence (hunk, dependency source, test)
  before grading it. Two vendors agreeing from the same training data is not ground truth.
- **`claude`** — runs `--safe-mode --restricted` (no CLAUDE.md, no hooks, no MCP, file tools
  confined to the scratch tree). If `ANTHROPIC_API_KEY` is set but invalid the lane hangs
  silently until timeout; `auth status` flags `env_key: true` so you know to look there.
- **`codex`** — `exec -s read-only --ephemeral`; it may take 30–45 s to fail on missing auth. Its
  sandbox can read outside the repo, so keep other lanes' report files out of its `cwd`.
- **A lane that returns the `… — skipped]` sentinel or an empty file** is a failed lane, not a
  clean review. Note it in the roster table and continue — one attempt only; do not re-run the
  same lane in the same round.

## Step 5 — Tamper check

```bash
git -C "/tmp/cork-pr$N/wt" status --porcelain
```

Any output means a reviewer wrote into the tree. Record it in the report, disregard those edits
(they never reach the deliverable), and `git -C … checkout -- . && git -C … clean -fd` before the
next round.

## Step 6 — Consolidate into one verdict

Read every `$OUT/review-*.txt`. Produce `$OUT/consolidated.md`:

1. **Verdict**: `PASS` / `PASS-WITH-NONBLOCKING` / `BLOCK`.
2. **Gates**: the exact commands and results from Step 2.
3. **Blocking** — deduplicated across lanes, each with `file:line`, why it violates the contract
   or is a correctness bug, the concrete fix, and *flagged by* (lane names). A finding raised by
   one lane and contradicted by another goes to **Disputed**, not Blocking, until the tie-break
   lane or your own spot-check against the scratch tree settles it.
4. **Non-blocking**, **Suggestions**, **Disputed / Uncertain** — same attribution.
5. **Contract checklist** — one row per contract item: met / partial / missing, with the
   evidence line a reviewer gave (or "not verified").
6. **Roster** — `Reviewer | Vendor | Model | Slice | Outcome` (outcome = reviewed / skipped /
   tampered). Every lane you launched appears here, including failed ones.
7. **Scope beyond the request** — anything the diff does that the contract did not ask for.

Your reconciliation is a *spot-check*, not a re-review: verify any finding that overrules the PR
body against the tree, settle cross-slice disputes, and apply the coding-standards checklist
(`~/.claude/skills/coding-standards/SKILL.md`) to *the findings* — stale-comment class, unproven
conditionals, fan-out result handling — to catch a reviewer that fixed the instance and missed the
class. Do not add findings from your own reading of the diff; if you believe something was
missed, run another lane on it.

## Step 7 — Route blocking findings; loop on the delta

- **You are the author's session** (the PR is yours): apply the fixes yourself, run the gates,
  commit, push (never force-push), then loop to Step 1 with `gh pr diff` again — and re-review
  **only the delta** (`git diff <old-head>..<new-head>`) with the *same* lanes, asking each to
  confirm its own blockers are closed and to look for regressions in the delta. Move the scratch
  tree to the new head (`git -C … checkout --detach <new-head>`).
- **Someone else's PR**: post `$OUT/consolidated.md` as a PR comment (`gh pr comment $N
  --body-file …`) or hand it to the author as they prefer. Never push to their branch.
- After **three** loops without reaching zero blockers, stop and escalate to the human with the
  specific findings that keep reopening.

## Step 8 — Done

Zero blockers **and** green gates → the PR is ready for the human to merge. Say so, link the
consolidated report, and clean up:

```bash
git worktree remove --force "/tmp/cork-pr$N/wt" && git worktree prune
```

You do **not** merge. Non-blocking findings are the author's follow-ups; list them, don't block
on them.

## Running the lanes from herdr (teammates without Claude Code)

The lanes are plain CLI invocations, so a teammate who drives agents from
[herdr](https://herdr.dev) can run the same review: open a pane per lane in the scratch worktree
and either run `orchestrate.py --review-model <lane>` directly, or use herdr's own agent driver
(`herdr agent start reviewer --kind codex -- -m gpt-5.6-sol`, `herdr agent prompt reviewer "<story
+ contract + rule above>" --wait`, then read the report file the agent wrote). The consolidation
step is the same. A native `herdr` transport inside `orchestrate.py` is planned; until then this
manual mapping is the supported path.

## Notes

- **Cost profile**: harness lanes take minutes, not seconds, and bill the harness's own vendor
  seat; API lanes are one Copilot premium request each. Use `--skip-validation` on fan-outs.
- **Standards** reach every lane the same way `cork` delivers them (cork's `standards/AGENTS.md`
  plus the repo's `code-review/AGENTS.md`), via system prompt where the CLI supports one and
  prepended to the story otherwise. A branch-controlled `code-review/AGENTS.md` can steer a
  reviewer running under your login — read it in the diff before you trust the lanes' findings
  about it.
- **When not to use this**: a one-line docs change, or a branch with no acceptance contract and
  an author unwilling to write one. Use `cork review` for a quick flat second opinion.
