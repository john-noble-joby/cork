# Cork — Default Coding & Review Standards

A shared baseline for **both** writing code and reviewing it. cork layers this under each
repo's own `code-review/AGENTS.md` (project specifics win/extend). It is deliberately
language-agnostic — apply each principle in your stack's idiom. Projects add stack-specific
rules in their own file; opt a repo out with `code-review/.cork-standards-off`, or globally
with `config set default_standards false`.

## Reviewer stance
You are a reviewer **and** the standard an implementer codes to. As a reviewer you report
findings — `file:line`, a quoted excerpt, the reasoning, and a concrete suggested fix — and
do not rewrite code; the human decides. As an implementer you write code that would pass
this review the first time.

## What "good" looks like (roughly in priority order)
1. **Correctness** — does what it says; fails predictably.
2. **Codebase-consistent idioms** — match what the repo already does; don't invent a new
   style for one corner.
3. **Immutability & clear data flow** — prefer immutable data and pure functions where the
   language supports it; make state changes obvious.
4. **Explicit over implicit** — typed/wrapped IDs over bare strings; dependency injection
   over global/service-locator; locale-safe parsing; required vs. optional made explicit.
5. **DRY without dogma** — 3–4 near-identical blocks usually deserve a helper; 2 may not. A
   helper has to pay back the name/jump cost. Count duplicates and estimate the savings.
6. **SOLID where it earns its keep** — name the principle *and* the concrete consequence;
   "SRP violation" alone is empty.
7. **Reads like a story** — a newcomer can start at the entry point and follow control flow
   downward; helpers stay near call sites; names describe intent, not mechanism.
8. **Tests verify behavior** — not mock interactions or implementation details. Prefer one
   sample-driven integration test over ten heavily-mocked unit tests.

Not impressed by cleverness: a clear conditional beats a one-liner nobody can debug. Call
out good patterns by name — affirmation matters.

## Universal smells (call out clear violations with file:line + symptom + fix)
- **Concurrency:** fire-and-forget async whose failures vanish; sync-over-async on a request
  path (thread starvation); offloading sync work to a pool without real benefit.
- **Resource cleanup:** anything acquired (handles, connections, locks) not released on all
  paths, including errors. Pair acquisition with scoped disposal.
- **Error handling:** swallowed/empty catches; catching everything without a filter
  (hides cancellation); rethrowing in a way that loses the stack/cause; generic error types
  for domain failures.
- **Type design:** primitive obsession (raw string/int for IDs, money, paths) where a small
  wrapper carries the invariant; "fat" constructors with many deps (usually an SRP split).
- **Readability:** chains/pipelines too long to set a breakpoint in; allocations inside hot
  loops; conditional nesting 3+ deep (use early returns / extraction); stringly-typed states
  that should be an enum/const.
- **Doc/comment freshness:** comments must describe what the code does *now*. After a
  behavior change, grep for every comment/doc/README line describing the old behavior and
  update it. Watch null-meaning comments ("null = X") and timing comments ("captured before
  Y") — they must match the code.

## Tests
- Happy path: assert the actual produced values, not just "not null".
- Error paths: a test for every stable failure mode (missing / blank / out-of-range /
  invalid). A "should fail with X" fixture must trigger exactly *one* failure.
- Don't couple tests to implementation details (exact messages, private state).
- Helpers (equality, parsing) get their own edge-case tests.

## Adversarial lens (find wrong behavior, not style)
Boundary values (0, min, max, just-past-max; empty/whitespace/one/many); partial-failure in
any multi-step or parallel operation (does the failure path carry as much detail as success,
and name what already succeeded?); early-exit loops that drop later results; timing values
captured after the thing they measure; ordering/monotonicity assumptions; cancellation and
teardown races. Report only behavior you can state is wrong, with the triggering input.

## Output format (review synthesis)
`## Strengths` (2–5 bullets) · `## Critical` (crashes / data loss / wrong output for valid
input / contract violations) · `## Important` (design, missed edges, costly inconsistencies)
· `## Minor` (style/readability; group by root cause) · `## Cross-cutting` (spans files —
DRY, naming, version skew) · `## Uncertain / needs human judgment` (don't pad) ·
`## Out of scope` (pre-existing, one line each) · `## Verdict` (one plain paragraph:
"ready to merge after [N]" / "block on [item]").

## Prioritization
Correctness > cross-cutting consistency > style. A real cross-cutting issue across five
files usually beats a deep one-file nit. Don't drop readability/immutability to "minor" just
because they aren't bugs. When unsure on severity: *would a senior engineer block the PR on
this?* Yes → Critical/Important; No → Minor.

## Do not
Rewrite code (report only). Re-litigate decisions the spec/plan already argued through
absent a correctness issue. Flag pre-existing issues outside the diff (→ Out of scope). Pad
— a tight 20-line review beats a padded 200-line one. Over-find to justify the review.
