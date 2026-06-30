---
name: copilot-review-loop
description: Use when the user says to run the Copilot review loop on a branch or PR — iterative Copilot code review with automated comment resolution, re-requesting after each clean pass, stopping when Copilot has no comments or after a maximum number of passes.
---

# Copilot Review Loop

**Version:** 0.8.1 — keep in sync with the repo `VERSION` file (`install.sh` checks this).

## Overview

Runs an iterative Copilot PR review cycle: request review → wait → process every comment (fix or push back) → re-request → repeat up to N times. Stops early if Copilot submits a pass with no comments.

## When invoked, do this immediately

### Step 1 — Gather context

```bash
# Identify the PR for the current branch
gh pr view --json number,headRefName,url

# Identify the repo
git remote get-url origin | sed 's/.*github.com[:/]\(.*\)\.git/\1/'

# Identify the worktree path (cwd or ask user)
pwd
```

Ask the user: **"Max passes? (default 3)"** — unless they already said.

Then proceed to Step 2.

### Step 2 — Request Copilot review (first pass)

```bash
gh api repos/{owner}/{repo}/pulls/{pr}/requested_reviewers \
  -X POST -f 'reviewers[]=copilot-pull-request-reviewer[bot]' -i 2>&1 | head -1
```

**Verify the request stuck before polling.** A wrong login (e.g. the display name `Copilot`)
returns `200 OK` and silently assigns nobody — no error, but `requested_reviewers` stays empty
and Copilot never reviews. Confirm `201 Created` above, then check Copilot is actually assigned:

```bash
gh api repos/{owner}/{repo}/pulls/{pr}/requested_reviewers \
  --jq '.users[].login' | grep -qi copilot && echo OK || echo "NOT REQUESTED — re-check login"
```

If it didn't stick, you almost certainly used the display name instead of the bot login — re-run
with `copilot-pull-request-reviewer[bot]`. Don't start the loop until this prints `OK`.

Start the loop:

```
/loop 3m
COPILOT_REVIEW_LOOP pr={PR_NUMBER} repo={owner/repo} max={MAX} worktree={WORKTREE_PATH} iteration=1
```

---

## Loop body — what to do on each tick

### 1. Parse state from the loop prompt

Extract: `pr`, `repo`, `max`, `worktree`, `iteration`.

### 2. Check Copilot review state *and* its comment count

Read the latest Copilot review's `state` **and its own `comments.totalCount`** in one call.
`totalCount` is the authoritative number of inline comments on that review — it is set when
the review is submitted, so it's correct *before* the separate `reviewThreads` index
finishes propagating. (Naively fetching threads on `COMMENTED` and seeing an empty list is
the bug: the review really has comments, the thread index just hasn't caught up — so you'd
declare a false "clean pass" and stop. Don't infer "clean" from an empty thread fetch;
read `totalCount`.)

```bash
gh api graphql -f query='
{ repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {pr}) {
      reviews(last: 10) { nodes { author { login } state comments(first: 0) { totalCount } } }
    }
  }
}' | python3 -c "
import json, sys
revs = json.load(sys.stdin)['data']['repository']['pullRequest']['reviews']['nodes']
cop = [r for r in revs if r['author']['login'].startswith('copilot-pull-request-reviewer')]
if not cop:
    print('NONE 0')
else:
    print(cop[-1]['state'], cop[-1]['comments']['totalCount'])
"
```

Output is `STATE TOTALCOUNT`. Route on it:

- `state` is `NONE`/`PENDING` (review not submitted yet) → reschedule and wait, nothing else this tick.
- `state` completed (`COMMENTED`/`APPROVED`) and `totalCount == 0` → **deterministic clean pass**, no race: no comments exist. Skip to step 6 (stop / re-request per iteration).
- `state` completed and `totalCount > 0` → that many comments exist; go to **2b** to wait for the thread index, then process.

### 2b. Wait for the thread index to surface the known comments

You now know `totalCount > 0` comments exist on this review. Poll the step-3 thread query
until the index catches up — don't process a partial set (you'd resolve a few, re-request,
and miss the rest):

1. Run the step-3 query; count the currently-unresolved Copilot threads.
2. Re-check after a short delay — ~60s, deliberately tighter than the 3-min review-wait
   poll because the review is already in (both stay within the 5-min cache window). In a
   `/loop` run, do it as a reschedule carrying the prior count as `settle_count=N`, not a
   blocking wait.
3. Proceed to step 3 once the count is **stable across two consecutive reads** *and* has
   reached `totalCount` (minus any threads already resolved on earlier passes). Stability
   **plus** matching the known count is the signal — never act while the count is still
   rising, and never treat "stopped rising" alone as settled.

**Watch the page cap.** Step 3 fetches `reviewThreads(first: 100)`. If the count plateaus at
the cap while still below `totalCount`, that's a capped artifact, not a settled index —
paginate (`pageInfo`/`endCursor`) before trusting it. Copilot rarely exceeds 100 inline
comments, but don't let the cap masquerade as "settled".

### 3. Get unresolved Copilot threads

