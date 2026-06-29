# Interactive setup skill + pause-between-reviews ("interactive review")

**Status:** design / approved, pending spec review
**Date:** 2026-06-25
**Scope:** Two cohesive additions to the cork toolkit — a conversational **setup skill**, and an **interactive-review** mode that pauses between reviews for human visibility/control. Plus a token-storage clarification (auth.json is canonical). One spec.

## Goals

1. **Easy, guided setup** — a `cork-setup` skill the session runs to get from a fresh clone to a working install: token, model config, the pause preference, the status line, and MCP checks — interactively, no manual file-poking.
2. **Pause between reviews (default on)** — in cork (full mode) and the Copilot review loop, stop after *each* reviewer, show its findings + the agent's recommendation, and let the user choose what to apply, with full visibility. Set during setup; stored in `config.json`.
3. **Tokens live in a JSON file, not env vars** — make `~/.config/cork/auth.json` the canonical, documented store for all provider tokens; env vars are overrides. Fix the README's over-emphasis on env vars.

---

## A. `cork-setup` skill

New skill `skills/cork-setup/SKILL.md`. Trigger phrases: "set up cork", "cork setup", "configure cork". Deployed by `install.sh`; the install script's final line becomes "restart Claude Code, then say **set up cork**." The active session runs the steps conversationally.

Flow:
1. **Locate** — resolve `CORK_HOME` (default `~/dev/cork`); confirm `orchestrate.py` exists.
2. **Copilot token** — check whether one resolves (run `orchestrate.py preflight`; an `auth`/401 or "no token" means none). If missing, run `python "$CORK_HOME/orchestrate.py" login` (GitHub device flow — the user approves in the browser; writes `~/.config/cork/auth.json`, chmod 600).
3. **Models** — if no `config.json`, run `config init`; run `preflight` and show the selected models. Offer to adjust `count` / `rotation`.
4. **Pause preference (feature B)** — ask: *"Pause between reviews so you can see each model's findings and choose what to apply? (recommended — default yes)."* Persist the answer to `config.json` as `interactive_review` (via `orchestrate.py config set interactive_review true|false`).
5. **Status line** — if `~/.claude/settings.json` has no `statusLine`, offer to add the block (`{ "type": "command", "command": "~/.claude/statusline.py" }`). Note a restart is needed.
6. **MCP check** — verify Linear and mem0 MCP connections exist (devit needs Linear; cork uses mem0). If absent, tell the user how to add them in Claude Code — the skill can't configure MCP for them.
7. **Summary** — print what's set (token ✓/✗, models, `interactive_review`, status line, MCP), and prompt a restart if `settings.json` changed.

**Secret handling:** only the Copilot token is obtained interactively, via `login`'s device flow (no secret pasted into the chat). For native OpenAI/Anthropic providers, the skill does **not** ask the user to paste an API key into the conversation — it tells them to set `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` or add the key to `auth.json` themselves.

---

## B. Interactive review (pause between reviews)

### Config flag
`config.json` gains a top-level **`"interactive_review": true`**, added to `DEFAULT_CONFIG` (so `config init` writes it and the no-file default has it). Skills read it with **`python orchestrate.py config get interactive_review`** (see Supporting code) → `true`/`false`; **absent → `true`** (default on). `_validate_config` already ignores unknown keys, so no validation change is needed beyond documenting it.

