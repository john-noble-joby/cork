import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import orchestrate

ROOT = Path(__file__).resolve().parent.parent


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class DoctorTest(unittest.TestCase):
    # A clone of a bare origin carrying the real VERSION, bin/cork and skills, plus an installed
    # skills dir and a bin dir — the three things `cork doctor` has to find in agreement.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.origin = root / "origin.git"; _git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.clone = root / "clone"; _git(root, "clone", "-q", str(self.origin), str(self.clone))
        _git(self.clone, "config", "user.email", "t@t"); _git(self.clone, "config", "user.name", "t")
        shutil.copy(ROOT / "VERSION", self.clone / "VERSION")
        (self.clone / "bin").mkdir(); shutil.copy(ROOT / "bin" / "cork", self.clone / "bin" / "cork")
        shutil.copytree(ROOT / "skills", self.clone / "skills")
        shutil.copy(ROOT / "statusline.py", self.clone / "statusline.py")
        manifest = next(l for l in (ROOT / "install.sh").read_text().splitlines() if l.startswith("SKILLS=("))
        (self.clone / "install.sh").write_text(f'#!/usr/bin/env bash\n{manifest}\nprintf installed > "$CLAUDE_SKILLS_DIR/marker"\n')
        _git(self.clone, "add", "-A"); _git(self.clone, "commit", "-qm", "ship"); _git(self.clone, "push", "-q", "origin", "main")
        self.skills = root / "installed" / "skills"; shutil.copytree(self.clone / "skills", self.skills)
        shutil.copy(ROOT / "statusline.py", self.skills.parent / "statusline.py")
        self.bin = root / "bin"; self.bin.mkdir(); (self.bin / "cork").symlink_to(self.clone / "bin" / "cork")
        self._env = {k: os.environ.get(k) for k in ("CLAUDE_SKILLS_DIR", "CORK_BIN_DIR", "CORK_HOME", "PATH")}
        os.environ["CLAUDE_SKILLS_DIR"] = str(self.skills); os.environ["CORK_BIN_DIR"] = str(self.bin)
        os.environ["CORK_HOME"] = str(self.clone); os.environ["PATH"] = f"{self.bin}{os.pathsep}{os.environ['PATH']}"

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _doctor(self) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            orchestrate.cmd_doctor(self.clone)
        return out.getvalue()

    def _advance_origin(self) -> None:
        other = Path(self.tmp.name) / "other"; _git(Path(self.tmp.name), "clone", "-q", str(self.origin), str(other))
        _git(other, "config", "user.email", "t@t"); _git(other, "config", "user.name", "t")
        (other / "NEW").write_text("x"); _git(other, "add", "-A"); _git(other, "commit", "-qm", "upstream"); _git(other, "push", "-q", "origin", "main")

    def test_everything_in_agreement_is_one_line(self):
        out = self._doctor()
        self.assertEqual(len(out.strip().splitlines()), 1); self.assertIn("up to date", out)
        self.assertIn(f"cork {(ROOT / 'VERSION').read_text().strip()}", out)

    def test_clone_behind_origin_is_reported_after_a_fetch(self):
        self._advance_origin()
        out = self._doctor()
        self.assertIn("1 commit(s) behind origin/main", out); self.assertIn("run `cork update`", out)
        # --no-fetch compares against the last fetch: already fetched above, so still behind
        out2 = io.StringIO()
        with redirect_stdout(out2):
            orchestrate.cmd_doctor(self.clone, fetch=False)
        self.assertIn("behind origin/main", out2.getvalue())

    def test_stale_installed_skill_is_detected_by_content_not_stamp(self):
        # stamps only move at a release, so a feature-PR change to a skill leaves the stamp equal
        (self.skills / "cork" / "SKILL.md").write_text((self.skills / "cork" / "SKILL.md").read_text() + "\nstale\n")
        out = self._doctor()
        self.assertIn("skill cork installed copy differs from the clone (SKILL.md)", out)
        shutil.rmtree(self.skills / "devit")
        self.assertIn("skill devit is not installed", self._doctor())
        # a file the clone no longer ships is drift too: install.sh replaces directories in full
        (self.skills / "cork-setup" / "OLD.md").write_text("left behind")
        self.assertIn("skill cork-setup installed copy differs from the clone (OLD.md (not in clone))", self._doctor())

    def test_shim_states(self):
        (self.bin / "cork").unlink(); (self.bin / "cork").symlink_to(Path(self.tmp.name) / "gone" / "bin" / "cork")
        self.assertIn("dangling symlink", self._doctor())
        (self.bin / "cork").unlink(); other = Path(self.tmp.name) / "other-clone" / "bin"; other.mkdir(parents=True)
        shutil.copy(ROOT / "bin" / "cork", other / "cork"); (self.bin / "cork").symlink_to(other / "cork")
        self.assertIn("points at another clone", self._doctor())
        (self.bin / "cork").unlink(); (self.bin / "cork").write_text("#!/bin/sh\n")
        self.assertIn("regular file", self._doctor())
        (self.bin / "cork").unlink(); os.environ["PATH"] = str(self.bin)   # only our (now empty) bin dir on PATH
        out = self._doctor()
        self.assertIn("no `cork` command", out)
        self.assertIn("`cork` is not on PATH", out)        # a broken link does not hide PATH drift: both reported at once
        # the clone's own shim missing must be a line, not a FileNotFoundError from samefile()
        (self.bin / "cork").symlink_to(ROOT / "bin" / "cork"); (self.clone / "bin" / "cork").unlink()
        self.assertIn("this clone has no bin/cork", self._doctor())

    def test_dirty_clone_is_reported_and_blocks_update(self):
        (self.clone / "VERSION").write_text("9.9.9\n")
        self.assertIn("uncommitted changes", self._doctor())
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            orchestrate.cmd_update(self.clone)
        self.assertIn("uncommitted changes", err.getvalue())

    def test_update_fast_forwards_and_runs_the_installer(self):
        self._advance_origin()
        out = io.StringIO()
        with redirect_stdout(out):
            orchestrate.cmd_update(self.clone)
        self.assertTrue((self.clone / "NEW").exists())                      # pulled
        self.assertEqual((self.skills / "marker").read_text(), "installed")  # installer ran with the skills dir
        self.assertIn("up to date", self._doctor())

    def test_ahead_fetch_failure_and_missing_origin_main_are_each_reported(self):
        (self.clone / "LOCAL").write_text("x"); _git(self.clone, "add", "-A"); _git(self.clone, "commit", "-qm", "local only")
        self.assertIn("1 commit(s) ahead of origin/main", self._doctor())
        _git(self.clone, "remote", "set-url", "origin", str(Path(self.tmp.name) / "nowhere.git"))
        out = self._doctor()
        self.assertIn("could not fetch origin", out); self.assertIn("ahead of origin/main", out)   # last fetch still compared
        _git(self.clone, "remote", "rename", "origin", "upstream")
        self.assertIn("no origin/main in the clone", self._doctor())

    def test_fetch_runs_with_a_timeout(self):
        seen = {}
        real = orchestrate.subprocess.run

        def spy(argv, **kw):
            if "fetch" in argv:
                seen["timeout"] = kw.get("timeout")
            return real(argv, **kw)
        orchestrate.subprocess.run = spy
        try:
            self._doctor()
        finally:
            orchestrate.subprocess.run = real
        self.assertIsNotNone(seen.get("timeout")); self.assertLessEqual(seen["timeout"], 10)   # inside the hook's 10 s budget

    def test_not_on_main_is_reported_and_blocks_update(self):
        _git(self.clone, "checkout", "-qb", "feature")
        self.assertIn("clone is on feature, not main", self._doctor())
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            orchestrate.cmd_update(self.clone)
        self.assertIn("is on feature", err.getvalue())
        _git(self.clone, "checkout", "-q", "--detach")
        self.assertIn("a detached HEAD, not main", self._doctor())

    def test_untracked_files_are_not_dirt(self):
        (self.clone / "notes.txt").write_text("scratch")
        self.assertIn("up to date", self._doctor())

    def test_cork_home_must_be_this_clone(self):
        os.environ["CORK_HOME"] = str(Path(self.tmp.name) / "other-clone")
        self.assertIn("CORK_HOME=", self._doctor()); self.assertIn("does not exist", self._doctor())
        (Path(self.tmp.name) / "other-clone").mkdir()
        self.assertIn("is not this clone", self._doctor())

    def test_statusline_and_path_drift_are_reported(self):
        (self.skills.parent / "statusline.py").write_text("stale")
        self.assertIn("statusline.py at", self._doctor())
        shutil.copy(ROOT / "statusline.py", self.skills.parent / "statusline.py")
        os.environ["PATH"] = "/nonexistent-bin"
        self.assertIn("`cork` is not on PATH", self._doctor())
        other = Path(self.tmp.name) / "other-bin"; other.mkdir(); shutil.copy(ROOT / "bin" / "cork", other / "cork")
        os.environ["PATH"] = f"{other}{os.pathsep}{self.bin}"
        self.assertIn("`cork` on PATH is", self._doctor())

    def test_update_failures_are_clean_exits(self):
        # diverged: ff-only cannot apply
        (self.clone / "LOCAL").write_text("x"); _git(self.clone, "add", "-A"); _git(self.clone, "commit", "-qm", "local")
        self._advance_origin()
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            orchestrate.cmd_update(self.clone)
        self.assertIn("git pull --ff-only failed", err.getvalue())
        # installer failing after a good pull
        _git(self.clone, "reset", "-q", "--hard", "origin/main")
        (self.clone / "install.sh").write_text("#!/usr/bin/env bash\nexit 1\n"); _git(self.clone, "commit", "-qam", "bad installer")
        _git(self.clone, "push", "-q", "origin", "main")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err), self.assertRaises(SystemExit):
            orchestrate.cmd_update(self.clone)
        self.assertIn("install.sh completed with warnings", err.getvalue())

    def test_doctor_entry_point_never_tracebacks_even_on_ascii_stdout(self):
        env = {**os.environ, "PYTHONIOENCODING": "ascii"}
        r = subprocess.run(["python3", str(ROOT / "orchestrate.py"), "doctor", "--no-fetch"], env=env, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertNotIn("Traceback", r.stderr); self.assertIn("cork ", r.stdout)

    def test_unreadable_skills_dir_is_a_problem_line_not_a_crash(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores permissions")
        (self.skills / "cork").chmod(0)
        try:
            out = self._doctor()
        finally:
            (self.skills / "cork").chmod(0o700)
        self.assertIn("skill cork could not be compared", out)

    def test_cli_dispatch(self):
        import sys
        seen = {}
        orig_doctor, orig_update = orchestrate.cmd_doctor, orchestrate.cmd_update
        orchestrate.cmd_doctor = lambda clone=None, fetch=True: seen.update(fetch=fetch)
        orchestrate.cmd_update = lambda clone=None: seen.update(update=True)
        try:
            for argv in (["orchestrate.py", "doctor", "--no-fetch"], ["orchestrate.py", "update"]):
                orig = sys.argv; sys.argv = argv
                try:
                    orchestrate.main()
                finally:
                    sys.argv = orig
        finally:
            orchestrate.cmd_doctor, orchestrate.cmd_update = orig_doctor, orig_update
        self.assertEqual(seen, {"fetch": False, "update": True})
        # an unexpected error inside doctor is one line and exit 0, never a traceback (hook boundary)
        orchestrate.cmd_doctor = lambda clone=None, fetch=True: (_ for _ in ()).throw(RuntimeError("boom"))
        out = io.StringIO(); orig = sys.argv; sys.argv = ["orchestrate.py", "doctor"]
        try:
            with redirect_stdout(out):
                orchestrate.main()
        finally:
            sys.argv = orig; orchestrate.cmd_doctor = orig_doctor
        self.assertIn("cork doctor could not complete: boom", out.getvalue())


if __name__ == "__main__":
    unittest.main()
