# Multi-Provider Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cork provider-aware (native Anthropic + native OpenAI + Copilot) and resilient to per-seat model availability via a ranked rotation, preflight auto-drop, and a dynamic headless pipeline.

**Architecture:** A `config.json` holds enabled providers + a ranked `rotation` + a `count`. A single low-level HTTP seam (`_http_post_json`) underlies provider adapters (OpenAI-compatible for copilot/openai; a distinct Anthropic Messages adapter). `preflight` walks the ranked list, probes each model, drops the unavailable, and selects the first `count` survivors. The legacy headless pipeline loops dynamically over the selected set with model-keyed checkpoint resume.

**Tech Stack:** Python 3.10+ stdlib only (`urllib`, `json`, `pathlib`). Tests use stdlib `unittest` (no third-party test deps).

## Global Constraints

- **Python 3.10+ stdlib only** — no third-party packages (incl. no pytest; use `unittest`).
- **No classes** — module-level functions and constants only.
- **Type hints on every function** — params and returns.
- **No docstrings** — comment only non-obvious WHY.
- **Errors:** `fail(msg)` (prints to stderr, exits 1) at system boundaries; `print(..., flush=True)` for progress.
- **`pathlib.Path`** for all file ops; **`subprocess.run(..., capture_output=True, text=True)`**.
- **Secrets never written to `config.json`** (mode default); tokens live in `auth.json` (chmod 600) or env.
- **Run tests:** `python -m unittest discover -s tests -v` from repo root.
- Spec: `docs/specs/2026-06-25-multi-provider-foundation.md`.

---

## File Structure

- **Modify `orchestrate.py`** — all production code lands here (cork is a single script by design; CLAUDE.md forbids turning it into a package). New sections: Config, Providers/HTTP, Preflight; rewrites of `startup_checks`→preflight usage, `copilot_review`→`review`, and `main()`.
- **Create `tests/test_config.py`** — config load/validate/default/init.
- **Create `tests/test_providers.py`** — token resolution, anthropic text extraction, classification.
- **Create `tests/test_preflight.py`** — ranked selection + stop-at-count (monkeypatched probe).
- **Create `tests/test_resume.py`** — headless remaining-work reconstruction (pure fn).
- **Modify `skills/cork/SKILL.md`, `README.md`, `CLAUDE.md`, `VERSION`** — docs + version bump (final task).

`tests/` is new. Add `tests/__init__.py` (empty) so `unittest discover` works.

---

### Task 1: Config schema, loader, and `config` subcommands

**Files:**
- Modify: `orchestrate.py` (add Config section after the Auth section, ~line 115)
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: `fail()` (existing), `_CORK_AUTH` path constant (existing).
- Produces: `CONFIG_PATH: Path`; `DEFAULT_CONFIG: dict`; `PROVIDER_BASE: dict[str,str]`; `load_config() -> dict`; `_validate_config(cfg: dict) -> None`; `cmd_config_init() -> None`; `cmd_config_show() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
import json, os, unittest, tempfile
from pathlib import Path
import orchestrate


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self._orig = orchestrate.CONFIG_PATH
        orchestrate.CONFIG_PATH = self.path

    def tearDown(self):
        orchestrate.CONFIG_PATH = self._orig
        self.tmp.cleanup()

    def test_missing_config_returns_default(self):
        cfg = orchestrate.load_config()
        self.assertEqual(cfg, orchestrate.DEFAULT_CONFIG)

    def test_default_is_valid(self):
        orchestrate._validate_config(orchestrate.DEFAULT_CONFIG)  # no raise

    def test_init_writes_default(self):
        orchestrate.cmd_config_init()
        self.assertTrue(self.path.exists())
        self.assertEqual(json.loads(self.path.read_text()), orchestrate.DEFAULT_CONFIG)

    def test_loads_written_config(self):
        self.path.write_text(json.dumps({
            "version": 1, "count": 2,
            "providers": {"copilot": {"enabled": True}},
            "rotation": [{"provider": "copilot", "model": "gpt-4.1"}],
        }))
        cfg = orchestrate.load_config()
        self.assertEqual(cfg["count"], 2)

    def test_unknown_provider_fails(self):
        self.path.write_text(json.dumps({
            "rotation": [{"provider": "bogus", "model": "x"}],
        }))
        with self.assertRaises(SystemExit):
            orchestrate.load_config()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_config -v`
Expected: FAIL with `AttributeError: module 'orchestrate' has no attribute 'CONFIG_PATH'`.

- [ ] **Step 3: Add the Config section to `orchestrate.py`**

Insert after the Auth section (after `_copilot_token()`, before `_copilot_headers()` is fine — but place constants near the top Config block and functions in a new "Config" section). Add the constants beside the other path constants (~line 64) and functions in a new section:

