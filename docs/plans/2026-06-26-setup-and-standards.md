# Setup Ergonomics + Shared Standards Rubric — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Some tasks are prose/bash — verification there is structure/manual, not `unittest`.

**Goal:** `install.sh` auto-sets `CORK_HOME`; clear FOLLOWUPS polish; and a generalized "coding & review" standards rubric layered as cork-universal-default + per-repo, used by both devit's implementers and the review models.

**Architecture:** A shipped `standards/AGENTS.md` (read from the cork repo dir) is prepended to the target repo's own standards by `load_agent_instructions`, gated by a global `default_standards` config flag and a per-repo `.cork-standards-off` sentinel. `standards status`/`init` subcommands manage per-repo state; cork/devit surface it non-blocking.

**Tech Stack:** Python 3.10+ stdlib only; stdlib `unittest`; Markdown skills; bash `install.sh`.

## Global Constraints

- Python 3.10+ **stdlib only**; tests use `unittest` (NOT pytest).
- **No classes** (test TestCase subclasses fine); type hints on every function; no docstrings (comment WHY only).
- Errors via `fail(msg)` (`-> NoReturn`); `pathlib.Path`.
- `default_standards` default **`true`**; gating: universal default applies iff `default_standards` AND NOT per-repo `code-review/.cork-standards-off`.
- Secrets never in `config.json`; skill version stamps must equal `VERSION`.
- Run tests: `python3 -m unittest discover -s tests -v`.
- Spec: `docs/specs/2026-06-26-setup-and-standards.md`.

---

## File Structure
- **Modify `orchestrate.py`** — `default_standards` in config + validation + settable; `_DEFAULT_STANDARDS` const, `_repo_opted_out`, rewritten `load_agent_instructions`; `cmd_standards_status`/`cmd_standards_init` + `_PROJECT_STANDARDS_TEMPLATE`; `standards` dispatch.
- **Create `standards/AGENTS.md`** — generalized rubric.
- **Create `tests/test_standards.py`**; **modify `tests/test_config.py`**.
- **Modify** `skills/cork/SKILL.md`, `skills/devit/SKILL.md`, `skills/cork-setup/SKILL.md`, `install.sh`, `README.md`, `docs/FOLLOWUPS.md`, `VERSION`.

---

### Task 1: `install.sh` — auto-set `CORK_HOME` + sort SKILLS

**Files:** Modify `install.sh`.

- [ ] **Step 1: Sort the SKILLS array** (line 13): `SKILLS=(copilot-review-loop cork cork-setup devit)`.

- [ ] **Step 2: Add the CORK_HOME prompt** before the final `echo`/`if [ "$rc" ...]` block. Insert:

```bash
# Offer to persist CORK_HOME so the skills resolve this clone (settings.json env is
# what Claude Code sessions — and thus the skills' bash — inherit).
SETTINGS="$HOME/.claude/settings.json"
current="$(python3 - "$SETTINGS" <<'PY' 2>/dev/null
import json, sys
try:
    print((json.load(open(sys.argv[1])).get("env") or {}).get("CORK_HOME", ""))
except Exception:
    print("")
PY
)"
if [ "$current" = "$REPO" ]; then
  echo "CORK_HOME already set to $REPO in $SETTINGS ✓"
else
  printf "Set CORK_HOME=%s in %s? [y/N] " "$REPO" "$SETTINGS"
  read -r ans
  if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
    python3 - "$SETTINGS" "$REPO" <<'PY'
import json, os, sys
path, repo = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(path)) if os.path.exists(path) else {}
except Exception:
    cfg = {}
cfg.setdefault("env", {})["CORK_HOME"] = repo
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
open(tmp, "w").write(json.dumps(cfg, indent=2) + "\n")
os.replace(tmp, path)
print(f"  ✓ set env.CORK_HOME={repo} in {path} (restart Claude Code to apply)")
PY
  else
    echo "  Skipped. (Or add 'export CORK_HOME=$REPO' to your shell profile.)"
  fi
fi
```

