import json, unittest
from pathlib import Path


class SkillMetadataTest(unittest.TestCase):
    def test_descriptions_are_quoted_yaml_scalars(self):
        root = Path(__file__).resolve().parents[1] / "skills"
        for name in ("coding-standards", "cork", "cork-setup", "copilot-review-loop", "devit"):
            with self.subTest(skill=name):
                text = (root / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"))
                frontmatter = text.split("---\n", 2)[1]
                self.assertIn(f"name: {name}\n", frontmatter)
                line = next(line for line in frontmatter.splitlines() if line.startswith("description: "))
                # JSON strings are YAML-compatible quoted scalars. This catches bare
                # descriptions with embedded ': ' without adding a YAML dependency.
                description = json.loads(line.removeprefix("description: "))
                self.assertIsInstance(description, str)
                self.assertTrue(description.strip())
                self.assertLessEqual(len(description), 1024)


if __name__ == "__main__":
    unittest.main()
