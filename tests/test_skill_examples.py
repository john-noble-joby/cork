import os, re, subprocess, tempfile, unittest
from pathlib import Path


class ReviewFanoutTest(unittest.TestCase):
    def run_example(self, preference: str, config_status: int = 0,
                    later_preference: str | None = None, include_snapshot: bool = True) -> tuple[int, list[str]]:
        skill = (Path(__file__).resolve().parents[1] / "skills/cork/SKILL.md").read_text()
        step0 = skill.split("### Step 0 — Gather context & pick mode", 1)[1]
        snapshot = step0.split("```bash\n", 1)[1].split("```", 1)[0]
        section = skill.split("### R1 — Fan out all reviewers at once", 1)[1]
        example = section.split("```bash\n", 1)[1].split("```", 1)[0]
        # Execute the documented loop, not a second implementation of its routing.
        # Shadow python3 so neither the config query nor review dispatch can spend quota.
        shim = '''
python3() {
  if [[ "$2" == config ]]; then
    printf 'config-read\\n' >> "$CALL_LOG"
    printf '%s\\n' "$HERDR_PREF"
    return "$CONFIG_STATUS"
  fi
  printf '%s\\n' "$*" >> "$CALL_LOG"
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.txt"
            script = (shim + (snapshot if include_snapshot else "")
                      + '\nHERDR_PREF="$LATER_PREF"\n'
                      + example.replace("/tmp/cork-review-", tmp + "/review-"))
            env = {**os.environ, "CORK_HOME": tmp, "HERDR_PREF": preference,
                   "LATER_PREF": later_preference if later_preference is not None else preference,
                   "CONFIG_STATUS": str(config_status), "CALL_LOG": str(log),
                   "PREFLIGHT_MODELS": "copilot/gpt-6-sol claude/claude-opus-5-5 "
                                       "copilot/claude-opus-5.5 codex/gpt-6-astra"}
            env.pop("HERDR_CLAUDE_REVIEWS", None)
            result = subprocess.run(["bash", "-c", script], env=env,
                                    capture_output=True, text=True, timeout=10)
            calls = log.read_text() if log.exists() else ""
            self.assertEqual(calls.count("config-read"), 1 if include_snapshot else 0)
        return result.returncode, re.findall(r"--review-model (\S+)", calls)

    def test_herdr_enabled_excludes_only_native_claude(self):
        status, models = self.run_example("true")
        self.assertEqual(status, 0)
        self.assertCountEqual(models, ["copilot/gpt-6-sol", "copilot/claude-opus-5.5",
                                       "codex/gpt-6-astra"])

    def test_herdr_disabled_keeps_every_lane(self):
        status, models = self.run_example("false")
        self.assertEqual(status, 0)
        self.assertCountEqual(models, ["copilot/gpt-6-sol", "claude/claude-opus-5-5",
                                       "copilot/claude-opus-5.5", "codex/gpt-6-astra"])

    def test_preference_changes_cannot_duplicate_or_drop_claude(self):
        for initial, later in (("true", "false"), ("false", "true")):
            with self.subTest(initial=initial, later=later):
                status, models = self.run_example(initial, later_preference=later)
                self.assertEqual(status, 0)
                self.assertEqual(models.count("claude/claude-opus-5-5"), int(initial == "false"))
                self.assertEqual(len(models), 3 + int(initial == "false"))

    def test_missing_snapshot_stops_before_any_review(self):
        status, models = self.run_example("true", include_snapshot=False)
        self.assertNotEqual(status, 0)
        self.assertEqual(models, [])

    def test_config_failure_stops_before_any_review(self):
        status, models = self.run_example("", config_status=1)
        self.assertNotEqual(status, 0)
        self.assertEqual(models, [])


if __name__ == "__main__":
    unittest.main()