- [ ] **Step 3: Verify** — `./install.sh` (answer `n` first → prints skip + shell hint; then `y` → check `python3 -c "import json;print(json.load(open('$HOME/.claude/settings.json'))['env']['CORK_HOME'])"` prints the repo path; other settings keys intact). Re-run → "already set ✓".

- [ ] **Step 4: Commit** — `git add install.sh && git commit -m "feat(install): offer to set CORK_HOME in settings.json; sort SKILLS"`

---

### Task 2: config — `default_standards` flag

**Files:** Modify `orchestrate.py` (DEFAULT_CONFIG ~74, `_validate_config` ~322, `_SETTABLE_KEYS` ~354); modify `tests/test_config.py`.

**Interfaces:** Produces config key `default_standards` (bool, default true), readable via `config get default_standards`, settable via `config set default_standards true|false`.

- [ ] **Step 1: Failing tests** (append to `tests/test_config.py` `ConfigGetSetTest`):

```python
    def test_get_default_standards_defaults_true(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            orchestrate.cmd_config_get("default_standards")
        self.assertEqual(buf.getvalue().strip(), "true")

    def test_set_default_standards_roundtrip(self):
        orchestrate.cmd_config_set("default_standards", "false")
        self.assertFalse(json.loads(self.path.read_text())["default_standards"])

    def test_set_default_standards_rejects_non_bool(self):
        with self.assertRaises(SystemExit):
            orchestrate.cmd_config_set("default_standards", "yes")
```

- [ ] **Step 2: Run → fail** (`KeyError`/`true`≠ assertion / no rejection). `python3 -m unittest tests.test_config -v`.

- [ ] **Step 3: Implement.** In `DEFAULT_CONFIG`, after `"interactive_review": True,` add `"default_standards": True,`. In `_validate_config`, after the interactive_review bool check add:
```python
    if not isinstance(cfg.get("default_standards", True), bool):
        fail("config.default_standards must be true or false (a JSON boolean)")
```
Change `_SETTABLE_KEYS = {"interactive_review"}` to `_SETTABLE_KEYS = {"interactive_review", "default_standards"}`.

- [ ] **Step 4: Run → pass.** Full `discover`.

- [ ] **Step 5: Commit** — `git add orchestrate.py tests/test_config.py && git commit -m "feat(config): default_standards flag (default on, settable)"`

---

### Task 3: the generalized rubric — `standards/AGENTS.md`

**Files:** Create `standards/AGENTS.md`.

- [ ] **Step 1: Write the file** with this content (generalized from edge-fmt; language-agnostic):

````markdown
# Cork — Default Coding & Review Standards

A shared baseline for **both** writing code and reviewing it. cork layers this under each
repo's own `code-review/AGENTS.md` (project specifics win/extend). It is deliberately
language-agnostic — apply each principle in your stack's idiom. Projects add stack-specific
rules in their own file; opt a repo out with `code-review/.cork-standards-off`, or globally
with `config set default_standards false`.

## Reviewer stance
You are a reviewer **and** the standard an implementer codes to. As a reviewer you report
findings — `file:line`, a quoted excerpt, the reasoning, and a concrete suggested fix — and
do not rewrite code; the human decides. As an implementer you write code that would pass
this review the first time.

## What "good" looks like (roughly in priority order)
1. **Correctness** — does what it says; fails predictably.
2. **Codebase-consistent idioms** — match what the repo already does; don't invent a new
   style for one corner.
3. **Immutability & clear data flow** — prefer immutable data and pure functions where the
   language supports it; make state changes obvious.
4. **Explicit over implicit** — typed/wrapped IDs over bare strings; dependency injection
   over global/service-locator; locale-safe parsing; required vs. optional made explicit.
5. **DRY without dogma** — 3–4 near-identical blocks usually deserve a helper; 2 may not. A
   helper has to pay back the name/jump cost. Count duplicates and estimate the savings.
6. **SOLID where it earns its keep** — name the principle *and* the concrete consequence;
   "SRP violation" alone is empty.
