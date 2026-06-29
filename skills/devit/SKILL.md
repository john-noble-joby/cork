---
name: devit
description: Use when the user says "devit <TICKET>", "run devit on <TICKET>", or "dev loop <TICKET>" — runs the full Linear-story dev loop: verify the story, gate on size (propose a split if too big), cut a worktree + branch from develop, implement (parallel subagents when decomposable), run cork review+fix, open a PR, run the Copilot review loop, and surface all pushbacks. Orchestrates the cork and copilot-review-loop skills; does not auto-merge.
---

# devit — Linear-story dev loop

**Version:** 0.6.2 — keep in sync with the repo `VERSION` file (`install.sh` checks this).

devit takes a Linear story and drives it from ticket to a reviewed PR. The active
Claude Code session is the agent; devit **sequences existing skills** — it does not
reimplement review or implementation machinery. It adds the front-end (Linear verify,
story-size gate, worktree/branch setup) and the conventions (branch naming, PR format,
pushback surfacing).

Resolve the orchestrator/skills location from `CORK_HOME` (default `~/dev/cork`):

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
```

## Gates — STOP and wait for the user (read this first)

devit has three human-approval **gates**. **Invoking devit (`devit <TICKET>`) only
authorizes you to *reach the first gate* — it is NOT approval to pass any gate.** At each
🛑 gate, STOP, post the prompt, and **wait for the user's reply** before continuing.
Creating the worktree is *not* "starting work"; implementing (Phase 3) is — G2 sits
between them.

| Gate | Where | You must |
|---|---|---|
| 🛑 **G0** | Phase 0 | If the story is unclear, ask — don't guess. |
| 🛑 **G1** | Phase 1 | If too big, propose a split and wait for verification before writing to Linear. |
| 🛑 **G2** | Phase 2 | Before Phase 3, post the confirm line and wait for explicit go-ahead. |

**Red flags — you are rationalizing past a gate. STOP.**
- "The user said run devit, so they approved the whole run." → No. Invocation *reaches* G2; it does not pass it.
- "The story looks clear enough, I'll just start implementing." → G2 is a stop regardless of how clear it looks.
- "I'll cut the worktree and start coding in one motion." → Worktree is Phase 2; coding is Phase 3. G2 is the line between them.
- "It's a tiny change, the gate is overkill." → Post the gate anyway; the user answers in one line.
- "I reached G2 without printing the `/rename` line." → You skipped a required Phase 2 output. Print it, then post G2.

Violating the letter of a gate is violating the spirit of devit.

## Phase 0 — Verify the story

Fetch the story with the Linear MCP tools (by `<TICKET>`). Read title, description,
acceptance criteria, type/labels, and links.

- 🛑 **G0 — Clarity gate:** if scope or acceptance criteria are unclear, ambiguous, or
  missing, **STOP and ask the user** before doing anything else. Do not guess.
- **Type:** classify feature vs. bug — Linear issue type/label first; else infer
  from content ("bug", "fix", "regression", an error report). This decides the
  branch prefix in Phase 2.

## Phase 1 — Story-size gate (before any code)

**Target ≤500 diff lines per branch.** cork's review degrades above ~1,500 lines and
fails hard above ~5,000 (model context overflow). You can't measure lines yet —
estimate from the story's scope and judge.

**Propose a split BEFORE implementing if the story requires:**
- changes across **more than one language runtime or architectural layer** (e.g.
  backend + frontend, service + its client, API + schema + UI);
- **a new domain type AND all its downstream consumers** (parser, schema, resolver,
  handler/dispatch, fixtures) — naturally two stories: (a) the type + its
  definition/parsing/schema, (b) the consumers + fixtures;
- **more than ~3 new test files.**

**🛑 G1 — If too big, split flow (HARD STOP before writing to Linear):**
1. **Propose** a split: a list of sub-stories, each a title + one-paragraph scope,
   and which is the smallest complete, mergeable slice to do first.
2. **STOP — wait for the user to verify/adjust. Do not write to Linear yet.**
3. **After confirmation, write it to Linear** via MCP: create the new sub-stories
   (and/or adjust existing ones), linked to the parent.
4. Proceed with the first slice as the active story for the rest of the run.

## Phase 2 — Setup (worktree + branch)

Base is `develop` (override if the user says otherwise). Derive `<slug>` as short
kebab-case from the story title. Prefix `feature/` (or `bugfix/` if Phase 0 found a
bug). All work happens in the worktree, not the main checkout.

```bash
git fetch origin develop
BR="feature/<TICKET>-<slug>"   # or bugfix/<TICKET>-<slug>
git worktree add ".worktrees/$BR" -b "$BR" origin/develop
cd ".worktrees/$BR"
```

**Session naming — REQUIRED output of Phase 2, do not skip.** The user tracks which
session is working on which ticket by its name, so this is not optional. Claude Code
can't rename programmatically (no tool/hook/API — devit cannot run `/rename` itself), so
after creating the branch you MUST print this line for the user verbatim:

> Run `/rename <TICKET>-<slug>` to label this session (so you can track it in `/resume`).
> Or start the session with `claude -n <TICKET>-<slug>`.

The **cork status line** (if enabled — see the repo README) *also* surfaces the ticket
automatically: it reads the branch of the current dir, so the moment you're in the
`feature/<TICKET>-…` worktree it shows `⎇ <TICKET> (<branch>)`. The `/rename` labels the
`/resume` picker; the status line is the always-visible indicator — devit needs to do
nothing extra for it (it's branch-driven), but still print the `/rename` line above.

### 🛑 G2 — Confirm before implementing (HARD STOP)

**Do NOT begin Phase 3 until the user explicitly replies.** Invoking devit does not pass
this gate; creating the worktree does not pass it. Post exactly this line and then wait:

`devit: <TICKET> | <BR> | worktree .worktrees/<BR> | base develop — start? (split needed: yes/no)`

If you catch yourself about to edit a file or dispatch an implementer before the user has
answered this line — STOP. That is the exact failure this gate exists to prevent.

## Phase 3 — Implement

- **Decomposable story** (independent tasks): use `writing-plans` to draft a short
  plan, then `subagent-driven-development` to execute it — fresh subagent per task,
  **dispatched in parallel where tasks are independent**, with a review gate between.
  > **Dependency:** `writing-plans` and `subagent-driven-development` are skills from
  > the `superpowers` plugin, not part of cork. If they aren't installed in your
  > environment, **fall back to implementing inline** (next bullet) — devit still works,
  > just without the parallel-subagent decomposition.
- **Atomic/small story (or no `superpowers` plugin):** implement inline in the session.
- **During-implementation size check:** if the diff will cross ~500 lines, STOP and
  flag the user. Propose the smallest complete, mergeable slice; file the remainder
  as a follow-on Linear story (same propose → verify → write-to-Linear gate as
  Phase 1). Don't silently blow past the target.
- Run the repo's tests before moving on.

## Phase 4 — cork review + fix

Run the usual cork **full** review→fix flow on the branch (invoke/follow the `cork`
skill): per-model blind review → apply the valid findings or **push back with
justification** → commit after each model. Record every pushback for the Phase 7
summary. cork's `preflight` picks the models available on this seat.

(If `interactive_review` is on — the default — cork and the Copilot loop will pause after each
reviewer for you to choose what to apply; devit inherits this, so expect to be prompted
between reviewers.)

## Phase 5 — Open the PR

Push the branch and open a PR with `gh`:
- **Title** starts with `<TICKET>: ` — e.g. `MXE-123: Add per-station backdoor routing`.
- **Body** MUST include an **"In plain terms"** section: what this PR **does / adds /
  removes**, in non-jargon language. Follow with a short bullet list of what each
  review pass caught, and the Linear ticket URL at the bottom.
- Base branch: `develop`. Not a draft.

## Phase 6 — Copilot review loop

Run the `copilot-review-loop` skill on the PR. For each addressed item: leave a reply
comment and **mark the thread resolved**. Where a finding is wrong or out-of-scope,
**push back with justification** and resolve. Record pushbacks for Phase 7. (The loop
already handles request → poll → fix/push-back → re-request up to its max passes.)

(If `interactive_review` is on — the default — cork and the Copilot loop will pause after each
reviewer for you to choose what to apply; devit inherits this, so expect to be prompted
between reviewers.)

## Phase 7 — Finish (surface pushbacks)

Print a final summary:
- PR URL + branch.
- What each review pass (cork models + Copilot) caught.
- **Every pushback** (cork + Copilot) with its justification, grouped together so the
  human can scan them.

**Do NOT merge.** devit ends here — the PR is through the loop; the human decides on
the merge.

## Notes

- **Human-in-the-loop:** at ANY phase, if something is unclear or risky, pause and ask
  the user. Clarification beats guessing.
- **Worktree cleanup** is the user's call (the PR branch worktree stays until they
  merge/close). Don't remove it automatically.
- **Path config:** skills/orchestrator come from `$CORK_HOME` (default `~/dev/cork`).
