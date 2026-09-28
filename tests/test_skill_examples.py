import os, re, subprocess, tempfile, unittest
from pathlib import Path


class ReviewFanoutTest(unittest.TestCase):
    def run_example(self, preference: str, config_status: int = 0) -> tuple[int, list[str]]:
        skill = (Path(__file__).resolve().parents[1] / "skills/cork/SKILL.md").read_text()
        section = skill.split("### R1 — Fan out all reviewers at once", 1)[1]
        example = section.split("```bash\n", 1)[1].split("```", 1)[0]
        # Execute the documented loop, not a second implementation of its routing.
        # Shadow python3 so neither the config query nor review dispatch can spend quota.
        shim = '''
python3() {
  if [[ "$2" == config ]]; then
    printf '%s\\n' "$HERDR_PREF"
    return "$CONFIG_STATUS"
  fi
  printf '%s\\n' "$*" >> "$CALL_LOG"
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.txt"
            script = shim + example.replace("/tmp/cork-review-", tmp + "/review-")
            env = {**os.environ, "CORK_HOME": tmp, "HERDR_PREF": preference,
                   "CONFIG_STATUS": str(config_status), "CALL_LOG": str(log),
                   "PREFLIGHT_MODELS": "copilot/gpt-6-sol claude/claude-opus-5-5 "
                                       "copilot/claude-opus-5.5 codex/gpt-6-astra"}
            result = subprocess.run(["bash", "-c", script], env=env,
                                    capture_output=True, text=True, timeout=10)
            calls = log.read_text() if log.exists() else ""
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

    def test_config_failure_stops_before_any_review(self):
        status, models = self.run_example("", config_status=1)
        self.assertNotEqual(status, 0)
        self.assertEqual(models, [])


if __name__ == "__main__":
    unittest.main()
