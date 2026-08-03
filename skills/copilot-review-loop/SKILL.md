---
name: copilot-review-loop
description: Use when the user says to run the Copilot review loop on a branch or PR — iterative Copilot code review with automated comment resolution, re-requesting after each clean pass, stopping when Copilot has no comments or after a maximum number of passes.
---

# Copilot Review Loop

**Version:** 0.8.3 — keep in sync with the repo `VERSION` file (`install.sh` checks this).

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

### 2. Check the review's state, comment count, **verdict, and suppressed comments**

Read the latest Copilot review's `state`, its own `comments.totalCount`, **and its `body`** in
one call. Two things gate a clean pass, and `totalCount == 0` alone is **not** one of them:

- `totalCount` — inline comments on this review. It's set at submission, so it's correct
  *before* the `reviewThreads` index finishes propagating; an empty thread fetch on a fresh
  `COMMENTED` is the index lagging, not a clean pass.
- **The body carries the verdict and any *suppressed* comments.** Copilot's *Lite* effort
  often posts **zero inline comments** (`totalCount == 0`) yet renders a `🟡 Not ready to
  approve` verdict with findings under a `### Suppressed comments (N)` section in the body.
  Gating on `totalCount`/threads alone misses these entirely and declares a false clean pass.
  **Clean = the verdict approves AND `totalCount == 0` AND zero suppressed comments.**

```bash
gh api graphql -f query='
{ repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {pr}) {
      reviews(last: 50) { nodes { author { login } state body comments(first: 0) { totalCount } } }
    }
  }
}' | python3 -c "
import json, sys, re
revs = json.load(sys.stdin)['data']['repository']['pullRequest']['reviews']['nodes']
# author can be null (ghost/deleted user); guard before .login. last:50 so the latest
# Copilot review isn't pushed out of the window by reply-wrapped reviews on busy PRs.
cop = [r for r in revs
       if r.get('author') and r['author']['login'].startswith('copilot-pull-request-reviewer')]
if not cop:
    print('state=NONE tc=0 verdict=none suppressed=0'); raise SystemExit
r = cop[-1]; low = (r.get('body') or '').lower()
verdict = ('block' if 'not ready to approve' in low
           else 'approve' if r['state'] == 'APPROVED' or 'ready to approve' in low else 'none')
m = re.search(r'suppressed comments \((\d+)\)', low)
print(f\"state={r['state']} tc={r['comments']['totalCount']} verdict={verdict} suppressed={m.group(1) if m else 0}\")
"
```

Route on `state tc verdict suppressed`:

- `state=NONE`/`PENDING` (review not submitted yet) → reschedule and wait, nothing else this tick.
- **`verdict=approve` AND `tc=0` AND `suppressed=0`** → clean pass → step 6 (stop / re-request per iteration). This is the ONLY clean case.
- Otherwise the review has findings — **process every channel that is non-zero this pass, not
  just one** (a Lite review can post some inline *and* suppress others; they are not
  mutually exclusive):
  - if `tc > 0` → **2b** (settle the thread index) → step 3 → step 4 (inline threads);
  - if `suppressed > 0` → **2c** (body findings);
  - do **both** when both are non-zero, then continue to step 5/6.

Never treat inline and suppressed as either/or — "all processed" in step 6 means inline
threads **and** suppressed body findings from this pass are all handled.

### 2b. Wait for the thread index to surface the known comments

You now know `totalCount > 0` comments exist on this review. Poll the step-3 thread query
until the index catches up — don't process a partial set (you'd resolve a few, re-request,
and miss the rest):

1. Run the step-3 query; count the currently-unresolved Copilot threads.
2. Re-check after a short delay — ~60s, deliberately tighter than the 3-min review-wait
   poll because the review is already in (both stay within the 5-min cache window). In a
   `/loop` run, do it as a reschedule carrying the prior count as `settle_count=N`, not a
   blocking wait.