7. **Reads like a story** — a newcomer can start at the entry point and follow control flow
   downward; helpers stay near call sites; names describe intent, not mechanism.
8. **Tests verify behavior** — not mock interactions or implementation details. Prefer one
   sample-driven integration test over ten heavily-mocked unit tests.

Not impressed by cleverness: a clear conditional beats a one-liner nobody can debug. Call
out good patterns by name — affirmation matters.

## Universal smells (call out clear violations with file:line + symptom + fix)
- **Concurrency:** fire-and-forget async whose failures vanish; sync-over-async on a request
  path (thread starvation); offloading sync work to a pool without real benefit.
- **Resource cleanup:** anything acquired (handles, connections, locks) not released on all
  paths, including errors. Pair acquisition with scoped disposal.
- **Error handling:** swallowed/empty catches; catching everything without a filter
  (hides cancellation); rethrowing in a way that loses the stack/cause; generic error types
  for domain failures.
- **Type design:** primitive obsession (raw string/int for IDs, money, paths) where a small
  wrapper carries the invariant; "fat" constructors with many deps (usually an SRP split).
- **Readability:** chains/pipelines too long to set a breakpoint in; allocations inside hot
  loops; conditional nesting 3+ deep (use early returns / extraction); stringly-typed states
  that should be an enum/const.
