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

## Notes

- `cork` requires an authenticated opencode GitHub Copilot token at
  `~/.local/share/opencode/auth.json` for the `--review-model` calls. A 401 means
  the token expired — re-auth opencode with GitHub Copilot. (This is separate from
  the `gh` CLI auth that `copilot-review-loop` uses for GitHub's hosted PR reviewer.)
