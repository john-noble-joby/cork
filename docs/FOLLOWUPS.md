# Deferred Follow-ups

Items from the code review that are intentionally deferred — do not implement without further discussion.

## Someday (not now)
`orchestrate.py` is one large file by deliberate design (CLAUDE.md says keep it a single script); only consider a module split if it keeps growing.

Pass the fetched story / acceptance criteria into `review()` as the `story` payload instead of only the implementer's summary, so blind reviewers can run the spec-conformance axis against real requirements (raised by Copilot on PR #10).