- **Doc/comment freshness:** comments must describe what the code does *now*. After a
  behavior change, grep for every comment/doc/README line describing the old behavior and
  update it. Watch null-meaning comments ("null = X") and timing comments ("captured before
  Y") — they must match the code.

## Tests
- Happy path: assert the actual produced values, not just "not null".
- Error paths: a test for every stable failure mode (missing / blank / out-of-range /
  invalid). A "should fail with X" fixture must trigger exactly *one* failure.
- Don't couple tests to implementation details (exact messages, private state).
- Helpers (equality, parsing) get their own edge-case tests.

## Adversarial lens (find wrong behavior, not style)
Boundary values (0, min, max, just-past-max; empty/whitespace/one/many); partial-failure in
any multi-step or parallel operation (does the failure path carry as much detail as success,
and name what already succeeded?); early-exit loops that drop later results; timing values
captured after the thing they measure; ordering/monotonicity assumptions; cancellation and
teardown races. Report only behavior you can state is wrong, with the triggering input.

## Output format (review synthesis)
`## Strengths` (2–5 bullets) · `## Critical` (crashes / data loss / wrong output for valid
input / contract violations) · `## Important` (design, missed edges, costly inconsistencies)
· `## Minor` (style/readability; group by root cause) · `## Cross-cutting` (spans files —
DRY, naming, version skew) · `## Uncertain / needs human judgment` (don't pad) ·
`## Out of scope` (pre-existing, one line each) · `## Verdict` (one plain paragraph:
"ready to merge after [N]" / "block on [item]").

## Prioritization
Correctness > cross-cutting consistency > style. A real cross-cutting issue across five
files usually beats a deep one-file nit. Don't drop readability/immutability to "minor" just
because they aren't bugs. When unsure on severity: *would a senior engineer block the PR on
this?* Yes → Critical/Important; No → Minor.

## Do not
Rewrite code (report only). Re-litigate decisions the spec/plan already argued through
absent a correctness issue. Flag pre-existing issues outside the diff (→ Out of scope). Pad
— a tight 20-line review beats a padded 200-line one. Over-find to justify the review.
````

- [ ] **Step 2: Verify structure** — `grep -c '^## ' standards/AGENTS.md` ≥ 7; `grep -qi 'language-agnostic' standards/AGENTS.md`; confirm no `C#`/`FluentResults`/`master-tables`/`csproj` leaked: `! grep -qiE 'c#|fluentresults|master-tables|csproj|inverter-sim' standards/AGENTS.md`.

- [ ] **Step 3: Commit** — `git add standards/AGENTS.md && git commit -m "feat(standards): generalized default coding & review rubric"`

---

### Task 4: `load_agent_instructions` layering + `_repo_opted_out`

**Files:** Modify `orchestrate.py` (replace `load_agent_instructions`; add const + helper near it); create `tests/test_standards.py`.

**Interfaces:**
- Consumes: `load_config` (quiet), `DEFAULT_CONFIG`.
- Produces: `_DEFAULT_STANDARDS: Path`; `_repo_opted_out(repo: str) -> bool`; `load_agent_instructions(repo: str) -> tuple[str, str]` now returning the **layered** effective rubric (universal default gated by `default_standards` AND not opted-out, then the repo's own file), `("", "")` if neither applies. The string-2 is a human label of active layers.

- [ ] **Step 1: Failing tests** (`tests/test_standards.py`):

```python
import json, os, unittest, tempfile
from pathlib import Path
import orchestrate


class LayeringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"; self.repo.mkdir()
        # isolate config + the shipped default
        self._cfg = orchestrate.CONFIG_PATH
        orchestrate.CONFIG_PATH = self.root / "config.json"
        self._std = orchestrate._DEFAULT_STANDARDS
        orchestrate._DEFAULT_STANDARDS = self.root / "standards.md"
        orchestrate._DEFAULT_STANDARDS.write_text("UNIVERSAL")

    def tearDown(self):
        orchestrate.CONFIG_PATH = self._cfg
        orchestrate._DEFAULT_STANDARDS = self._std
        self.tmp.cleanup()

    def _project(self, text="PROJECT"):
        d = self.repo / "code-review"; d.mkdir(exist_ok=True)
        (d / "AGENTS.md").write_text(text)

    def test_default_on_plus_project(self):
        self._project()
        text, label = orchestrate.load_agent_instructions(str(self.repo))
        self.assertIn("UNIVERSAL", text); self.assertIn("PROJECT", text)

    def test_default_on_no_project(self):
        text, _ = orchestrate.load_agent_instructions(str(self.repo))
        self.assertEqual(text, "UNIVERSAL")

    def test_sentinel_opts_out_default(self):
        self._project()
        (self.repo / "code-review" / ".cork-standards-off").write_text("")
        text, _ = orchestrate.load_agent_instructions(str(self.repo))
        self.assertNotIn("UNIVERSAL", text); self.assertIn("PROJECT", text)

    def test_global_off(self):
        orchestrate.CONFIG_PATH.write_text(json.dumps({
            "rotation": [{"provider": "copilot", "model": "gpt-4.1"}],
            "default_standards": False}))
        self._project()
        text, _ = orchestrate.load_agent_instructions(str(self.repo))
        self.assertNotIn("UNIVERSAL", text); self.assertIn("PROJECT", text)

    def test_nothing_applies(self):
        (self.repo / "code-review").mkdir()
        (self.repo / "code-review" / ".cork-standards-off").write_text("")
        text, label = orchestrate.load_agent_instructions(str(self.repo))
        self.assertEqual((text, label), ("", ""))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run → fail** (`AttributeError: _DEFAULT_STANDARDS`). `python3 -m unittest tests.test_standards -v`.

- [ ] **Step 3: Implement.** Replace `load_agent_instructions` and add above it:

```python
_DEFAULT_STANDARDS = Path(__file__).resolve().parent / "standards" / "AGENTS.md"

_PROJECT_STANDARDS = [
    "code-review/AGENTS.md", "code-review/agent.md",
    "AGENTS.md", "agent.md", ".github/AGENTS.md",
]


def _repo_opted_out(repo: str) -> bool:
    return (Path(repo) / "code-review" / ".cork-standards-off").exists()


def load_agent_instructions(repo: str) -> tuple[str, str]:
    # Effective review/coding rubric = cork universal default (gated) + the repo's own.
    project_text, project_path = "", ""
    for rel in _PROJECT_STANDARDS:
        p = Path(repo) / rel
        if p.exists():
            project_text, project_path = p.read_text(errors="replace"), str(p)
            break
    use_default = (load_config(quiet=True).get("default_standards", True)
                   and not _repo_opted_out(repo))
    universal_text = (_DEFAULT_STANDARDS.read_text(errors="replace")
                      if use_default and _DEFAULT_STANDARDS.exists() else "")
    parts, labels = [], []
    if universal_text.strip():
        parts.append(universal_text); labels.append("cork default")
    if project_text.strip():
        parts.append(project_text); labels.append(project_path)
    if not parts:
        return "", ""
    return "\n\n---\n\n".join(parts), " + ".join(labels)
```

- [ ] **Step 4: Run → pass** (`tests.test_standards` + full `discover`).

- [ ] **Step 5: Commit** — `git add orchestrate.py tests/test_standards.py && git commit -m "feat(standards): layer universal default + per-repo in load_agent_instructions"`

---

### Task 5: `standards status` / `standards init` subcommands

**Files:** Modify `orchestrate.py` (add `_PROJECT_STANDARDS_TEMPLATE`, `cmd_standards_status`, `cmd_standards_init`, `standards` dispatch in `main()`); modify `tests/test_standards.py`.

**Interfaces:** Consumes `_repo_opted_out`, `_PROJECT_STANDARDS`, `load_config`, `_DEFAULT_STANDARDS`, `fail`. Produces `cmd_standards_status(repo)`, `cmd_standards_init(repo, opt_out=False)`, and `standards status|init [repo] [--opt-out]` CLI.

- [ ] **Step 1: Failing tests** (append to `tests/test_standards.py`):

```python
class StandardsCmdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self._cfg = orchestrate.CONFIG_PATH
        orchestrate.CONFIG_PATH = self.repo / "config.json"

    def tearDown(self):
        orchestrate.CONFIG_PATH = self._cfg
        self.tmp.cleanup()

    def test_init_scaffolds_project_file(self):
        orchestrate.cmd_standards_init(str(self.repo))
        f = self.repo / "code-review" / "AGENTS.md"
        self.assertTrue(f.exists())
        self.assertIn("project-specific", f.read_text().lower())

    def test_init_refuses_overwrite(self):
        d = self.repo / "code-review"; d.mkdir()
        (d / "AGENTS.md").write_text("mine")
        with self.assertRaises(SystemExit):
            orchestrate.cmd_standards_init(str(self.repo))
        self.assertEqual((d / "AGENTS.md").read_text(), "mine")

    def test_init_opt_out_writes_sentinel(self):
        orchestrate.cmd_standards_init(str(self.repo), opt_out=True)
        self.assertTrue(orchestrate._repo_opted_out(str(self.repo)))
```

- [ ] **Step 2: Run → fail** (`AttributeError: cmd_standards_init`).

- [ ] **Step 3: Implement.** Add near the other `cmd_*`:

```python
_PROJECT_STANDARDS_TEMPLATE = """\
# <Project> — Coding & Review Standards

This file **extends cork's universal default standards** (it's layered underneath the
default, not instead of it). Put this project's stack-specific conventions and checks here.

## Project conventions
- Language/runtime, formatter, naming, file layout, result/error pattern, test framework.

## Project-specific review checks
- Things a reviewer must verify for THIS codebase (required update sites for a new type,
  protocol/schema invariants, fixture conventions, etc.).
"""


def cmd_standards_status(repo: str) -> None:
    global_on = load_config(quiet=True).get("default_standards", True)
    opted = _repo_opted_out(repo)
    project = next((str(Path(repo) / rel) for rel in _PROJECT_STANDARDS
                    if (Path(repo) / rel).exists()), None)
    print(f"standards for {repo}:")
    if not global_on:
        print("  universal default: OFF (global default_standards=false)")
    elif opted:
        print(f"  universal default: OFF (opted out via code-review/.cork-standards-off)")
    else:
        print(f"  universal default: ON ({_DEFAULT_STANDARDS})")
    print(f"  project standards: {project or 'none — run `standards init` to add one'}")


def cmd_standards_init(repo: str, opt_out: bool = False) -> None:
    cr = Path(repo) / "code-review"
    if opt_out:
        sentinel = cr / ".cork-standards-off"
        if sentinel.exists():
            print(f"{sentinel} already exists."); return
        cr.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("# This repo opts out of cork's universal default standards.\n"
                            "# Delete this file to re-enable. See cork README.\n")
        print(f"Wrote opt-out sentinel {sentinel}.")
        return
    target = cr / "AGENTS.md"
    if target.exists():
        fail(f"{target} already exists — edit it directly (won't overwrite).")
    cr.mkdir(parents=True, exist_ok=True)
    target.write_text(_PROJECT_STANDARDS_TEMPLATE)
    print(f"Scaffolded {target} — add project-specific conventions; it layers under cork's default.")
```

In `main()`, add a dispatch block beside the `config`/`preflight` blocks:
```python
    if len(sys.argv) >= 2 and sys.argv[1] == "standards":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        rest = sys.argv[3:]
        opt = "--opt-out" in rest
        repo = next((a for a in rest if not a.startswith("--")), ".")
        repo = str(Path(repo).expanduser().resolve())
        if sub == "status":
            cmd_standards_status(repo)
        elif sub == "init":
            cmd_standards_init(repo, opt_out=opt)
        else:
            fail("usage: orchestrate.py standards status|init [repo] [--opt-out]")
        return
```

- [ ] **Step 4: Run → pass** + manual: `python3 orchestrate.py standards status .` prints layers; `python3 orchestrate.py standards init /tmp/r` scaffolds; `--opt-out` writes sentinel.

- [ ] **Step 5: Commit** — `git add orchestrate.py tests/test_standards.py && git commit -m "feat(standards): status/init subcommands"`

---

### Task 6: skill surfacing + devit note dedup

**Files:** Modify `skills/cork/SKILL.md`, `skills/devit/SKILL.md`, `skills/cork-setup/SKILL.md`.

- [ ] **Step 1: cork Step 0** — add a line after the preflight command in the Step 0 bash block:
```bash
python3 "$CORK_HOME/orchestrate.py" standards status .   # show the active review-standards layers
```
And a prose line: "If `standards status` shows *no project standards* and the default is on, mention once (non-blocking): the repo has no project standards layer — `standards init` adds one, `--opt-out` skips the default. Proceed regardless."

- [ ] **Step 2: devit Phase 0** — add under the verify step: "Run `python3 "$CORK_HOME/orchestrate.py" standards status {WORKTREE}`; if unconfigured (no project file, default on), tell the user once they can `standards init` / `--opt-out` — non-blocking, proceed." **devit Phase 3 (implement)** — add: "Follow the **effective standards**: cork's universal default (`$CORK_HOME/standards/AGENTS.md`) plus this repo's `code-review/AGENTS.md` if present (`standards status` shows what applies)."

- [ ] **Step 3: devit note dedup (polish)** — remove the verbatim `(If interactive_review is on …)` parenthetical from Phase 4 and Phase 6; add it once under `## Notes` as: "**Interactive review:** when `interactive_review` is on (default), cork and the Copilot loop pause after each reviewer for you to choose what to apply; devit inherits this." Reference from Phase 4 & 6 with: "(Pauses per reviewer when `interactive_review` is on — see Notes.)"

- [ ] **Step 4: cork-setup** — add a step after "Pause-between-reviews preference":
```markdown
## 3b. Default standards
Ask: **"Use cork's built-in coding & review standards as a baseline for all repos?
(recommended — default yes; a repo can opt out with `standards init --opt-out`)."**
Persist: `python3 "$CORK_HOME/orchestrate.py" config set default_standards true` (or `false`).
Mention: per-repo, `standards init` scaffolds a project file that extends the default.
```

- [ ] **Step 5: Verify + deploy** — `grep -q 'standards status' skills/cork/SKILL.md skills/devit/SKILL.md`; `grep -q 'default_standards' skills/cork-setup/SKILL.md`; devit has the note in `## Notes` and not duplicated in Phase 4/6 (`grep -c 'see Notes' skills/devit/SKILL.md` → 2). `./install.sh >/dev/null`.

- [ ] **Step 6: Commit** — `git add skills/ && git commit -m "feat(skills): surface standards status in cork/devit; cork-setup default-standards step; dedup devit note"`

---

### Task 7: docs + version 0.8.0 + test polish

**Files:** Modify `README.md`, `docs/FOLLOWUPS.md`, `tests/test_config.py`, `VERSION`, all 4 `skills/*/SKILL.md`.

- [ ] **Step 1: README standards section** — add a `### Coding & review standards (layering)` subsection under "How it works":
```markdown
### Coding & review standards (layering)

cork ships a generalized **coding & review rubric** (`standards/AGENTS.md`) used by both
devit's implementer and the blind review models. The **effective** rubric for a repo is:

  cork's universal default  +  that repo's own `code-review/AGENTS.md` (if present)

- **Use the default** (on by default): nothing to do — every review/implementation carries
  the baseline.
- **Add project specifics:** `python3 orchestrate.py standards init <repo>` scaffolds a
  `code-review/AGENTS.md` that layers *under* the default; fill in your stack's conventions.
- **Opt a repo out:** `standards init <repo> --opt-out` (writes `code-review/.cork-standards-off`).
- **Opt out everywhere:** `python3 orchestrate.py config set default_standards false`.
- **See what applies:** `python3 orchestrate.py standards status <repo>`.
```

- [ ] **Step 2: README env table** — add a row for `CORK_HOME` note that `install.sh` can set it in `settings.json` (one line near the env table or Setup step 1).

- [ ] **Step 3: FOLLOWUPS** — remove the now-done bullets (assertTrue/assertFalse, sort SKILLS, devit note dedup). If the file is then empty of actionable items, leave only the "Someday (module split)" section.

- [ ] **Step 4: test polish** — in `tests/test_config.py`, change boolean `assertEqual(x, True/False)` to `assertTrue(x)`/`assertFalse(x)`.

- [ ] **Step 5: VERSION 0.8.0 + re-stamp** — set `VERSION` to `0.8.0`; change `**Version:** 0.7.0` → `0.8.0` in `skills/cork/SKILL.md`, `skills/copilot-review-loop/SKILL.md`, `skills/devit/SKILL.md`, `skills/cork-setup/SKILL.md`.

- [ ] **Step 6: Install + verify** — `./install.sh` → four skills at v0.8.0, no drift; `python3 -m unittest discover -s tests` all pass.

- [ ] **Step 7: Commit** — `git add README.md docs/FOLLOWUPS.md tests/test_config.py VERSION skills/ && git commit -m "docs: standards layering section; bump 0.8.0; test polish"`

---

## Self-Review

**Spec coverage:** A (install.sh CORK_HOME) → Task 1 ✓; B polish (assertTrue/assertFalse → Task 7, sort SKILLS → Task 1, devit note dedup → Task 6) ✓; C: shared rubric file → Task 3 ✓; default_standards flag → Task 2 ✓; layering in load_agent_instructions + sentinel → Task 4 ✓; standards status/init → Task 5 ✓; cork/devit surfacing + devit-implement uses effective standards + cork-setup toggle → Task 6 ✓; README + version → Task 7 ✓.

**Placeholder scan:** complete code/tests/file content in every step; `<repo>`/`<Project>` are intentional template tokens. ✓

**Type consistency:** `_DEFAULT_STANDARDS`, `_repo_opted_out`, `_PROJECT_STANDARDS`, `default_standards`, `cmd_standards_status/init` names used identically across Tasks 4–6; `default_standards` default `true` consistent (DEFAULT_CONFIG, validation, layering, tests); VERSION `0.8.0` across all four stamps (Task 7). ✓

**Note:** Tasks 4 & 5 both edit `orchestrate.py`; Task 5 depends on Task 4's `_repo_opted_out`/`_PROJECT_STANDARDS`/`_DEFAULT_STANDARDS` (sequential, called out in Interfaces).