```python
# (near the other path constants, ~line 64)
CONFIG_PATH = Path(os.environ.get("CORK_CONFIG_FILE",
                   str(Path.home() / ".config/cork/config.json")))

PROVIDER_BASE = {
    "copilot":   "https://api.githubcopilot.com",
    "openai":    "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}

DEFAULT_CONFIG = {
    "version": 1,
    "count": 3,
    "providers": {
        "copilot":   {"enabled": True},
        "openai":    {"enabled": False},
        "anthropic": {"enabled": False},
    },
    "rotation": [
        {"provider": "copilot", "model": "gpt-5.5"},
        {"provider": "copilot", "model": "claude-opus-4.7"},
        {"provider": "copilot", "model": "gpt-4.1"},
        {"provider": "copilot", "model": "gemini-3.1-pro-preview"},
        {"provider": "copilot", "model": "claude-sonnet-4.6"},
        {"provider": "copilot", "model": "claude-haiku-4.5"},
    ],
}
```

```python
# ── Config ──────────────────────────────────────────────────────────────────

def _validate_config(cfg: dict) -> None:
    rotation = cfg.get("rotation")
    if not isinstance(rotation, list) or not rotation:
        fail("config.rotation must be a non-empty list")
    for entry in rotation:
        if not isinstance(entry, dict) or "provider" not in entry or "model" not in entry:
            fail(f"config.rotation entry needs provider+model: {entry}")
        if entry["provider"] not in PROVIDER_BASE:
            fail(f"unknown provider '{entry['provider']}' "
                 f"(known: {', '.join(PROVIDER_BASE)})")
    count = cfg.get("count", 3)
    if not isinstance(count, int) or count < 1:
        fail("config.count must be a positive integer")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"  ⚠ no {CONFIG_PATH}; using built-in default — run "
              f"`orchestrate.py config init` to customize", flush=True)
        return DEFAULT_CONFIG
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        fail(f"Cannot parse {CONFIG_PATH}: {e}")
    _validate_config(cfg)
    return cfg


def cmd_config_init() -> None:
    if CONFIG_PATH.exists():
        print(f"{CONFIG_PATH} already exists — leaving it untouched.")
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    print(f"Wrote starter config to {CONFIG_PATH} — edit `rotation`/`count` to taste.")


def cmd_config_show() -> None:
    print(json.dumps(load_config(), indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_config -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add orchestrate.py tests/__init__.py tests/test_config.py
git commit -m "feat: config schema, loader, and config init/show"
```

---

### Task 2: Per-provider token resolution

**Files:**
- Modify: `orchestrate.py` (add beside `_copilot_token`, ~line 115)
- Create: `tests/test_providers.py`

**Interfaces:**
- Consumes: `_copilot_token()` (existing), `_CORK_AUTH` (existing), `fail()`.
- Produces: `_provider_token(provider: str) -> str`; `_resolve_native_token(env_var: str, auth_key: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_providers.py
import json, os, unittest, tempfile
from pathlib import Path
import orchestrate


class TokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = Path(self.tmp.name) / "auth.json"
        self._orig = orchestrate._CORK_AUTH
        orchestrate._CORK_AUTH = self.auth
        os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self):
        orchestrate._CORK_AUTH = self._orig
        os.environ.pop("OPENAI_API_KEY", None)
        self.tmp.cleanup()

    def test_env_var_wins(self):
        os.environ["OPENAI_API_KEY"] = "env-key"
        self.auth.write_text(json.dumps({"openai": "file-key"}))
        self.assertEqual(orchestrate._provider_token("openai"), "env-key")

    def test_auth_file_fallback(self):
        self.auth.write_text(json.dumps({"openai": "file-key"}))
        self.assertEqual(orchestrate._provider_token("openai"), "file-key")

    def test_missing_token_fails(self):
        self.auth.write_text(json.dumps({}))
        with self.assertRaises(SystemExit):
            orchestrate._provider_token("anthropic")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_providers -v`
Expected: FAIL with `AttributeError: module 'orchestrate' has no attribute '_provider_token'`.

- [ ] **Step 3: Add token resolution to `orchestrate.py`**

```python
def _resolve_native_token(env_var: str, auth_key: str) -> str:
    tok = os.environ.get(env_var)
    if tok:
        return tok.strip()
    if _CORK_AUTH.exists():
        try:
            data = json.loads(_CORK_AUTH.read_text())
        except json.JSONDecodeError as e:
            fail(f"Cannot parse {_CORK_AUTH}: {e}")
        if data.get(auth_key):
            return data[auth_key].strip()
    fail(f"No {auth_key} token — set {env_var} or add "
         f'"{auth_key}" to {_CORK_AUTH}.')


def _provider_token(provider: str) -> str:
    if provider == "copilot":
        return _copilot_token()
    if provider == "openai":
        return _resolve_native_token("OPENAI_API_KEY", "openai")
    if provider == "anthropic":
        return _resolve_native_token("ANTHROPIC_API_KEY", "anthropic")
    fail(f"unknown provider: {provider}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_providers -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add orchestrate.py tests/test_providers.py
git commit -m "feat: per-provider token resolution (env-first, auth.json fallback)"
```

