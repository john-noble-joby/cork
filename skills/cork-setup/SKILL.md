---
name: cork-setup
description: Use when the user says "set up cork", "cork setup", "configure cork", or is getting cork working for the first time — guided, interactive setup of the Copilot token, review models, the pause-between-reviews preference, the status line, and required MCP connections.
---

# cork-setup — guided setup

**Version:** 0.7.0 — keep in sync with the repo `VERSION` file (`install.sh` checks this).

Walk the user through getting cork working. Resolve `CORK_HOME` (default `~/dev/cork`).
Do the steps in order; confirm each before moving on.

```bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
```

## 1. Copilot token
Check whether a token resolves: `python3 "$CORK_HOME/orchestrate.py" preflight`.
- If it lists models → a token is present; continue.
- If it fails with an auth error / "no token" → the user must mint one. `login` runs GitHub's
  **device-authorization flow** (no secret pasted): it prints a verification URL + a user
  code, the user approves in the browser, and it polls and writes the token to
  `~/.config/cork/auth.json` (chmod 600).
  **The user runs `login`, not you** — it blocks ~15 min polling and the device code must
  stream to them live, so do NOT run it via your own tool calls. Tell the user to run it in
  the Claude Code prompt with the `!` prefix (runs in-session, output shows inline) or in a
  terminal:

  `! python3 "$CORK_HOME/orchestrate.py" login`

  Wait for them to confirm they've approved in the browser, then re-run `preflight` yourself
  to confirm a token now resolves.

## 2. Review models
If `~/.config/cork/config.json` doesn't exist, run `python3 "$CORK_HOME/orchestrate.py" config init`.
Run `preflight` and show the selected `provider/model` list. Offer to edit `rotation`/`count`
in the config if the user wants different/more models.

## 3. Pause-between-reviews preference
Ask: **"Pause between reviews so you can see each model's findings and choose what to apply?
(recommended — default yes)."** Persist it:
`python3 "$CORK_HOME/orchestrate.py" config set interactive_review true`  (or `false`).

## 3b. Default standards
Ask: **"Use cork's built-in coding & review standards as a baseline for all repos?
(recommended — default yes; a repo can opt out with `standards init --opt-out`)."**
Persist: `python3 "$CORK_HOME/orchestrate.py" config set default_standards true` (or `false`).
Mention: per-repo, `standards init` scaffolds a project file that extends the default.

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
Print a checklist: token ✓/✗, models selected, interactive_review on/off, status line
enabled/not, Linear ✓/✗, mem0 ✓/✗. If `settings.json` changed, tell them to restart.

## Secrets
Only the Copilot token is obtained here, via `login` (device flow — nothing pasted). Do
**not** ask the user to paste an OpenAI/Anthropic API key into the chat; if they want those
providers, tell them to set `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` or add the key to
`~/.config/cork/auth.json` themselves.
