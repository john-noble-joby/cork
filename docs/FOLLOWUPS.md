# Deferred Follow-ups

Items from the code review that are intentionally deferred — do not implement without further discussion.

## python3 standardization
SKILL.md command snippets and README use `python`; standardize to `python3` everywhere (portability on systems without a `python` symlink / where it's py2).

## Polish
- Use `assertTrue`/`assertFalse` for booleans in `tests/test_config.py` (currently `assertEqual(..., True/False)`).
- Sort the `SKILLS` array in `install.sh` (currently appended in install order).
- devit Phases 4 & 6 repeat the `interactive_review` note verbatim — move it to `## Notes` and reference once.

## Someday (not now)
`orchestrate.py` is one large file by deliberate design (CLAUDE.md says keep it a single script); only consider a module split if it keeps growing.
