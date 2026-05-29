---
name: copilot-review-loop
description: Use when the user says to run the Copilot review loop on a branch or PR — iterative Copilot code review with automated comment resolution, re-requesting after each clean pass, stopping when Copilot has no comments or after a maximum number of passes.
---

# Copilot Review Loop

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

### 2. Check Copilot review state

```bash
gh api repos/{repo}/pulls/{pr}/reviews | python3 -c "
import json, sys
reviews = json.load(sys.stdin)
copilot = [r for r in reviews if r['user']['login'].startswith('copilot-pull-request-reviewer')]
print(copilot[-1]['state'] if copilot else 'NONE')
"
```

- `NONE` or `PENDING` → reschedule and wait, do nothing else this tick
- `COMMENTED` or `APPROVED` → proceed

### 3. Get unresolved Copilot threads

```bash
gh api graphql -f query='
{ repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {pr}) {
      reviewThreads(first: 20) {
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
| Copilot review complete, zero threads, `iteration == max` | **STOP** |
| Copilot review complete, zero threads, Copilot had no comments at all this pass | **STOP** — satisfied |
| Copilot review complete, all resolved, `iteration < max` | Re-request, increment, reschedule |

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
- **Run tests** after every fix commit before pushing. Don't push broken builds.
- **Worktree:** all edits go in the PR's worktree, not the main checkout.
- **Re-request works** once Copilot has completed a review — same POST endpoint.
- **Default max:** 3 passes unless the user specifies otherwise.
- **Copilot's login is `copilot-pull-request-reviewer[bot]`** (display login `Copilot`, type `Bot`). Request it with that exact login, and match submitted reviews / threads with `.startswith('copilot-pull-request-reviewer')` so the `[bot]` suffix (or any future change to it) doesn't break detection. **Do not request with the display name `Copilot`** — it returns `200 OK` but silently assigns nobody (confirmed on joby/edge-fmt, 2026-05); only the `[bot]` login returns `201 Created` and actually assigns. Always verify the assignment stuck (Step 2) rather than trusting the POST not to error.
- **Reply endpoint is PR-scoped:** use `repos/{owner}/{repo}/pulls/{pr}/comments/{comment_id}/replies` — the `{pr}` number is required. The shorter `repos/{repo}/pulls/comments/{id}/replies` form returns `404 Not Found` (confirmed on joby/edge-fmt, 2026-05).
- **Reply-POST parsing:** the replies response can carry extra data or omit keys like `in_reply_to_id` — parse it defensively (`.get(...)`), and treat the `resolveReviewThread` GraphQL mutation as the reliable success signal, not the reply parse.
- **Human comments too:** Copilot is not the only reviewer. After processing Copilot threads, also check for unresolved threads from human reviewers (the `reviewThreads` query without the `copilot-pull-request-reviewer` filter) — those still need a reply + fix/resolve, and the Copilot-only filter will silently skip them.
