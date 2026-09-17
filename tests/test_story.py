import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import orchestrate


class ReviewStoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.story_file = Path(self.tmp.name) / "story.md"
        self._originals = {
            name: getattr(orchestrate, name)
            for name in ("CONFIG_PATH", "load_agent_instructions", "git_diff_branch",
                         "changed_files_branch", "load_state", "_call_and_extract", "_probe")
        }
        orchestrate.CONFIG_PATH = Path(self.tmp.name) / "config.json"
        orchestrate.load_agent_instructions = lambda repo: ("STANDARDS", "/repo/AGENTS.md")
        orchestrate.git_diff_branch = lambda repo, base: "diff --git a/a.py b/a.py\n+change"
        orchestrate.changed_files_branch = lambda repo, base: {"a.py": "current"}
        orchestrate.load_state = lambda tid: {
            "done": {"summary": "done checkpoint story"},
            "summary": "legacy checkpoint story",
        }
        orchestrate._call_and_extract = lambda *a, **k: (200, "review ok")
        orchestrate._probe = lambda *a, **k: self.fail("probe called before story validation")

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(orchestrate, name, value)
        self.tmp.cleanup()

    def _api_prompt(self, **kwargs) -> tuple[str, str]:
        seen = {}

        def fake_http(provider, model, system, user_msg, max_out=None, repo=""):
            seen["prompt"] = user_msg
            return 200, "review ok"

        orchestrate._call_and_extract = fake_http
        output = io.StringIO()
        with redirect_stdout(output):
            orchestrate.cmd_review("TASK-1", self.tmp.name, "origin/main", "copilot/model",
                                   validate=False, **kwargs)
        return seen["prompt"], output.getvalue()

    def test_story_file_reaches_api_lane(self):
        self.story_file.write_text("api acceptance contract", encoding="utf-8")
        prompt, output = self._api_prompt(story_file=str(self.story_file))
        self.assertIn("## Story / Task\napi acceptance contract", prompt)
        self.assertIn(f"Story: --story-file {self.story_file} (23 chars)", output)

    def test_story_file_reaches_harness_lane(self):
        self.story_file.write_text("harness acceptance contract", encoding="utf-8")
        orchestrate._call_and_extract = self._originals["_call_and_extract"]
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="review ok", stderr="")

        original_run = orchestrate.subprocess.run
        orchestrate.subprocess.run = fake_run
        try:
            with redirect_stdout(io.StringIO()):
                orchestrate.cmd_review("TASK-1", self.tmp.name, "origin/main", "codex/model",
                                       validate=False, story_file=str(self.story_file))
        finally:
            orchestrate.subprocess.run = original_run
        self.assertIn("## Story / Task\nharness acceptance contract", calls[0][1]["input"])

    def test_story_file_wins_over_every_other_source(self):
        self.story_file.write_text("file story", encoding="utf-8")
        prompt, _ = self._api_prompt(story_file=str(self.story_file), story_text="inline story")
        self.assertIn("## Story / Task\nfile story", prompt)
        self.assertNotIn("inline story", prompt)
        self.assertNotIn("checkpoint story", prompt)

    def test_inline_story_wins_over_both_checkpoint_sources(self):
        prompt, output = self._api_prompt(story_text="inline story")
        self.assertIn("## Story / Task\ninline story", prompt)
        self.assertNotIn("checkpoint story", prompt)
        self.assertIn("Story: --story (12 chars)", output)

    def test_done_summary_wins_over_legacy_checkpoint_summary(self):
        prompt, output = self._api_prompt()
        self.assertIn("## Story / Task\ndone checkpoint story", prompt)
        self.assertNotIn("legacy checkpoint story", prompt)
        self.assertIn("Story: checkpoint done.summary (21 chars)", output)

    def test_legacy_checkpoint_summary_wins_over_fallback(self):
        orchestrate.load_state = lambda tid: {"summary": "legacy checkpoint story"}
        prompt, output = self._api_prompt()
        self.assertIn("## Story / Task\nlegacy checkpoint story", prompt)
        self.assertNotIn("Review the branch changes", prompt)
        self.assertIn("Story: checkpoint summary (23 chars)", output)

    def test_fallback_is_used_when_no_source_exists(self):
        orchestrate.load_state = lambda tid: {"done": {}}
        prompt, output = self._api_prompt()
        self.assertIn("## Story / Task\nReview the branch changes for TASK-1.", prompt)
        self.assertIn("Story: fallback (37 chars)", output)

    def test_missing_story_file_fails_without_traceback(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            orchestrate.cmd_review("TASK-1", self.tmp.name, "origin/main", "copilot/model",
                                   story_file=str(self.story_file))
        self.assertNotEqual(raised.exception.code, 0)
        self.assertEqual(len(stderr.getvalue().strip().splitlines()), 1)
        self.assertIn("Cannot read story file", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_non_utf8_story_file_fails_without_traceback(self):
        self.story_file.write_bytes(b"\xff")
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            orchestrate.cmd_review("TASK-1", self.tmp.name, "origin/main", "copilot/model",
                                   validate=False, story_file=str(self.story_file))
        self.assertEqual(len(stderr.getvalue().strip().splitlines()), 1)
        self.assertIn("Cannot read story file", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_empty_story_file_fails(self):
        self.story_file.write_text(" \n", encoding="utf-8")
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            orchestrate.cmd_review("TASK-1", self.tmp.name, "origin/main", "copilot/model",
                                   validate=False, story_file=str(self.story_file))
        self.assertIn(f"Story from --story-file {self.story_file} is empty.", stderr.getvalue())

    def test_empty_inline_story_fails(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            orchestrate.cmd_review("TASK-1", self.tmp.name, "origin/main", "copilot/model",
                                   validate=False, story_text="")
        self.assertIn("Story from --story is empty.", stderr.getvalue())

    def test_mutually_exclusive_story_flags_fail_parsing(self):
        original_argv = sys.argv
        sys.argv = ["orchestrate.py", "TASK-1", self.tmp.name, "--review-model", "copilot/model",
                    "--story-file", str(self.story_file), "--story", "inline"]
        stderr = io.StringIO()
        try:
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                orchestrate.main()
        finally:
            sys.argv = original_argv
        self.assertNotEqual(raised.exception.code, 0)
        self.assertIn("not allowed with argument", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
