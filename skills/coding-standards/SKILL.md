---
name: coding-standards
description: "Use when writing, modifying, or reviewing code in any language — before implementing a feature or bugfix, when sweeping a diff or PR for defects, and when judging what a review should flag or how severe a finding is. Default engineering standards: correctness-first priorities, recurring defect classes (stale comments, fan-out result handling, timing capture, boundary normalization, unproven conditionals, presentation-surface parity), test standards, spec conformance (does the diff do what the story asked — missing, partial, or unrequested behaviour), a Fowler smell baseline, and review conduct."
---

# Coding Standards — default for writing and reviewing code

**Version:** 0.18.0 — keep in sync with the repo `VERSION` file (`install.sh` checks this).

One baseline for **both** roles: as an implementer, write code that would pass this review the first time; as a reviewer, report findings (`file:line`, excerpt, reasoning, concrete fix) and let the human decide what to apply.

These are the universal defaults. A repo's own standards file (e.g. `code-review/AGENTS.md`) layers on top — project specifics win and extend, never dilute. Where a repo standard deliberately endorses something a baseline smell below would flag, the repo wins — suppress the smell.

This skill is a harness-level default: cork's `default_standards` / `.cork-standards-off`
toggles gate only what the orchestrator injects into API reviewers, not whether this skill
loads.

Full review-session protocol (pass order, adversarial checklist, report format, dependency/doc hygiene): [references/review-checklist.md](references/review-checklist.md).

## Priority order — what "good" looks like

1. **Correctness** — does what it says; fails predictably.
2. **Codebase-consistent idioms** — match what the repo already does; don't invent a new style for one corner.
3. **Immutability and clear data flow** — immutable data, read-only collection views, pure functions where the language supports them; state changes obvious.
4. **Explicit over implicit** — typed/wrapped IDs over bare strings; constructor/dependency injection over globals and service locators; locale-pinned parsing and formatting; optional vs. required stated, not implied.
5. **DRY without dogma** — 3–4 near-identical blocks usually deserve a helper; 2 may not. A helper must pay back its name/jump cost. When flagging duplication, count the duplicates and estimate the savings.
6. **SOLID where it earns its keep** — name the principle *and* the concrete consequence ("splitting a method that mixes parsing, validation, and side effects cuts the test surface"). "SRP violation" alone is empty.
7. **Reads like a story** — a newcomer starts at the entry point and follows control flow downward without jumping files; helpers stay near call sites; names describe intent, not mechanism.
8. **Tests verify behavior** — not mock interactions, not implementation details. One sample-driven integration test beats ten heavily-mocked unit tests.

Cleverness is not a value: a clear three-line conditional beats a one-liner nobody can debug. Call out good patterns by name — affirmation locks in what works.

## Recurring defect classes

These classes recur across real review history. Apply them **while writing** (cheaper than a review round) and sweep for them **when reviewing**. When a diff fixes an instance of a class, verify the whole class was swept — including sites the fix itself introduced.

