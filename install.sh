#!/usr/bin/env bash
# Install cork's Claude Code skills into ~/.claude/skills and report versions.
#
# orchestrate.py is NOT installed: the skills invoke it via $CORK_HOME
# (default ~/dev/cork), so it runs from this repo clone directly — a git pull
# is all it takes to update the script. Only the SKILL.md files are copies that
# can drift, which is what this script keeps in sync and version-checks.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
VERSION="$(tr -d '[:space:]' < "$REPO/VERSION")"
SKILLS=(copilot-review-loop cork cork-setup devit)

echo "Installing cork skills v$VERSION → $DEST"
echo

rc=0
for s in "${SKILLS[@]}"; do
  src="$REPO/skills/$s/SKILL.md"
  if [ ! -f "$src" ]; then echo "  ✗ $s: missing $src"; rc=1; continue; fi

  # The skill body carries a version stamp so an agent (and you) can tell which
  # prompt is loaded. Warn if it drifted from VERSION — bump them together.
  stamp="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' <(grep -m1 'Version:' "$src") || true)"
  if [ "$stamp" != "$VERSION" ]; then
    echo "  ⚠ $s: skill stamp '${stamp:-none}' != VERSION '$VERSION' — update the **Version:** line in $src"
    rc=1
  fi

  mkdir -p "$DEST/$s"
  cp -r "$REPO/skills/$s/." "$DEST/$s/"
  echo "  ✓ $s installed (stamp v${stamp:-?})"
done

echo
# Status line: deploy the cork status-line script (shows the active ticket/branch).
# Activation is opt-in — add to ~/.claude/settings.json:
#   "statusLine": { "type": "command", "command": "~/.claude/statusline.py" }
cp "$REPO/statusline.py" "$DEST/../statusline.py"
chmod +x "$DEST/../statusline.py"
echo "  ✓ statusline.py installed to $(cd "$DEST/.." && pwd)/statusline.py"
if ! grep -q '"statusLine"' "$HOME/.claude/settings.json" 2>/dev/null; then
  echo "    (not yet enabled — add a statusLine block to ~/.claude/settings.json; see README)"
fi

echo
echo "orchestrate.py: $(python3 "$REPO/orchestrate.py" --version)"

cork_home="${CORK_HOME:-$HOME/dev/cork}"
if [ "$cork_home" != "$REPO" ]; then
  echo "  ⚠ CORK_HOME=$cork_home but this repo is $REPO — the skills will run a"
  echo "    different orchestrate.py than the one just version-checked. Point"
  echo "    CORK_HOME at this clone, or run install.sh from the clone in use."
else
  echo "CORK_HOME: $cork_home ✓ (skills run this repo's orchestrate.py)"
fi

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
  read -r ans || ans=""   # EOF / non-interactive stdin must not abort under set -e
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
with open(tmp, "w") as f:
    f.write(json.dumps(cfg, indent=2) + "\n")
os.replace(tmp, path)
print(f"  ✓ set env.CORK_HOME={repo} in {path} (restart Claude Code to apply)")
PY
  else
    echo "  Skipped. (Or add 'export CORK_HOME=$REPO' to your shell profile.)"
  fi
fi

echo
if [ "$rc" -eq 0 ]; then
  echo "Next: restart Claude Code, then say \"set up cork\" to finish configuration."
  echo
  echo "Done."
else
  echo "Completed with warnings."
  exit 1
fi
