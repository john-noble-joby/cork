import datetime
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "skills").mkdir(parents=True)
        shutil.copy(ROOT / "release.sh", self.repo / "release.sh")
        (self.repo / "VERSION").write_text("1.2.3\n")
        for name in ("alpha", "beta"):
            d = self.repo / "skills" / name; d.mkdir()
            (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n\n# {name}\n\n**Version:** 1.2.3 — keep in sync.\n\nbody mentions 1.2.3 too\n")
        (self.repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Versioning\n\ntext\n\n## [Unreleased]\n\n### Fixed\n- something\n\n## [1.2.3] — 2026-01-01\n\n### Added\n- old\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(["bash", str(self.repo / "release.sh"), *args], capture_output=True, text=True,
                              env={"REPO": str(self.repo), "PATH": "/usr/bin:/bin:/usr/local/bin"})

    def _snapshot(self):
        return {p.relative_to(self.repo): p.read_text() for p in self.repo.rglob("*") if p.is_file()}

    def test_release_stamps_everything_once(self):
        r = self._run("1.3.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual((self.repo / "VERSION").read_text(), "1.3.0\n")
        for name in ("alpha", "beta"):
            text = (self.repo / "skills" / name / "SKILL.md").read_text()
            self.assertIn("**Version:** 1.3.0 — keep in sync.", text)
            self.assertIn("body mentions 1.2.3 too", text)          # only the stamp line changes
        log = (self.repo / "CHANGELOG.md").read_text()
        self.assertIn(f"## [Unreleased]\n\n## [1.3.0] — {today}\n\n### Fixed\n- something\n", log)
        self.assertIn("## [1.2.3] — 2026-01-01", log)
        self.assertIn("1.2.3 → 1.3.0", r.stdout); self.assertIn("2 skill stamps", r.stdout)

    def test_refuses_without_unreleased_notes_and_changes_nothing(self):
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n## [1.2.3] — 2026-01-01\n- old\n")
        before = self._snapshot()
        r = self._run("1.3.0")
        self.assertEqual(r.returncode, 1); self.assertIn("no release notes", r.stderr)
        self.assertEqual(self._snapshot(), before)

    def test_refuses_bad_or_same_version_and_existing_section(self):
        before = self._snapshot()
        for args, code, needle in ((("1.3",), 2, "usage"), (("1.2.3",), 1, "does not exceed"), (("v1.3.0",), 2, "usage"),
                                   (("01.3.0",), 2, "usage"), (("1.03.0",), 2, "usage"), (("1.3.00",), 2, "usage"),
                                   (("1.2.2",), 1, "does not exceed"), (("0.9.9",), 1, "does not exceed"), (("1.10.0",), 0, "")):
            if code == 0:
                continue  # 1.10.0 > 1.2.3 numerically (not lexically) — exercised in the happy-path test below
            with self.subTest(args=args):
                r = self._run(*args); self.assertEqual(r.returncode, code); self.assertIn(needle, r.stderr)
        (self.repo / "CHANGELOG.md").write_text((self.repo / "CHANGELOG.md").read_text().replace("## [1.2.3]", "## [1.3.0] — x\n\n## [1.2.3]"))
        r = self._run("1.3.0"); self.assertEqual(r.returncode, 1); self.assertIn("already has a [1.3.0]", r.stderr)
        (self.repo / "CHANGELOG.md").write_text(before[Path("CHANGELOG.md")])
        self.assertEqual(self._snapshot(), before)

    def test_numeric_precedence_not_lexical(self):
        # "1.10.0" sorts before "1.2.3" as a string but is the newer version
        r = self._run("1.10.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.repo / "VERSION").read_text(), "1.10.0\n")

    def test_headings_only_unreleased_is_not_release_notes(self):
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n### Added\n\n### Fixed\n\n## [1.2.3] — 2026-01-01\n- old\n")
        before = self._snapshot()
        r = self._run("1.3.0")
        self.assertEqual(r.returncode, 1); self.assertIn("headings alone do not count", r.stderr)
        self.assertEqual(self._snapshot(), before)

    def test_refuses_when_no_skill_files_exist(self):
        shutil.rmtree(self.repo / "skills"); (self.repo / "skills").mkdir()
        before = self._snapshot()
        r = self._run("1.3.0")
        self.assertEqual(r.returncode, 1); self.assertIn("no skills/*/SKILL.md", r.stderr)
        self.assertEqual(self._snapshot(), before)          # VERSION and CHANGELOG untouched

    def test_refuses_stamp_drift_before_releasing(self):
        (self.repo / "skills" / "beta" / "SKILL.md").write_text("# beta\n\n**Version:** 1.2.2 — stale\n")
        before = self._snapshot()
        r = self._run("1.3.0")
        self.assertEqual(r.returncode, 1); self.assertIn("not 1.2.3", r.stderr)
        self.assertEqual(self._snapshot(), before)

    def test_real_repo_is_release_ready_shape(self):
        # the real tree: one stamp per skill, all equal to VERSION, an Unreleased heading present
        version = (ROOT / "VERSION").read_text().strip()
        for f in ROOT.glob("skills/*/SKILL.md"):
            lines = [l for l in f.read_text().splitlines() if l.startswith("**Version:** ")]
            self.assertEqual(len(lines), 1, f); self.assertTrue(lines[0].startswith(f"**Version:** {version} "), f)
        self.assertIn("## [Unreleased]", (ROOT / "CHANGELOG.md").read_text())


if __name__ == "__main__":
    unittest.main()
