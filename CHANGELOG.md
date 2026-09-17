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

## [0.13.0] — 2026-09-17

### Added
- **`cork-cross-review` skill** — top-level, cross-vendor PR verification driven from a Claude
  Code session acting as tech lead. Fetches the PR diff + acceptance contract, creates a detached
  scratch worktree at the PR head, runs the repo's deterministic gates there, fans the review out
  in parallel to independent lanes from vendors other than the author's (harness reviewers
  `codex/…`, `claude/…`, `opencode/…`, `pi/…` running read-only inside the scratch tree, plus
  Copilot API models), slices large PRs by concern, uses `pi`/GLM as the tie-breaker between
  disagreeing vendors, consolidates into one verdict with a `Reviewer | Vendor | Model | Slice`
  roster and a tamper check on the scratch tree, routes blocking findings back to the author and
  re-reviews only the delta. The human merges. Builds on PR #12's `claude`/`codex` harness lanes
  (0.11.0), and also depends on PR #11's `auth status` (0.10.0, merging separately) plus the `opencode`/`pi` harness lanes (0.12.0); the herdr mapping is documented as a manual path until a
  native transport lands.
- `install.sh` installs the new skill; `skills/README.md` documents the invocation phrase.
## [0.12.0] — 2026-09-17

### Added
- **OpenCode and Pi harness reviewers** — opt-in `opencode/<provider/model>` and
  `pi/<provider/model>` lanes use each CLI's read-only mode, ephemeral/no-session operation,
  and argument-based prompts. Pi auth checks include `--no-refresh`, so preflight never writes
  refreshed credentials.
- **Live harness auth probes** — enabled harness lanes now check CLI login state during
  preflight and report `ok`, `not_logged_in`, `missing_binary`, `timeout`, or `error`, with the
  lane-specific login command. Preflight continues past a full selection to report every enabled
  harness, marking live lanes that were not selected because the count was reached.
- **Immutable OpenCode isolation** — through `OPENCODE_PERMISSION`, cork denies `bash`; `edit`
  (which governs write and patch tools); `task`; `webfetch`; `websearch`; and
  `external_directory`. It also disables branch-controlled OpenCode project configuration.
  Pi ignores project-local `.pi/` resources with `--no-approve`.

## [0.11.0] — 2026-09-17

### Added
- **Harness reviewers** — `--review-model claude/<m>` and `codex/<m>` run a locally installed
  coding-agent CLI as an independent, read-only blind reviewer, with the same review inputs
  (standards, story, files, diff), stdout findings, rotation, preflight and consolidation as
  API models. New
  `HARNESSES` table beside `PROVIDER_BASE` (adding a lane is data-only); `providers.claude` /
  `providers.codex` config keys (`enabled` default false, optional `bin`, `extra_args`,
  `timeout`); `CORK_CLAUDE_BIN` / `CORK_CODEX_BIN` env overrides. `preflight` treats
  "binary on PATH" as the credential and never spends a turn probing. A harness that exits
  non-zero, times out or prints nothing yields the existing `— skipped]` sentinel (one attempt,
  no retry). `review()` / `cmd_review` now take `repo` so the harness runs with `cwd=repo`.
  `opencode` and `pi` lanes follow in a separate PR.

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