---

### Task 3: HTTP seam + provider routing (incl. Anthropic adapter)

**Files:**
- Modify: `orchestrate.py` (refactor `_copilot_chat`/`_copilot_responses`; add `_http_post_json`, `_anthropic_call`, `_extract_anthropic_text`; rename `copilot_review`→`review`)
- Modify: `tests/test_providers.py` (add extraction test)

**Interfaces:**
- Consumes: `_provider_headers` (defined here), `_provider_token`, `_extract_chat_text`/`_extract_responses_text` (existing), `_uses_responses_api` (existing), `_retry_wait` (existing), `_budget_files` (existing), `REVIEW_SYSTEM` (existing).
- Produces:
  - `_http_post_json(url: str, headers: dict, payload: dict, timeout: int = 300) -> tuple[int, object]` — returns `(status, parsed_json_or_body_text)`; raises `TimeoutError`/`urllib.error.URLError` on transport failure only.
  - `_provider_headers(provider: str) -> dict[str, str]`
  - `_anthropic_call(model, system, user_msg, max_tokens=8000, timeout=300) -> tuple[int, object]`
  - `_extract_anthropic_text(data: dict) -> str`
  - `review(provider: str, model: str, instructions: str, story: str, diff: str, files: dict, char_budget: int = _DEFAULT_CHAR_BUDGET, max_attempts: int = 3) -> str`

- [ ] **Step 1: Write the failing test (pure extractor)**

Append to `tests/test_providers.py`:

```python
class AnthropicExtractTest(unittest.TestCase):
    def test_extracts_text_blocks(self):
        data = {"content": [{"type": "text", "text": "FILE | LINE | ISSUE"},
                            {"type": "text", "text": " | FIX"}]}
        self.assertEqual(orchestrate._extract_anthropic_text(data),
                         "FILE | LINE | ISSUE | FIX")

    def test_empty_content(self):
        self.assertEqual(orchestrate._extract_anthropic_text({"content": []}), "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_providers.AnthropicExtractTest -v`
Expected: FAIL with `AttributeError: ... '_extract_anthropic_text'`.

- [ ] **Step 3: Add the HTTP seam, headers, Anthropic adapter, and extractor**

Add `_http_post_json` (the single transport seam) near `_copilot_headers`:

```python
def _http_post_json(url: str, headers: dict, payload: dict,
                    timeout: int = 300) -> tuple[int, object]:
    # Returns (status, parsed-json) on 2xx, (status, body-text) on HTTP error.
    # Transport failures (timeout, connection) raise for the caller's retry loop.
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={**headers, "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def _provider_headers(provider: str) -> dict[str, str]:
    tok = _provider_token(provider)
    if provider == "copilot":
        return _copilot_headers()
    if provider == "openai":
        return {"Authorization": f"Bearer {tok}"}
    if provider == "anthropic":
        return {"x-api-key": tok, "anthropic-version": "2023-06-01"}
    fail(f"unknown provider: {provider}")


def _anthropic_call(model: str, system: str, user_msg: str,
                    max_tokens: int = 8000, timeout: int = 300) -> tuple[int, object]:
    return _http_post_json(
        f"{PROVIDER_BASE['anthropic']}/v1/messages",
        _provider_headers("anthropic"),
        {"model": model, "max_tokens": max_tokens, "system": system,
         "messages": [{"role": "user", "content": user_msg}]},
        timeout=timeout,
    )


def _extract_anthropic_text(data: dict) -> str:
    parts = [b["text"] for b in data.get("content", []) or []
             if b.get("type") == "text" and b.get("text")]
    return "".join(parts).strip()
```

- [ ] **Step 4: Refactor `_copilot_chat`/`_copilot_responses` onto the seam**

Replace the bodies of `_copilot_chat` and `_copilot_responses` so they go through `_http_post_json` and return `(status, object)` (callers updated in Step 5). Replace:

```python
def _copilot_chat(payload: dict, timeout: int = 300) -> tuple[int, object]:
    return _http_post_json(f"{COPILOT_BASE}/chat/completions",
                           _copilot_headers(), payload, timeout)


def _copilot_responses(payload: dict, timeout: int = 300) -> tuple[int, object]:
    return _http_post_json(f"{COPILOT_BASE}/responses",
                           _copilot_headers(), payload, timeout)
```