### Where it pauses
| Surface | Pause point | When off (today's behavior) |
|---|---|---|
| **cork full mode** (Steps 3+) | after *each model's* review is fetched, before applying anything or advancing to the next model | agent applies the valid findings itself, commits, continues |
| **copilot-review-loop** | after *each pass's* Copilot comments are fetched, before fixing/replying | agent fixes/pushes-back each comment autonomously |
| **devit** | inherits both (it invokes cork then the loop, which read the flag) | — |

Not cork **review-only** (changes nothing already) and not the **headless** pipeline (unattended by design — it ignores the flag).

### The pause: pre-pass → present → wait
At each pause, when `interactive_review` is on:
1. **Pre-pass (recommendation):** the agent reads the findings and forms its own take — which it judges valid (would fix), which it would push back on (with a reason), which are out of scope. It applies **nothing**.
2. **Present:** show the reviewer's findings *and* the agent's recommendation, grouped/numbered, so the user has full visibility.
3. **Wait** for the user to choose from this menu:
   1. **Fix all** — apply every finding, run tests, commit, continue.
   2. **Pick specific** — the user names which (e.g. "1, 3, 4"); apply those, commit, continue. Unpicked findings are left (optionally pushed back if the user says so).
   3. **Push back** — mark finding(s) won't-fix with a reason; recorded for the end-of-run pushback summary, and (in the Copilot loop) posted as the PR reply before resolving.
   4. **Proceed — no changes** — apply nothing from this reviewer and move on. **Explicitly makes zero edits/commits for this round.**

The pause gates **both** applying fixes *and* advancing to the next reviewer. After the user's choice is carried out, continue to the next model (cork) or re-request (Copilot loop), pausing again next round.

When `interactive_review` is **off**, both skills behave exactly as they do today (autonomous apply / push-back), preserving the unattended path.

---

## C. Token storage clarification

- **`~/.config/cork/auth.json` is the canonical token store** for all providers: `{"token": "<copilot>", "openai": "<key>", "anthropic": "<key>"}` (chmod 600). `login` writes `token`. Env vars (`CORK_COPILOT_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) remain **overrides**, resolved first, but are not required.
- **README fix:** lead the token story with `auth.json` + `login`; demote the env-var table to "overrides." (The resolution order in `orchestrate.py` is already auth.json-capable — this is a docs accuracy fix, no code change to resolution.)

---

## Supporting code (`orchestrate.py`)

Minimal additions:
- **`DEFAULT_CONFIG["interactive_review"] = true`**.
- **`config set <key> <value>`** subcommand — sets a top-level config key (creating `config.json` from the default if absent), so the setup skill persists `interactive_review` deterministically rather than hand-editing JSON. Coerces `true`/`false`→bool, integers→int, else string. (Pairs with the existing `config` / `config init`.)
- **`config get <key>`** subcommand — prints one resolved value (default-aware), so skills can read `interactive_review` cleanly: `python orchestrate.py config get interactive_review` → `true`/`false`.

No change to token resolution, the pipeline, or preflight.

## Files

- Create: `skills/cork-setup/SKILL.md`
- Modify: `orchestrate.py` (DEFAULT_CONFIG + `config get`/`config set`), `tests/test_config.py` (cover get/set + default)
- Modify: `skills/cork/SKILL.md`, `skills/copilot-review-loop/SKILL.md` (interactive-review pause + menu, reading the flag); `skills/devit/SKILL.md` (one-line note that it inherits)
- Modify: `install.sh` (register `cork-setup`; final message → "set up cork"), `README.md` (token clarification + point setup at the skill + document `interactive_review`), `skills/README.md` (cork-setup entry)
- Bump `VERSION` 0.6.2 → **0.7.0**; re-stamp all skills (cork, copilot-review-loop, devit, cork-setup) to 0.7.0.

## Non-goals
- No GUI; the menu is text in the session.
- The setup skill does not paste/store native API keys from the chat (secret-handling) — it points the user to env/auth.json.
- No change to headless behavior (it ignores `interactive_review`).

## Verification
- `config set`/`get` round-trip + default-when-absent (unit tests).
- Setup skill: dry-run the documented commands; confirm it writes `interactive_review` and (optionally) the statusLine block.
- Interactive-review behavior: a fresh-agent pressure test (like the devit gates) — with `interactive_review` on, the agent must present findings and WAIT, applying nothing, at each reviewer; "Proceed" makes zero edits.
- `install.sh` deploys 4 skills at 0.7.0, no drift.
