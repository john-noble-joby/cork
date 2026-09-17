# Changelog

All notable changes to cork are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Versioning

cork uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) —
`MAJOR.MINOR.PATCH`:

- **MAJOR** — incompatible changes to the config schema, CLI, or the
  orchestrate.py↔skill contract.
- **MINOR** — new backward-compatible capability (a new subcommand, skill, or
  config option that defaults to today's behavior).
- **PATCH** — backward-compatible bug fixes and doc/prompt corrections.

The **single source of truth is the `VERSION` file**. Every skill's
`**Version:**` stamp and `orchestrate.py --version` must match it — `install.sh`
warns on drift. Bump `VERSION` and every skill stamp together in the same
change, and add a section here.

## [Unreleased]

## [0.9.0] — 2026-09-17

### Added
- **`coding-standards` skill now lives in this repo** (`skills/coding-standards/`) and is
  installed by `install.sh` alongside the other four — cork is the source of truth for the
  shared coding & review rubric that Claude Code, Codex (via symlink), and Pi (via its
  `skills` path) all load. Previously it existed only as a loose copy in `~/.claude/skills`.
- **Spec-conformance review axis.** Both the skill and `standards/AGENTS.md` now run a
  second, separately-reported axis — does the diff do what the story asked (missing /
  partial / unrequested / implemented-wrong, quoting the spec line) — never reranked against
  correctness/standards findings. Spec-source lookup (ticket id → fetched issue) is a
  per-repo binding in `code-review/AGENTS.md`. Adapted from mattpocock/skills `code-review`.
- **Fowler structural smell baseline** (Mysterious Name, Feature Envy, Data Clumps, Repeated
  Switches, Shotgun Surgery, Divergent Change, Speculative Generality, Message Chains,
  Middle Man, Refused Bequest) as labelled judgement calls; a documented repo standard that
  endorses the pattern overrides the smell.

### Changed
- Skill review protocol: pin the base first (`git diff <base>...HEAD`, verify the ref resolves and
  the diff is non-empty before fanning out); skip anything tooling already enforces; report
  format gains `## Spec conformance`; verdict names the worst item per axis.
- cork review-only consolidated report gains a `## Spec conformance` section; the injected
  rubric's output format gains `## Promotion candidates` (the fixer prompt already expected it).
- `prompt_fix` now covers the `## Spec conformance` section (implement missing/partial
  requirements; never auto-delete unrequested behaviour). `standards/AGENTS.md` gains a
  condensed `Recurring defect classes` section so the injected rubric carries the skill's
  defect classes.
- The pipeline's self-review prompt now carries the implementer's summary as `## Story / Task`
  so the spec-conformance axis is actionable there; `standards/AGENTS.md` mirrors the skill's
  remaining universal smells and test rules.
- The spec-conformance instruction is appended to the API reviewer's system prompt on
  every path (custom instructions or fallback), so every review produces the same report
  shape.

### Fixed
- Review diffs are now merge-base (`git diff <base>...HEAD`) in `orchestrate.py` and the cork skill's self-review, and the base ref (a reachable commit) and its merge base with HEAD are validated up front in every mode (review-only, full run, seed-only) — a bad `--base-branch` fails before any implementation step runs, and a base branch that advanced after forking no longer leaks base-only changes into the review. The cork skill validates the base before provider preflight, then applies its empty-diff guard per mode: before review-only fan-out, or after full-mode implementation.
- `install.sh` now replaces each installed skill directory instead of merging into it, stages each skill in a temp dir, keeps the old copy until the new one is in place, serializes installs with a lock, sweeps stale staging/rollback dirs, and refuses overlapping or dangling-symlink destinations before creating them.

## [0.8.3] — 2026-08-03

### Fixed
- **copilot-review-loop now gates on the review verdict + suppressed comments**, not just
  inline `reviewThreads`/`totalCount`. Copilot's *Lite* effort posts findings into the review
  *body* under `### Suppressed comments (N)` with `totalCount == 0`; the loop previously read
  that as a clean pass and stopped while the PR was still `Not ready to approve`. Step 2 now
  reads `review.body` for the verdict and suppressed count; clean = verdict approves AND
  `tc == 0` AND zero suppressed.
- Auth reads (`_read_cork_auth`, opencode fallback) now catch `UnicodeDecodeError`/`OSError`
  and fail with a clear message instead of a traceback on non-UTF8/unreadable files; add the
  missing `_auth_lock` return annotation. (Surfaced by the fixed loop on PR #8's post-merge review.)

## [0.8.2] — 2026-07-31

### Fixed
- **Copilot token self-refreshes.** `login` now persists the `refresh_token` and
  `expires_at` returned by GitHub's device flow, and `_copilot_token()` exchanges
  an expired access token for a fresh one automatically (`grant_type=refresh_token`),
  rewriting the auth file. Previously `login` kept only the `access_token`, so when
  it expired (GitHub-App user-to-server tokens last ~8h) there was nothing to
  refresh with — forcing a full interactive re-login. Because cork's own auth file
  takes priority over opencode's non-expiring token, running `login` once turned
  re-auth into a once-or-twice-a-day chore; that regression is fixed and `login` is
  safe to run again (the refresh token lasts ~6 months).
- **opencode fallback reads `access`** (honouring `expires`) instead of `refresh`,
  matching how opencode maintains its token.

### Added
- `CHANGELOG.md` and a documented semantic-versioning policy (this file).

## [0.8.1] — 2026-06-30

### Fixed
- **copilot-review-loop** gates on the review's own `comments.totalCount` rather
  than an empty thread fetch. A review reaches `COMMENTED`/`APPROVED` before its
  inline comments are indexed, so an empty thread fetch at that moment used to be
  misread as a "clean pass" and stop the loop early. Settle now keys off
  thread-count stability, with a null-author guard and a paginated thread query. (#7)

## [0.8.0] — 2026-06-30

### Added
- **Shared, layered coding & review standards.** A shipped `standards/AGENTS.md`
  universal default is layered under each repo's own `code-review/AGENTS.md`, gated
  by a global `default_standards` toggle and a per-repo `code-review/.cork-standards-off`
  sentinel. New `standards status` / `standards init [--opt-out]` subcommands; the
  effective standards drive both devit's implementer and the blind review models.
- **`install.sh` offers to set `CORK_HOME`** in `~/.claude/settings.json` (additive,
  refuses to clobber a malformed file). SKILLS array sorted; FOLLOWUPS polish. (#6)

## [0.7.0] — 2026-06-29

### Added
- **Interactive `cork-setup` skill** and pause-between-reviews (`interactive_review`,
  on by default): cork and the Copilot loop pause after each reviewer so you can
  choose what to apply. (#5)