(Existing callers in `startup_checks` are replaced in Task 4; the only other caller is `copilot_review`, replaced next.)

- [ ] **Step 5: Replace `copilot_review` with provider-aware `review`**

Replace the whole `copilot_review` function with `review`, dispatching by provider and using `(status, body)` returns. Keep the budget/prompt build and retry/empty-guard:

```python
def _openai_compatible_call(provider: str, model: str, system: str,
                            user_msg: str, timeout: int = 300) -> tuple[int, object]:
    base = PROVIDER_BASE[provider]
    headers = _provider_headers(provider)
    if _uses_responses_api(model):
        return _http_post_json(f"{base}/responses", headers, {
            "model": model, "instructions": system, "input": user_msg,
            "max_output_tokens": _RESPONSES_MAX_OUTPUT,
            "reasoning": {"effort": _RESPONSES_EFFORT},
        }, timeout)
    return _http_post_json(f"{base}/chat/completions", headers, {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user_msg}],
    }, timeout)


def _call_and_extract(provider: str, model: str, system: str,
                      user_msg: str) -> tuple[int, str]:
    # Returns (status, extracted_text). Text is "" if status != 200.
    if provider == "anthropic":
        status, body = _anthropic_call(model, system, user_msg)
        text = _extract_anthropic_text(body) if status == 200 else ""
        return status, text
    status, body = _openai_compatible_call(provider, model, system, user_msg)
    if status != 200:
        return status, ""
    text = (_extract_responses_text(body) if _uses_responses_api(model)
            else _extract_chat_text(body))
    return status, text


def review(provider: str, model: str, instructions: str, story: str,
           diff: str, files: dict[str, str],
           char_budget: int = _DEFAULT_CHAR_BUDGET,
           max_attempts: int = 3) -> str:
    system = (
        instructions + "\n\n---\n"
        "Note: you are a single-pass API reviewer — you cannot spawn "
        "sub-agents or invoke skills. Apply §3–§7 in one pass and produce "
        "the §8 output format. Do NOT apply fixes; report findings only."
        if instructions else REVIEW_SYSTEM
    )
    fixed_chars = len(system) + len(story) + len(diff) + 500
    file_block, n_included = _budget_files(files, max(0, char_budget - fixed_chars))
    if n_included < len(files):
        print(f"  → token budget: included {n_included}/{len(files)} files "
              f"(diff-only for the rest)")
    user_msg = (f"## Story / Task\n{story}\n\n"
                f"## Changed Files (current state)\n{file_block}\n\n"
                f"## Branch Diff\n```diff\n{diff}\n```")

    for attempt in range(max_attempts):
        try:
            status, text = _call_and_extract(provider, model, system, user_msg)
        except TimeoutError:
            _retry_wait(attempt, max_attempts, "timeout"); continue
        except urllib.error.URLError as e:
            _retry_wait(attempt, max_attempts, f"connection error: {e.reason}"); continue

        if status == 200 and text:
            return text
        if status == 200:  # empty content — retry then skip
            if attempt < max_attempts - 1:
                print(f"  → {provider}/{model} returned empty content, retrying "
                      f"({attempt + 1}/{max_attempts})"); continue
            return f"[{provider}/{model} returned no usable content — skipped]"
        if status in (429, 500, 502, 503, 504):
            _retry_wait(attempt, max_attempts, f"HTTP {status}", long=status == 429); continue
        body_preview = text or "(no body)"
        fail(f"{provider}/{model} API error {status}: {str(body_preview)[:500]}")
    fail(f"{provider}/{model} failed after {max_attempts} attempts")
```

- [ ] **Step 6: Run extractor test + import check**

Run: `python -m unittest tests.test_providers -v && python -c "import orchestrate"`
Expected: PASS; clean import (no references to the removed `copilot_review`).

- [ ] **Step 7: Live smoke (Copilot path, real token)**

Run:
```bash
python -c "import orchestrate as o; print(o.review('copilot','gpt-4.1','','t', \
'--- a/x.py\n+++ b/x.py\n@@\n-def d(a,b): return a/b', {'x.py':'def d(a,b): return a/b'}, 50000)[:120])"
```
Expected: a FILE|LINE|ISSUE|FIX line (real review). If an `ANTHROPIC_API_KEY` is present, repeat with `'anthropic','claude-sonnet-4.6'` to exercise the new adapter.

- [ ] **Step 8: Commit**

```bash
git add orchestrate.py tests/test_providers.py
git commit -m "feat: provider routing + Anthropic adapter, generalize copilot_review->review"
```

---

### Task 4: Preflight (classify, probe, ranked select) + wire into cmd_review

