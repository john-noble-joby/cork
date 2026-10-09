import io
import json
import os
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
                         "_git_changed_names", "_file_contents", "load_state", "_call_and_extract",
                         "_probe", "require_base_ref", "git_toplevel", "pin_ref", "preflight", "resolve_story", "_state_path")
        }
        self._env = {k: os.environ.get(k) for k in ("XDG_CACHE_HOME",)}
        self.cache = tempfile.TemporaryDirectory()   # outside the test repo: a cache inside the reviewed tree is rejected by design
        os.environ["XDG_CACHE_HOME"] = self.cache.name   # no devit scratch unless a test writes one
        orchestrate.CONFIG_PATH = Path(self.tmp.name) / "config.json"
        orchestrate.require_base_ref = lambda repo, base: None  # the temp dir is not a git repo
        orchestrate.git_toplevel = lambda repo: repo
        orchestrate.pin_ref = lambda repo, ref: ref
        orchestrate.load_agent_instructions = lambda repo, changed=None, ref=None: ("STANDARDS", "/repo/AGENTS.md")
        orchestrate.git_diff_branch = lambda repo, base: "diff --git a/a.py b/a.py\n+change"
        orchestrate._git_changed_names = lambda repo, *diff_args: ["a.py"]
        orchestrate._file_contents = lambda repo, names: {"a.py": "current"}
        orchestrate.load_state = lambda tid: {
            "done": {"summary": "done checkpoint story"},
            "summary": "legacy checkpoint story",
        }
        orchestrate._call_and_extract = lambda *a, **k: (200, "review ok", None)
        orchestrate._probe = lambda *a, **k: self.fail("probe called before story validation")

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(orchestrate, name, value)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.cache.cleanup()
        self.tmp.cleanup()

    def test_unknown_user_in_story_path_fails_cleanly(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            orchestrate.cmd_review("TASK-1", self.tmp.name, "origin/main", "copilot/model",
                                   validate=False, story_file="~no-such-user-cork-test/story.md")
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Cannot read story file ~no-such-user-cork-test/story.md", err.getvalue())

    def test_cli_forwards_both_story_flags_to_cmd_review(self):
        # Positive dispatch: main() must hand each flag to cmd_review, or the advertised CLI
        # silently falls back to the checkpoint while the direct cmd_review tests stay green.
        seen = []
        orig = orchestrate.cmd_review
        orchestrate.cmd_review = lambda *a, **k: seen.append((a, k))
        self.addCleanup(setattr, orchestrate, "cmd_review", orig)
        base = ["orchestrate.py", "TASK-1", self.tmp.name, "--review-model", "copilot/model"]
        for extra, expected in ((["--story", "inline text"], {"story_file": None, "story_text": "inline text"}),
                                (["--story-file", str(self.story_file)], {"story_file": str(self.story_file), "story_text": None})):
            with self.subTest(flag=extra[0]):
                orig_argv = sys.argv; sys.argv = base + extra
                try:
                    orchestrate.main()
                finally:
                    sys.argv = orig_argv
                args, kwargs = seen[-1]
                self.assertEqual(args[3], "copilot/model")
                self.assertEqual({k: kwargs.get(k) for k in expected}, expected)

    def test_story_flags_are_accepted_without_review_model(self):
        # Headless runs grade against the ticket too (Copilot on PR #33): the flags must pass
        # argparse and reach the headless flow. require_base_ref is the first headless call
        # after parsing; raising there proves parsing accepted the flag and nothing ran before.
        class Reached(Exception):
            pass

        def stop(repo, base):
            raise Reached()
        orchestrate.require_base_ref = stop
        for argv in (["orchestrate.py", "TASK-1", self.tmp.name, "--story", "x"],
                     ["orchestrate.py", "TASK-1", self.tmp.name, "--story-file", str(self.story_file)]):
            with self.subTest(flag=argv[3]):
                orig = sys.argv; sys.argv = argv
                try:
                    with self.assertRaises(Reached):
                        orchestrate.main()
                finally:
                    sys.argv = orig

    def test_headless_story_file_is_read_before_preflight(self):
        # A bad --story-file must fail before any probe spends Copilot quota and before Step 1.
        self.story_file.write_text("ticket contract", encoding="utf-8")
        orchestrate.CONFIG_PATH.write_text(json.dumps(orchestrate.DEFAULT_CONFIG))
        seen = {}

        def fake_preflight(*a, **k):
            seen["preflight"] = True
            raise SystemExit(99)   # stop here: the story was already resolved by then
        orchestrate.preflight = fake_preflight
        self.addCleanup(setattr, orchestrate, "preflight", self._originals.get("preflight"))
        orchestrate.resolve_story = lambda *a, **k: (seen.setdefault("story_args", a), ("S", "src"))[1]
        self.addCleanup(setattr, orchestrate, "resolve_story", self._originals["resolve_story"])
        orig = sys.argv; sys.argv = ["orchestrate.py", "TASK-1", self.tmp.name, "--story-file", str(self.story_file)]
        try:
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
                orchestrate.main()
        finally:
            sys.argv = orig
        self.assertEqual(cm.exception.code, 99)
        self.assertEqual(seen["story_args"][1], str(self.story_file))
        self.assertTrue(seen["preflight"])

    def test_headless_fresh_run_without_contract_warns_before_preflight(self):
        # Copilot on PR #33: a fresh run has no contract and the fallback must be reported, so
        # the implementer summary — so the warning must fire before preflight or never.
        orchestrate.CONFIG_PATH.write_text(json.dumps(orchestrate.DEFAULT_CONFIG))
        orchestrate._state_path = lambda tid: Path(self.tmp.name) / "no-checkpoint.json"
        self.addCleanup(setattr, orchestrate, "_state_path", self._originals["_state_path"])

        def fake_preflight(*a, **k):
            raise SystemExit(99)
        orchestrate.preflight = fake_preflight
        out = io.StringIO(); orig = sys.argv; sys.argv = ["orchestrate.py", "TASK-1", self.tmp.name]
        try:
            with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                orchestrate.main()
        finally:
            sys.argv = orig
        self.assertEqual(cm.exception.code, 99)
        self.assertIn("no story supplied", out.getvalue())

    def _scratch(self, name: str, text: str) -> Path:
        assert Path(os.environ["XDG_CACHE_HOME"]).is_absolute(), "scratch writes must never land in the worktree"
        d = Path(os.environ["XDG_CACHE_HOME"]) / "cork" / "devit" / "TASK-1"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
        return d / name

    def test_devit_scratch_story_wins_over_fallback_and_md_over_txt(self):
        self._scratch("story.txt", "bare ticket")
        prompt, output = self._api_prompt()
        self.assertIn("## Story / Task\nbare ticket", prompt)
        self.assertIn("Story: devit scratch ", output)
        md = self._scratch("story.md", "ticket plus sweep")   # written later → newer → wins
        prompt, output = self._api_prompt()
        self.assertIn("## Story / Task\nticket plus sweep", prompt)
        self.assertIn(f"Story: devit scratch {md} (", output)

    def test_devit_scratch_story_loses_to_flags_and_skips_blank_or_odd_ids(self):
        self._scratch("story.md", "scratch story")
        prompt, _ = self._api_prompt(story_text="inline story")
        self.assertIn("## Story / Task\ninline story", prompt)
        self.assertNotIn("scratch story", prompt)
        # a traversal id must not read a file that exists where the walk would land
        cache = Path(os.environ["XDG_CACHE_HOME"]) / "cork"
        (cache / "TASK-1").mkdir(parents=True); (cache / "TASK-1" / "story.md").write_text("escaped")
        (cache / "devit" / "story.md").write_text("escaped")
        self.assertIsNone(orchestrate._devit_scratch_story("../TASK-1"))   # one path component only
        self.assertIsNone(orchestrate._devit_scratch_story(""))
        (cache / "story.md").write_text("escaped"); (cache / "devit" / "story.txt").write_text("escaped")
        self.assertIsNone(orchestrate._devit_scratch_story(".."))          # Path("..").name == ".."
        self.assertIsNone(orchestrate._devit_scratch_story("."))
        self._scratch("story.md", " \n"); self._scratch("story.txt", " \n")
        self.assertIsNone(orchestrate._devit_scratch_story("TASK-1"))      # blank files are not a story

    def test_empty_xdg_cache_home_means_the_default_not_the_cwd(self):
        # Path("") is the current directory — the reviewed worktree — so a branch could supply the story
        os.environ["XDG_CACHE_HOME"] = ""
        self.assertEqual(orchestrate._devit_scratch_dir("TASK-1"), Path.home() / ".cache" / "cork" / "devit" / "TASK-1")

    def test_newest_scratch_candidate_wins_so_a_refreshed_ticket_beats_a_stale_sweep(self):
        md = self._scratch("story.md", "old story + old sweep"); txt = self._scratch("story.txt", "refreshed ticket")
        os.utime(md, (1_000_000, 1_000_000)); os.utime(txt, (2_000_000, 2_000_000))
        self.assertEqual(orchestrate._devit_scratch_story("TASK-1"), ("refreshed ticket", f"devit scratch {txt}"))
        os.utime(md, (3_000_000, 3_000_000))                      # a newer story.md (Phase 4 rebuilt it) wins again
        self.assertEqual(orchestrate._devit_scratch_story("TASK-1")[0], "old story + old sweep")
        os.utime(txt, (3_000_000, 3_000_000))                     # tie: the richer story.md
        self.assertEqual(orchestrate._devit_scratch_story("TASK-1")[0], "old story + old sweep")

    def _api_prompt(self, **kwargs) -> tuple[str, str]:
        seen = {}

        def fake_http(provider, model, system, user_msg, max_out=None, repo=""):
            seen["prompt"] = user_msg
            return 200, "review ok", None

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
        lane_call = next(c for c in calls if "input" in c[1])   # git helpers (stale-base check) also go through run()
        self.assertIn("## Story / Task\nharness acceptance contract", lane_call[1]["input"])

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

    def test_checkpoint_summaries_are_never_the_story(self):
        # the checkpoint holds the implementer's own description of the work; grading against it
        # has no spec axis, so without a flag or a persisted ticket the fallback is used and named
        prompt, output = self._api_prompt()
        self.assertIn("## Story / Task\nReview the branch changes for TASK-1.", prompt)
        self.assertNotIn("checkpoint story", prompt)
        self.assertIn("Story: fallback", output); self.assertIn("no story supplied", output)

    def test_relative_or_in_repo_cache_never_supplies_the_story(self):
        os.environ["XDG_CACHE_HOME"] = "cache"                                        # relative: the XDG rule says ignore it
        self.assertEqual(orchestrate._devit_scratch_dir("TASK-1"), Path.home() / ".cache" / "cork" / "devit" / "TASK-1")
        inside = Path(self.tmp.name) / "cache"; os.environ["XDG_CACHE_HOME"] = str(inside)   # absolute, but inside the reviewed repo
        (inside / "cork" / "devit" / "TASK-1").mkdir(parents=True)
        (inside / "cork" / "devit" / "TASK-1" / "story.md").write_text("branch-authored contract")
        self.assertIsNone(orchestrate._devit_scratch_story("TASK-1", self.tmp.name))   # provenance: a branch must not supply it
        prompt, output = self._api_prompt()
        self.assertNotIn("branch-authored", prompt); self.assertIn("Story: fallback", output)
        # a scratch path symlinked into the repo is the same thing
        outside = Path(self.cache.name) / "real"; (outside / "cork" / "devit" / "TASK-1").mkdir(parents=True)
        (outside / "cork" / "devit" / "TASK-1" / "story.md").write_text("linked contract")
        os.environ["XDG_CACHE_HOME"] = str(Path(self.tmp.name) / "link"); (Path(self.tmp.name) / "link").symlink_to(outside)
        self.assertEqual(orchestrate._devit_scratch_story("TASK-1", self.tmp.name), ("linked contract", f"devit scratch {Path(self.tmp.name) / 'link' / 'cork' / 'devit' / 'TASK-1' / 'story.md'}"))
        # ... wait: a link OUT of the repo to a real external cache is fine (the content lives outside); a link INTO the repo is not
        os.environ["XDG_CACHE_HOME"] = str(outside)
        (outside / "cork" / "devit" / "TASK-1" / "story.md").unlink()
        (outside / "cork" / "devit" / "TASK-1" / "story.md").symlink_to(Path(self.tmp.name) / "in-repo.md")
        (Path(self.tmp.name) / "in-repo.md").write_text("in-repo contract")
        self.assertIsNone(orchestrate._devit_scratch_story("TASK-1", self.tmp.name))

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
