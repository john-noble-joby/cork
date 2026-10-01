import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import orchestrate


def _git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


class ReviewDiffSourceTest(unittest.TestCase):
    # A real repository with three commits: c1 adds a.py; c2 edits a.py and adds b.py;
    # c3 edits b.py. The "delta round" is c2..c3 and must contain b.py only.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.repo = Path(self.tmp.name) / "repo"; self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.py").write_text("a = 1\n"); _git(self.repo, "add", "."); _git(self.repo, "commit", "-qm", "c1")
        self.c1 = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "a.py").write_text("a = 2\n"); (self.repo / "b.py").write_text("b = 1\n")
        _git(self.repo, "add", "."); _git(self.repo, "commit", "-qm", "c2"); self.c2 = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "b.py").write_text("b = 2\n"); _git(self.repo, "add", "."); _git(self.repo, "commit", "-qm", "c3")
        self.c3 = _git(self.repo, "rev-parse", "HEAD")
        self._originals = {n: getattr(orchestrate, n) for n in ("CONFIG_PATH", "load_agent_instructions", "_call_and_extract", "_probe")}
        orchestrate.CONFIG_PATH = Path(self.tmp.name) / "config.json"
        orchestrate.load_agent_instructions = lambda repo: ("STANDARDS", None)
        self.seen = {}
        def fake(provider, model, system, user_msg, max_out=None, repo=""):
            self.seen["prompt"] = user_msg; return 200, "review ok", None
        orchestrate._call_and_extract = fake
        orchestrate._probe = lambda *a, **k: self.fail("validate=False must not probe")

    def tearDown(self):
        for n, v in self._originals.items(): setattr(orchestrate, n, v)
        self.tmp.cleanup()

    def _review(self, **kwargs):
        out = io.StringIO()
        with redirect_stdout(out):
            orchestrate.cmd_review("T-1", str(self.repo), "origin/main", "copilot/model", validate=False, story_text="story", **kwargs)
        return self.seen["prompt"], out.getvalue()

    def _fails(self, needle, **kwargs):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            orchestrate.cmd_review("T-1", str(self.repo), "origin/main", "copilot/model", validate=False, story_text="story", **kwargs)
        self.assertEqual(cm.exception.code, 1); self.assertIn(needle, err.getvalue())

    def test_diff_range_reviews_only_the_delta(self):
        prompt, out = self._review(diff_range=f"{self.c2}..{self.c3}")
        self.assertIn("+b = 2", prompt); self.assertNotIn("+a = 2", prompt)          # delta only
        self.assertIn("### b.py", prompt); self.assertNotIn("### a.py", prompt)       # changed-files set follows the range
        self.assertIn(f"vs {self.c2}..{self.c3}", out)
        prompt, _ = self._review(diff_range=f"{self.c1}...{self.c3}")                # three-dot form accepted
        self.assertIn("+a = 2", prompt); self.assertIn("### a.py", prompt)

    def test_diff_file_reviews_the_supplied_patch(self):
        patch = Path(self.tmp.name) / "delta.patch"
        patch.write_text(_git(self.repo, "diff", f"{self.c1}..{self.c2}") + "\n")
        prompt, out = self._review(diff_file=str(patch))
        self.assertIn("+a = 2", prompt); self.assertIn("### a.py", prompt); self.assertIn("### b.py", prompt)
        self.assertNotIn("+b = 2", prompt)
        self.assertIn(f"vs diff file {patch}", out)

    def test_range_and_file_errors_are_clean(self):
        self._fails("must be A..B or A...B", diff_range=self.c3)
        self._fails("must be A..B or A...B", diff_range=f"..{self.c3}")
        self._fails("does not resolve", diff_range=f"nope..{self.c3}")
        self._fails("No diff for", diff_range=f"{self.c3}..{self.c3}")
        self._fails("Cannot read diff file", diff_file=str(Path(self.tmp.name) / "missing.patch"))
        bad = Path(self.tmp.name) / "bad.patch"; bad.write_bytes(b"\xff\xfe")
        self._fails("Cannot read diff file", diff_file=str(bad))
        empty = Path(self.tmp.name) / "empty.patch"; empty.write_text("\n")
        self._fails("No diff for", diff_file=str(empty))

    def test_default_path_still_uses_base_branch(self):
        _git(self.repo, "branch", "base", self.c1)
        prompt, out = self._review_with_base("base")
        self.assertIn("+a = 2", prompt); self.assertIn("+b = 2", prompt); self.assertIn("vs base", out)
        self._fails_with_base("does not resolve", "origin/main")

    def _review_with_base(self, base):
        out = io.StringIO()
        with redirect_stdout(out):
            orchestrate.cmd_review("T-1", str(self.repo), base, "copilot/model", validate=False, story_text="story")
        return self.seen["prompt"], out.getvalue()

    def _fails_with_base(self, needle, base):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err), self.assertRaises(SystemExit):
            orchestrate.cmd_review("T-1", str(self.repo), base, "copilot/model", validate=False, story_text="story")
        self.assertIn(needle, err.getvalue())

    def test_cli_wiring_and_guards(self):
        seen = []
        orig = orchestrate.cmd_review
        orchestrate.cmd_review = lambda *a, **k: seen.append((a, k))
        self.addCleanup(setattr, orchestrate, "cmd_review", orig)
        base = ["orchestrate.py", "T-1", str(self.repo), "--review-model", "copilot/model"]
        for extra, key, val in ((["--diff-range", "a..b"], "diff_range", "a..b"), (["--diff-file", "x.patch"], "diff_file", "x.patch")):
            with self.subTest(flag=extra[0]):
                orig_argv = sys.argv; sys.argv = base + extra
                try: orchestrate.main()
                finally: sys.argv = orig_argv
                self.assertEqual(seen[-1][1][key], val)
                self.assertEqual(seen[-1][0][2], "origin/develop")       # default base still reaches cmd_review
        for argv, needle in ((["orchestrate.py", "T-1", str(self.repo), "--diff-range", "a..b"], "review-only flags"),
                             (base + ["--diff-range", "a..b", "--base-branch", "main"], "drop --base-branch"),
                             (base + ["--diff-range", "a..b", "--diff-file", "x"], "not allowed with")):
            with self.subTest(argv=argv[3:]):
                err = io.StringIO(); orig_argv = sys.argv; sys.argv = argv
                try:
                    with redirect_stderr(err), self.assertRaises(SystemExit) as cm: orchestrate.main()
                finally: sys.argv = orig_argv
                self.assertEqual(cm.exception.code, 2); self.assertIn(needle, err.getvalue())


if __name__ == "__main__":
    unittest.main()
