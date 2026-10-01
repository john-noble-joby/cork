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
# Exactly one argument: this rewrites every version surface, so surplus arguments are a
# malformed command, not something to ignore.
[ "$#" -eq 1 ] || { echo "usage: release.sh X.Y.Z  (exactly one argument)" >&2; exit 2; }
NEW="$1"
# SemVer core: numeric identifiers without leading zeroes (01.2.3 is not a version).
SEMVER='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
[[ "$NEW" =~ $SEMVER ]] || { echo "usage: release.sh X.Y.Z  (SemVer, no leading zeroes)" >&2; exit 2; }

# Validate what the file actually stores, byte for byte: the file must be exactly one SemVer
# version followed by one newline — "1 . 2 . 3", surrounding blanks, a second line, a missing
# or doubled trailing newline are all rejected rather than normalised.
raw="$(cat "$REPO/VERSION"; printf x)"; raw="${raw%x}"      # keep trailing newlines ($(…) would drop them)
OLD="${raw%$'\n'}"
{ [ "$raw" = "${OLD}"$'\n' ] && [[ "$OLD" =~ $SEMVER ]]; } || { echo "✗ VERSION file must be exactly one SemVer version plus a newline; holds $(printf '%q' "$raw"), not a SemVer version" >&2; exit 1; }
# The new version must have higher precedence: a release never moves the source of truth backwards.
newer="$(python3 -c 'import sys; o, n = (tuple(map(int, v.split("."))) for v in sys.argv[1:]); print("yes" if n > o else "no")' "$OLD" "$NEW")"
[ "$newer" = "yes" ] || { echo "✗ $NEW does not exceed the current VERSION $OLD" >&2; exit 1; }
CHANGELOG="$REPO/CHANGELOG.md"
# Literal prefix match (no regex: "1.3.0" as a pattern would also match a malformed "## [1x3y0]").
awk -v h="## [$NEW]" 'index($0, h) == 1 {found=1} END {exit !found}' "$CHANGELOG" && { echo "✗ CHANGELOG.md already has a [$NEW] section" >&2; exit 1; }
# The heading must be exactly `## [Unreleased]` (the same form the rewrite below replaces), so a
# variant such as a trailing space cannot pass validation and then be left un-rewritten.
[ "$(grep -cx '## \[Unreleased\]' "$CHANGELOG")" = "1" ] || { echo "✗ CHANGELOG.md needs exactly one line reading '## [Unreleased]'" >&2; exit 1; }

# The Unreleased section must hold real notes: blank lines and bare `### Added`/`### Fixed`
# headings do not count, so a note-free release is refused.
unreleased_body="$(awk '$0 == "## [Unreleased]"{f=1; next} /^## /{f=0} f' "$CHANGELOG" | grep -v '^[[:space:]]*$' | grep -v '^[[:space:]]*#' || true)"
[ -n "$unreleased_body" ] || { echo "✗ '## [Unreleased]' has no release notes (headings alone do not count) — nothing to release" >&2; exit 1; }

# The set of skills is the installer's manifest (SKILLS=(…) in install.sh): every listed skill
# must be present with exactly one stamp equal to the current VERSION, and nothing is stamped
# that the installer does not ship. A missing skill means a damaged checkout, not "one fewer
# stamp to update".
manifest_line="$(grep -m1 '^SKILLS=(' "$REPO/install.sh" || true)"
[ -n "$manifest_line" ] || { echo "✗ $REPO/install.sh has no 'SKILLS=(…)' manifest line" >&2; exit 1; }
manifest_list="${manifest_line#SKILLS=(}"; manifest_list="${manifest_list%)}"   # strip "SKILLS=(" and ")" before splitting
read -r -a required <<< "$manifest_list"                                         # no negative subscripts: macOS ships Bash 3.2
skills=()
for name in "${required[@]}"; do
  f="$REPO/skills/$name/SKILL.md"
  [ -f "$f" ] || { echo "✗ skills/$name/SKILL.md is missing but install.sh lists '$name' — refusing to release from a damaged checkout" >&2; exit 1; }
  skills+=("$f")
done
shopt -s nullglob; present=("$REPO"/skills/*/SKILL.md); shopt -u nullglob
[ "${#present[@]}" -eq "${#skills[@]}" ] || { echo "✗ skills/ holds ${#present[@]} SKILL.md files but install.sh lists ${#skills[@]} — add the skill to install.sh or remove it" >&2; exit 1; }
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
