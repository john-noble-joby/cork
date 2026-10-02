import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LENSES = sorted(p for p in (ROOT / "lenses").glob("*.md") if p.name != "README.md")


class LensFilesTest(unittest.TestCase):
    def test_four_lenses_ship_with_the_shared_header_and_placeholders(self):
        self.assertEqual([p.name for p in LENSES], ["http-contract-and-store.md", "spec-and-test-coverage.md",
                                                   "standards-and-docs.md", "state-and-concurrency.md"])
        for p in LENSES:
            text = p.read_text()
            with self.subTest(lens=p.name):
                self.assertIn("Read-only reviewer.", text)                     # the common header
                for ph in ("{WORKTREE}", "{BASE}", "{STORY_FILE}", "{STANDARDS}"):
                    self.assertIn(ph, text)
                self.assertRegex(text, r"file:line")

    def test_lenses_diff_against_the_merge_base_like_every_other_reviewer(self):
        # two dots is tip-to-tip: once origin/<base> advances past the merge-base every lens sees
        # the base's own catch-up as reversed hunks — the stale-base symptom cork warns about
        for p in LENSES:
            with self.subTest(lens=p.name):
                self.assertIn("diff {BASE}...HEAD", p.read_text())
                self.assertIsNone(re.search(r"\{BASE\}\.\.HEAD", p.read_text()))

    def test_readme_lists_every_lens(self):
        readme = (ROOT / "lenses" / "README.md").read_text()
        for p in LENSES:
            self.assertIn(f"`{p.name}`", readme)
        self.assertIn("never skip spec-and-test-coverage", readme)


if __name__ == "__main__":
    unittest.main()
