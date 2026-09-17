# Review checklist — full protocol

Long-form companion to `SKILL.md`. Load this when actually running a structured review of a branch, diff, or PR. Everything here is project-agnostic; a repo's own standards file adds bindings.

## Pass structure

1. **Pin the base** — the review target is `git diff <base>...HEAD` (three-dot, so the comparison is against the merge-base) plus `git log <base>..HEAD --oneline`. If no base was given, ask for one. Confirm the base resolves (`git rev-parse <base>`) and the diff is non-empty *before* fanning out any passes — a bad ref or empty diff should fail here, not inside parallel subagents.
2. **Orient** — read the change description/spec, the diff stat, and the touched files *around* the changed hunks so changes are reviewed in context, not in isolation. The surface area of a real branch is too wide for one undifferentiated pass; review by concern (domain design, implementation/error handling, tests, quality/idioms, docs, spec conformance), whether via parallel subagents or sequential focused passes.
3. **Correctness pass** — hunt real defects before any style concern: logic errors, inverted/off-by-one conditions, race conditions, unhandled nulls/errors, broken edge cases, security issues, and behavior changes the description doesn't mention. Each suspected defect gets a concrete failure scenario.
4. **Defect-class sweep** — check each recurring class from `SKILL.md`. For every finding, name the class, then grep the rest of the diff and adjacent code for sibling instances.
5. **Spec conformance pass** — isolated from the passes above (see *Spec conformance pass* below); its findings stay under their own heading.
6. **Quality pass** — idioms, DRY, SOLID, smells (including the Fowler structural baseline, labelled as judgement calls), naming, docs.
7. **Synthesize** — de-duplicate across the standards passes; promote genuinely cross-cutting issues to their own section. Do **not** merge or rerank spec findings into the standards findings.

## Adversarial lens — find wrong behavior, not style

Report only cases where you can state with confidence what the wrong behavior is: (1) the specific input or timing condition, (2) what the code actually does, (3) what it should do, (4) `file:line`.

- **Fan-out combinatorics:** for N parallel targets, walk N=1; N=2 both pass; first fails/second passes; first passes/second fails; both fail. Each combination must produce a correct and *complete* failure payload.
- **Fan-out semantics (per mechanism):** .NET `Task.WhenAll` — when the await throws, the other tasks *have* still completed; does the exception handler read the retained task objects to report partial success? JS `Promise.all` — rejection fires on the **first** failure while siblings may still be pending; is `Promise.allSettled` (or settling all retained promises) used before building partial-failure diagnostics?
- **Accumulation loops:** any loop that accumulates state and exits early on first failure is suspect — can later-index outcomes be silently dropped?
- **Information parity:** does the failure outcome include per-target results for *all* targets and name which already succeeded?
- **Boundary values:** numerics at 0 / min / max / just-past-max; strings blank / whitespace-only / format-level null markers; collections empty / one / maximum.
- **Timing capture:** find every start/window/capture timestamp; verify assignment precedes the first async dispatch it measures.
- **Ordering/monotonicity:** out-of-order arrival into head-drained queues or timestamp merges — terminate, spin, or over-drain?
- **Cancellation/teardown races:** cancellation between completion and evaluation; disposal mid-iteration of a background loop.

## Test-strategy checks

- An assertion for every section/field of the produced data, not just non-null.
- A test for every stable, publicly documented error code or failure mode; missing / blank / out-of-range / invalid-value each covered separately.
- Failure fixtures trigger exactly one failure; fixture mutations target the minimal unique context (a broad replace matching two blocks makes the test pass for the wrong reason — an earlier validation fires before the one under test).
- Determinism tests exercise structural equality, not only fingerprint/hash equality.
- Helpers (deep-equals, parsers, builders) have their own edge-case tests: cycles, indexed properties, collection ordering, null vs. empty.
- Tests not coupled to exact message strings, private state, or internals-visible types.
- Set-then-read verifications exercise the real wire/protocol path and decode actual response bytes.
- Every new conditional has a mutation test (delete/invert the clause → some test must fail).

## Spec conformance pass

Run this isolated from the correctness/standards reads so the two don't contaminate each other — a parallel subagent where the harness has them (Claude Code, Codex), otherwise a separate sequential pass with a fresh read of the diff.