**Files:**
- Modify: `orchestrate.py` (replace `startup_checks` internals; add `_classify_preflight`, `_probe`, `preflight`; add `preflight` subcommand; update `cmd_review`)
- Create: `tests/test_preflight.py`

**Interfaces:**
- Consumes: `_call_and_extract` (Task 3), `_http_post_json`, `load_config` (Task 1), `_DEFAULT_CHAR_BUDGET` (existing), `fail`.
- Produces:
  - `_classify_preflight(status: int, body: str) -> str` → one of `ok`/`model_not_supported`/`integrator_mismatch`/`auth`/`other`.
  - `_probe(provider: str, model: str) -> str` (classification of a real 1-token call).
  - `preflight(rotation: list[dict], count: int) -> list[dict]` → selected survivors `[{provider, model}]`, rank order.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_preflight.py
import unittest
import orchestrate


class ClassifyTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(orchestrate._classify_preflight(200, "ok"), "ok")

    def test_model_not_supported(self):
        self.assertEqual(orchestrate._classify_preflight(
            400, '{"error":{"code":"model_not_supported"}}'), "model_not_supported")

    def test_integrator(self):
        self.assertEqual(orchestrate._classify_preflight(
            400, 'model "x" is not available for integrator "zed"'),
            "integrator_mismatch")

    def test_auth(self):
        self.assertEqual(orchestrate._classify_preflight(401, "nope"), "auth")

    def test_other(self):
        self.assertEqual(orchestrate._classify_preflight(503, "busy"), "other")


class SelectTest(unittest.TestCase):
    def test_stops_at_count_and_skips_dead(self, ):
        rotation = [
            {"provider": "copilot", "model": "dead1"},
            {"provider": "copilot", "model": "good1"},
            {"provider": "copilot", "model": "good2"},
            {"provider": "copilot", "model": "good3"},
        ]
        calls = []
        def fake_probe(provider, model):
            calls.append(model)
            return "ok" if model.startswith("good") else "model_not_supported"
        orig = orchestrate._probe
        orchestrate._probe = fake_probe
        try:
            sel = orchestrate.preflight(rotation, count=2)
        finally:
            orchestrate._probe = orig
        self.assertEqual([s["model"] for s in sel], ["good1", "good2"])
        self.assertEqual(calls, ["dead1", "good1", "good2"])  # stopped, never probed good3

    def test_zero_survivors_exits(self):
        rotation = [{"provider": "copilot", "model": "dead"}]
        orig = orchestrate._probe
        orchestrate._probe = lambda p, m: "model_not_supported"
        try:
            with self.assertRaises(SystemExit):
                orchestrate.preflight(rotation, count=3)
        finally:
            orchestrate._probe = orig


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_preflight -v`
Expected: FAIL with `AttributeError: ... '_classify_preflight'`.

- [ ] **Step 3: Implement classification, probe, and ranked selection**

```python
def _classify_preflight(status: int, body: str) -> str:
    if status == 200:
        return "ok"
    if status in (401, 403):
        return "auth"
    low = (body or "").lower()
    if status == 400 and ("model_not_supported" in low or "not supported" in low):
        return "model_not_supported"
    if status == 400 and "not available for integrator" in low:
        return "integrator_mismatch"
    return "other"


def _probe(provider: str, model: str) -> str:
    # Real 1-token call on the right endpoint; classify the outcome.
    try:
        status, text = _call_and_extract(provider, model, "", "ok")
    except (TimeoutError, urllib.error.URLError):
        return "other"
    if provider == "anthropic":
        return "ok" if status == 200 else _classify_preflight(status, text)
    return _classify_preflight(status, text)


def preflight(rotation: list[dict], count: int) -> list[dict]:
    selected: list[dict] = []
    print(f"Preflight: selecting up to {count} of {len(rotation)} ranked models…",
          flush=True)
    for entry in rotation:
        if len(selected) >= count:
            break
        provider, model = entry["provider"], entry["model"]
        verdict = _probe(provider, model)
        if verdict == "ok":
            selected.append({"provider": provider, "model": model})
            print(f"  ✓ {provider}/{model}")
        elif verdict == "auth":
            fail(f"{provider}: auth failed (401/403) — token invalid/expired. "
                 f"Fix the {provider} token and retry.")
        else:
            print(f"  ✗ {provider}/{model} dropped ({verdict})")
    if not selected:
        fail("No usable models on this seat — check your config rotation / tokens.")
    if len(selected) < count:
        print(f"  ⚠ only {len(selected)}/{count} models available — running with these.")
    return selected
