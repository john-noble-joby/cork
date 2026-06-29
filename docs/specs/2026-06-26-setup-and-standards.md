# Setup ergonomics + shared default standards rubric

**Status:** design / approved, pending spec review
**Date:** 2026-06-26
**Scope:** Three cohesive additions — (a) `install.sh` auto-sets `CORK_HOME`, (b) FOLLOWUPS polish, (c) a shared, layered "coding & review" standards rubric used by both devit's implementer agents and the blind review models. One spec.

---

## A. `install.sh` sets `CORK_HOME`

`install.sh` already detects `REPO` (its own dir) and warns if `CORK_HOME != REPO`. Make it *offer to fix it*:

- After install, if `CORK_HOME` is unset or `!= REPO`, **prompt** (interactive `read`):
  `Set CORK_HOME=<REPO> in ~/.claude/settings.json? [y/N]`
- On yes, **additively merge** `{"env": {"CORK_HOME": "<REPO>"}}` into `~/.claude/settings.json` via a Python snippet (install.sh already calls `python3`), preserving all other keys. This is the env Claude Code sessions (and thus the skills' bash) inherit.
- Print the shell-profile alternative for non-Claude-Code use: `export CORK_HOME=<REPO>`.
- Idempotent: if `settings.json` already has `env.CORK_HOME == REPO`, say so and skip.

Why `settings.json env` (not shell profile): the skills run their bash inside the Claude Code session, which reads `settings.json` `env`. That's the path that actually makes `$CORK_HOME` resolve for the skills.

## B. FOLLOWUPS polish

- `tests/test_config.py`: use `assertTrue`/`assertFalse` for booleans (currently `assertEqual(..., True/False)`).
- `install.sh`: sort the `SKILLS` array (`cork copilot-review-loop cork-setup devit` → alphabetical).
- `skills/devit/SKILL.md`: the `interactive_review` note is repeated verbatim in Phase 4 & 6 — move it once into `## Notes` and reference it with a single line in each phase.

(These clear the remaining `docs/FOLLOWUPS.md` items; update that file.)

---

## C. Shared default standards rubric (layered)

### The model
- **One shared rubric** — a generalized "coding & review standards" doc, used by **both** devit's implementer agents and the blind review models. Built by stripping the C#/.NET/edge-fmt specifics out of `~/dev/edge-fmt/code-review/AGENTS.md`, keeping the universal spine.
- **Layered effective rubric = cork's universal default + the target repo's own standards**, in that order.

### Where the default lives
- Ship it at **`$CORK_HOME/standards/AGENTS.md`** (a new `standards/` dir in the cork repo). `orchestrate.py` reads it from `CORK_HOME` (same as it runs the engine) — no deploy/copy step needed; `git pull` updates it.

### How the layers resolve (`load_agent_instructions`)
Today `load_agent_instructions(repo)` returns the target repo's first-found `code-review/AGENTS.md` / `AGENTS.md` / `.github/AGENTS.md` (or `""`). Change it to compute the **effective** rubric:

```
use_default = config.default_standards AND NOT _repo_opted_out(repo)
project_text = (first-found project standards file, as today) or ""
universal_text = read($CORK_HOME/standards/AGENTS.md) if use_default else ""

effective = universal_text + "\n\n---\n\n" + project_text   # whichever parts are non-empty
```
- If `effective` is empty (default off/opted-out AND no project file), the review models fall back to the existing built-in `REVIEW_SYSTEM`, exactly as today.
- Return `(effective_text, description)` where `description` names the active layers (e.g. `"cork default + <repo>/code-review/AGENTS.md"`).

### Opt-out — both levels (per the user)
- **Global:** `config.json` key **`default_standards`** (bool, **default `true`**), set via `cork-setup` or `config set default_standards false`. `false` = never layer cork's universal default anywhere ("I don't want your standard").
- **Per-repo sentinel:** presence of **`<repo>/code-review/.cork-standards-off`** opts that one repo out of the universal default (project file alone, if any, still applies). Travels with the repo.
- `_repo_opted_out(repo)` = sentinel file exists. Effective default applies only when global on AND not opted out.

### Per-repo detection + helpers (subcommands)
- **`standards status [repo]`** — prints the effective layers for a repo: universal default `on/off` (and why — global toggle / sentinel), project file `<path>`/none. Read-only.
- **`standards init [repo]`** — scaffolds `<repo>/code-review/AGENTS.md` from a starter template (header: *"Extends cork's universal default standards — add project-specific conventions below"* + skeleton sections: Project conventions, Project-specific checks). With `--opt-out`, instead writes the `code-review/.cork-standards-off` sentinel (with a comment explaining it). Refuses to overwrite an existing file.

### Where it surfaces (non-blocking)
- **cork Step 0** and **devit Phase 0** run `standards status`. If the repo is **unconfigured** (no project file, no sentinel, default on), print **one non-blocking line**: *"This repo has no project standards layer — using cork's universal default. Run `orchestrate.py standards init <repo>` to add project-specifics, or `--opt-out`. See README."* Then proceed. Never a gate.
- **devit Phase 3 (implement):** the implementer is told to follow the **effective standards** — cork's `$CORK_HOME/standards/AGENTS.md` plus the repo's `code-review/AGENTS.md` if present (respecting the toggles). So coding and review share one rubric.
- **Blind review models** already receive `load_agent_instructions` output as their system prompt — now the layered effective rubric, automatically.
- **`cork-setup`** gains a step: confirm the global default (`default_standards`), and explain add-to (`standards init`) / opt-out (sentinel or global off).

### The generalized rubric content (`standards/AGENTS.md`)
Keep (generalized, language-agnostic): reviewer-vs-implementer framing; reviewer values (correctness › codebase-consistent idioms › immutability/functional where the language supports it › explicit-over-implicit › DRY-without-dogma › SOLID-where-it-earns › reads-like-a-story › tests-verify-behavior); explicit DRY & SOLID checks; universal smells (async/concurrency, resource cleanup, exception handling, type/primitive-obsession, readability, hidden allocations) framed as principles "in your language's idiom"; doc/comment freshness; test strategy (behavior over mocks, error paths, boundary values); the §8 output format (Strengths/Critical/Important/Minor/Cross-cutting/Uncertain/Out-of-scope/Verdict); prioritization; the "do not" list.
Drop/parameterize: C#/.NET syntax specifics, FluentResults/records/`net10`/`Joby.*`, the multi-endpoint-dispatch and master-tables/inverter-sim sections, the csproj/global.json dependency-matrix agent, the edge-fmt-specific 8-agent fan-out. A short closing note tells projects to put their stack-specific rules in `code-review/AGENTS.md` (which layers under this).

---

## Config / files

- `orchestrate.py`: `DEFAULT_CONFIG["default_standards"] = True`; add `"default_standards"` to `_SETTABLE_KEYS` (bool-validated like `interactive_review`); rewrite `load_agent_instructions` for layering; add `_repo_opted_out`, `cmd_standards_status`, `cmd_standards_init`, and `standards` dispatch in `main()`.
- Create `standards/AGENTS.md` (the generalized rubric).
- `skills/cork/SKILL.md`, `skills/devit/SKILL.md`: surface `standards status` (non-blocking) + devit implement-phase references the effective standards; `skills/cork-setup/SKILL.md`: the default-standards confirm step.
- `install.sh`: `CORK_HOME` prompt + merge; sort SKILLS.
- `tests/test_config.py`: assertTrue/assertFalse; `tests/test_standards.py` (new): `_repo_opted_out`, layering in `load_agent_instructions` (global off / sentinel / project-only / default-only / both), `standards init` scaffold + `--opt-out` + refuse-overwrite.
- `README.md`: a plain-language **"Coding & review standards (layering)"** section; `docs/FOLLOWUPS.md`: clear the done items; `VERSION` → **0.8.0** (re-stamp all 4 skills).

## Non-goals
- Not auto-editing a repo's standards file beyond the `standards init` scaffold.
- No per-repo opt-*in* when the global toggle is off (a repo that wants standards just keeps its own `code-review/AGENTS.md`).
- The generalized rubric is a starting baseline, not exhaustive; projects extend it.

## Verification
- Unit: layering matrix in `load_agent_instructions` (global on+repo file → both; global on+no file → default only; sentinel → project-only; global off → project-only/empty); `standards init` scaffolds + `--opt-out` writes sentinel + refuses overwrite; `config set default_standards` validates bool. (stdlib `unittest`, monkeypatching `CORK_HOME`/paths.)
- Manual: `standards status` in a repo with/without a project file and with the sentinel; `install.sh` CORK_HOME prompt writes `settings.json` additively; install reports 4 skills at 0.8.0, no drift.
