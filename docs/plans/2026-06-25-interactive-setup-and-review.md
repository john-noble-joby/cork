# Interactive Setup + Pause-Between-Reviews Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tasks 2–5 are prose-skill/doc work — verification is structure + install + a fresh-agent behavior check, not `unittest`.

**Goal:** Add a conversational `cork-setup` skill and an `interactive_review` mode (default on) that pauses cork full-mode and the Copilot loop after each reviewer for human-controlled fixes; make `auth.json` the documented canonical token store.

**Architecture:** A new top-level `config.json` key `interactive_review` (default true) is read by the skills via a new `config get` subcommand and written by `cork-setup` via a new `config set` subcommand. The cork and copilot-review-loop skills gain a "pre-pass → present → wait" pause gated on that flag. No change to token resolution or the pipeline engine.

**Tech Stack:** Python 3.10+ stdlib only; stdlib `unittest`. Skills are Markdown.

## Global Constraints

- **Python 3.10+ stdlib only** — no third-party packages; tests use `unittest`.
- **No classes** (TestCase subclasses in tests are fine); **type hints on every function**; **no docstrings** (comment only non-obvious WHY).
- Errors via `fail(msg)` (`-> NoReturn`); `pathlib.Path`; `print(..., flush=True)` for progress.
- **Secrets never written to `config.json`**; `auth.json` is chmod 600. The setup skill must NOT ask the user to paste native API keys into the chat.
- **`interactive_review` default is `true`** (absent → true).
- Skill version stamps must equal `VERSION`; `install.sh` enforces this.
- Run tests: `python -m unittest discover -s tests -v`.
- Spec: `docs/specs/2026-06-25-interactive-setup-and-review.md`.

---

## File Structure

- **Modify `orchestrate.py`** — `DEFAULT_CONFIG` gets `interactive_review`; add `_load_config_quiet`, `cmd_config_get`, `cmd_config_set`, and `config get`/`config set` dispatch.
- **Modify `tests/test_config.py`** — cover get default, set round-trip, set-creates-file.
- **Create `skills/cork-setup/SKILL.md`** — the setup skill.
- **Modify `skills/cork/SKILL.md`** — interactive-review pause in full mode.
- **Modify `skills/copilot-review-loop/SKILL.md`** — interactive-review pause; **`skills/devit/SKILL.md`** — one-line inherit note.
- **Modify `install.sh`** — register `cork-setup`; final message → "set up cork".
- **Modify `README.md`, `skills/README.md`, `VERSION`** — token clarification, docs, version bump (final task).

---

### Task 1: Config plumbing — `interactive_review` default + `config get`/`config set`

**Files:**
- Modify: `orchestrate.py` (DEFAULT_CONFIG ~line 72; new fns near `cmd_config_show` ~line 347; dispatch ~line 1013)
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: `CONFIG_PATH`, `DEFAULT_CONFIG`, `_validate_config`, `fail`, `copy`, `json` (all existing).
- Produces: `_load_config_quiet() -> dict`; `cmd_config_get(key: str) -> None` (prints the JSON value, `interactive_review` defaulting to `true`); `cmd_config_set(key: str, value: str) -> None` (coerces + writes `config.json`); `config get <key>` / `config set <key> <value>` CLI.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
import io
from contextlib import redirect_stdout


class ConfigGetSetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self._orig = orchestrate.CONFIG_PATH
        orchestrate.CONFIG_PATH = self.path

    def tearDown(self):
        orchestrate.CONFIG_PATH = self._orig
        self.tmp.cleanup()

    def test_get_interactive_review_defaults_true_when_no_file(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            orchestrate.cmd_config_get("interactive_review")
        self.assertEqual(buf.getvalue().strip(), "true")

    def test_get_interactive_review_defaults_true_when_key_absent(self):
        self.path.write_text(json.dumps({
            "rotation": [{"provider": "copilot", "model": "gpt-4.1"}],
        }))
        buf = io.StringIO()
        with redirect_stdout(buf):
            orchestrate.cmd_config_get("interactive_review")
        self.assertEqual(buf.getvalue().strip(), "true")

    def test_set_then_get_roundtrip(self):
        self.path.write_text(json.dumps({
            "rotation": [{"provider": "copilot", "model": "gpt-4.1"}],
        }))
        orchestrate.cmd_config_set("interactive_review", "false")
        self.assertEqual(json.loads(self.path.read_text())["interactive_review"], False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            orchestrate.cmd_config_get("interactive_review")
        self.assertEqual(buf.getvalue().strip(), "false")

    def test_set_creates_file_from_default(self):
        orchestrate.cmd_config_set("interactive_review", "false")
        self.assertTrue(self.path.exists())
        cfg = json.loads(self.path.read_text())
        self.assertEqual(cfg["interactive_review"], False)
        self.assertIn("rotation", cfg)   # seeded from DEFAULT_CONFIG
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m unittest tests.test_config -v`
Expected: FAIL with `AttributeError: ... 'cmd_config_get'`.

- [ ] **Step 3: Add `interactive_review` to `DEFAULT_CONFIG`**

In `DEFAULT_CONFIG` (after `"count": 3,`):

```python
    "count": 3,
    "interactive_review": True,
```

- [ ] **Step 4: Add the quiet loader + get/set functions** (after `cmd_config_show`)

```python
# Known top-level config defaults (used when the key is absent / no file).
_CONFIG_DEFAULTS = {"interactive_review": True}


def _load_config_quiet() -> dict:
    # Like load_config() but without the "no config" stdout nudge — so `config get`
    # output stays clean for callers that parse it.
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError) as e:
            fail(f"Cannot read {CONFIG_PATH}: {e}")
        _validate_config(cfg)
        return cfg
    return copy.deepcopy(DEFAULT_CONFIG)


def cmd_config_get(key: str) -> None:
    cfg = _load_config_quiet()
    value = cfg.get(key, _CONFIG_DEFAULTS.get(key))
    print(json.dumps(value))


def _coerce(value: str) -> object:
    if value == "true":
        return True
    if value == "false":
        return False
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def cmd_config_set(key: str, value: str) -> None:
    cfg = _load_config_quiet()
    cfg[key] = _coerce(value)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"Set {key} = {json.dumps(cfg[key])} in {CONFIG_PATH}")
```

- [ ] **Step 5: Wire `config get`/`config set` into `main()`**

Replace the existing `config` dispatch block:

```python
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
        else:
            cmd_config_show()
        return
```

- [ ] **Step 6: Run tests + smoke**

Run: `python -m unittest tests.test_config -v && python orchestrate.py config get interactive_review`
Expected: all PASS; the command prints `true`.

- [ ] **Step 7: Commit**

```bash
git add orchestrate.py tests/test_config.py
git commit -m "feat(config): interactive_review default + config get/set"
```

---

### Task 2: `cork-setup` skill + install registration

**Files:**
- Create: `skills/cork-setup/SKILL.md`
- Modify: `install.sh` (`SKILLS` array; final message)

**Interfaces:**
- Consumes: `orchestrate.py` `login`, `preflight`, `config init`, `config get`/`config set` (Task 1), `statusline.py` + the `settings.json` snippet.

- [ ] **Step 1: Create `skills/cork-setup/SKILL.md`**

```markdown
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
```

- [ ] **Step 2: Register in `install.sh`**

```bash
SKILLS=(cork copilot-review-loop devit cork-setup)
```

- [ ] **Step 3: Update the install final message** — change the `orchestrate.py:` line area so the closing guidance mentions setup. After the `Done.`/CORK_HOME block, the script already prints status; add before `echo "Done."` (or in the trailing echo):

```bash
echo "Next: restart Claude Code, then say \"set up cork\" to finish configuration."
```

- [ ] **Step 4: Verify install deploys it**

Run: `./install.sh`
Expected: a `✓ cork-setup installed (stamp v0.7.0)` line. It will warn about stamp-vs-VERSION drift (0.7.0 vs current 0.6.2) until Task 5 bumps VERSION — that's expected; confirm the file copied (`ls ~/.claude/skills/cork-setup/SKILL.md`).

- [ ] **Step 5: Commit**

```bash
git add skills/cork-setup/SKILL.md install.sh
git commit -m "feat(cork-setup): guided setup skill; register in install.sh"
```

---

### Task 3: Interactive-review pause in the cork skill (full mode)

**Files:**
- Modify: `skills/cork/SKILL.md` (the "Steps 3+" full-mode section)

**Interfaces:**
- Consumes: `config get interactive_review` (Task 1).

- [ ] **Step 1: Add the interactive-review block** to the "### Steps 3+ — One blind pass per model" section, immediately after its rotation paragraph (before the `--review-model` code block):

```markdown
**Interactive review (default on).** Read the preference once before the rotation:

