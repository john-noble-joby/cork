# Cork multi-provider foundation (A→B→C) + dynamic headless pipeline

**Status:** design / approved decisions, pending spec review
**Date:** 2026-06-25
**Scope:** Phases A, B, C of the larger "provider-aware, self-configuring cork" vision, plus the
dynamic headless-pipeline refactor (option b). The setup interview, cost-advisor, and
skill-reconfigure layers (phases D, E, F) are **out of scope** here.

## Goal

Make cork's review calls **provider-aware** (native Anthropic + native OpenAI + Copilot) and
**resilient to per-seat model availability**. Today a single model `400` aborts the whole run, the
model list is hardcoded to Copilot, and the catalog endpoints over-report what a seat can actually
invoke. After this phase:

- Reviewers are identified by **`{provider, model}`**, not a bare string.
- cork can call any of three providers with the right endpoint/auth/shape.
- A **preflight** probes each candidate with a real 1-token call, **auto-drops** the unavailable
  ones, and only errors if *none* survive.
- The legacy headless pipeline loops **dynamically** over the surviving rotation (any N ≥ 1),
  with checkpoint/resume re-keyed by model.

## Non-goals (deferred to D–F)

- The interactive setup interview that gathers tokens and writes the config.
- The cost/ranking table and "this model is ~2× tokens" recommendations.
- Skill flows for explaining/reconfiguring the config.

This phase ships a config cork can *read* and a hand-written default; phase E is what *writes* it.

---

## A — Config schema

`~/.config/cork/config.json` (non-secret; ordinary perms):

```json
{
  "version": 1,
  "count": 3,
  "providers": {
    "copilot":   { "enabled": true },
    "openai":    { "enabled": false },
    "anthropic": { "enabled": false }
  },
  "rotation": [
    { "provider": "copilot", "model": "gpt-5.5" },
    { "provider": "copilot", "model": "claude-opus-4.7" },
    { "provider": "copilot", "model": "gpt-4.1" },
    { "provider": "copilot", "model": "gemini-3.1-pro-preview" },
    { "provider": "copilot", "model": "claude-sonnet-4.6" },
    { "provider": "copilot", "model": "claude-haiku-4.5" }
  ]
}
```

- **Secrets never live here.** Only structure: enabled providers, a ranked `rotation`, and `count`.
- **`rotation` is a ranked preference list; `count` is how many reviewers actually run** (default
  `3`). Preflight walks the list top-down, skips dead models, and selects the **first `count`
  survivors**. Lower-ranked entries are backups: drop a provider (or a model dies on a seat) and the
  next-ranked models fill the gap automatically.
  > Example: with the default above, a **work** seat selects `gpt-5.5, claude-opus-4.7, gpt-4.1`;
  > a **personal** seat (gpt-5.5 + opus hard-blocked) selects `gpt-4.1, gemini-3.1-pro-preview,
  > claude-sonnet-4.6`. Same config, same `count`, seat-appropriate set — no per-seat tailoring.
- **Onboarding owns this file.** `cork config init` writes a starter `config.json` from the in-code
  ranked default (the phase-A onboarding you hand-edit); the phase-E interview later replaces that
  with a guided version (discovers access, gathers tokens, sets `count`/`rotation`). The in-code
  default exists only as the **seed** these write out.
