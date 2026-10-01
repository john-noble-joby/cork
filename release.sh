#!/usr/bin/env bash
# release.sh X.Y.Z — the ONE place cork's version changes.
#
# Feature PRs never touch VERSION or the skills' **Version:** stamps; they add their notes
# under `## [Unreleased]` in CHANGELOG.md. This script turns that accumulated section into a
# release: it stamps VERSION, every skills/*/SKILL.md, and the changelog heading in one
# change, so stacked PRs stop conflicting on six files and install.sh's drift check keeps
# holding (stamps only ever move here).
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
NEW="${1:-}"
# SemVer core: numeric identifiers without leading zeroes (01.2.3 is not a version).
SEMVER='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
[[ "$NEW" =~ $SEMVER ]] || { echo "usage: release.sh X.Y.Z  (SemVer, no leading zeroes)" >&2; exit 2; }

# Validate what the file actually stores: only the trailing newline is dropped, so "1 . 2 . 3"
# or a second line is rejected rather than normalised into a version.
OLD="$(<"$REPO/VERSION")"
[[ "$OLD" =~ $SEMVER ]] || { echo "✗ VERSION file holds '$OLD', not a SemVer version" >&2; exit 1; }
# The new version must have higher precedence: a release never moves the source of truth backwards.
newer="$(python3 -c 'import sys; o, n = (tuple(map(int, v.split("."))) for v in sys.argv[1:]); print("yes" if n > o else "no")' "$OLD" "$NEW")"
[ "$newer" = "yes" ] || { echo "✗ $NEW does not exceed the current VERSION $OLD" >&2; exit 1; }
CHANGELOG="$REPO/CHANGELOG.md"
grep -q "^## \[$NEW\]" "$CHANGELOG" && { echo "✗ CHANGELOG.md already has a [$NEW] section" >&2; exit 1; }
# The heading must be exactly `## [Unreleased]` (the same form the rewrite below replaces), so a
# variant such as a trailing space cannot pass validation and then be left un-rewritten.
[ "$(grep -cx '## \[Unreleased\]' "$CHANGELOG")" = "1" ] || { echo "✗ CHANGELOG.md needs exactly one line reading '## [Unreleased]'" >&2; exit 1; }

# The Unreleased section must hold real notes: blank lines and bare `### Added`/`### Fixed`
# headings do not count, so a note-free release is refused.
unreleased_body="$(awk '$0 == "## [Unreleased]"{f=1; next} /^## \[/{f=0} f' "$CHANGELOG" | grep -v '^[[:space:]]*$' | grep -v '^[[:space:]]*#' || true)"
[ -n "$unreleased_body" ] || { echo "✗ '## [Unreleased]' has no release notes (headings alone do not count) — nothing to release" >&2; exit 1; }

# Every skill must carry exactly one stamp, and it must equal the current VERSION. A checkout
# with no skills at all is damaged, not "zero stamps to update".
shopt -s nullglob; skills=("$REPO"/skills/*/SKILL.md); shopt -u nullglob
[ "${#skills[@]}" -gt 0 ] || { echo "✗ no skills/*/SKILL.md under $REPO — refusing to release from a damaged checkout" >&2; exit 1; }
for f in "${skills[@]}"; do
  n="$(grep -c '^\*\*Version:\*\* ' "$f" || true)"
  [ "$n" = "1" ] || { echo "✗ $f: expected exactly one '**Version:**' line, found $n" >&2; exit 1; }
  # Compare the stamp token literally (no regex: a "1.2.3" pattern would also accept "1x2y3").
  stamp="$(grep -m1 '^\*\*Version:\*\* ' "$f" | awk '{print $2}')"
  [ "$stamp" = "$OLD" ] || { echo "✗ $f: stamp is '$stamp', not $OLD — fix drift before releasing" >&2; exit 1; }
done

DATE="$(date -u +%Y-%m-%d)"
printf '%s\n' "$NEW" > "$REPO/VERSION"
# Each rewrite replaces the validated, line-anchored token and asserts exactly one substitution,
# so a stamp with nothing after the version (or any other accepted variant) cannot slip through.
for f in "${skills[@]}"; do
  python3 - "$f" "$OLD" "$NEW" <<'PY'
import re, sys; p, old, new = sys.argv[1:]
s = open(p, encoding="utf-8").read()
s, n = re.subn(rf"(?m)^\*\*Version:\*\* {re.escape(old)}(?=\s|$)", f"**Version:** {new}", s)
assert n == 1, f"{p}: expected exactly one stamp substitution, made {n}"
open(p, "w", encoding="utf-8").write(s)
PY
done
python3 - "$CHANGELOG" "$NEW" "$DATE" <<'PY'
import re, sys; p, new, date = sys.argv[1:]
s = open(p, encoding="utf-8").read()
s, n = re.subn(r"(?m)^## \[Unreleased\]$", f"## [Unreleased]\n\n## [{new}] — {date}", s)
assert n == 1, f"{p}: expected exactly one Unreleased heading, rewrote {n}"
open(p, "w", encoding="utf-8").write(s)
PY

echo "✓ $OLD → $NEW: VERSION, ${#skills[@]} skill stamps, CHANGELOG '## [$NEW] — $DATE'"
echo "  review:  git -C \"$REPO\" diff --stat"
echo "  commit:  git -C \"$REPO\" commit -am \"Release $NEW\""
