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

## [0.16.0] — 2026-09-30

### Added
- `responses_effort` config field (`low`/`medium`/`high`, default `medium`) for Responses
  API reviews and probes. Document a hybrid high-effort Copilot + Claude Enterprise setup,
  including a Pi lane on `pi/openai-codex/gpt-6-sol` with `--thinking high` via `extra_args`.

### Changed
- Setup chooses review providers before requesting credentials, so subscription-backed
  harness lanes do not require unused API credentials.
- The Pi lane's prompt travels on stdin (`--print`) instead of as an argument after `--`, so
  only its `--system-prompt` standards argument is subject to the 128 KiB per-argument limit;
  the argv byte-budget repack now applies to OpenCode alone. The lane keeps 0.13.0's live
  `pi auth check … --no-refresh` preflight probe and `<provider>/<model>` ref validation.

### Fixed
- Preserve Responses API non-completion diagnostics: skip explicitly incomplete, failed,
  cancelled, pending, or unknown-state reviews without repeating the same request or
  accepting partial findings as complete. Missing or malformed diagnostic containers use
  the unknown-reason fallback. Only missing/null/empty-string statuses retain compatibility
  handling; other falsy values cannot bypass the completion guard. HTTP-status-based
  availability probes retain their existing handling.
- Correct remaining auth-probe diagnostics: preserve scratch-directory failures and avoid
  presenting the Pi provider as the reason for malformed or contradictory auth output.
- Quote skill descriptions so YAML frontmatter parsers accept embedded colons.
- Route GPT-6 models, including Sol and Astra, through `/responses` rather than unsupported
  chat completions; share the same response extraction as GPT-5 and Codex.

## [0.15.0] — 2026-09-30

### Added
- **`cork-cross-review` skill** — top-level, cross-vendor PR verification driven from a Claude
  Code session acting as tech lead. Fetches the PR diff + acceptance contract, creates a detached
  scratch worktree at the PR head, runs the repo's deterministic gates there, fans the review out
  in parallel to independent lanes from vendors other than the author's (harness reviewers
  `codex/…`, `claude/…`, `opencode/…`, `pi/…` running read-only against the scratch tree —
  `claude`/`opencode` can read it, `codex`/`pi` are prompt-only — plus Copilot API models),
  slices large PRs by concern, uses `pi`/GLM as the tie-breaker between disagreeing vendors,
  consolidates into one verdict with a `Reviewer | Vendor | Model | Slice` roster and a
  baseline-compared tamper check on the scratch tree, routes blocking findings back to the author
  and re-reviews after each fix round with the previous blockers and the delta called out in the
  story (review-only mode always sends the full base…HEAD diff). The human merges. Builds on the `claude`/`codex` harness lanes
  (0.11.0), `auth status` (0.10.0) and the `opencode`/`pi` harness lanes with live auth probes
  (0.13.0); the herdr mapping is documented as a manual path until a native transport lands.
- `install.sh` installs the new skill; `skills/README.md` documents the invocation phrase.

## [0.13.0] — 2026-09-29

### Added
- **OpenCode and Pi harness reviewers** — opt-in `opencode/<provider/model>` and
  `pi/<provider/model>` lanes run under cork-enforced isolation — OpenCode through immutable
  `OPENCODE_PERMISSION` denies (its stock `plan` agent is not read-only on its own) and Pi
  prompt-only with no tools — with ephemeral/no-session operation and argument-based prompts.
  Pi auth checks include `--no-refresh`, so preflight never writes refreshed credentials.
- **Live harness auth probes** — enabled harness lanes now check CLI login state during
  preflight and report `ok`, `not_logged_in`, `missing_binary`, `timeout`, or `error`, with the
  lane-specific login command. Preflight continues past a full selection to report every enabled
  harness, marking live lanes that were not selected because the count was reached.
