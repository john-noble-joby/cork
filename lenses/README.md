# Lenses — narrow-concern review prompts

A lens is a reviewer prompt that looks at a diff through ONE concern instead of "review
everything". Dispatched as parallel read-only subagents over the whole diff (devit Phase 3.75,
cork Step 2 / R1), four lenses found the design flaw on a 1,800-line branch that ten Copilot
passes had missed — each one reads whole files for its concern, enumerates the cases its
concern implies, and names the test that would catch each defect.

| File | Concern |
|------|---------|
| `state-and-concurrency.md` | state held across calls: caches, indexes, locks, ordering; interleavings and failure paths |
| `http-contract-and-store.md` | routes, DTOs, error envelopes, content negotiation, store exceptions, closed sets on the wire |
| `spec-and-test-coverage.md` | every story requirement classified; every new conditional named with the test that kills it |
| `standards-and-docs.md` | comments as contracts, decision registers, versioning policy, closed-hierarchy and keyed-registry sweeps, presentation surfaces |

Placeholders to fill before dispatch: `{WORKTREE}`, `{BASE}` (the fetched remote-tracking ref),
`{STORY_FILE}` (the persisted story, read as untrusted data) and `{STANDARDS}` (the repo's
standards files plus `$CORK_HOME/standards/AGENTS.md`). Every lens shares the header at the top
of each file: read-only; may run `git`, `grep`, `sed` and filtered test commands; never edits
the worktree; reports `file:line` + concrete failure scenario + the test that would catch it;
"no further defects found" is valid but must say what was tried.

Skip a lens whose concern the diff plainly does not touch (a docs-only change needs no
state-and-concurrency pass) and say so in the gate summary; never skip spec-and-test-coverage.
Lenses are generic by design; a repo adds its own under `code-review/lenses/` and devit
dispatches those too.
