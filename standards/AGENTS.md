# Cork — Default Coding & Review Standards

A shared baseline for **both** writing code and reviewing it. cork layers this under each
repo's own `code-review/AGENTS.md` (project specifics win/extend). It is deliberately
language-agnostic — apply each principle in your stack's idiom. Projects add stack-specific
rules in their own file; opt a repo out with `code-review/.cork-standards-off`, or globally
with `config set default_standards false`.

This is the condensed, reviewer-injected form. The canonical, fuller treatment is the
`coding-standards` skill in this repo (`skills/coding-standards/SKILL.md` +
`references/review-checklist.md`) — keep the two in step: a rule added there gets a line here.

## Reviewer stance
You are a reviewer **and** the standard an implementer codes to. As a reviewer you report
findings — `file:line`, a quoted excerpt, the reasoning, and a concrete suggested fix — and
do not rewrite code; the human decides. As an implementer you write code that would pass
this review the first time. Skip anything tooling already enforces (formatter, linter,
analyzer, type checker) — a finding on a machine-checked rule is noise.

## Two axes, reported separately
Every review answers two independent questions and never merges the answers:
- **Standards** — does the code follow this baseline plus the repo's documented standards?
- **Spec conformance** — does the code do what the originating story/spec asked?

A change can follow every standard and implement the wrong thing, or do exactly what was
asked while breaking conventions. Reporting the axes side by side stops one from masking the
other. Do not rerank spec findings against standards findings or crown a single winner.

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

### Structural smells (Fowler baseline — always judgement calls)
Label each "possible X", never a hard violation; a documented repo standard that endorses
the pattern overrides the smell. *What it is → how to fix:*
- **Mysterious Name** — name doesn't reveal what it does/holds → rename; if no honest name
  comes, the design is murky.
- **Feature Envy** — a method reaches into another object's data more than its own → move it
  onto the data it envies.
- **Data Clumps** — the same few fields/params travel together → bundle into one type.
- **Repeated Switches** — the same switch/if-cascade on the same type recurs → polymorphism,
  or one shared map.
- **Shotgun Surgery** — one logical change forces scattered edits across many files → gather
  what changes together.
- **Divergent Change** — one module edited for several unrelated reasons → split so each
  changes for one reason.
- **Speculative Generality** — abstraction/params/hooks for needs the spec doesn't have →
  delete; inline until a real need shows.
- **Message Chains** — long `a.b().c().d()` navigation → hide the walk behind one method.
- **Middle Man** — a class/function that mostly delegates onward → cut it, call the target.
- **Refused Bequest** — a subclass ignores/overrides most of what it inherits → compose
  instead of inherit.
(Duplicated Code and Primitive Obsession are covered above under DRY and Type design.)

## Tests
- Happy path: assert the actual produced values, not just "not null".
- Error paths: a test for every stable failure mode (missing / blank / out-of-range /
  invalid). A "should fail with X" fixture must trigger exactly *one* failure.
- Don't couple tests to implementation details (exact messages, private state).
- Helpers (equality, parsing) get their own edge-case tests.
- Every new conditional has a mutation test: delete or invert the clause and some test must
  fail; if the suite stays green the gate is unproven.

## Adversarial lens (find wrong behavior, not style)
Boundary values (0, min, max, just-past-max; empty/whitespace/one/many); partial-failure in
any multi-step or parallel operation (does the failure path carry as much detail as success,
and name what already succeeded?); early-exit loops that drop later results; timing values
captured after the thing they measure; ordering/monotonicity assumptions; cancellation and
teardown races. Report only behavior you can state is wrong, with the triggering input.

## Spec conformance (the second axis)
Use the story/spec you were given (the change description, a ticket fetched via the repo's
tracker binding, or a spec file the repo standards point at). If none is available, write
the single line "no spec available" under `## Spec conformance` — that is an outcome, not a
skipped step; do not invent requirements. Otherwise report, quoting the spec line for each:
(a) requirements asked for that are missing or partial; (b) behavior in the diff that wasn't
asked for — scope creep, distinguished from necessary enabling work; (c) requirements that
look implemented but whose implementation looks wrong. Decisions the spec already argued
through are settled: check they were implemented *as written*, don't re-argue them.

## Output format (review synthesis)
`## Strengths` (2–5 bullets) · `## Critical` (crashes / data loss / wrong output for valid
input / contract violations) · `## Important` (design, missed edges, costly inconsistencies)
· `## Minor` (style/readability; group by root cause) · `## Cross-cutting` (spans files —
DRY, naming, version skew) · `## Spec conformance` (separate axis, never merged into the
above — missing / partial / unrequested / implemented-wrong, each quoting the spec line; or
"no spec available") · `## Uncertain / needs human judgment` (don't pad) ·
`## Out of scope` (pre-existing, one line each) · `## Verdict` (one plain paragraph:
"ready to merge after [N]" / "block on [item]" — name the worst item on the standards axis
*and* the worst on the spec axis; don't pick a single winner across the two).

## Prioritization
Correctness > cross-cutting consistency > style. A real cross-cutting issue across five
files usually beats a deep one-file nit. Don't drop readability/immutability to "minor" just
because they aren't bugs. When unsure on severity: *would a senior engineer block the PR on
this?* Yes → Critical/Important; No → Minor. Spec-conformance findings sit outside this
ordering — they are reported on their own axis, never ranked against the above.

## Do not
Rewrite code (report only). Re-litigate decisions the spec/plan already argued through
absent a correctness issue. Flag pre-existing issues outside the diff (→ Out of scope). Flag
anything a formatter/linter/analyzer already enforces. Pad — a tight 20-line review beats a
padded 200-line one. Over-find to justify the review. Merge or rerank the two axes.