### 1. Fix the class, not the instance
Before committing any fix (yours or a review finding's), name the defect class the instance belongs to, then grep every other site in that class — including sites your own fix just created. A finding that recurs one layer up in the next review round means the class was never swept.

### 2. Comments are contracts (doc freshness)
A comment describing behavior the code no longer has is a bug, not a nit. When behavior changes — null semantics, return shapes, timing, error codes — grep for every doc comment, inline comment, README line, and schema/column comment that describes the old behavior and update them in the same commit. Watch especially: "null means X" comments (must match the return contract callers infer by testing `== null`), "captured before Y" timing comments, and DB column comments mirroring an entity's doc.

**Restatement inventory (the sweep form).** A behavior change has N restatements — code comments, docstrings, CLI help, hints and error/status messages, READMEs, runbook, env-file and compose comments, the PR body, the story — and each stale one becomes a separate late review finding (about a third of all findings on edge-fmt #534/#537). Before review, list every claim the diff alters, grep every restatement of each claim across the whole repo, and fix them together; one agent owning *consistency* across locations beats several owning locations (devit Phase 3.5 f, `skills/devit/references/docs-sweep.md`). Also report documentation the acceptance criteria asked for that the diff lacks.

### 3. Timing capture before the operation
Capture `startTime` / `windowStart` / `captureTime` variables **immediately before** the operation they measure — never after the first async task has been dispatched. A timestamp assigned after `tasks = items.map(dispatch)` misses everything that happened while the tasks were being constructed.

### 4. Fan-out results: no early exit, explicit partial-failure strategy
Principle (any language): a multi-target parallel dispatch must **define** its partial-failure strategy — roll back (when partial application is hazardous), or report exactly which targets already applied — and must never decide on the first failure alone. Compute the full per-target pass/fail split, then decide once. **Error-path information parity:** the failure outcome carries outcomes for *all* targets, naming which failed and which had already succeeded, not just the one that triggered the failure. A new dispatch path using a third strategy documents the rationale at the decision site.

Per mechanism — the settled-ness guarantees differ:
- **.NET `Task.WhenAll`:** when the await returns *or throws*, every task has completed — an early `return`/`break` while iterating results silently drops later-index outcomes. Retain the individual task objects so `catch` blocks can inspect `t.IsCompletedSuccessfully`/`t.Result` for partial-success detail.
- **JS `Promise.all`:** rejects on the **first** rejection while siblings may still be pending — the rejection path has no complete picture. Use `Promise.allSettled` (or settle all retained promises) *before* computing partial-failure diagnostics.

### 5. Mutation test for every new conditional
Each new gate/branch needs at least one test that fails when the condition is deleted or inverted. If you can remove the clause and the suite stays green, the gate is unproven — flag the mutation survivor.

### 6. Normalize user-authored strings once at the boundary
Enumerable user-authored strings (units, kinds, sources, categories) go through **one** shared normalizer with an input-domain test table: null, empty, whitespace-only, case variants, singular/plural, symbols, garbage. An ad-hoc `trim().toLowerCase()` inside an individual helper is a flag by itself — per-helper parsing is how independent bugs accrete.

(Display-side guidance only. It does not soften class 8: a validating parser stays exactly as strict as its schema.)

### 7. Formatting preserves distinctions
Any rounding/snapping/truncation applied to values displayed as a pair or set (endpoints, ranges, before/after) must be checked **jointly**: if two distinct resolved values would render identically (a 0.04→0 delta displaying as `0→0`), the precision is too low. Check the pair, not each value alone.

### 8. Schema/parser strictness lockstep
A validation schema and its parser must be *exactly* equally strict. No case folding, lenient trimming, or accepting values the schema rejects (and vice versa) — divergence means input can pass validation yet behave unexpectedly wherever validation is bypassed (CLI tools, direct parser tests). **Case normalization and whitespace trimming are different decisions** — each must be individually intentional and documented; neither implies the other.

### 9. Closed-hierarchy consumer audit
When adding a new variant to a closed set (a subtype in a sealed hierarchy, an enum member, a discriminated-union case, a message kind), grep for **every consumer of the base type** and verify each handles the new variant or fails explicitly ("not implemented" beats silent fall-through). Typical consumer sites: serializer/polymorphism registration, parser/projection switch, validation schema (`oneOf`/enum list), dispatch handler, and a round-trip test fixture exercising the new variant end to end.

### 10. Collision handling on keyed registries and derived labels
Additions to any keyed table/registry must define collision semantics — silent first-match-wins or last-write-overwrite is a bug factory. Derived display labels get the full collision matrix: exact duplicates, compacted forms, shortened qualifiers, authored names colliding with derived fallbacks — checked on **every surface that renders the label**, not just the one the diff touches.

### 11. Presentation-surface inventory
When output/rendering behavior changes, list every surface that displays that data (live view, badges, history, exports, print/report styling, …) and confirm each was updated **or explicitly waived in the change description**. Silence on a surface is a miss, not a waiver.

### 12. Cross-system identifier mappings verified at the source
IDs that map across systems (command id ↔ parameter id, wire tag ↔ register, API code ↔ enum) are not always equal and mismatches are silent — the write lands in the wrong place and nothing errors. Verify every new mapping against the authoritative upstream source and cite it in a comment at the mapping site.

### 13. Boundary values
For every input: numeric at 0, minimum, maximum, just past the range; strings blank, whitespace-only, and format-level null markers (e.g. YAML `~`); collections empty, single-element, and at the expected maximum.

### 14. Ordering/monotonicity assumptions
Code that drains a queue from the head or merges by timestamp assumes monotone arrival. Ask: what happens when an item arrives out of order — does the loop terminate, spin, or over-drain?

### 15. Cancellation and teardown races
What happens if cancellation fires between a parallel operation completing and its results being evaluated — surfaced cleanly or swallowed? What happens if disposal/teardown runs while a background loop is mid-iteration?

### 16. Domain-model suitability and boundary hygiene
Closed hierarchies must be well-bounded — consumer pattern-matching should feel natural, not force awkward default arms. Types carry real invariants: a no-invariant string wrapper is over-typed; an enum prematurely locking a concept that should stay string-valued is too. Nothing crosses a layer boundary that shouldn't: parser/DTO types and third-party attributes stay out of the domain; transport types stay off public service APIs.

### Long-tail classes — sweep before the first review
Classes 17–21 come from edge-fmt #534 and #537 (2026-10), where Copilot ran 6 and 11 passes and five passes *after* a clean one each found one or two real items. Each class is checkable from the diff in pass 1, so an implementer sweeps them before review (devit Phase 3.5 produces one artifact per class and passes the set to reviewers inside the story via `--story-file`). A reviewer who receives that `## Pre-review sweep` inventory verifies it against the diff and flags what it omits; a reviewer who does not receive one applies the classes to the diff directly. The absence of an inventory is never itself a finding — a reviewer's prompt may not carry the PR body or story artifacts at all.

### 17. Sibling surfaces swept (surface inventory for gates)
A new gate, guard, hint, validation or message applied to one command, path or handler must be applied to — or explicitly waived for — every sibling surface of the same shape (`start` → `pull`, `status`, `seed`, `stop`; one route → every route with that shape). Write the inventory: gate → siblings → applied / waived (why). This is class 11 generalized from rendering surfaces to behavior surfaces.

### 18. Input domain enumerated once
Every external value the change reads — URL, env var, path, CLI flag, tool output, config key — gets a written table of the accepted domain and the rejected cases, with one test per rejected row: empty, whitespace-only, case variants, bare delimiters, credentials / query / fragment in a URL, bad port, malformed authority, loopback spellings, IPv6 bracketing, scheme, path prefix. Validation that grows one rejected case per review pass is the failure mode; the table written up front is the fix. A reviewer who sees validation with no stated domain names the missing rows in one finding.

### 19. External-tool contracts probed, not assumed
Code that parses another tool's or service's output — Docker labels and `<no value>` sentinels, `host-gateway-ip` vs `host-gateway-ips`, IPv6 bracketing, image names, a CLI that needs a checkout — is written against **captured real output**: run the real command once in each state that matters (present / missing / error) and store the output as a test fixture. A contract inferred from memory is a finding even when it turns out to be right, because nothing proves it.

### 20. Upstream drift checked at design time
For every repo or service the story depends on, fetch its current `main` and diff the contract the change touches — routes, auth requirements, schema, env names — against what the story assumed, before implementing. A dependency that moved before the branch was cut (routes under `/api/v1`, auth now required) is a design change to raise with the human, not a correctness finding to absorb in a late pass.

### 21. Platform / network matrix written and tested
When behavior varies by viewpoint or platform — host view vs container view, Linux vs macOS, loopback vs gateway vs daemon override — write the full matrix before review, with the expected value and the test that proves it in every cell. Each unwritten cell surfaces later as its own finding; name the empty cells in one finding instead.

## Universal smells

Call out clear violations with `file:line` + symptom + fix; don't dogmatically demand changes. Apply in your stack's idiom:

- **Concurrency:** fire-and-forget async whose failures vanish (`async void` and kin); sync-over-async on a request path (thread starvation); offloading sync work to a pool for no throughput gain.
- **Resource cleanup:** anything acquired (handles, connections, locks) not released on all paths including errors — pair acquisition with scoped disposal; an owner storing a disposable becomes disposable itself.
- **Error handling:** empty/swallowing catches; catch-everything without a filter (hides cancellation); rethrows that reset the stack or drop the cause; generic exception types for domain failures; exceptions as flow control — reserve `try/catch` for external-library boundaries, use result types for expected failures where that's the convention.
- **Error attribution:** errors carry a precise, structured location (line/column, JSON-pointer or path), not just a message — "invalid value" without a locus forces the user to bisect.
- **Boundary parsing:** prefer non-throwing Try-style parse APIs at input boundaries (`Uri.TryCreate` over a throwing constructor, `TryParse` over `Parse`) — a throwing parse turns expected bad input into exception flow control.
- **Locale/culture:** every parse/format of numbers, dates, and URIs pinned to an invariant or explicit culture — implicit culture is a latent bug that ships fine and breaks abroad.
- **Nullability escape hatches:** nullability annotations reviewed for correctness; every null-forgiving assertion (`!` and equivalents) needs a justification or a refactor that removes it.
- **Type design:** primitive obsession (raw string/int for IDs, money, paths where a small wrapper carries the invariant); fat constructors (~10+ deps ⇒ split); manual state where the language has a declarative form.
- **Public surface docs:** public library APIs get doc comments — coverage, not only freshness (that's class 2).
- **Readability:** pipelines/chains too long to set a breakpoint in — break into named intermediates; conditional nesting 3+ deep — early returns or extraction; stringly-typed states that should be an enum/const; materializing collections inside loops.
- **Hidden allocations (hot paths only):** string concatenation in tight loops; boxing via untyped variadic logging — use structured templates; per-call closures in hot pipelines.
- **Structure (Fowler baseline, *Refactoring* ch.3 — always judgement calls; label them "possible X", never a hard violation):** each reads *what it is* → *how to fix*.
  - *Mysterious Name* — a function, variable, or type whose name doesn't reveal what it does or holds → rename; if no honest name comes, the design is murky.
  - *Feature Envy* — a method that reaches into another object's data more than its own → move it onto the data it envies.
  - *Data Clumps* — the same few fields or params keep travelling together (a type wanting to be born) → bundle them into one type, pass that.
  - *Repeated Switches* — the same `switch`/`if`-cascade on the same type recurs across the change → polymorphism, or one map both sites share.
  - *Shotgun Surgery* — one logical change forces scattered edits across many files in the diff → gather what changes together into one module.
  - *Divergent Change* — one file or module is edited for several unrelated reasons → split so each module changes for one reason.
  - *Speculative Generality* — abstraction, parameters, or hooks added for needs the spec doesn't have → delete it; inline back until a real need shows.
  - *Message Chains* — long `a.b().c().d()` navigation the caller shouldn't depend on → hide the walk behind one method on the first object.
  - *Middle Man* — a class or function that mostly just delegates onward → cut it, call the real target directly.
  - *Refused Bequest* — a subclass or implementer that ignores or overrides most of what it inherits → drop the inheritance, use composition.
  - (*Duplicated Code* and *Primitive Obsession* are already covered above under DRY and Type design.)

## Tests

- **Happy path:** assert every field/section of the produced value, not just "not null".
- **Error paths:** one test per stable, documented failure mode — missing, blank, out-of-range, and invalid-value are *four different tests*, not two.
- **Fixtures:** a "should fail with X" fixture triggers exactly **one** failure. When mutating a fixture to induce a failure, target the minimal unique surrounding context — a broad string replace that matches two places makes the test pass for the wrong reason.
- **Test helpers** (deep-equality, parsing, builders) get their own edge-case tests: cycles, ordering, null vs. empty.
- **No coupling to implementation details:** exact message strings, private state, internals-only types.
- **Test the wire, not the internals:** a set-then-read verification must exercise the real protocol path (send the packet, decode the response bytes) — reading internal state directly passes even when the read path is broken.
- **New-path scenario coverage:** every new dispatch path (new command type, parameter, route) is exercised by at least one scenario/integration test — or its omission is noted as intentional in the change description.

## Spec conformance — did it do what was asked?

Correctness and standards say nothing about whether the change implements the *right* thing. Every structured review carries a separate spec pass, reported under its own heading and **never reranked** against correctness/standards findings — a change can follow every standard and implement the wrong thing, or do exactly what was asked while breaking conventions. Reporting the axes side by side stops one from masking the other.

- **Find the spec**, in this order: issue/ticket references in the branch name and commit messages (resolved via the repo's tracker binding — see *Project-specific bindings*); a path the requester supplied; a spec or plan file under `docs/`, `specs/`, or the repo's scratch dir matching the branch or feature; otherwise ask. If there is genuinely none, report "no spec available" — that is an outcome, not a skipped step.
- **Report three things**, quoting the spec line for each: (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for — scope creep, distinguished from necessary enabling work; (c) requirements that look implemented but whose implementation looks wrong.
- Design decisions the spec already argued through are settled — the spec pass checks that they were *implemented as written*, not whether they were right (a correctness defect still counts, on the correctness axis).

## Review conduct (short form)

- **Pass order:** correctness first, then the defect-class sweep, then style; spec conformance runs isolated from those (parallel subagent where the harness has them, otherwise its own sequential pass) and is reported under its own heading, not reranked. Every suspected defect needs a **concrete failure scenario** (inputs/state → wrong output); no "might be an issue".
- **Skip anything tooling already enforces** (formatter, linter, analyzer, type checker) — a review finding on a machine-checked rule is noise.
- **Severity heuristic:** *would a senior engineer block the PR on this?* Yes → Critical/Important; no → Minor. Correctness > cross-cutting consistency > style.
- **Don't** rewrite code, re-litigate decisions the spec already argued through (absent a correctness issue), flag pre-existing issues outside the diff (one-line them as out-of-scope), pad the report, or over-find to justify the review — "ready to merge with [short list]" is a valid, valuable report.
- Full protocol and report format: [references/review-checklist.md](references/review-checklist.md).

## Project-specific bindings

This file is deliberately project-agnostic. Concrete bindings (file paths, ticket references, service names, hardware-safety rollback commands) belong in each repo's own standards file — e.g. a repo's `code-review/AGENTS.md` binding class 9 to its own parser/schema/handler files and class 12 to its device parameter tables. The spec-source lookup is a binding too: how a ticket id found in a branch or commit resolves to a fetched issue (tracker CLI or MCP tool, id pattern) lives in the repo file, never here. Both live in the cork repo: this skill (`skills/coding-standards/`) is the canonical, fuller treatment; `standards/AGENTS.md` is the condensed copy cork injects into blind reviewer models. Every defect class and material rule added here gets a condensed line there in the same change; stack-specific elaboration and the long-form review protocol stay here.

## Deliberate exclusions

Intentionally out of scope (not oversights): harness/workflow mechanics (review orchestration, session bootstrap, PR/commit/branch conventions) — they belong to the review pipeline (cork/devit) and repo files; and repo- or language-specific conventions (target frameworks, namespaces, toolchain choices) — they belong in each project's standards file, layered on top of this one.