- **Missing-config fallback (preserves today's zero-config flow):** if `config.json` is absent, cork
  runs off the in-code ranked default but prints a one-line nudge — *"no config.json; using built-in
  default — run `cork config init` to customize."* Nothing silently surprises you, and a bare
  Copilot token still Just Works like today.
- `load_config()` reads + validates; unknown providers or malformed entries fail with a clear
  message. A `config` subcommand prints the resolved config (skill legibility — a small down payment
  on phase F).

## Token resolution (decision 1, settled)

Per provider, **env-var-first, then cork's chmod-600 `auth.json`**, mirroring today's Copilot chain:

| Provider | Resolution order |
|---|---|
| `copilot` | `CORK_COPILOT_TOKEN` → `auth.json["token"]` → opencode (unchanged) |
| `openai` | `OPENAI_API_KEY` → `auth.json["openai"]` |
| `anthropic` | `ANTHROPIC_API_KEY` → `auth.json["anthropic"]` |

`~/.config/cork/auth.json` extends backward-compatibly: today's `{"token": "..."}` still means
Copilot; new optional keys `{"openai": "...", "anthropic": "..."}` may be added. File stays
chmod 600. A missing token for an *enabled* provider is a clear startup error; for a *disabled*
provider it's ignored.

## B — Multi-provider routing

Generalize the single Copilot client into dispatch-by-provider (module-level functions, no classes).

- **`copilot` and `openai`** are OpenAI-compatible — one helper handles both, differing only by
  base URL + auth header (+ Copilot's extra `x-initiator`/intent headers). Endpoint chosen by the
  existing `_uses_responses_api(model)` (`gpt-5.x`/codex → `/responses`, else `/chat/completions`).
  Reuses `_extract_chat_text` / `_extract_responses_text`.
- **`anthropic`** is the one genuinely different adapter: `POST {base}/v1/messages`, headers
  `x-api-key` + `anthropic-version: 2023-06-01`, body `{model, max_tokens, system, messages:[…]}`,
  output from `content[0].text`. New `_anthropic_call` + `_extract_anthropic_text`.
- Base URLs: `copilot`=`https://api.githubcopilot.com`, `openai`=`https://api.openai.com/v1`,
  `anthropic`=`https://api.anthropic.com`.

`copilot_review(...)` becomes `review(provider, model, instructions, story, diff, files, char_budget)`
that builds the prompt once and dispatches to the right adapter. Retry/backoff and the
empty-content guard stay as they are, wrapping whichever adapter runs.

## C — Discovery + preflight

`preflight(rotation, count) -> list[dict]`: walk the **ranked** rotation top-down, firing a **real
1-token probe** on the correct provider/endpoint for each `{provider, model}` and classifying:

| Outcome | Signal | Action |
|---|---|---|
| `ok` | 200 with usable content | keep (record provider, model, endpoint) |
| `model_not_supported` | 400 body has `model_not_supported` / "not supported" | **drop** (plan gate) |
| `integrator_mismatch` | 400 body has "not available for integrator" | **drop** (token/routing) |
| `auth` | 401/403 | **fail** the whole provider (token problem, not per-model) |
| other 4xx/5xx | — | drop, logged distinctly |

- **Stops once `count` survivors are collected** (lower-ranked entries are backups, probed only as
  needed). Returns the selected survivors in rank order.
- **Errors only if zero survive.** If fewer than `count` survive, runs with what it has and says so.
  Prints a one-line summary of what was selected, and what was dropped and why (no silent
  truncation).
- Do **not** trust `/models` for availability (it over-reports). `/models` is still used, where the
  provider offers it, only to read `max_prompt_tokens` for the char budget; absent that, a
  conservative default.
- Exposed as a **`preflight` subcommand** (prints the live working rotation for the current seat)
  so the `cork` skill can show/choose the real rotation instead of a hardcoded list.
- Wired into `cmd_review` (`--review-model`): a single model that fails its probe prints a clear
  classified message and exits non-zero, so the skill can skip it.

## Dynamic headless pipeline (decision 2 = option b)

The legacy headless pipeline (`python orchestrate.py TICKET repo`) stops hardcoding three models.

**Flow:** implement → self-review → apply self-review → *for each surviving model:* blind review →
apply → … → push + PR.

**Step numbering** becomes dynamic: `total = 3 + 2 × N` where `N` = number of selected survivors
(≤ `count`). `step()`, `step_done()`, and `write_status()` take `total` as a parameter (computed
once after preflight) instead of reading a module-level `TOTAL_STEPS` constant.

**The selected rotation is frozen at first run.** Preflight runs once at the start; the selected
top-`count` survivors are written into the checkpoint. **Resume reads that frozen selection from the
checkpoint, not a fresh preflight** — so availability shifting mid-pipeline can't corrupt resume. If
a frozen model already has a stored review, it's never re-called.

### Checkpoint schema v2 (re-keyed by model)

```json
{
  "version": 2,
  "ticket_id": "MXE-123",
  "summary": "…", "base": "origin/develop", "repo": "/abs/path",
  "rotation": [{ "provider": "copilot", "model": "gpt-4.1" }, …],
  "done": {
    "implement": true,
    "self_review": "<review text>",
    "self_fix": "<fix note>",
    "models": {
      "copilot/gpt-4.1": { "review": "<text>", "fix": "<note>" },
      "copilot/gemini-3.1-pro-preview": { "review": "<text>" }
    }
  }
}
```

- Model key is `"provider/model"` to disambiguate the same model id across providers.
- **Resume** reconstructs remaining work from `done`: skip phases already present; for each model in
  the frozen `rotation`, run review if absent, then apply if absent. No step-number arithmetic.
- **Migration:** a checkpoint without `version` or with `version < 2` is treated as **stale → start
  fresh** (with a printed notice). These checkpoints are transient per-ticket state; no migration
  code is warranted.

### CLI changes

- **`--start-from N` is removed.** It was tied to fixed step numbers that no longer exist; resume is
  now automatic (reads `done`). `--reset` (delete checkpoint, start over) is unchanged.
- `--seed-only` updated to write the v2 schema (`done.implement = true` + `summary`).
- New subcommands: `config init` (write a starter `config.json` from the in-code default — the
  phase-A onboarding), `config` (print the resolved config, read-only), `preflight` (print the
  selected top-`count` rotation for the current seat, read-only).
- `--skip-validation` (existing) bypasses preflight probes and uses the configured rotation's first
  `count` entries as-is with the default char budget.

## Backward compatibility

- **Default behavior ≈ unchanged for the common case:** no `config.json` + Copilot token → the
  in-code default (Copilot-only ranked list, `openai`/`anthropic` disabled), `count` 3, preflighted.
  On the work seat that selects essentially today's three models.
- `auth.json` `{"token": "..."}` still works.
- The **session-driven `cork` skill** keeps calling `--review-model` one model at a time; it gains
  the `preflight` subcommand to discover the seat's live rotation.
- **Breaking:** `--start-from` removal and checkpoint schema bump (old in-flight headless
  checkpoints restart). Acceptable — headless is the legacy/unattended path and the schema had to
  change for model-keyed resume.

## Verification

- Unit-ish: `preflight` classification against synthetic 200 / `model_not_supported` /
  `integrator_mismatch` / 401 responses (monkeypatch the HTTP helpers) → correct keep/drop/fail.
- Live: `preflight` on the work seat (Copilot) and, if a key is present, OpenAI/Anthropic native —
  confirm survivors match real 1-token probes.
- Live: one `--review-model` per provider returns real findings (Anthropic `/v1/messages` adapter
  especially, since it's new).
- Ranked selection: with a default-style ranked list, a seat that hard-blocks the top entries
  selects the next-ranked survivors up to `count` (the work-vs-personal example in section A).
- Headless dynamic loop: run with selections of length 1, 2, and 3; kill mid-run and resume; confirm
  it skips completed models and re-keys correctly.
- Regression: no `config.json` + Copilot token behaves like today (default ranked list, `count` 3,
  preflighted) and prints the nudge.