```bash
gh api graphql -f query='
{ repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {pr}) {
      reviewThreads(first: 100) {
        nodes { id isResolved comments(first:1){ nodes { databaseId body author { login } } } }
      }
    }
  }
}' | python3 -c "
import json, sys
data = json.load(sys.stdin)
threads = data['data']['repository']['pullRequest']['reviewThreads']['nodes']
unresolved = [
    t for t in threads
    if not t['isResolved']
    and t['comments']['nodes']
    and t['comments']['nodes'][0]['author']['login'].startswith('copilot-pull-request-reviewer')
]
for t in unresolved:
    print(t['id'], t['comments']['nodes'][0]['databaseId'])
    print(t['comments']['nodes'][0]['body'])
    print('---')
"
```

### 3b. Interactive review (default on)

Read the preference once at loop start:

Run `python3 "$CORK_HOME/orchestrate.py" config get interactive_review`. If it prints `true` (the default), pause as below; if `false`, behave autonomously.

- **`true` (default):** after fetching this pass's unresolved comments (step 3), apply
  NOTHING yet. (1) **Pre-pass:** form your recommendation per comment (fix / push back +
  reason / out of scope). (2) **Present** the comments *and* your recommendation, numbered.
  (3) **Wait** for the user to choose: **Fix all** · **Pick specific** · **Push back**
  (reason → posted as the PR reply, then resolve) · **Proceed (no changes)** — leave the
  threads unresolved this tick and make zero edits. Then carry out step 4 for the chosen
  items only.
- **`false`:** process every comment autonomously (step 4 as written).

### 4. Process each unresolved thread

Read the comment body and the file + line it references.

**Fix** — if correct: implement the change in the worktree, run tests, commit, push. Then:

```bash
# Reply — the endpoint is PR-scoped; the {pr} number is REQUIRED in the path.
# Omitting it (repos/{repo}/pulls/comments/{id}/replies) returns 404 Not Found.
gh api repos/{owner}/{repo}/pulls/{pr}/comments/{comment_id}/replies \
  -X POST -f body="Fixed in {sha} — {brief explanation}"

# Resolve
gh api graphql -f query='mutation {
  resolveReviewThread(input: {threadId: "{thread_id}"}) { thread { isResolved } }
}'
```

**Push back** — if wrong, already addressed, or out of scope: reply with concise reasoning, resolve without changing code.

### 5. After all threads processed

Push any commits, then evaluate stop conditions.

### 6. Stop conditions

| Condition | Action |
|---|---|
| Review complete, `totalCount == 0` this pass (step 2 — not an empty thread fetch) | **STOP** — satisfied, clean pass |
| Review complete, comments all processed/resolved, `iteration == max` | **STOP** |
| Review complete, comments all processed/resolved, `iteration < max` | Re-request, increment, reschedule |

Judge "clean pass" from step 2's `totalCount == 0`, **never** from an empty `reviewThreads`
fetch — the index lags the review, so an empty fetch on a fresh `COMMENTED` is the race, not
a clean pass.

Print final summary on stop: iterations run, commits made, PR URL.

### 7. Re-request and continue

```bash
gh api repos/{owner}/{repo}/pulls/{pr}/requested_reviewers \
  -X POST -f 'reviewers[]=copilot-pull-request-reviewer[bot]'
```

Update loop prompt with `iteration={N+1}` and reschedule.

---

## Notes

- **Polling interval:** 3 minutes — stays within the 5-minute cache window.
- **Review state flips before its threads are indexed.** A review reaches `COMMENTED`/`APPROVED`, but its inline comments take seconds-to-longer to appear in `reviewThreads` / the pulls-comments API — so a thread fetch right at the transition can return an empty or partial list and trick the loop into a false "clean pass" + early STOP. The review's *own* `comments.totalCount` (GraphQL, step 2) is set atomically at submission and is the authoritative "are there comments?" signal; gate on it, and for `totalCount > 0` wait for the thread count to stabilize before processing (confirmed on cork PR #6, 2026-06: pass-3 clean review reported `totalCount=0`, comment-bearing passes reported their exact counts).
- **Run tests** after every fix commit before pushing. Don't push broken builds.
- **Worktree:** all edits go in the PR's worktree, not the main checkout.
- **Re-request works** once Copilot has completed a review — same POST endpoint.
- **Default max:** 3 passes unless the user specifies otherwise.
- **Copilot's login is `copilot-pull-request-reviewer[bot]`** (display login `Copilot`, type `Bot`). Request it with that exact login, and match submitted reviews / threads with `.startswith('copilot-pull-request-reviewer')` so the `[bot]` suffix (or any future change to it) doesn't break detection. **Do not request with the display name `Copilot`** — it returns `200 OK` but silently assigns nobody (confirmed on joby/edge-fmt, 2026-05); only the `[bot]` login returns `201 Created` and actually assigns. Always verify the assignment stuck (Step 2) rather than trusting the POST not to error.
- **Reply endpoint is PR-scoped:** use `repos/{owner}/{repo}/pulls/{pr}/comments/{comment_id}/replies` — the `{pr}` number is required. The shorter `repos/{repo}/pulls/comments/{id}/replies` form returns `404 Not Found` (confirmed on joby/edge-fmt, 2026-05).
- **Reply-POST parsing:** the replies response can carry extra data or omit keys like `in_reply_to_id` — parse it defensively (`.get(...)`), and treat the `resolveReviewThread` GraphQL mutation as the reliable success signal, not the reply parse.
- **Human comments too:** Copilot is not the only reviewer. After processing Copilot threads, also check for unresolved threads from human reviewers (the `reviewThreads` query without the `copilot-pull-request-reviewer` filter) — those still need a reply + fix/resolve, and the Copilot-only filter will silently skip them.