**Locate the spec** in order: ticket ids in branch name / commit messages (`#123`, `Closes #45`, `MXE-###`, …) resolved via the repo's tracker binding → a path the requester supplied → a spec/plan file under `docs/`, `specs/`, or scratch matching the branch/feature → ask. No spec → report "no spec available" and stop this pass; don't invent requirements.

**What the pass (or its subagent prompt) gets:** the diff command and commit list, the spec path or fetched contents, and this brief: "Report (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for (scope creep — call out necessary enabling work separately); (c) requirements that look implemented but where the implementation looks wrong. Quote the spec line for each finding. Under 400 words."

**What it does not do:** re-argue design decisions the spec settled, or rank its findings against standards findings. The standards subagent, symmetrically, gets the standards sources and the smell baseline pasted in full (it has no other access to them) and reports documented-standard breaches as hard findings, baseline smells as judgement calls, and skips anything tooling enforces.

## DRY / SOLID — how to flag usefully

- DRY: count the duplicates and estimate the savings ("five near-identical methods, ~20 lines each, collapse to one ~25-line helper + five one-liners"). Magic strings/numbers repeated across files → shared const. Mirror types (DTO/domain/view of one concept) are legitimate at decoupling boundaries — ask *what would actually drift between them?* Repeated test setup → builder, fixture, or parameterized data source. Repeated error-plumbing boilerplate → a bind/map chain is a taste call; flag the cost either way.
- SOLID: name the principle and the specific consequence. A 700-line class with one responsibility can be fine; a 700-line method never is. Open/closed is intentionally violated by closed unions matched at one site — only flag when the design promised extensibility. Interface bloat: a consumer shouldn't depend on methods it doesn't use. Watch overrides that throw "not supported" or shift pre/postconditions.

## Dependency & toolchain hygiene

- **Version matrix:** enumerate project manifests; flag any package used at two versions in one solution/workspace, and drift from the pinned language/runtime version.
- **Promotion candidates:** packages/settings shared by ≥2 projects → central version management at the right scope (repo root vs. per-solution — different solutions may legitimately diverge).
- **Dead references:** declared dependencies never imported.
- **Pinning:** every solution/workspace pinned to a toolchain version, and the pins agree.
- **Gate consistency:** lint/analyzer gates enabled uniformly, not only on some projects (a gate that fires on half the tree gives false confidence).
- Fixture/asset copying follows the existing convention (and uses portable path separators).

## Docs, specs, and CI accuracy

- Spec error-code/contract tables match exactly what the code emits today — no rogue codes either direction.
- Plan/spec deviation logs still describe the final code; steps executed differently get a note.
- README examples still compile against the shipped API; counts and defaults up to date.
- Build/CI defaults and ARG values aligned across Makefile/Dockerfile/pipeline.
- No stale references to renamed/removed types in docs or commit messages in the diff.
- Open questions in the spec that implementation answered are marked answered.

## Report format

`## Strengths` (2–5 bullets — affirm non-obvious good choices) ·
`## Critical` (crashes, data loss, wrong output for valid input, contract violations — each with `file:line`, quoted excerpt, why, suggested fix) ·
`## Important` (design concerns, missed edge cases, inconsistencies that cost later) ·
`## Minor` (style/readability; group by shared root cause) ·
`## Cross-cutting` (spans files — DRY, naming, version skew) ·
`## Spec conformance` (separate axis, never merged into the above — missing / partial / unrequested / implemented-wrong, each quoting the spec line; or the single line "no spec available") ·
`## Promotion candidates` (what should move to central/shared configuration or version management — for each, the scope [repo root vs. per-solution] and one-time migration cost S/M/L) ·
`## Uncertain / needs human judgment` (don't pad; include what would change your mind) ·
`## Out of scope` (pre-existing, one line each) ·
`## Verdict` (one plain paragraph: "ready to merge after [N]" / "block on [item]" — name the worst item on the standards axis *and* the worst on the spec axis; don't crown a single winner across the two).

## Prioritization when findings compete

Correctness > cross-cutting consistency > style. A real cross-cutting issue across five files usually beats a deep one-file nit — but don't inflate minor style into cross-cutting. Readability and immutability are real goods; don't auto-demote them because they aren't bugs. DRY/SOLID findings rank higher when they touch the public API or test surface, lower when internal-only and reading fine. Spec-conformance findings are a separate axis and are never ranked against any of the above — report them side by side; picking one winner across axes is exactly the masking the separation exists to prevent.
