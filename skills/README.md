# Claude Code Skills

Skills that drive the orchestrator from an interactive Claude Code session.

## Install

Copy a skill into your personal skills directory (or symlink it):

```bash
cp -r skills/cork ~/.claude/skills/cork
cp -r skills/copilot-review-loop ~/.claude/skills/copilot-review-loop
```

Then invoke by phrase in any session:
- **cork** — "cork" / "run cork on this branch"
- **copilot-review-loop** — "run the copilot review loop on this branch"

## Skills

### cork
Session-driven multi-model review pipeline. The active Claude session implements and
applies fixes; `orchestrate.py --review-model MODEL` is called once per model
(gpt-4o, gemini-3.1-pro-preview, claude-opus-4.7) to fetch blind review findings
between fix passes. Each review call is stateless — the reviewer sees only the diff,
changed files, and the repo's `AGENTS.md`.

### copilot-review-loop
Iterative GitHub Copilot PR review: request review → poll → fix/push-back each comment
→ reply + resolve → re-request → repeat up to N passes, stopping when Copilot has no
comments or the max is reached. Reviewer login is `Copilot` for requesting,
`copilot-pull-request-reviewer[bot]` for filtering comments.

## Configuration

`cork` resolves the orchestrator location from the `CORK_HOME` environment variable,
defaulting to `~/dev/code-orchestrator`. If your clone lives elsewhere, set it once —
in your shell profile, or in `~/.claude/settings.json`:

```json
{ "env": { "CORK_HOME": "/path/to/code-orchestrator" } }
```

## Copilot token

`--review-model` calls the Copilot chat API and resolves its token in priority order:

1. **`CORK_COPILOT_TOKEN`** env var — used directly. Best for CI or a dedicated
   token; fully decoupled from opencode.
2. **cork's own auth file** — `CORK_AUTH_FILE` (default `~/.config/cork/auth.json`),
   JSON of either `{"token": "..."}` or `{"github-copilot": {"refresh": "..."}}`.
3. **opencode** `~/.local/share/opencode/auth.json` — legacy fallback.

### Easiest: `cork login` (device flow)

Run the built-in GitHub device-authorization flow — it mints a Copilot token and
writes `~/.config/cork/auth.json` (chmod 600) for you, with no token copying and no
dependency on opencode:

```bash
python "$CORK_HOME/orchestrate.py" login
#   Open:  https://github.com/login/device
#   Code:  FD65-A4B3
# (authorize in the browser; cork polls and writes the token automatically)
```

Re-run `login` any time the token expires. The OAuth client id is the public
Copilot one (overridable via `CORK_COPILOT_CLIENT_ID`).

### Manual alternatives

Or export `CORK_COPILOT_TOKEN`, or hand-write the auth file:

```bash
mkdir -p ~/.config/cork
echo '{"token": "<your-copilot-token>"}' > ~/.config/cork/auth.json
chmod 600 ~/.config/cork/auth.json
```

A 401 means the token expired — re-run `cork login`. This Copilot auth is entirely
separate from the `gh` CLI auth that `copilot-review-loop` uses for GitHub's hosted
PR reviewer.