```

Note: `_call_and_extract` for anthropic returns text only on 200; for the probe we need the status to classify a non-200. Adjust `_call_and_extract` to also surface status for anthropic (it already returns `(status, text)`), so `_probe` classifies via `_classify_preflight(status, text)` uniformly. The `provider == "anthropic"` branch above is therefore redundant — simplify `_probe` to a single `return _classify_preflight(status, text)`.

- [ ] **Step 4: Simplify `_probe` per the note**

```python
def _probe(provider: str, model: str) -> str:
    try:
        status, text = _call_and_extract(provider, model, "", "ok")
    except (TimeoutError, urllib.error.URLError):
        return "other"
    return _classify_preflight(status, text)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_preflight -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Add the `preflight` subcommand and rewire `cmd_review`**

Replace `startup_checks`'s role in `cmd_review`. New `cmd_review` resolves the single model's provider from config (or treats a bare `--review-model X` as `copilot/X` for backward compat), probes it, and reviews:

```python
def _split_model_ref(ref: str) -> tuple[str, str]:
    # "provider/model" or bare "model" (defaults to copilot for back-compat).
    if "/" in ref:
        provider, model = ref.split("/", 1)
        return provider, model
    return "copilot", ref


def cmd_review(tid: str, repo: str, base: str, model_ref: str,
               validate: bool = True) -> None:
    provider, model = _split_model_ref(model_ref)
    if validate:
        verdict = _probe(provider, model)
        if verdict != "ok":
            fail(f"{provider}/{model} not usable on this seat ({verdict}).")
    instructions, instructions_path = load_agent_instructions(repo)
    if instructions_path:
        print(f"Review instructions: {instructions_path} ({len(instructions)} chars)")
    diff = git_diff_branch(repo, base)
    if not diff.strip():
        fail(f"No diff vs {base} — nothing to review.")
    files = changed_files_branch(repo, base)
    story = load_state(tid).get("summary") or f"Review the branch changes for {tid}."
    print(f"\n── Review: {provider}/{model} — {len(files)} files, "
          f"{len(diff.splitlines())} diff lines vs {base}\n", flush=True)
    print(review(provider, model, instructions, story, diff, files, _DEFAULT_CHAR_BUDGET))


def cmd_preflight() -> None:
    cfg = load_config()
    selected = preflight(cfg["rotation"], cfg.get("count", 3))
    for s in selected:
        print(f"{s['provider']}/{s['model']}")
```

Add subcommand dispatch in `main()` before argparse (beside `login`/`--version`):

```python
    if len(sys.argv) >= 2 and sys.argv[1] == "config":
        if len(sys.argv) >= 3 and sys.argv[2] == "init":
            cmd_config_init()
        else:
            cmd_config_show()
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "preflight":
        cmd_preflight()
        return
```

Update the `--review-model` call site: `cmd_review(tid, repo, base, args.review_model, validate=not args.skip_validation)` (signature param renamed `model`→`model_ref`; behavior unchanged for bare ids).

- [ ] **Step 7: Verify import + live preflight**

Run: `python -m unittest discover -s tests -v && python orchestrate.py preflight`
Expected: tests PASS; `preflight` prints the selected `provider/model` lines for your seat (e.g. on work: `copilot/gpt-5.5`, `copilot/claude-opus-4.7`, `copilot/gpt-4.1`).

- [ ] **Step 8: Commit**

```bash
git add orchestrate.py tests/test_preflight.py
git commit -m "feat: preflight classify/probe/select + preflight & config subcommands"
```

---

### Task 5: Dynamic headless pipeline + checkpoint v2 (model-keyed resume)

**Files:**
- Modify: `orchestrate.py` (`step`/`step_done`/`write_status` take `total`; rewrite `main()` pipeline; add `_remaining_work`; update `--seed-only`; remove `--start-from`; drop `MODELS`/`TOTAL_STEPS`/`startup_checks`)
- Create: `tests/test_resume.py`

**Interfaces:**
- Consumes: `preflight`, `load_config`, `run_claude`, `git_commit_all`, `prompt_*` (existing), `review` (Task 3), `load_state`/`mark_done`/`clear_state` (existing).
- Produces: `_model_key(entry: dict) -> str` (`"provider/model"`); `_remaining_work(state: dict) -> dict` (what phases/models still need doing).

- [ ] **Step 1: Write the failing test (pure reconstruction)**