\`\`\`bash
PAUSE=$(python "$CORK_HOME/orchestrate.py" config get interactive_review)   # true | false
\`\`\`

- **`true` (default):** after fetching **each** model's review, apply NOTHING yet.
  (1) **Pre-pass:** read the findings and form your recommendation — which you'd fix, which
  you'd push back on (with a reason), which are out of scope. (2) **Present** the model's
  findings *and* your recommendation, numbered. (3) **Wait** for the user to choose:
    - **Fix all** — apply every finding, run tests, commit, continue.
    - **Pick specific** (e.g. "1, 3, 4") — apply those, commit; leave/push back the rest as they say.
    - **Push back** — record won't-fix items + reasons for the final pushback summary.
    - **Proceed (no changes)** — apply nothing from this model; make **zero edits/commits**; move on.
  Do not apply anything or advance to the next model until they answer.
- **`false`:** behave autonomously (you apply the valid findings, push back with reasoning
  where wrong, and commit) — the flow described below.
```

- [ ] **Step 2: Verify structure**

Run:
```bash
grep -q 'config get interactive_review' skills/cork/SKILL.md && echo "flag read present"
grep -q 'Proceed (no changes)' skills/cork/SKILL.md && echo "no-change option present"
```
Expected: both print.

- [ ] **Step 3: Deploy + commit**

```bash
./install.sh >/dev/null
git add skills/cork/SKILL.md
git commit -m "feat(cork): interactive-review pause between model reviews (full mode)"
```

---

### Task 4: Interactive-review pause in the Copilot loop (+ devit note)

**Files:**
- Modify: `skills/copilot-review-loop/SKILL.md` (loop body, step 4)
- Modify: `skills/devit/SKILL.md` (Phases 4 & 6 — one-line inherit note)

**Interfaces:**
- Consumes: `config get interactive_review` (Task 1).

- [ ] **Step 1: Add interactive-review to the loop body.** In `skills/copilot-review-loop/SKILL.md`, insert a new subsection just before "### 4. Process each unresolved thread":

```markdown
### 3b. Interactive review (default on)

Read the preference once at loop start:

\`\`\`bash
CORK_HOME="${CORK_HOME:-$HOME/dev/cork}"
PAUSE=$(python "$CORK_HOME/orchestrate.py" config get interactive_review)   # true | false
\`\`\`

- **`true` (default):** after fetching this pass's unresolved comments (step 3), apply
  NOTHING yet. (1) **Pre-pass:** form your recommendation per comment (fix / push back +
  reason / out of scope). (2) **Present** the comments *and* your recommendation, numbered.
  (3) **Wait** for the user to choose: **Fix all** · **Pick specific** · **Push back**
  (reason → posted as the PR reply, then resolve) · **Proceed (no changes)** — leave the
  threads unresolved this tick and make zero edits. Then carry out step 4 for the chosen
  items only.
- **`false`:** process every comment autonomously (step 4 as written).
```

- [ ] **Step 2: Add the devit inherit note.** In `skills/devit/SKILL.md`, append to Phase 4 (after the cork review paragraph) and Phase 6 (after the copilot-loop paragraph) the sentence:

```markdown
(If `interactive_review` is on — the default — cork and the Copilot loop will pause after each
reviewer for you to choose what to apply; devit inherits this, so expect to be prompted
between reviewers.)
```

- [ ] **Step 3: Verify**

Run:
```bash
grep -q 'config get interactive_review' skills/copilot-review-loop/SKILL.md && echo "loop reads flag"
grep -q 'interactive_review' skills/devit/SKILL.md && echo "devit notes inherit"
```
Expected: both print.

- [ ] **Step 4: Deploy + commit**

```bash
./install.sh >/dev/null
git add skills/copilot-review-loop/SKILL.md skills/devit/SKILL.md
git commit -m "feat(copilot-loop): interactive-review pause; devit inherit note"
```

---

### Task 5: Docs + version bump to 0.7.0

**Files:**
- Modify: `README.md` (token clarification; `interactive_review`; setup pointer)
- Modify: `skills/README.md` (cork-setup entry)
- Modify: `VERSION`; re-stamp `skills/cork`, `skills/copilot-review-loop`, `skills/devit`, `skills/cork-setup`