- **Immutable OpenCode isolation** — through `OPENCODE_PERMISSION`, cork denies `bash`; `edit`
  (which governs write and patch tools); `task`; `webfetch`; `websearch`; `external_directory`;
  and the experimental `lsp` tool (with the `OPENCODE_EXPERIMENTAL*` switches that enable it
  cleared). External skill discovery and Claude Code compatibility are disabled so a branch
  cannot inject instructions through `.claude/skills`, `.agents/skills` or `CLAUDE.md`. It also
  disables branch-controlled OpenCode project configuration and
  isolates the global one (`XDG_CONFIG_HOME` → a fresh cork-owned scratch dir per run, deleted
  afterwards), so your interactive MCP servers and plugins are not loaded into the reviewer;
  login and the models cache are unaffected. The reviewer's session goes to a throwaway
  `OPENCODE_DB` in that scratch dir and repo snapshots are off (`snapshot: false`), so a review
  leaves no session or snapshot in `~/.local/share/opencode`. Session sharing is refused at
  both levels (`share: disabled` in the enforced config, inherited `OPENCODE_AUTO_SHARE`
  cleared, `OPENCODE_DISABLE_SHARE=1`), every inherited `OPENCODE_EXPERIMENTAL*` /
  `OPENCODE_ENABLE_*` feature switch is cleared, and auto-update is off. The legacy global directory
  `~/.opencode/` is loaded in full (config, agents, custom tools, plugins) regardless of
  `XDG_CONFIG_HOME`, so the lane (and its auth probe) refuses to run while it holds anything
  beyond OpenCode's own install artifacts (`bin/`, `node_modules/`, `package.json`, lockfiles,
  `.gitignore`), naming the entries and pointing at `~/.config/opencode/`.
  Because OpenCode still executes a project's `.opencode/{plugin,plugins}/*.{ts,js}` despite
  those switches (anomalyco/opencode#49836), the lane refuses to run when either directory
  exists in the tree under review or any directory above it, and it is reported as a skipped
  reviewer. The auth probe never sees the target repo — it runs from cork's own state dir — so
  it applies the same scan to that directory's ancestors (and the legacy `~/.opencode/` check)
  before launching the CLI.
  Pi runs prompt-only — `--no-tools` (its `read`/`find` accept absolute paths, so a read
  allowlist cannot confine it to the repo), no extensions/skills/templates/themes/context files,
  ambient `APPEND_SYSTEM.md` suppressed — and ignores project-local `.pi/` resources with
  `--no-approve`.

### Fixed
- The OpenCode lane's config-home isolation no longer breaks on the second run: OpenCode
  scaffolds a stub `opencode.jsonc` into `$XDG_CONFIG_HOME/opencode/` on every start, which
  tripped cork's "must be empty" check on the next review (or auth probe). Each run now gets a
  fresh scratch directory that is removed when the CLI exits.
- An unwritable or file-occupied cork state dir makes a harness auth probe report `error` with
  the cause instead of aborting preflight with a `PermissionError`/`FileExistsError` traceback.
- A failed harness auth probe with no structured reason (malformed Pi JSON, a nonzero OpenCode
  `auth list`) reports an empty detail instead of presenting the model's provider as the cause
  (`unavailable (error: glm-internal)`); structured JSON reasons are still preserved.
- OpenCode's auth probe is provider-aware: it requires a listed credential — stored or
  environment-backed (`OpenAI OPENAI_API_KEY`) — for the model's own provider instead of any
  nonzero credential count, so an Anthropic login no longer makes a `github-copilot/…` lane
  look live. Display names resolve to exact provider ids via OpenCode's models.dev cache
  (`Vertex` → `google-vertex`, distinct from `google`).
- `<provider>/<model>` refs for OpenCode/Pi lanes must have both parts non-empty (`/model` and
  `provider/` are rejected at config-load, not at invocation).
- Pi's auth JSON is parsed from stdout only, so a warning on stderr no longer turns a valid
  `{"status":"ready"}` into an `error` verdict.
- Harness lanes refuse an argv element over 128 KiB (Linux `MAX_ARG_STRLEN`) up front — an
  OpenCode/Pi prompt or a `--system-prompt` standards layer that large is skipped with an
  explicit size message instead of an `Argument list too long` error.

## [0.12.0] — 2026-09-29

### Added
- **One cork login can authenticate the local Codex reviewer lane.** `auth print-token`
  exposes the resolved, automatically refreshed Copilot credential as exact plain text or
  structured JSON for Codex's command-backed provider auth. The README documents the verified
  Codex 0.146 custom Responses provider, static Copilot headers, refresh interval, and lane
  selection, including the unsupported-integration and subprocess-pipe security caveats.

### Fixed
- The Codex-lane Copilot passthrough is configured entirely through `-c` overrides in
  `providers.codex.extra_args`. The lane passes `--ignore-user-config`, so a provider defined
  only in `~/.codex/config.toml` was never loaded (`Model provider \`copilot\` not found`);
  the README example now carries the whole provider + auth-command definition and a test
  guards that it stays valid config and reaches Codex ahead of the isolation flags.

## [0.11.0] — 2026-09-17

### Added
- **Harness reviewers** — `--review-model claude/<m>` and `codex/<m>` run a locally installed
  coding-agent CLI as an independent, read-only blind reviewer, with the same review inputs
  (standards, story, files, diff), stdout findings, rotation, preflight and consolidation as
  API models. New
  `HARNESSES` table beside `PROVIDER_BASE` (adding a lane is data-only); `providers.claude` /
  `providers.codex` config keys (`enabled` default false, optional `bin`, `extra_args`,
  `timeout`); `CORK_CLAUDE_BIN` / `CORK_CODEX_BIN` env overrides. `preflight` treats
  "binary present" (on PATH, or at its configured absolute path) as the credential and never spends a turn probing. A harness that exits
  non-zero, times out or prints nothing yields the existing `— skipped]` sentinel (one attempt,
  no retry). `review()` / `cmd_review` now take `repo` so the harness runs with `cwd=repo`.
  Read-only per lane: `claude --safe-mode --restricted` (no code-running tools, repo-confined);
  `codex -s read-only` plus its shell/exec tools and user MCP config disabled, so codex reviews
  from the prompt alone.
  `opencode` and `pi` lanes follow in a separate PR.

### Fixed
- Harness config validation: a `timeout` integer too large for a C double now fails with the
  documented one-line config error instead of an `OverflowError`; a relative `bin` path is
  rejected at config-load (preflight resolved it from cork's cwd, the review from the target
  repo, so `./tools/codex` passed preflight and failed at review time); `~` in `bin` is expanded.
- A harness argument containing a NUL byte (e.g. from a branch-controlled standards file passed
  via `--system-prompt`) is now a skipped reviewer, not a `ValueError` that aborts the review.

## [0.10.0] — 2026-09-17

### Added
- **`auth status [--json]` makes Copilot credential ownership visible.** It reports the
  resolved source and path, expiry, refreshability, and one cheap API probe, exiting nonzero
  when no credential resolves or the probe fails.

### Changed
- Preflight now identifies the credential source before model results. Environment overrides
  are informational; warnings identify silently borrowed opencode auth or a non-refreshable
  cork file. Missing-token and 401/403 failures name the relevant source and point to the exact
  `login` command; expired token-only cork files no longer count as provider availability.
- `cork-setup` now gates setup on structured auth status and prompts for cork-owned,
  refreshable credentials instead of inferring token ownership from successful model probes.

### Fixed
- `auth status` reports a rejected token refresh as `probe.reason == "refresh_failed"` (the
  JSON is still emitted before the exit-1) instead of exiting before printing anything, and
  its 401/403 guidance names the winning source (env override, token-only file) like preflight.
- A connect-phase timeout, which `urlopen` wraps as `URLError`, is classified `timeout`, not
  `connection`. A non-numeric `expires_at` in `auth.json` fails as a malformed file instead of
  a `TypeError` traceback. Expired-token guidance says to re-login (which replaces only the
  Copilot fields) rather than delete the shared auth file.

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
- `install.sh` now replaces each installed skill directory instead of merging into it, stages each skill in a temp dir, keeps or recovers a rollback copy until the new one is in place (with a brief reader-visible gap during the rename swap), serializes installs with a lock, sweeps stale staging/rollback dirs, and refuses overlapping, dangling-symlink, or `..` destinations before creating them.

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
