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
        (self.repo / "b.py").write_text("b = 2\n"); (self.repo / "café.py").write_text("c = 1\n")
        _git(self.repo, "add", "."); _git(self.repo, "commit", "-qm", "c3")
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
        self.assertIn("### café.py", prompt)        # git C-quotes this name in plain --name-only output
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
        # git's default output C-quotes non-ASCII headers: +++ "b/caf\303\251.py"
        quoted = Path(self.tmp.name) / "quoted.patch"
        text = _git(self.repo, "diff", f"{self.c2}..{self.c3}") + "\n"
        self.assertIn('+++ "b/caf\\303\\251.py"', text)      # confirm git really quotes it
        quoted.write_text(text)
        prompt, _ = self._review(diff_file=str(quoted))
        self.assertIn("### café.py", prompt); self.assertIn("### b.py", prompt)

    @unittest.skipIf(sys.platform == "win32", "backslash is a separator on Windows")
    def test_backslash_in_posix_filename_is_a_valid_patch_path(self):
        # git C-quotes the name (`+++ "b/a\\\\b.py"`); the restored backslash is an ordinary byte
        (self.repo / "a\\b.py").write_text("bs = 1\n"); _git(self.repo, "add", "."); _git(self.repo, "commit", "-qm", "c4")
        patch = Path(self.tmp.name) / "backslash.patch"
        text = _git(self.repo, "diff", f"{self.c3}..HEAD") + "\n"
        self.assertIn('+++ "b/a\\\\b.py"', text)
        patch.write_text(text)
        self.assertEqual(orchestrate.read_diff_file(str(patch))[1], ["a\\b.py"])
        prompt, _ = self._review(diff_file=str(patch))
        self.assertIn("### a\\b.py", prompt); self.assertIn("bs = 1", prompt)

    def test_unquote_git_path(self):
        self.assertEqual(orchestrate._unquote_git_path("caf\\303\\251.py"), "café.py")
        self.assertEqual(orchestrate._unquote_git_path("a\\tb\\\"c\\\\d"), 'a\tb"c\\d')
        for bad in ("bad\\q", "x\\999", "x\\777", "x\\000", "x\\12", "x\\8"):   # unknown, non-octal, > 255, NUL, short, bad digit
            with self.subTest(bad=bad), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                orchestrate._unquote_git_path(bad)

    def test_hunk_content_is_not_a_file_header(self):
        # an added source line `++ b/secret.txt` appears in the hunk as `+++ b/secret.txt`
        (self.repo / "secret.txt").write_text("TOP SECRET\n")
        patch = Path(self.tmp.name) / "tricky.patch"
        patch.write_text("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n a = 1\n+++ b/secret.txt\n")
        prompt, _ = self._review(diff_file=str(patch))
        self.assertIn("### a.py", prompt); self.assertNotIn("### secret.txt", prompt); self.assertNotIn("TOP SECRET", prompt)
        # a second file after a hunk is still picked up (its `---`/`+++` pair sits outside any hunk)
        patch.write_text(patch.read_text() + "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-b = 1\n+b = 2\n")
        prompt, _ = self._review(diff_file=str(patch))
        self.assertIn("### a.py", prompt); self.assertIn("### b.py", prompt); self.assertNotIn("### secret.txt", prompt)

    def test_plain_unified_diff_without_git_headers(self):
        # `diff -urN a b` output has no `diff --git` line: files are found from the `---`/`+++` pair.
        # Hunk extent comes from the @@ counts, so a removed `-- x` + added `++ b/secret.txt`
        # pair inside a hunk (rendered `--- x` / `+++ b/secret.txt`) is content, not a header.
        (self.repo / "secret.txt").write_text("TOP SECRET\n")
        patch = Path(self.tmp.name) / "plain.patch"
        patch.write_text(
            "--- a/a.py\t2026-10-01\n+++ b/a.py\t2026-10-01\n"
            "@@ -1,3 +1,3 @@\n a = 1\n--- x\n+++ b/secret.txt\n\n"          # blank context line stripped to ""
            "\\ No newline at end of file\n"
            "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-b = 1\n+b = 2\n")
        prompt, _ = self._review(diff_file=str(patch))
        self.assertIn("### a.py", prompt); self.assertIn("### b.py", prompt)
        self.assertNotIn("### secret.txt", prompt); self.assertNotIn("TOP SECRET", prompt)
        _, names = orchestrate.read_diff_file(str(patch))
        self.assertEqual(names, ["a.py", "b.py"])
        # a deletion header is fine; an unprefixed path (`diff -u old new`, `--no-prefix`) is refused
        # loudly instead of silently yielding no changed files
        patch.write_text("--- a/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-a = 1\n")
        self.assertEqual(orchestrate.read_diff_file(str(patch))[1], [])
        patch.write_text("--- a.py\n+++ a.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n")
        self._fails("lacks the b/ prefix", diff_file=str(patch))

    def test_changed_submodule_directory_is_skipped(self):
        # a changed submodule pointer lists the submodule *directory* in --name-only; reading it
        # would raise IsADirectoryError and abort the review before the model is called
        (self.repo / "vendor").mkdir(); (self.repo / "vendor" / "x.py").write_text("x\n")
        self.assertEqual(orchestrate._file_contents(str(self.repo), ["vendor", "a.py"]), {"a.py": "a = 2"})

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
        # three-dot range with no merge base: a clean failure, not a CalledProcessError traceback
        _git(self.repo, "checkout", "-q", "--orphan", "island"); _git(self.repo, "rm", "-rfq", "."); (self.repo / "z.py").write_text("z\n")
        _git(self.repo, "add", "."); _git(self.repo, "commit", "-qm", "island"); island = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "checkout", "-q", "main")
        self._fails("no merge base", diff_range=f"{island}...{self.c3}")
        self._review(diff_range=f"{island}..{self.c3}")   # the two-dot form needs none

    def test_patch_paths_cannot_escape_the_repository(self):
        secret = Path(self.tmp.name) / "secret.txt"; secret.write_text("TOP SECRET\n")
        # `.git/config` is inside the tree but holds remote URLs that may embed credentials
        _git(self.repo, "remote", "add", "origin", "https://user:TOP%20SECRET@example.com/r.git")
        cases = {"../secret.txt": "escapes the repository", "/etc/passwd": "escapes the repository",
                 "sub/../../secret.txt": "escapes the repository",
                 ".git/config": "names git metadata", "vendor/.GIT/config": "names git metadata"}
        for escaping, message in cases.items():
            with self.subTest(path=escaping):
                patch = Path(self.tmp.name) / "evil.patch"
                patch.write_text(f"diff --git a/{escaping} b/{escaping}\n--- a/{escaping}\n+++ b/{escaping}\n@@ -1 +1 @@\n-x\n+y\n")
                self._fails(message, diff_file=str(patch))
                self.assertNotIn("TOP", self.seen.get("prompt", ""))
        # a symlink inside the tree that points outside is refused at content-read time, too
        (self.repo / "link.txt").symlink_to(secret)
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            orchestrate._file_contents(str(self.repo), ["link.txt"])
        self.assertIn("resolves outside the repository", err.getvalue())
        # ...and so is a symlink whose target stays inside the tree but lands in .git
        (self.repo / "alias").symlink_to(self.repo / ".git" / "config")
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            orchestrate._file_contents(str(self.repo), ["alias"])
        self.assertIn("resolves into git metadata", err.getvalue())

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
