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
        (self.clone / "install.sh").write_text('#!/usr/bin/env bash\nprintf installed > "$CLAUDE_SKILLS_DIR/marker"\n')
        _git(self.clone, "add", "-A"); _git(self.clone, "commit", "-qm", "ship"); _git(self.clone, "push", "-q", "origin", "main")
        self.skills = root / "skills"; shutil.copytree(self.clone / "skills", self.skills)
        self.bin = root / "bin"; self.bin.mkdir(); (self.bin / "cork").symlink_to(self.clone / "bin" / "cork")
        self._env = {k: os.environ.get(k) for k in ("CLAUDE_SKILLS_DIR", "CORK_BIN_DIR")}
        os.environ["CLAUDE_SKILLS_DIR"] = str(self.skills); os.environ["CORK_BIN_DIR"] = str(self.bin)

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

    def test_shim_states(self):
        (self.bin / "cork").unlink(); (self.bin / "cork").symlink_to(Path(self.tmp.name) / "gone" / "bin" / "cork")
        self.assertIn("dangling symlink", self._doctor())
        (self.bin / "cork").unlink(); other = Path(self.tmp.name) / "other-clone" / "bin"; other.mkdir(parents=True)
        shutil.copy(ROOT / "bin" / "cork", other / "cork"); (self.bin / "cork").symlink_to(other / "cork")
        self.assertIn("points at another clone", self._doctor())
        (self.bin / "cork").unlink(); (self.bin / "cork").write_text("#!/bin/sh\n")
        self.assertIn("regular file", self._doctor())
        (self.bin / "cork").unlink()
        self.assertIn("no `cork` command", self._doctor())

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

    def test_cli_dispatch(self):
        import sys
        for argv in (["orchestrate.py", "doctor", "--no-fetch"],):
            orig = sys.argv; sys.argv = argv
            seen = {}
            orig_doctor = orchestrate.cmd_doctor; orchestrate.cmd_doctor = lambda clone=None, fetch=True: seen.update(fetch=fetch)
            try:
                orchestrate.main()
            finally:
                sys.argv = orig; orchestrate.cmd_doctor = orig_doctor
            self.assertEqual(seen, {"fetch": False})


if __name__ == "__main__":
    unittest.main()
