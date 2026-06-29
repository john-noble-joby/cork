# Deferred Follow-ups

Items from the code review that are intentionally deferred — do not implement without further discussion.

## python3 standardization
SKILL.md command snippets and README use `python`; standardize to `python3` everywhere (portability on systems without a `python` symlink / where it's py2).

## Polish
- `config get` prints JSON-encoded / `null` for unknown keys — document the contract or emit bare scalars.
- Drop the unused `os` import in `tests/test_config.py` and use `assertTrue`/`assertFalse` for bools.
- Sort the `SKILLS` array in `install.sh`.
- The install "Next: set up cork" line prints even on rc=1 — gate on rc=0.
- `auth.json` accepts both `{"token":...}` and `{"github-copilot":{"refresh":...}}` — cross-reference the shapes in the docs.
- devit Phases 4 & 6 repeat the `interactive_review` note verbatim — move it to `## Notes` and reference once.

## Someday (not now)
`orchestrate.py` is one large file by deliberate design (CLAUDE.md says keep it a single script); only consider a module split if it keeps growing.
