---
name: cork-setup
description: "Use when the user says \"set up cork\", \"cork setup\", \"configure cork\", or is getting cork working for the first time — guided, interactive setup of review providers and their authentication, models, review preferences, the status line, and required MCP connections."
---

# cork-setup — guided setup

**Version:** 0.17.0 — keep in sync with the repo `VERSION` file (`install.sh` checks this).

Walk the user through getting cork working. Resolve `CORK_HOME` (default `~/dev/cork`).
Do the steps in order; confirm each before moving on.

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
```

## 1. Choose review providers and models (before authentication)
Run `python3 "$CORK_HOME/orchestrate.py" config init` (leaves an existing file untouched),
then inspect `config show`. Respect `CORK_CONFIG_FILE` when set.
Ask which providers/models the user wants, offering the existing rotation as the default.
After confirmation, update `rotation`, `count`, and `providers.<name>.enabled`, preserving
unrelated settings. For Claude-only reviews, enable `claude`, disable `copilot`, and remove
Copilot entries from the rotation. Do not run `preflight` or require any token before
this choice. Never switch providers without approval.

## 2. Authenticate only the chosen lanes, then preflight
Authenticate the chosen lanes below, then run `python3 "$CORK_HOME/orchestrate.py" preflight`
on the approved rotation and show the selected models. Diagnose unavailable/skipped requested lanes individually; a successful
preflight with fewer lanes is not proof that every requested provider is authenticated.
- **Claude harness:** uses Claude Code's login, not a Copilot token. Preflight probes login
  state live (`claude: live (…)` / `logged-out — run claude auth login`) but not model access.
  Verify the intended subscription/account with the configured binary's
  `--safe-mode --restricted auth status` from the shell that will run the reviews.
  If logged out, ask the user to run `claude auth login` using that binary. Never fall back
  to an API lane or ask for Copilot credentials to fix Claude authentication.
- **Pi harness:** use `providers.pi.enabled: true` and qualified model IDs such as
  `openai-codex/gpt-6-sol` (CLI: `--review-model pi/openai-codex/gpt-6-sol`). Check the
  configured Pi binary's `auth check --provider openai-codex --json --no-refresh` without credential
  printing; if needed, have the user `/login` in Pi. Preflight runs that probe live
  (`--no-refresh`, so it never rewrites credentials) and reports `live` / `logged-out` /
  `unavailable (<reason>)`, but it does not validate model access: smoke-test requested
  models with Cork's no-tools/resource-disabled harness flags.
  Pi keeps its own credentials; neither Codex login nor an OpenAI API key is required.
- **OpenAI/Anthropic API lanes:** require their own credentials; follow **Secrets** below.
- **Copilot:** only when an enabled Copilot lane is in the chosen rotation, run
  `python3 "$CORK_HOME/orchestrate.py" auth status --json` and parse its JSON stdout
  (it intentionally exits 1 when no token resolves or the probe fails).
  - If `source == "none"` or `probe.reason in ("missing", "expired", "refresh_failed", "auth")`,
    the user must run `login`. Never tell them to delete `auth.json` — it may also hold
    `openai`/`anthropic` keys; `login` replaces only Copilot fields and keeps the rest.
    When `source == "env"`, unset `CORK_COPILOT_TOKEN` before login so it cannot keep winning.
  - If `probe.status == "fail"` for any other reason, relay stderr recovery guidance and
    stop; model, integrator, timeout, or connection failures are not fixed by logging in.
  - Once the probe succeeds, continue only when `source == "cork"` and `refreshable == true`.
    Otherwise prompt for `login`: opencode fallback and token-only/legacy cork files are
    not cork-owned refreshable auth. Unset any `CORK_COPILOT_TOKEN` override first.
  `login` runs GitHub's
  **device-authorization flow** (no secret pasted): it prints a verification URL + a user
  code, the user approves in the browser, and it polls and writes the token to
  `~/.config/cork/auth.json` (chmod 600).
  **The user runs `login`, not you** — it blocks ~15 min polling and the device code must
  stream to them live, so do NOT run it via your own tool calls. Tell the user to run it in
  the Claude Code prompt with the `!` prefix (runs in-session, output shows inline) or in a
  terminal:

  `! python3 "$CORK_HOME/orchestrate.py" login`

  Wait for browser approval, then re-run `auth status --json` and require `source == "cork"`,
  `refreshable == true`, and `probe.status == "ok"`; use preflight to verify the chosen models.
  Skip this entire login flow when no Copilot lane was chosen; Claude-only setup needs no
  Copilot credentials.

## 3. Pause-between-reviews preference
Ask: **"Pause between reviews so you can see each model's findings and choose what to apply?
(recommended — default yes)."** Persist it:
`python3 "$CORK_HOME/orchestrate.py" config set interactive_review true`  (or `false`).

## 3b. Default standards
Ask: **"Use cork's built-in coding & review standards as a baseline for all repos?
(recommended — default yes; a repo can opt out with `standards init --opt-out`)."**
Persist: `python3 "$CORK_HOME/orchestrate.py" config set default_standards true` (or `false`).
Mention: per-repo, `standards init` scaffolds a project file that extends the default.

## 3c. Review effort (optional)
Offer effort configuration: `responses_effort` in `config.json` accepts `low`, `medium`
(default), or `high` for Responses API lanes. Claude Code uses its own
`providers.claude.extra_args`, e.g. `["--effort", "high"]`. Edit these JSON fields directly;
`config set` accepts only boolean preferences. For Pi, use `providers.pi.extra_args`:
`["--thinking", "high"]`. Preserve unrelated settings and extra args.

## 4. Status line (optional)
If `~/.claude/settings.json` has no `statusLine`, offer to add it (so a session shows its
active ticket/branch):
`{ "statusLine": { "type": "command", "command": "~/.claude/statusline.py" } }`
Edit the file additively (don't disturb other keys). Note a Claude Code restart is needed.

## 5. MCP connections
Confirm the user has **Linear** (devit fetches stories; cork/devit file follow-ups) and
**mem0** (codebase context) connected as MCP servers in Claude Code. If not, tell them to
add them in Claude Code's MCP settings — this skill can't configure MCP for them.

## 6. Summary
Print a checklist: authentication per chosen provider (Copilot token N/A when unused),
models selected, interactive_review on/off, configured API/harness effort, status line enabled/not,
Linear ✓/✗, mem0 ✓/✗. If `settings.json` changed, tell them to restart.

## Secrets
Copilot tokens, when needed, are obtained via `login` (device flow — nothing pasted);
Claude harness credentials stay in Claude Code's own login store. Do
**not** ask the user to paste an OpenAI/Anthropic API key into the chat; if they want those
providers, tell them to set `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` or add the key to
`~/.config/cork/auth.json` themselves.
