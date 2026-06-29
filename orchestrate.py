#!/usr/bin/env python3
"""
orchestrate.py — Multi-model coding pipeline with independent sequential reviews.

Pipeline (3 + 2*N steps, where N = number of preflight-selected reviewer models):
  1. Claude Code: implement story (branch + commit)
  2. Claude Code: parallel multi-agent review of own work → findings
  3. Claude Code: apply Claude findings → commit
  4..3+2N. For each reviewer model:
       even step: blind review of current branch state → findings
       odd step:  Claude Code applies findings → commit

Each Copilot reviewer sees only the current code state, never prior review text.
Commits after each fix step create a clear audit trail of what each model caught.

Resume after failure:
  The orchestrator writes a v2 checkpoint (model-keyed) to
  ~/.local/share/code-orchestrator/<TICKET>.json after each completed step.
  Re-running the same command resumes automatically from where it left off.
  To reset and start over, use --reset.

Usage:
    python orchestrate.py <TICKET-ID> <repo-path> [options]
    python orchestrate.py ENG-123 ~/dev/edge-fmt --base-branch origin/develop
    python orchestrate.py --version        # print "cork X.Y.Z (<git-sha>)"

Requirements:
    Python 3.10+ stdlib only — no third-party packages.
    A GitHub Copilot token (see `login` subcommand, CORK_COPILOT_TOKEN, or
    opencode's auth.json).
"""

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import NoReturn

# ── Config ────────────────────────────────────────────────────────────────────

CLAUDE         = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
COPILOT_BASE   = "https://api.githubcopilot.com"
MAX_FILE_LINES = 500
STATE_DIR      = Path.home() / ".local/share/code-orchestrator"
_OPENCODE_AUTH = Path.home() / ".local/share/opencode/auth.json"
# cork's own token store (XDG default), overridable with CORK_AUTH_FILE.
_CORK_AUTH     = Path(os.environ.get("CORK_AUTH_FILE",
                      str(Path.home() / ".config/cork/auth.json")))
# Public GitHub Copilot OAuth client id (same one editor integrations / opencode
# use). Overridable in case GitHub rotates it.
_COPILOT_CLIENT_ID = os.environ.get("CORK_COPILOT_CLIENT_ID", "Iv1.b507a08c87ecfe98")
_DEFAULT_CHAR_BUDGET = 192_000  # fallback if /models fetch fails

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
    "interactive_review": True,
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

REVIEW_SYSTEM = """\
You are a senior code reviewer. For each issue output exactly:
FILE: <path> | LINE: <n> | ISSUE: <description> | FIX: <suggestion>
Be specific. Reference exact file paths and line numbers.
Cover: correctness, error handling, edge cases,
style consistency with surrounding code, test coverage.\
"""

# ── Auth ──────────────────────────────────────────────────────────────────────

def _copilot_token() -> str:
    """Resolve the Copilot API token from (in priority order):

    1. CORK_COPILOT_TOKEN env var — the token used directly. Best for CI or a
       dedicated token; cork is fully decoupled from opencode.
    2. cork's own auth file (CORK_AUTH_FILE, default ~/.config/cork/auth.json) —
       JSON with either {"token": "..."} or the opencode shape
       {"github-copilot": {"refresh": "..."}}.
    3. opencode's auth.json (legacy fallback) — {"github-copilot": {"refresh"}}.
    """
    # 1. Explicit env var.
    env_tok = os.environ.get("CORK_COPILOT_TOKEN")
    if env_tok:
        return env_tok.strip()

    # 2. cork's own auth file, then 3. opencode's — same parse logic.
    for src in (_CORK_AUTH, _OPENCODE_AUTH):
        if not src.exists():
            continue
        try:
            data = json.loads(src.read_text())
        except json.JSONDecodeError as e:
            fail(f"Cannot parse Copilot token file {src}: {e}")
        tok = data.get("token") or data.get("github-copilot", {}).get("refresh")
        if tok:
            return tok.strip()

    fail(
        "No Copilot API token found. Set one of:\n"
        f"  • CORK_COPILOT_TOKEN env var (a Copilot token), or\n"
        f"  • {_CORK_AUTH} with {{\"token\": \"...\"}}, or\n"
        f"  • authenticate opencode with GitHub Copilot ({_OPENCODE_AUTH})."
    )


def _resolve_native_token(env_var: str, auth_key: str) -> str:
    tok = os.environ.get(env_var, "").strip()
    if tok:
        return tok
    if _CORK_AUTH.exists():
        try:
            data = json.loads(_CORK_AUTH.read_text())
        except json.JSONDecodeError as e:
            fail(f"Cannot parse {_CORK_AUTH}: {e}")
        except OSError as e:
            fail(f"Cannot read {_CORK_AUTH}: {e}")
        val = (data.get(auth_key) or "").strip()
        if val:
            return val
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