```python
# tests/test_resume.py
import unittest
import orchestrate


class RemainingTest(unittest.TestCase):
    def _state(self, done):
        return {"rotation": [{"provider": "copilot", "model": "a"},
                             {"provider": "copilot", "model": "b"}],
                "done": done}

    def test_fresh(self):
        r = orchestrate._remaining_work(self._state({}))
        self.assertTrue(r["implement"])
        self.assertEqual(r["models"], ["copilot/a", "copilot/b"])

    def test_partial(self):
        r = orchestrate._remaining_work(self._state({
            "implement": True, "self_review": "x", "self_fix": "y",
            "models": {"copilot/a": {"review": "r", "fix": "f"}},
        }))
        self.assertFalse(r["implement"])
        self.assertEqual(r["models"], ["copilot/b"])  # a fully done, b remains

    def test_model_review_only(self):
        r = orchestrate._remaining_work(self._state({
            "implement": True, "self_review": "x", "self_fix": "y",
            "models": {"copilot/a": {"review": "r"}},  # review yes, fix no
        }))
        self.assertEqual(r["models"], ["copilot/a", "copilot/b"])
        self.assertEqual(r["needs_fix_only"], ["copilot/a"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_resume -v`
Expected: FAIL with `AttributeError: ... '_remaining_work'`.

- [ ] **Step 3: Add `_model_key` + `_remaining_work`**

```python
def _model_key(entry: dict) -> str:
    return f"{entry['provider']}/{entry['model']}"


def _remaining_work(state: dict) -> dict:
    done = state.get("done", {})
    models_done = done.get("models", {})
    pending, needs_fix_only = [], []
    for entry in state["rotation"]:
        key = _model_key(entry)
        rec = models_done.get(key, {})
        if "review" in rec and "fix" in rec:
            continue
        pending.append(key)
        if "review" in rec and "fix" not in rec:
            needs_fix_only.append(key)
    return {
        "implement": not done.get("implement"),
        "self_review": "self_review" not in done,
        "self_fix": "self_fix" not in done,
        "models": pending,
        "needs_fix_only": needs_fix_only,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_resume -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Thread `total` through progress helpers**

Change signatures (drop the module `TOTAL_STEPS` constant):

```python
def step(n: int, total: int, msg: str, ticket_id: str = "") -> None:
    global _step_start, _current_ticket
    _step_start = time.monotonic()
    _current_ticket = ticket_id or _current_ticket
    print(f"\n── Step {n}/{total} — {msg}", flush=True)
    if _current_ticket:
        write_status(_current_ticket, n, total, msg, phase="running")


def step_done(n: int, total: int, msg: str) -> None:
    elapsed = time.monotonic() - _step_start
    print(f"  ✓ done in {elapsed:.0f}s", flush=True)
    if _current_ticket:
        write_status(_current_ticket, n, total, msg, phase="done", elapsed=elapsed)
```

Update `write_status(ticket_id, step_n, total, label, phase=..., elapsed=...)` to take `total` and write `"of": total`. Update `cmd_status` (reads `of` from the file — unchanged).

- [ ] **Step 6: Rewrite `main()`'s pipeline body**

Replace the fixed steps 1–9 + the `startup_checks(MODELS)` call with: load config → preflight (or read frozen rotation on resume) → dynamic loop. Concrete structure:

```python
    cfg = load_config()
    state = load_state(tid)
    if state.get("version") != 2:
        state = {"version": 2, "ticket_id": tid, "done": {}}

    # Freeze the selected rotation at first run; reuse it on resume.
    if "rotation" not in state:
        sel = (cfg["rotation"][:cfg.get("count", 3)] if args.skip_validation
               else preflight(cfg["rotation"], cfg.get("count", 3)))
        state["rotation"] = sel
        mark_done_v2(tid, state)
    rotation = state["rotation"]
    N = len(rotation)
    total = 3 + 2 * N

    rem = _remaining_work(state)
    summary = state.get("done", {}).get("summary") or state.get("summary", "")
    # ... implement / self-review / self-fix guarded by rem[...]; then:
    for i, entry in enumerate(rotation):
        key = _model_key(entry)
        review_step = 4 + 2 * i
        fix_step = 5 + 2 * i
        if key in rem["models"] and key not in rem["needs_fix_only"]:
            step(review_step, total, f"Blind review: {key}", ticket_id=tid)
            diff = git_diff_branch(repo, base); files = changed_files_branch(repo, base)
            out = review(entry["provider"], entry["model"], instructions,
                         summary, diff, files, char_budget)
            _save_model(tid, state, key, "review", out)
            step_done(review_step, total, f"Blind review: {key}")
        if key in rem["models"]:
            step(fix_step, total, f"Apply {key} findings", ticket_id=tid)
            fix = run_claude(prompt_fix(summary, base, state["done"]["models"][key]["review"],
                             tid, is_final=(i == N - 1)), cwd=repo)
            git_commit_all(repo, f"fix: apply {key} review [{tid}]")
            _save_model(tid, state, key, "fix", fix)
            step_done(fix_step, total, f"Apply {key} findings")