3. Proceed to step 3 once the thread count is **stable across two consecutive reads** — that
   is the settle signal. Don't equate it to `totalCount`: `totalCount` counts review
   *comments* and step 3 counts *threads*, and they aren't 1:1 (a thread can hold several
   comments), so requiring `threads == totalCount` could never settle. Use `totalCount` only
   for the step-2 gate (`0` → clean pass; `>0` → there's something to wait for); use
   thread-count stability for *when* it's safe to process.

**Watch the page cap.** Step 3 fetches `reviewThreads(first: 100)`. If the count plateaus at
exactly 100 and step 3's `pageInfo.hasNextPage` is true, that's a capped artifact, not a
settled index — page through with `endCursor` before trusting it. Copilot rarely exceeds 100
inline comments, but don't let the cap masquerade as "settled".

### 2c. Process suppressed (body-level) comments

`suppressed > 0` means Copilot put its findings in the review **body**, not as inline threads
— there is nothing for step 3 to fetch and nothing to `resolveReviewThread`. Fetch the body
with the **same null-safe, `last: 50` GraphQL** as step 2 (the REST `reviews` endpoint is
paginated and can return a stale review on a busy PR), and extract the
`### Suppressed comments (N)` section:

```bash
gh api graphql -f query='
{ repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {pr}) { reviews(last: 50) { nodes { author { login } body } } }
  }
}' | python3 -c "
import json, sys
revs = json.load(sys.stdin)['data']['repository']['pullRequest']['reviews']['nodes']
cop = [r for r in revs if r.get('author') and r['author']['login'].startswith('copilot-pull-request-reviewer')]
print(cop[-1]['body'] if cop else '')
"
```

Each suppressed item is a `**path:line**` header + a description bullet + a code snippet. For
each: **fix it (run tests, commit, push) or push back with reasoning** — same judgement as any
comment. There is no thread to reply to/resolve, so instead post **one PR comment**
(`gh pr comment {pr} --body "…"`) summarizing what you fixed (with the SHA) and what you pushed
back on. Then re-request review (step 7) and rely on the next pass's **verdict** to confirm.

Also compare against the prior pass: a suppressed note you already addressed in an earlier
commit is done — acknowledge it as already-fixed rather than re-doing it.

### 3. Get unresolved Copilot threads

```bash
gh api graphql -f query='
{ repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {pr}) {
      reviewThreads(first: 100) {
        pageInfo { hasNextPage endCursor }
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
    and t['comments']['nodes'][0].get('author')   # author can be null (ghost/deleted user)
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
| `verdict=approve` AND `tc=0` AND `suppressed=0` this pass | **STOP** — satisfied, clean pass |
| Comments (inline + suppressed) all processed/resolved, `iteration == max` | **STOP** |
| Comments (inline + suppressed) all processed/resolved, `iteration < max` | Re-request, increment, reschedule |

Judge "clean pass" from step 2's **verdict + `tc` + `suppressed`** together — **never** from an
empty `reviewThreads` fetch (the index lags a fresh `COMMENTED`) and **never** from `tc == 0`
alone (Lite-mode reviews suppress findings into the body with `tc=0` but a `block` verdict).

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
- **Lite-mode reviews suppress findings into the body.** Copilot's *Lite* effort often posts
  `totalCount = 0` inline comments yet renders a `🟡 Not ready to approve` verdict with findings
  under a `### Suppressed comments (N)` section in the review **body**. A loop that gates only on
  threads/`totalCount` reads this as a clean pass and stops while the PR is unapproved. Gate on
  the **verdict + `tc` + `suppressed`** (step 2 reads `review.body`), and process suppressed
  items from the body — there's no thread to resolve, so acknowledge via a PR comment
  (confirmed on cork PR #8, 2026-07: two passes were `Not ready to approve` with `tc=0` and 1–2
  suppressed comments each).
- **Review state flips before its threads are indexed.** A review reaches `COMMENTED`/`APPROVED`, but its inline comments take seconds-to-longer to appear in `reviewThreads` / the pulls-comments API — so a thread fetch right at the transition can return an empty or partial list and trick the loop into a false "clean pass" + early STOP. The review's *own* `comments.totalCount` (GraphQL, step 2) is set atomically at submission and is the authoritative "are there comments?" signal; gate on it, and for `totalCount > 0` wait for the thread count to stabilize before processing (confirmed on cork PR #6, 2026-06: pass-3 clean review reported `totalCount=0`, comment-bearing passes reported their exact counts).
- **Run tests** after every fix commit before pushing. Don't push broken builds.
- **Worktree:** all edits go in the PR's worktree, not the main checkout.
- **Re-request works** once Copilot has completed a review — same POST endpoint.
- **Default max:** 3 passes unless the user specifies otherwise.
- **Copilot's login is `copilot-pull-request-reviewer[bot]`** (display login `Copilot`, type `Bot`). Request it with that exact login, and match submitted reviews / threads with `.startswith('copilot-pull-request-reviewer')` so the `[bot]` suffix (or any future change to it) doesn't break detection. **Do not request with the display name `Copilot`** — it returns `200 OK` but silently assigns nobody (confirmed on joby/edge-fmt, 2026-05); only the `[bot]` login returns `201 Created` and actually assigns. Always verify the assignment stuck (Step 2) rather than trusting the POST not to error.
- **Reply endpoint is PR-scoped:** use `repos/{owner}/{repo}/pulls/{pr}/comments/{comment_id}/replies` — the `{pr}` number is required. The shorter `repos/{repo}/pulls/comments/{id}/replies` form returns `404 Not Found` (confirmed on joby/edge-fmt, 2026-05).
- **Reply-POST parsing:** the replies response can carry extra data or omit keys like `in_reply_to_id` — parse it defensively (`.get(...)`), and treat the `resolveReviewThread` GraphQL mutation as the reliable success signal, not the reply parse.
- **Human comments too:** Copilot is not the only reviewer. After processing Copilot threads, also check for unresolved threads from human reviewers (the `reviewThreads` query without the `copilot-pull-request-reviewer` filter) — those still need a reply + fix/resolve, and the Copilot-only filter will silently skip them.
