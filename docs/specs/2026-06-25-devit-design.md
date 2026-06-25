# devit — Linear-story dev-loop skill

**Status:** design / approved, pending spec review
**Date:** 2026-06-25
**Type:** new Claude Code skill in the cork repo (`skills/devit/SKILL.md`), session-driven, deployed via `install.sh`.

## Goal

`devit <STORY>` runs the full developer loop for a Linear story — from "fetch the ticket" to "PR reviewed and ready to land" — by **orchestrating cork's existing skills** (`cork`, `copilot-review-loop`) and the superpowers workflow skills (`writing-plans`, `subagent-driven-development`). It does not reimplement review or implementation machinery; it sequences them and adds the front-end (Linear verification, story-size gate, worktree/branch setup) and the conventions (branch naming, PR format, pushback surfacing).

The active Claude Code session is the agent (same model as cork's session-driven design). devit is a sequence of instructions that session follows.

## Phases

### 0. Verify the story
- Fetch the story via Linear MCP. Read title, description, acceptance criteria, type/labels, links.
- **Clarity gate:** if scope or acceptance criteria are unclear/ambiguous/missing, **ask the user for clarification before doing anything else.** Do not guess.
- **Type:** classify feature vs. bug (Linear issue type/label first; fall back to content — "bug", "fix", "regression", error reports).

### 1. Story-size gate (before any code)
Apply the constraints in "Story-size constraints" below. If the story is **too big**:
1. **Propose a split** — a list of sub-stories, each with a title and a one-paragraph scope, and which slice to do first (the smallest complete, mergeable one).
2. **User verifies / adjusts** the proposed split. **This is a gate — wait for confirmation.**
3. **devit then writes the split to Linear** — create the new sub-stories (and/or adjust existing ones) via Linear MCP, linked to the parent story. Only after the user has verified.
4. Proceed with the first slice as the active story for the rest of the run.

### 2. Setup
```bash
git fetch origin develop          # fresh base
git worktree add .worktrees/<branch> -b <branch> origin/develop
```
- **Base branch:** `develop` (override with a flag/arg for repos that differ). devit targets the work repos.
- **Worktree:** `<repo>/.worktrees/<branch>` (git-ignored). All edits happen in the worktree, not the main checkout.
- **Branch name:** `feature/<TICKET>-<slug>` for features, `bugfix/<TICKET>-<slug>` for bugs. `<slug>` is a short kebab-case derivation of the story title.

### 3. Implement
- **Decomposable story** → write a quick plan (`writing-plans`) and execute via `subagent-driven-development` (fresh subagent per task, **parallel where tasks are independent**, review gate between). This is the parallelization the user asked for.
- **Atomic/small story** → implement inline in the session.
- Either way, honor the during-implementation size check (see constraints): if the diff crosses ~500 lines mid-flight, stop and flag.

### 4. cork review + fix
Run the usual cork **full** review→fix flow (per-model blind review → apply or push back → commit) — invoke/follow the `cork` skill. Reviewers may **push back on findings with justification**; record those pushbacks for the final summary.

### 5. PR
Push the branch and open a PR with `gh`:
- **Title** starts with `<TICKET>: ` — e.g. `MXE-123: Add per-station backdoor routing`.
- **Body** always includes an **"In plain terms"** section describing what the PR **does / adds / removes** in non-jargon language, plus the Linear ticket link.

### 6. copilot-review-loop
Run the `copilot-review-loop` skill on the PR. For each addressed item: **leave a comment and mark the thread resolved.** Push back (with justification + resolve) where a finding is wrong/out-of-scope. Record pushbacks.

### 7. Finish — surface pushbacks
Print a final summary that **surfaces every pushback** (from cork and Copilot) with its justification, plus the PR URL and what each review pass caught. The human reviews the pushbacks.

**Cross-cutting:** at any phase, any agent may **pause and ask the user** for clarification or input.

## Story-size constraints (generalized)

> **Target: ≤500 diff lines per story branch.** The cork multi-model pipeline degrades above ~1,500 lines and fails hard above ~5,000 (model context overflow). Split stories that span **multiple language runtimes or multiple architectural layers**.

**Split signals — propose a split *before* implementing if the story requires:**
- changes across **more than one language runtime or architectural layer** in one branch (e.g. backend + frontend, service + its client, API + schema + UI);
- **a new domain type AND all its downstream consumers** (parser, schema, resolver, handler/dispatch, test fixtures) — naturally two stories: (a) the type + its definition/parsing/schema, (b) the consumers + fixtures;
- **more than ~3 new test files**.

**Three checkpoints:**
1. **Before starting** — if scope touches multiple runtimes/layers (or trips a split signal), propose the split upfront, before writing code.
2. **During implementation** — if the branch will exceed ~500 lines, stop and flag to the user; propose the smallest complete, mergeable slice and file the remainder as a follow-on Linear story (same propose → verify → write-to-Linear gate).
3. **Backstop** — cork itself is the hard limit (≥1,500 lines degraded review, ≥5,000 blocked).

These numbers live in the devit skill as guidance the session applies with judgment — not a hard pre-commit line count (you estimate from scope before code exists; cork's diff gate enforces the hard limit after).

## What devit orchestrates (does not reimplement)
- **`cork`** skill — the review+fix passes (Phase 4).
- **`copilot-review-loop`** skill — the Copilot PR cycle (Phase 6).
- **`writing-plans` + `subagent-driven-development`** — decompose + parallel implementation (Phase 3).
- **Linear MCP** — fetch story, create/adjust split sub-stories.
- **`gh`** — push + PR.

## Skill packaging
- Lives at `skills/devit/SKILL.md`; carries a `**Version:**` stamp synced to the repo `VERSION` (checked by `install.sh`).
- `install.sh` copies it to `~/.claude/skills/devit/` alongside the other skills.
- Trigger phrases: "devit <TICKET>", "run devit on <TICKET>", "dev loop <TICKET>".

## Non-goals
- Not a Python code change to `orchestrate.py` — devit is a skill (markdown) that drives existing tooling.
- Does not replace `cork` or `copilot-review-loop` — it sequences them.
- Auto-merge is out of scope: devit ends at "PR reviewed, pushbacks surfaced." The human merges.

## Open dependency
- **Held until the multi-provider rework (PR #1) merges**, because devit's Phase 4 invokes cork and Phase 3's parallel implementation leans on the same orchestration the rework hardens. Spec + plan are written now; execution waits.
