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
Check whether a token resolves: `python "$CORK_HOME/orchestrate.py" preflight`.
- If it lists models → a token is present; continue.
- If it fails with an auth error / "no token" → run `python "$CORK_HOME/orchestrate.py" login`
  and have the user approve the GitHub device code in their browser. It writes
  `~/.config/cork/auth.json` (chmod 600). Re-run `preflight` to confirm.

## 2. Review models
If `~/.config/cork/config.json` doesn't exist, run `python "$CORK_HOME/orchestrate.py" config init`.
Run `preflight` and show the selected `provider/model` list. Offer to edit `rotation`/`count`
in the config if the user wants different/more models.

## 3. Pause-between-reviews preference
Ask: **"Pause between reviews so you can see each model's findings and choose what to apply?
(recommended — default yes)."** Persist it:
`python "$CORK_HOME/orchestrate.py" config set interactive_review true`  (or `false`).

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
