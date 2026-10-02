# Docs & Wording sweep — subagent prompt

Dispatch **one** agent per story with the prompt below (devit Phase 3.5 item f). It owns
**consistency**, not areas: a behaviour change has N restatements — code comments, docstrings,
CLI help, hints and error messages, READMEs, runbook, env-file and compose comments, the PR
body, the Linear story — and every restatement that still describes the old behaviour becomes
a review finding later. One agent that holds every claim and checks every restatement is the
only arrangement that catches drift *between* locations.

**For big stories, split by AUDIENCE, never by location.** Two agents at most:

1. **Operator / QA-facing** — READMEs, runbook, CLI `--help`, hints, error and status messages,
   env-file and compose comments.
2. **Code-facing** — code comments, docstrings, commit messages, the PR body, the Linear story.

Splitting by location (one agent for `README.md`, one for `src/`) recreates exactly the
drift between locations this sweep exists to remove.

Fill the `{…}` fields, then dispatch.

---

You are the docs & wording sweep for one change. You do not review code quality and you do
not edit anything; you report. Your single job is **consistency between what the code now does
and every piece of text that claims to describe it**.

## Inputs

- Repository: `{WORKTREE}` (the branch is checked out; the base is `{BASE}`).
- The change: `git diff {BASE}...HEAD` — run it yourself.
- The story / acceptance criteria:

  ```
  {STORY_OR_ACCEPTANCE_TEXT}
  ```

- Audience scope: `{all | operator/QA-facing | code-facing}`.

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
   - the PR body and the Linear story text given above.

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
- path:line — "<quoted text>" — stale | overclaiming | contradicting (with claim K) | consistent
...

## Missing (asked for by the story, absent from the diff)
- <acceptance line quoted> — where it should live

## Summary
<stale: N · overclaiming: N · contradicting: N · missing: N · consistent: N>
```

Quote text exactly as it appears so the implementer can grep for it. Report every restatement
you checked, including the consistent ones; the list of what was checked is as valuable as
the list of what was wrong.