- [ ] **Step 1: README token clarification.** In the "### Environment variables" section, change the lead-in so tokens are auth.json-first. Replace the sentence "None are required if you clone to `~/dev/cork` and use `orchestrate.py login`." with:

```markdown
Tokens live in **`~/.config/cork/auth.json`** (written by `orchestrate.py login`; chmod 600) —
`{"token": "<copilot>", "openai": "<key>", "anthropic": "<key>"}`. The env vars below are
**overrides** (resolved first), not required. None are needed if you clone to `~/dev/cork`
and run `login`.
```

- [ ] **Step 2: README — document interactive review + setup skill.** In the Setup section, change step 7's tail and add a line about setup; and in "How it works", add a short subsection. Add after the Setup numbered list:

```markdown
Most of steps 3–6 are handled for you by the **`cork-setup` skill** — after `install.sh` and a
restart, just say **"set up cork"** and it walks you through the token, models, the
pause-between-reviews preference, and the status line.
```

And add to "How it works" (after "Model selection"):

```markdown
### Interactive review (`interactive_review`, default on)

When on, cork (full mode) and the Copilot review loop **pause after each reviewer**: the
session presents that reviewer's findings plus its own recommendation and waits for you to
choose — **fix all**, **pick specific**, **push back** (with a reason), or **proceed with no
changes**. devit inherits this. Turn it off for fully autonomous runs:
`python orchestrate.py config set interactive_review false` (or via `cork-setup`). It does
not affect cork *review-only* or the headless pipeline.
```

- [ ] **Step 3: skills/README.md — add cork-setup entry.** After the `devit` entry:

```markdown
### cork-setup
Guided, interactive first-time setup. Say "set up cork" and it walks through the Copilot
token (`login`), review models (`config init`/`preflight`), the pause-between-reviews
preference (`interactive_review`), the status line, and Linear/mem0 MCP checks. Run it after
`install.sh` + a restart.
```

- [ ] **Step 4: Bump VERSION + re-stamp all four skills.**

Set `VERSION` to `0.7.0`. In each of `skills/cork/SKILL.md`, `skills/copilot-review-loop/SKILL.md`, `skills/devit/SKILL.md`, `skills/cork-setup/SKILL.md`, change the `**Version:** 0.6.2` (cork-setup is already `0.7.0` from Task 2) line to `**Version:** 0.7.0`.

- [ ] **Step 5: Install + verify no drift**

Run: `./install.sh`
Expected: four `✓ … installed (stamp v0.7.0)` lines (cork, copilot-review-loop, devit, cork-setup), `orchestrate.py: cork 0.7.0 (<sha>)`, **no drift warning**.

- [ ] **Step 6: Commit**

```bash
git add README.md skills/README.md VERSION skills/
git commit -m "docs: document cork-setup + interactive_review; tokens via auth.json; bump 0.7.0"
```

---

## Self-Review

**Spec coverage:**
- A. cork-setup skill (token/models/pause/statusline/MCP/summary, secret-handling) → Task 2 ✓
- B. interactive_review flag default true + read via `config get` → Task 1 ✓; pause in cork full mode → Task 3 ✓; pause in copilot loop → Task 4 ✓; devit inherits → Task 4 note ✓; menu incl. "Proceed (no changes)" → Tasks 3 & 4 ✓; pre-pass→present→wait → Tasks 3 & 4 ✓; off = autonomous → Tasks 3 & 4 ✓
- C. auth.json canonical + README env demotion → Task 5 ✓
- Supporting: DEFAULT_CONFIG key, `config get`/`set` → Task 1 ✓; install registration → Task 2 ✓; VERSION 0.7.0 + re-stamp 4 → Task 5 ✓

**Placeholder scan:** Task 1 has complete code + tests; skill tasks have the literal Markdown to insert; `<TICKET>`/`<key>` are runtime tokens, not plan gaps. ✓

**Type consistency:** `interactive_review` (snake_case) used identically in DEFAULT_CONFIG, `_CONFIG_DEFAULTS`, `config get/set`, and all three skills; `config get interactive_review` → `true`/`false` string consumed by skills consistently; version `0.7.0` consistent (cork-setup stamped in Task 2, others bumped in Task 5). ✓

**Note (intentional, called out in Task 2 Step 4):** cork-setup is stamped 0.7.0 before VERSION is bumped (Task 5), so `install.sh` warns about drift between Tasks 2–4 and resolves at Task 5.
