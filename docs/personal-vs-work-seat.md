# Running cork on separate Copilot seats (personal vs work)

cork authenticates every review to GitHub Copilot, so **which seat is billed is decided by the
token cork resolves**. If you use cork for both work and personal repos, you almost certainly want
those routed to *different* Copilot subscriptions. This is supported today with no code changes —
it's purely how you point `CORK_AUTH_FILE` / `CORK_COPILOT_TOKEN`.

## How cork resolves its token (priority order)

From `orchestrate.py:_copilot_token()`:

1. **`CORK_COPILOT_TOKEN`** env var — used directly, no fallback.
2. **`CORK_AUTH_FILE`** — JSON file (default `~/.config/cork/auth.json`); `{"token": "..."}` or the
   opencode shape `{"github-copilot": {"refresh": "..."}}`.
3. **opencode's** `~/.local/share/opencode/auth.json` — legacy fallback.

The key consequence: if a personal token file is *missing*, cork silently falls through to (3), which
may be the **wrong** seat. The pattern below makes that fail *closed* instead.

## One-time setup: mint a token per seat

cork's `login` subcommand runs GitHub's device flow and writes the token to whatever `CORK_AUTH_FILE`
points at. The seat is decided by **which GitHub account approves the device code in the browser**,
not by any flag — so log into the intended account first.

```sh
# Personal seat -> its own file (approve as your PERSONAL github account):
CORK_AUTH_FILE="$HOME/.config/cork/auth.personal.json" \
  python /path/to/cork/orchestrate.py login

# Work seat stays in the default ~/.config/cork/auth.json (or its own file).
```

## Per-directory isolation with direnv (fail-closed)

Pin `CORK_AUTH_FILE` for an entire directory tree so every cork run under it uses the right seat.
Put this in an `.envrc` at the root of your personal workspace (e.g. `~/dev/personal/.envrc`) and
`direnv allow` it:

```sh
# cork -> personal Copilot seat for everything under this tree.
export CORK_AUTH_FILE="$HOME/.config/cork/auth.personal.json"

# Fail closed: if the personal token file doesn't exist yet, set a sentinel so cork errors (401)
# instead of falling back to the work seat / opencode token (resolution order above).
if [ ! -f "$CORK_AUTH_FILE" ]; then
  export CORK_COPILOT_TOKEN="NO_PERSONAL_COPILOT_TOKEN__run_cork_login_first"
fi
```

Outside that tree, nothing changes — cork uses the default `~/.config/cork/auth.json` (your work
seat). A child repo with its own `.envrc` must `source_up` to inherit this.

> The same pattern works for `gh` (`GH_CONFIG_DIR` -> an isolated `gh` profile) if you also want
> git/PR operations on the personal account. That's outside cork's scope but pairs naturally with it.

## Verify which seat is active

```sh
# Token works + shows which models your plan exposes (200 = good):
TOKEN=$(python -c "import json,os;print(json.load(open(os.path.expanduser('$CORK_AUTH_FILE')))['token'])")
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $TOKEN" https://api.githubcopilot.com/models
```

## Note: available models are per-account

The model rotation cork can actually call depends on the **authenticated account's Copilot plan**,
not just cork's integration id. The "Model availability" note in the cork skill reflects one
seat at a point in time; a personal Copilot Pro+ seat, for example, may expose newer/Opus-class
models that a given work seat does not (and vice-versa). When in doubt, hit `/models` (above) for
the seat you're on rather than trusting a hardcoded rotation.