def _provider_token_available(provider: str) -> bool:
    match provider:
        case "copilot":
            if os.environ.get("CORK_COPILOT_TOKEN"):
                return True
            for src in (_CORK_AUTH, _OPENCODE_AUTH):
                if not src.exists():
                    continue
                try:
                    data = json.loads(src.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                tok = data.get("token") or data.get("github-copilot", {}).get("refresh")
                if tok:
                    return True
            return False
        case "openai":
            if os.environ.get("OPENAI_API_KEY", "").strip():
                return True
            if _CORK_AUTH.exists():
                try:
                    data = json.loads(_CORK_AUTH.read_text())
                    if (data.get("openai") or "").strip():
                        return True
                except (json.JSONDecodeError, OSError):
                    pass
            return False
        case "anthropic":
            if os.environ.get("ANTHROPIC_API_KEY", "").strip():
                return True
            if _CORK_AUTH.exists():
                try:
                    data = json.loads(_CORK_AUTH.read_text())
                    if (data.get("anthropic") or "").strip():
                        return True
                except (json.JSONDecodeError, OSError):
                    pass
            return False
        case _:
            return False


def _copilot_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_copilot_token()}",
        "x-initiator": "user",
        "Openai-Intent": "conversation-edits",
        "User-Agent": "opencode/0.1.0",
    }


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
    if provider == "copilot":
        return _copilot_headers()
    tok = _provider_token(provider)
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


def _copilot_chat(payload: dict, timeout: int = 300) -> tuple[int, object]:
    return _http_post_json(f"{COPILOT_BASE}/chat/completions",
                           _copilot_headers(), payload, timeout)


_RESPONSES_MAX_OUTPUT = 32_000      # ceiling, not a target — reasoning + findings share it
_RESPONSES_EFFORT     = "medium"    # reasoning effort for gpt-5.x review calls


def _uses_responses_api(model: str) -> bool:
    # gpt-5.x and codex models are gated to the Responses endpoint on Copilot —
    # /chat/completions returns 400 unsupported_api_for_model for them.
    return model.startswith("gpt-5") or "codex" in model


def _copilot_responses(payload: dict, timeout: int = 300) -> tuple[int, object]:
    return _http_post_json(f"{COPILOT_BASE}/responses",
                           _copilot_headers(), payload, timeout)


def _extract_chat_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    content = choices[0].get("message", {}).get("content")
    return content.strip() if content else ""


def _extract_responses_text(data: dict) -> str:
    # Copilot's proxy leaves the convenience `output_text` empty, so walk the
    # output array: skip reasoning items, collect text from message items.
    flat = data.get("output_text")
    if isinstance(flat, str) and flat.strip():
        return flat.strip()
    parts = []
    for item in data.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for c in item.get("content", []) or []:
            if c.get("type") == "output_text" and c.get("text"):
                parts.append(c["text"])
    return "".join(parts).strip()


# ── Config ──────────────────────────────────────────────────────────────────

def _validate_config(cfg: dict) -> None:
    rotation = cfg.get("rotation")
    if not isinstance(rotation, list) or not rotation:
        fail("config.rotation must be a non-empty list")
    seen: set[str] = set()
    for entry in rotation:
        if not isinstance(entry, dict) or "provider" not in entry or "model" not in entry:
            fail(f"config.rotation entry needs provider+model: {entry}")
        if entry["provider"] not in PROVIDER_BASE:
            fail(f"unknown provider '{entry['provider']}' "
                 f"(known: {', '.join(PROVIDER_BASE)})")
        key = f"{entry['provider']}/{entry['model']}"
        if key in seen:
            fail(f"duplicate rotation entry: {key}")
        seen.add(key)
    count = cfg.get("count", 3)
    if not isinstance(count, int) or count < 1:
        fail("config.count must be a positive integer")


def load_config(quiet: bool = False) -> dict:
    if not CONFIG_PATH.exists():
        if not quiet:
            print(f"  ⚠ no {CONFIG_PATH}; using built-in default — run "
                  f"`orchestrate.py config init` to customize", flush=True)
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        fail(f"Cannot read {CONFIG_PATH}: {e}")
    _validate_config(cfg)
    return cfg


def cmd_config_init() -> None:
    if CONFIG_PATH.exists():
        print(f"{CONFIG_PATH} already exists — leaving it untouched.")
        return
    _atomic_write_json(CONFIG_PATH, DEFAULT_CONFIG)
    print(f"Wrote starter config to {CONFIG_PATH} — edit `rotation`/`count` to taste.")


def cmd_config_show() -> None:
    print(json.dumps(load_config(), indent=2))


_SETTABLE_KEYS = {"interactive_review"}  # scalar bool prefs settable via `config set`; structural fields are edited in config.json directly


def cmd_config_get(key: str) -> None:
    cfg = load_config(quiet=True)
    print(json.dumps(cfg.get(key, DEFAULT_CONFIG.get(key))))


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)   # atomic on POSIX — an interrupted write can't truncate the real file


def cmd_config_set(key: str, value: str) -> None:
    if key not in _SETTABLE_KEYS:
        fail(f"Cannot set '{key}' via config set "
             f"(settable: {', '.join(sorted(_SETTABLE_KEYS))}); "
             f"edit {CONFIG_PATH} directly for structural fields.")
    low = value.strip().lower()
    if low not in ("true", "false"):
        fail(f"{key} must be true or false, got {value!r}")
    cfg = load_config(quiet=True)
    cfg[key] = (low == "true")
    _validate_config(cfg)                      # defense: never persist an invalid config
    _atomic_write_json(CONFIG_PATH, cfg)
    print(f"Set {key} = {json.dumps(cfg[key])} in {CONFIG_PATH}")

# ── Checkpoint ────────────────────────────────────────────────────────────────