```

Add helpers `mark_done_v2(tid, state)` (writes the whole v2 state dict to the checkpoint) and `_save_model(tid, state, key, field, value)` (sets `state["done"]["models"][key][field]` then `mark_done_v2`). Implement the implement/self-review/self-fix blocks with the same guard-and-save pattern (storing into `state["done"]["implement"/"self_review"/"self_fix"]` and `summary`). `char_budget = _DEFAULT_CHAR_BUDGET` (per-provider live token-limit detection is deferred; conservative budget is fine).

- [ ] **Step 7: Update `--seed-only` and remove `--start-from`**

`--seed-only` writes v2: `{"version":2,"ticket_id":tid,"done":{"implement":True,"summary":<commits>},"base":base,"repo":repo}` (no rotation yet — preflight fills it on the next run). Delete the `--start-from` argparse arg and its branches; resume is automatic via `_remaining_work`.

- [ ] **Step 8: Verify import, tests, and a live dynamic run**

Run: `python -m unittest discover -s tests -v && python -c "import orchestrate"`
Then a live resume check on a scratch branch with a 2-entry config (`count: 2`): start a run, Ctrl-C after the first model's fix commit, re-run, and confirm it prints `[skipped — already done]`-style progress for the completed model and resumes at the second. (Manual; document the observed step numbers.)

- [ ] **Step 9: Commit**

```bash
git add orchestrate.py tests/test_resume.py
git commit -m "feat: dynamic headless pipeline with model-keyed v2 checkpoint resume"
```

---

### Task 6: Docs, skill wiring, version bump

**Files:**
- Modify: `skills/cork/SKILL.md` (rotation comes from `preflight`; mention `config`), `README.md`, `CLAUDE.md`, `VERSION`, `skills/copilot-review-loop/SKILL.md` (version stamp)

**Interfaces:**
- Consumes: the `preflight`/`config` subcommands (Task 4). No new code.

- [ ] **Step 1: Bump `VERSION`**

Set `VERSION` to `0.5.0`.

- [ ] **Step 2: Update the cork skill**

In `skills/cork/SKILL.md`: replace the hardcoded rotation line with a Step-0 instruction to run `python "$CORK_HOME/orchestrate.py" preflight` and use the printed `provider/model` list as the rotation. Add a one-liner that `config`/`config init` manage the per-seat model config. Update the `**Version:** 0.5.0` stamp. In the review-only parallel loop, source models from `preflight` output instead of the literal `for M in …` list.

- [ ] **Step 3: Update README.md and CLAUDE.md**

README: replace the fixed 9-step "Pipeline" table with a note that the pipeline is `3 + 2×N` over the preflight-selected rotation, and document `config.json` (ranked `rotation` + `count`), `config init`, and `preflight`. CLAUDE.md: update the Purpose bullet (reviewers come from the preflight-selected ranked rotation across copilot/openai/anthropic) and the env table (add `CORK_CONFIG_FILE`). Bump the `copilot-review-loop` SKILL.md `**Version:**` stamp to `0.5.0`.

- [ ] **Step 4: Install + verify version**

Run: `./install.sh && python orchestrate.py --version`
Expected: install reports v0.5.0, both skill stamps match, `--version` prints `cork 0.5.0 (<sha>)`.

- [ ] **Step 5: Commit**

```bash
git add VERSION skills/ README.md CLAUDE.md
git commit -m "docs: wire skill/docs to preflight+config, bump to 0.5.0"
```

---

## Self-Review

**Spec coverage:**
- A (config schema + token resolution) → Tasks 1, 2 ✓
- B (provider routing, Anthropic adapter) → Task 3 ✓
- C (preflight classify/probe/ranked-select, subcommands, cmd_review wiring) → Task 4 ✓
- Dynamic headless + v2 checkpoint + resume + `--start-from` removal + `--seed-only` v2 → Task 5 ✓
- `count`/ranked selection, missing-config nudge, `config init` → Tasks 1, 4 ✓
- Docs/skill/version → Task 6 ✓
- Deferred (D–F interview/cost) → correctly out of scope ✓

**Placeholder scan:** No TBD/TODO; every code step has real code; every test step has assertions. ✓

**Type consistency:** `review(provider, model, …)` used consistently (Tasks 3→4→5); `_probe`/`preflight` signatures match tests; `_remaining_work` keys (`implement`, `self_review`, `self_fix`, `models`, `needs_fix_only`) consumed in Task 5 match Task 5's test; `_model_key` `"provider/model"` matches checkpoint keys and `_split_model_ref`. `write_status`/`step`/`step_done` all gain `total` consistently. ✓

**Known follow-ups (intentionally out of scope):** per-provider live token-limit detection (uses `_DEFAULT_CHAR_BUDGET` for now); the phase-E interview that writes a richer `config.json`.
