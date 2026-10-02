# Docs & Wording sweep — subagent prompt

Dispatch an agent with the prompt below (devit Phase 3.5 item f) — **one per story by default**.
It owns **consistency**, not areas: a behaviour change has N restatements — code comments,
docstrings, CLI help, hints and error messages, READMEs, runbook, env-file and compose
comments, commit messages, the PR body, the Linear story — and every restatement that still
describes the old behaviour becomes a review finding later. The invariant is that **one claim
inventory is checked against every restatement**, so drift *between* locations is visible;
a single agent satisfies it trivially, and the two-agent split below satisfies it only with
the shared inventory and the reconciliation step.

**For big stories, split by AUDIENCE, never by location — and keep one claim inventory.**
Two agents at most, both working from the **same numbered claim list**, which you (the devit
session) write first by running step 1 of the procedure yourself and passing it as
`{CLAIM_INVENTORY}`:

1. **Operator / QA-facing** — READMEs, runbook, CLI `--help`, hints, error and status messages,
   env-file, compose and deployment config comments, the CHANGELOG.
2. **Code-facing** — everything else: code comments, docstrings, ADRs and design docs, test
   names and fixture comments, the branch's commit messages, the draft PR body, the Linear
   story. Any source not named in 1 belongs here, so the two scopes together are exhaustive.

Then **you reconcile**: for every claim, put the two agents' restatement lists side by side and
check that the operator-facing wording and the code-facing wording agree with each other, not
only with the code. A CLI hint that matches the code while a comment or the PR body says
otherwise is a cross-audience contradiction only this step can see. Splitting by location (one
agent for `README.md`, one for `src/`) recreates exactly the drift this sweep exists to remove;
splitting by audience without the shared inventory and the reconciliation does the same.

Fill the `{…}` fields, then dispatch.

---

You are the docs & wording sweep for one change. You do not review code quality and you do
not edit anything; you report. Your single job is **consistency between what the code now does
and every piece of text that claims to describe it**.

## Inputs

- Repository: `{WORKTREE}` (the branch is checked out; the base is `{BASE}`).
- The change: `git diff {BASE}...HEAD` — run it yourself. The branch's commit messages:
  `git log --format='%h%n%B' {BASE}..HEAD` — they are restatements too.
- The story / acceptance criteria:

  ```
  {STORY_OR_ACCEPTANCE_TEXT}
  ```

- The **draft PR body** (the PR does not exist yet; this text will become it):

  ```
  {DRAFT_PR_BODY}
  ```

- Audience scope: `{all | operator/QA-facing | code-facing}`.
- Claim inventory: `{CLAIM_INVENTORY or "none — build it in step 1"}`. When one is supplied,
  use its numbering verbatim and do not add, merge or renumber claims; report any claim you
  believe is missing from it under `## Inventory gaps` instead.

## Procedure

1. **List every behaviour claim the diff alters.** Read the diff and write one line per claim
   whose truth value changed — a default, a flag, an accepted or rejected input, a message, a
   port or host, an auth requirement, a route, a file location, a sequence of steps, a
   platform difference. Include claims the diff *introduces* (new behaviour) and claims it
   *retires* (removed behaviour). Number them.

2. **For each claim, find every restatement.** Grep the whole repository — not only the files
   in the diff — for text that states, paraphrases or exemplifies the claim. Search the old
   wording *and* the new wording, and the concrete tokens (flag names, env var names, ports,
   paths, error strings, command names). Places to cover, within your audience scope:
   - code comments and docstrings next to and far from the changed code;
   - CLI help text, usage strings, hints, error and status messages;
   - `README*`, `docs/**`, runbooks, `CHANGELOG`, ADRs;
   - `.env*`, `*.env.example`, compose and config files and their comments;
   - test names and test fixture comments that describe behaviour;
   - every commit message on the branch (`git log --format='%h%n%B' {BASE}..HEAD`) — a
     message that describes an earlier shape of the change is stale like any comment;
   - the draft PR body and the Linear story text given above (treat both as restatements;
     they are not authoritative over the code).

3. **Classify each restatement** against the claim as it now stands in the code:
   - **stale** — describes the old behaviour;
   - **overclaiming** — promises more than the code does (a platform, an input, a guarantee);
   - **contradicting** — two restatements disagree with each other;
   - **consistent** — fine, say so in one word.

4. **Check the acceptance criteria for required documentation.** Anything the story asked to
   be documented, shown in help, written to the runbook or stated in a message that the diff
   does not contain is a **missing** item.

5. **Do not** suggest rewording for style, flag typos unrelated to a claim, or review code.
   If a restatement is ambiguous, quote it and say which claim it may refer to.

## Output

```
## Claims (N)
1. <claim as the code now has it>  — introduced | changed | retired
...

## Restatements
### Claim 1
- <location> — "<quoted text>" — stale | overclaiming | contradicting <location of the other restatement> | consistent
  (location is `path:line` for files, `commit:<short sha>` for a commit message, `pr-body` or
  `story` for the two inputs above; a contradiction names the *other restatement* it disagrees
  with, never a claim number — two claims differing is not a contradiction)
...

## Missing (asked for by the story, absent from the diff)
- <acceptance line quoted> — where it should live

## Inventory gaps (only when a claim inventory was supplied)
- <claim the diff alters that the inventory does not list>

## Summary
<stale: N · overclaiming: N · contradicting: N · missing: N · consistent: N>
```

Quote text exactly as it appears so the implementer can grep for it (a stale commit message is reported for the record — it is not rewritten on a pushed branch). Report every restatement
you checked, including the consistent ones; the list of what was checked is as valuable as
the list of what was wrong.