def _state_path(ticket_id: str) -> Path:
    return STATE_DIR / f"{ticket_id}.json"


def _status_path(ticket_id: str) -> Path:
    return STATE_DIR / f"{ticket_id}.status.json"


def write_status(ticket_id: str, step_n: int, total: int, label: str,
                 phase: str = "running", elapsed: float | None = None) -> None:
    """Write a machine-readable status snapshot for external polling."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _status_path(ticket_id).write_text(json.dumps({
        "ticket_id": ticket_id,
        "step": step_n,
        "of": total,
        "label": label,
        "phase": phase,          # running | done | failed
        "elapsed_sec": round(elapsed, 1) if elapsed is not None else None,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }, indent=2))


def load_state(ticket_id: str) -> dict:
    p = _state_path(ticket_id)
    if p.exists():
        return json.loads(p.read_text())
    return {"ticket_id": ticket_id, "completed": []}


def mark_done(ticket_id: str, step_n: int, **extras) -> None:
    state = load_state(ticket_id)
    if step_n not in state["completed"]:
        state["completed"].append(step_n)
    state.update(extras)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(ticket_id).write_text(json.dumps(state, indent=2))


def clear_state(ticket_id: str) -> None:
    p = _state_path(ticket_id)
    if p.exists():
        p.unlink()
        print(f"  → cleared checkpoint {p}")


def mark_done_v2(tid: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(tid).write_text(json.dumps(state, indent=2))


def _save_model(tid: str, state: dict, key: str, field: str, value: str) -> None:
    state["done"].setdefault("models", {}).setdefault(key, {})[field] = value
    mark_done_v2(tid, state)

# ── Helpers ───────────────────────────────────────────────────────────────────

_step_start: float = 0.0
_current_ticket: str = ""


def step(n: int, total: int, msg: str, ticket_id: str = "") -> None:
    global _step_start, _current_ticket
    _step_start = time.monotonic()
    _current_ticket = ticket_id or _current_ticket
    print(f"\n── Step {n}/{total} — {msg}", flush=True)
    if _current_ticket:
        write_status(_current_ticket, n, total, msg, phase="running")


def skip(n: int, total: int, msg: str) -> None:
    print(f"\n── Step {n}/{total} — {msg} [skipped — already done]", flush=True)


def step_done(n: int, total: int, msg: str) -> None:
    elapsed = time.monotonic() - _step_start
    print(f"  ✓ done in {elapsed:.0f}s", flush=True)
    if _current_ticket:
        write_status(_current_ticket, n, total, msg, phase="done", elapsed=elapsed)


def fail(msg: str) -> NoReturn:
    print(f"\nFAIL: {msg}", file=sys.stderr)
    if _current_ticket:
        write_status(_current_ticket, 0, 0, msg, phase="failed")
    sys.exit(1)


def run_claude(prompt: str, cwd: str) -> str:
    result = subprocess.run(
        [CLAUDE, "--print", prompt],
        cwd=cwd, capture_output=True, text=True
    )
    if result.returncode != 0:
        fail(f"Claude exited {result.returncode}:\n{result.stderr[-2000:]}")
    return result.stdout.strip()


def git_diff_branch(cwd: str, base: str) -> str:
    return subprocess.check_output(
        ["git", "diff", f"{base}..HEAD"], cwd=cwd, text=True
    )


def changed_files_branch(cwd: str, base: str) -> dict[str, str]:
    names = subprocess.check_output(
        ["git", "diff", f"{base}..HEAD", "--name-only"], cwd=cwd, text=True
    ).strip().splitlines()
    contents: dict[str, str] = {}
    for name in names:
        path = Path(cwd) / name
        if not path.exists():
            continue
        lines = path.read_text(errors="replace").splitlines()
        if len(lines) <= MAX_FILE_LINES:
            contents[name] = "\n".join(lines)
        elif Path(name).suffix.lower() in {
            ".json", ".yaml", ".yml", ".toml", ".xml",   # config / data
            ".md", ".txt", ".rst", ".adoc",               # docs / specs
            ".props", ".targets", ".csproj", ".sln",      # MSBuild
            ".proto", ".graphql", ".sql",                 # schemas
        }:
            contents[name] = (
                f"[{len(lines)}-line {Path(name).suffix} file — "
                f"large size expected for this type; see diff for changes]"
            )
        else:
            contents[name] = (
                f"[{len(lines)}-line file — NOTE: this may itself be a finding. "
                f"Files this large often violate SRP. See diff for changes.]"
            )
    return contents


def git_commit_all(cwd: str, message: str) -> bool:
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=cwd, capture_output=True, text=True
    )
    if result.returncode == 0:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=cwd, text=True
        ).strip()
        print(f"  → committed {sha}: {message}")
        return True
    if "nothing to commit" in (result.stdout + result.stderr):
        print("  → nothing to commit")
        return False
    fail(f"git commit failed:\n{result.stderr}")
    return False


def load_agent_instructions(repo: str) -> tuple[str, str]:
    candidates = [
        Path(repo) / "code-review" / "AGENTS.md",
        Path(repo) / "code-review" / "agent.md",
        Path(repo) / "AGENTS.md",
        Path(repo) / "agent.md",
        Path(repo) / ".github" / "AGENTS.md",
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(errors="replace"), str(p)
    return "", ""


def _budget_files(files: dict[str, str], budget_chars: int) -> tuple[str, int]:
    """
    Pack as many file contents as fit within budget_chars.
    Returns (file_block_str, included_count).
    Sorts by size ascending so small files always get in.
    """
    sorted_files = sorted(files.items(), key=lambda x: len(x[1]))
    included, used = [], 0
    for name, content in sorted_files:
        entry = f"### {name}\n```\n{content}\n```"
        if used + len(entry) > budget_chars:
            break
        included.append(entry)
        used += len(entry)
    if not included:
        return "(files omitted — diff too large; see diff section)", 0
    block = "\n\n".join(included)
    if len(included) < len(files):
        block += f"\n\n_(+{len(files) - len(included)} files omitted for token budget — see diff)_"
    return block, len(included)


def _openai_compatible_call(provider: str, model: str, system: str,
                            user_msg: str, timeout: int = 300,
                            max_out: int | None = None) -> tuple[int, object]:
    # max_out caps output tokens — set small for preflight probes; None = review-sized.
    base = PROVIDER_BASE[provider]
    headers = _provider_headers(provider)
    if _uses_responses_api(model):
        return _http_post_json(f"{base}/responses", headers, {
            "model": model, "instructions": system, "input": user_msg,
            "max_output_tokens": max_out or _RESPONSES_MAX_OUTPUT,
            "reasoning": {"effort": _RESPONSES_EFFORT},
        }, timeout)
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user_msg}],
    }
    if max_out is not None:
        payload["max_tokens"] = max_out
    return _http_post_json(f"{base}/chat/completions", headers, payload, timeout)


def _call_and_extract(provider: str, model: str, system: str,
                      user_msg: str, max_out: int | None = None) -> tuple[int, str]:
    # Returns (status, extracted_text) on 200, or (status, raw_body) on non-200.
    if provider == "anthropic":
        status, body = _anthropic_call(model, system, user_msg, max_tokens=max_out or 8000)
        if status == 200:
            text = _extract_anthropic_text(body)
            return status, text
        return status, str(body)
    status, body = _openai_compatible_call(provider, model, system, user_msg, max_out=max_out)
    if status != 200:
        return status, str(body)
    text = (_extract_responses_text(body) if _uses_responses_api(model)
            else _extract_chat_text(body))
    return status, text


# ── Preflight ────────────────────────────────────────────────────────────────

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
    # A cheap availability probe — cap output hard so it can't burn review-sized
    # quota (the classification only needs the HTTP status, not the content).
    try:
        status, text = _call_and_extract(provider, model, "", "ok", max_out=16)
    except (TimeoutError, urllib.error.URLError):
        return "other"
    return _classify_preflight(status, text)


def _eligible_rotation(cfg: dict) -> list[dict]:
    kept: list[dict] = []
    providers_cfg = cfg.get("providers", {})
    for entry in cfg.get("rotation", []):
        provider = entry["provider"]
        model    = entry["model"]
        enabled  = providers_cfg.get(provider, {}).get("enabled", True)
        if not enabled:
            print(f"  ✗ {provider}/{model} skipped (provider disabled)", flush=True)
            continue
        if not _provider_token_available(provider):
            print(f"  ✗ {provider}/{model} skipped (no {provider} token)", flush=True)
            continue
        kept.append(entry)
    return kept


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


def _split_model_ref(ref: str) -> tuple[str, str]:
    # "provider/model" or bare "model" (defaults to copilot for back-compat).
    if "/" in ref:
        provider, model = ref.split("/", 1)
        return provider, model
    return "copilot", ref


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


def _retry_wait(attempt: int, max_attempts: int, reason: str, long: bool = False) -> None:
    if attempt == max_attempts - 1:
        fail(f"Review API: {reason} — giving up after {max_attempts} attempts")
    wait = (2 ** attempt) * (5 if long else 1)
    print(f"  → {reason}, retrying in {wait}s (attempt {attempt + 1}/{max_attempts})")
    time.sleep(wait)


def extract_uncertain(review: str) -> str:
    """
    Pull out the 'Uncertain / needs human judgment' section from a review.
    Returns the section body, or "" if not present.
    """
    match = re.search(
        r"#+\s*(?:Uncertain|needs human|human judgment)[^\n]*\n(.*?)(?=\n#+\s|\Z)",
        review, re.IGNORECASE | re.DOTALL
    )
    if not match:
        return ""
    body = match.group(1).strip()
    # Skip if the section is empty or just says "none" / "n/a"
    if not body or re.match(r"^(none|n/?a|—|-)\s*$", body, re.IGNORECASE):
        return ""
    return body


def print_human_summary(
    uncertain: list[tuple[str, str]],
    notes: list[tuple[str, str]],
) -> None:
    """
    Print items needing human attention after the pipeline completes.
    uncertain: [(reviewer_label, uncertain_section_text), ...]
    notes:     [(step_label, claude_fix_response), ...]
    """
    has_uncertain = any(text for _, text in uncertain)
    has_notes = any(text for _, text in notes)
    if not has_uncertain and not has_notes:
        return

    print("\n── Human attention needed ────────────────────────────────")

    if has_uncertain:
        print("\nUncertain items requiring your judgment:")
        for label, text in uncertain:
            if text:
                print(f"\n  [{label}]")
                for line in text.splitlines():
                    print(f"    {line}")

    if has_notes:
        print("\nClaude Code notes from fix steps (pushbacks / partial applies):")
        for label, text in notes:
            if text:
                # Show first 600 chars — enough to see reasoning without flooding terminal
                preview = text[:600].strip()
                if len(text) > 600:
                    preview += "\n    … (truncated — full text in checkpoint)"
                print(f"\n  [{label}]")
                for line in preview.splitlines():
                    print(f"    {line}")


# ── Prompt builders ───────────────────────────────────────────────────────────

def prompt_initial(ticket_id: str) -> str:
    return (
        f"Use your Linear MCP tools to fetch ticket {ticket_id}. "
        "Search mem0 for relevant context about this codebase — architecture, "
        "patterns, past decisions. "
        f"Create a git branch following the repo's branch naming convention in CLAUDE.md. "
        f"The branch must start with 'feature/{ticket_id}' and include a short kebab-case "
        f"slug derived from the ticket title "
        f"(e.g. feature/{ticket_id.lower()}-per-station-backdoor-routing). "
        "Implement the story. Write or update tests if the codebase has them. "
        "\n\n"
        "IMPORTANT — keep the diff small and focused:\n"
        "- Target ≤500 changed lines. If you find yourself touching more than ~3 files "
        "outside the story's stated scope, stop and reconsider.\n"
        "- Do NOT fix pre-existing issues, refactor surrounding code, or add features "
        "beyond what the story explicitly requires. Those belong in separate stories.\n"
        "- If the story's acceptance criteria genuinely require >500 lines to implement "
        "correctly, implement only the smallest complete, mergeable slice and call out "
        "in your summary what was deferred and why. Do not silently expand scope.\n"
        "- If you discover a split signal mid-implementation (e.g. the story touches "
        "multiple language runtimes, or requires a new domain type AND all its downstream "
        "consumers), flag it explicitly in your summary so a follow-on story can be filed.\n"
        "\n"
        "When done, output a concise paragraph summarising what you changed, why, "
        "and — if scope was trimmed — what was intentionally deferred. "
        "Do NOT commit — the orchestrator will commit after this step."
    )


def prompt_claude_review(base: str, instructions_path: str) -> str:
    review_src = (
        f"Read and follow the review instructions in {instructions_path}."
        if instructions_path
        else "Perform a thorough multi-agent code review."
    )
    return (
        f"Review the current feature branch against {base}. "
        f"The full branch diff is available via: git diff {base}..HEAD\n\n"
        f"{review_src}\n\n"
        "Output ONLY a structured findings report. "
        "Do NOT apply any fixes. Do NOT edit any files."
    )


def prompt_fix(summary: str, base: str, review: str, ticket_id: str,
               is_final: bool = False) -> str:
    save_note = (
        "\n\nAfter making fixes, use your mem0 MCP tools to save any non-obvious "
        "architectural decisions, patterns, or gotchas from this implementation."
        if is_final else ""
    )
    return (
        f"## Story Summary\n{summary}\n\n"
        "## Current Branch State\n"
        f"Run `git diff {base}..HEAD` to see all changes on this branch.\n\n"
        f"## Code Review Findings\n{review}\n\n"
        "Address findings in the Critical, Important, Minor, Cross-cutting, and "
        "Promotion candidates sections. Make targeted fixes — don't rewrite what works. "
        "Search mem0 if you need context about patterns or past decisions.\n\n"
        "DO NOT attempt to resolve items in 'Uncertain', 'needs human judgment', or "
        "'Out of scope' sections — those are flagged for human review, not automated fixing.\n\n"
        "If you choose not to apply a finding (because it conflicts with established patterns, "
        "would break something, or is genuinely wrong for this codebase), explain your "
        "reasoning clearly in your response. Your response is captured and shown to the human.\n\n"
        f"If a finding requires effort too large to address inline (a significant refactor, "
        f"a new service, a cross-cutting change), use your Linear MCP tools to create a new "
        f"story for it, linked to {ticket_id}. Include the created story ID in your response.\n\n"
        f"If you discover something during fixes that materially changes the scope or approach "
        f"of the current story (a pivot, a learned constraint, a design correction), update "
        f"ticket {ticket_id} via your Linear MCP tools to reflect it.\n\n"
        f"Do NOT commit — the orchestrator will commit after this step.{save_note}"
    )

def prompt_push_pr(ticket_id: str, base: str, summary: str) -> str:
    return (
        f"The implementation and all review passes for {ticket_id} are complete. "
        f"Do the following in order:\n\n"
        f"1. Push the branch to origin: `git push -u origin HEAD`\n\n"
        f"2. Create a GitHub PR using `gh pr create` with:\n"
        f"   - Title: the Linear ticket title (fetch it from Linear MCP if needed)\n"
        f"   - Body: a summary of what was implemented, followed by a brief "
        f"     bullet list of the most significant findings each review pass caught. "
        f"     Include the Linear ticket URL at the bottom.\n"
        f"   - Base branch: {base}\n"
        f"   - Do NOT mark as draft — this is ready for human review.\n\n"
        f"3. Output the PR URL.\n\n"
        f"## Implementation summary\n{summary}"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def cmd_status(ticket_id: str) -> None:
    """Print current pipeline status for a ticket."""
    sp = _status_path(ticket_id)
    cp = _state_path(ticket_id)
    if sp.exists():
        s = json.loads(sp.read_text())
        phase = s.get("phase", "?")
        icon = {"running": "⏳", "done": "✓", "failed": "✗"}.get(phase, "?")
        elapsed = f"  ({s['elapsed_sec']}s)" if s.get("elapsed_sec") else ""
        print(f"{icon} {ticket_id}: Step {s['step']}/{s['of']} — {s['label']}{elapsed}")
        print(f"   phase={phase}  updated={s.get('updated_at','?')}")
    elif cp.exists():
        state = json.loads(cp.read_text())
        done = sorted(state.get("completed", []))
        print(f"✓ {ticket_id}: checkpoint exists, steps done: {done}")
    else:
        print(f"? {ticket_id}: no status or checkpoint found")


def _post_form(url: str, fields: dict[str, str], timeout: int = 15) -> dict:
    """POST application/x-www-form-urlencoded, parse JSON. GitHub device-flow
    returns errors as HTTP 200 (with an `error` field) or 4xx — handle both."""
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            fail(f"HTTP {e.code} from {url}")


def cmd_login() -> None:
    """GitHub device-authorization flow → mint a Copilot OAuth token → write it to
    cork's own auth file (CORK_AUTH_FILE, default ~/.config/cork/auth.json).

    Makes cork self-sufficient: no manual token copying and no dependency on
    opencode's auth.json. Re-run any time the token expires.
    """
    print(f"Requesting device code (client_id={_COPILOT_CLIENT_ID})…", flush=True)
    dc = _post_form("https://github.com/login/device/code",
                    {"client_id": _COPILOT_CLIENT_ID, "scope": "read:user"})
    if "device_code" not in dc:
        fail(f"Device-code request failed: {dc.get('error_description') or dc}")

    print(f"\n  Open:  {dc['verification_uri']}")
    print(f"  Code:  {dc['user_code']}\n")
    print("Waiting for authorization (Ctrl-C to cancel)…", flush=True)

    interval = int(dc.get("interval", 5))
    deadline = time.time() + int(dc.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        tok = _post_form("https://github.com/login/oauth/access_token", {
            "client_id": _COPILOT_CLIENT_ID,
            "device_code": dc["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if tok.get("access_token"):
            _CORK_AUTH.parent.mkdir(parents=True, exist_ok=True)
            _CORK_AUTH.write_text(json.dumps({"token": tok["access_token"]}, indent=2))
            _CORK_AUTH.chmod(0o600)
            print(f"\n✓ Authorized. Token written to {_CORK_AUTH} (chmod 600).")
            print("  cork will now use this token before falling back to opencode.")
            return
        err = tok.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        fail(f"Device authorization failed: {err or tok}")
    fail("Device authorization timed out — re-run `orchestrate.py login`.")


def cmd_review(tid: str, repo: str, base: str, model_ref: str, validate: bool = True) -> None:
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
    _st = load_state(tid)
    story = (_st.get("done", {}).get("summary") or _st.get("summary")
             or f"Review the branch changes for {tid}.")
    print(f"\n── Review: {provider}/{model} — {len(files)} files, "
          f"{len(diff.splitlines())} diff lines vs {base}\n", flush=True)
    print(review(provider, model, instructions, story, diff, files, _DEFAULT_CHAR_BUDGET))


def cmd_preflight() -> None:
    cfg = load_config()
    selected = preflight(_eligible_rotation(cfg), cfg.get("count", 3))
    for s in selected:
        print(f"{s['provider']}/{s['model']}")


def _version() -> str:
    here = Path(__file__).resolve().parent
    vfile = here / "VERSION"
    ver = vfile.read_text().strip() if vfile.exists() else "unknown"
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=here, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        dirty = subprocess.run(
            ["git", "diff", "--quiet"], cwd=here,
        ).returncode != 0
        return f"cork {ver} ({sha}{'+dirty' if dirty else ''})"
    except Exception:
        return f"cork {ver}"


def main() -> None:
    # `login` and `--version` are standalone — no ticket_id, handled before
    # argparse (which requires a positional ticket_id).
    if len(sys.argv) >= 2 and sys.argv[1] == "login":
        cmd_login()
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "-V", "version"):
        print(_version())
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "config":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "init":
            cmd_config_init()
        elif sub == "get":
            if len(sys.argv) < 4:
                fail("usage: orchestrate.py config get <key>")
            cmd_config_get(sys.argv[3])
        elif sub == "set":
            if len(sys.argv) < 5:
                fail("usage: orchestrate.py config set <key> <value>")
            cmd_config_set(sys.argv[3], sys.argv[4])
        elif sub in ("", "show"):
            cmd_config_show()
        else:
            fail(f"unknown config subcommand: {sub!r} (use init|show|get|set)")
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "preflight":
        cmd_preflight()
        return

    parser = argparse.ArgumentParser(
        description="Linear story → dynamic multi-model review pipeline"
    )
    parser.add_argument("ticket_id",  help="Linear ticket ID, e.g. ENG-123")
    parser.add_argument("repo_path",  nargs="?", default=None,
                        help="Absolute path to target git repo (omit with --status)")
    parser.add_argument("--base-branch", default="origin/develop",
                        help="Branch to diff against (default: origin/develop)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete checkpoint and start from scratch")
    parser.add_argument("--seed-only", action="store_true",
                        help="Seed a v2 checkpoint from existing branch commits and exit. "
                             "Use when implementation is already done — then re-run "
                             "to begin reviews (preflight runs automatically on the next run).")
    parser.add_argument("--status", action="store_true",
                        help="Print current pipeline status for ticket_id and exit.")
    parser.add_argument("--skip-validation", action="store_true",
                        help="Bypass preflight probes and use the configured rotation's "
                             "first `count` entries directly, with the conservative default "
                             "char budget. Saves Copilot quota on repeated runs.")
    parser.add_argument("--review-model", metavar="MODEL",
                        help="Review-only mode: run ONE Copilot model's review of the "
                             "branch diff, print findings to stdout, and exit. Stateless "
                             "(reviewer sees only diff + changed files + AGENTS.md). Used by "
                             "the session-driven cork skill, where the active Claude session "
                             "does the implementing and fixing instead of a headless subprocess.")
    args = parser.parse_args()

    if args.status:
        cmd_status(args.ticket_id)
        return

    if not args.repo_path:
        fail("repo_path is required (omit only with --status)")

    tid  = args.ticket_id
    repo = str(Path(args.repo_path).expanduser().resolve())
    base = args.base_branch

    if not Path(repo).is_dir():
        fail(f"repo_path does not exist: {repo}")

    if args.review_model:
        cmd_review(tid, repo, base, args.review_model, validate=not args.skip_validation)
        return

    if args.reset:
        clear_state(tid)

    # ── --seed-only: write v2 checkpoint from existing commits and exit ───────
    if args.seed_only:
        commits = subprocess.check_output(
            ["git", "log", f"{base}..HEAD", "--oneline"], cwd=repo, text=True
        ).strip()
        if not commits:
            fail(f"No commits found on branch vs {base}. Is the branch checked out?")
        summary = f"Implementation already complete on branch. Commits:\n{commits}"
        seed_state: dict = {
            "version": 2, "ticket_id": tid,
            "done": {"implement": True, "summary": summary},
            "base": base, "repo": repo,
        }
        mark_done_v2(tid, seed_state)
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, text=True
        ).strip()
        print(f"Seeded v2 checkpoint for {tid}")
        print(f"Branch:  {branch}")
        print(f"Commits: {len(commits.splitlines())} commits vs {base}")
        print(f"Run:     python orchestrate.py {tid} {repo} --base-branch {base}")
        return

    # ── Load config + v2 checkpoint ───────────────────────────────────────────
    cfg   = load_config()
    if _state_path(tid).exists():
        state = load_state(tid)
        if state.get("version") != 2:
            fail(
                f"Found an old (pre-v2) checkpoint for {tid} at {_state_path(tid)}. "
                f"The checkpoint format changed. Re-run with --reset to start fresh "
                f"(this discards the old in-flight state)."
            )
    else:
        state = {"version": 2, "ticket_id": tid, "done": {}}

    # Freeze the selected rotation at first run; reuse it on resume.
    # Never re-preflight on resume — the probes cost Copilot quota and the
    # rotation must stay stable so step numbers don't shift mid-run.
    if "rotation" not in state:
        if args.skip_validation:
            sel = _eligible_rotation(cfg)[:cfg.get("count", 3)]
        else:
            sel = preflight(_eligible_rotation(cfg), cfg.get("count", 3))
        if not sel:
            fail("No eligible models in rotation — check providers.enabled and tokens in config.json/auth.json.")
        state["rotation"] = sel
        mark_done_v2(tid, state)

    rotation = state["rotation"]
    N     = len(rotation)
    total = 3 + 2 * N

    rem     = _remaining_work(state)
    summary = state.get("done", {}).get("summary") or state.get("summary", "")

    instructions, instructions_path = load_agent_instructions(repo)
    if instructions_path:
        print(f"Review instructions: {instructions_path} ({len(instructions)} chars)")
    else:
        print("No AGENTS.md found — using default review format")

    # Accumulators for human-attention summary printed at the end
    uncertain_items: list[tuple[str, str]] = []
    fix_notes: list[tuple[str, str]] = []

    # ── Step 1: Implement ────────────────────────────────────────────────────
    if rem["implement"]:
        step(1, total, f"Claude Code: implement {tid}", ticket_id=tid)
        summary = run_claude(prompt_initial(tid), cwd=repo)
        print(f"  Summary: {summary[:200]}…")
        git_commit_all(repo, f"feat: implement {tid}")
        state["done"]["implement"] = True
        state["done"]["summary"]   = summary
        mark_done_v2(tid, state)
        step_done(1, total, f"Claude Code: implement {tid}")
    else:
        skip(1, total, f"Claude Code: implement {tid}")

    diff       = git_diff_branch(repo, base)
    files      = changed_files_branch(repo, base)
    if not diff.strip():
        fail("No diff vs base branch — nothing to review.")
    diff_lines = len(diff.splitlines())
    print(f"  {len(files)} files, {diff_lines} diff lines vs {base}")

    # ── Diff-size gate ───────────────────────────────────────────────────────
    # A diff > ~1,500 lines saturates reviewer context and overflows smaller
    # models (gpt-4o at 64k tokens fails around 7,000 lines). Warn early so
    # the story can be split before investing review time.
    _WARN_LINES  = 1_500   # soft: flag for splitting consideration
    _BLOCK_LINES = 5_000   # hard: refuse to continue (almost certainly too large)
    if diff_lines >= _BLOCK_LINES:
        fail(
            f"Diff is {diff_lines} lines — too large for reliable multi-model review "
            f"(hard limit: {_BLOCK_LINES}). Split the branch into smaller stories "
            f"(target ≤500 lines each) before re-running the pipeline."
        )
    if diff_lines >= _WARN_LINES:
        print(
            f"\n  ⚠  WARNING: diff is {diff_lines} lines (soft limit: {_WARN_LINES}).\n"
            f"     Consider splitting into smaller stories. Smaller diffs:\n"
            f"     • Keep each review pass under the smallest model's token budget\n"
            f"     • Give reviewers a focused surface to reason about\n"
            f"     • Make findings easier to attribute and fix\n"
            f"     Continuing — but expect reduced review quality.\n"
        )

    # ── Step 2: Claude multi-agent self-review ────────────────────────────────
    if rem["self_review"]:
        step(2, total, "Claude Code: multi-agent self-review", ticket_id=tid)
        self_review_out = run_claude(prompt_claude_review(base, instructions_path), cwd=repo)
        print(f"  {self_review_out[:300]}…")
        state["done"]["self_review"] = self_review_out
        mark_done_v2(tid, state)
        step_done(2, total, "Claude Code: multi-agent self-review")
    else:
        skip(2, total, "Claude Code: multi-agent self-review")
        self_review_out = state["done"].get("self_review", "")

    uncertain_items.append(("Claude self-review", extract_uncertain(self_review_out)))

    # ── Step 3: Apply self-review findings ───────────────────────────────────
    if rem["self_fix"]:
        step(3, total, "Claude Code: apply self-review findings", ticket_id=tid)
        fix_out = run_claude(prompt_fix(summary, base, self_review_out, tid), cwd=repo)
        fix_notes.append(("Step 3 — self-review fixes", fix_out))
        git_commit_all(repo, f"fix: apply Claude self-review [{tid}]")
        state["done"]["self_fix"] = fix_out
        mark_done_v2(tid, state)
        step_done(3, total, "Claude Code: apply self-review findings")
    else:
        skip(3, total, "Claude Code: apply self-review findings")
        fix_notes.append(("Step 3 — self-review fixes", state["done"].get("self_fix", "")))

    # ── Per-model review + fix loop ───────────────────────────────────────────
    for i, entry in enumerate(rotation):
        key        = _model_key(entry)
        review_step = 4 + 2 * i
        fix_step    = 5 + 2 * i

        if key in rem["models"] and key not in rem["needs_fix_only"]:
            step(review_step, total, f"Blind review: {key}", ticket_id=tid)
            diff  = git_diff_branch(repo, base)
            files = changed_files_branch(repo, base)
            print(f"  Sending {len(files)} files, {len(diff.splitlines())} lines to {key}")
            review_out = review(
                entry["provider"], entry["model"],
                instructions, summary, diff, files, _DEFAULT_CHAR_BUDGET,
            )
            print(f"  {review_out[:300]}…")
            _save_model(tid, state, key, "review", review_out)
            step_done(review_step, total, f"Blind review: {key}")
        else:
            skip(review_step, total, f"Blind review: {key}")
            review_out = state["done"].get("models", {}).get(key, {}).get("review", "")

        uncertain_items.append((key, extract_uncertain(review_out)))

        if key in rem["models"]:
            is_final = (i == N - 1)
            label    = f"Apply {key} findings" + (" + save to mem0" if is_final else "")
            step(fix_step, total, f"Claude Code: {label}", ticket_id=tid)
            fix_out = run_claude(
                prompt_fix(summary, base, review_out, tid, is_final=is_final), cwd=repo
            )
            fix_notes.append((f"Step {fix_step} — {key} fixes", fix_out))
            git_commit_all(repo, f"fix: apply {key} review [{tid}]")
            _save_model(tid, state, key, "fix", fix_out)
            step_done(fix_step, total, f"Claude Code: {label}")
        else:
            skip(fix_step, total, f"Claude Code: apply {key} findings")
            fix_notes.append((
                f"Step {fix_step} — {key} fixes",
                state["done"].get("models", {}).get(key, {}).get("fix", ""),
            ))

    final_diff = git_diff_branch(repo, base)
    clear_state(tid)

    # ── Push + open PR ───────────────────────────────────────────────────────
    print(f"\n── Push & PR ─────────────────────────────────────────────")
    pr_output = run_claude(prompt_push_pr(tid, base, summary), cwd=repo)
    print(f"  {pr_output[:300]}…" if len(pr_output) > 300 else f"  {pr_output}")

    branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, text=True
    ).strip()

    write_status(tid, total, total, "Pipeline complete", phase="done")
    print(f"\n── Done ──────────────────────────────────────────────────")
    print(f"Branch:     {branch}")
    print(f"Base:       {base}")
    print(f"Total diff: {len(final_diff.splitlines())} lines vs {base}")
    print(f"Commits:    git log --oneline {base}..HEAD")

    print_human_summary(uncertain_items, fix_notes)


if __name__ == "__main__":
    main()
