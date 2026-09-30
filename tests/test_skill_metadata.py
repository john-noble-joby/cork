import json, unittest
from pathlib import Path


class SkillMetadataTest(unittest.TestCase):
    def test_all_skill_stamps_match_version(self) -> None:
        root = Path(__file__).resolve().parents[1]
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
        paths = sorted((root / "skills").glob("*/SKILL.md"))
        self.assertTrue(version)
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(skill=path.parent.name):
                stamp = next(line for line in path.read_text(encoding="utf-8").splitlines()
                             if line.startswith("**Version:**"))
                self.assertEqual(stamp.split()[1], version)

    def test_descriptions_are_quoted_yaml_scalars(self):
        root = Path(__file__).resolve().parents[1] / "skills"
        paths = sorted(root.glob("*/SKILL.md"))
        self.assertTrue(paths)
        for path in paths:
            name = path.parent.name
            with self.subTest(skill=name):
                text = path.read_text(encoding="utf-8")
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
