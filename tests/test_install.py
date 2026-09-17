import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallSafetyTest(unittest.TestCase):
    def test_nonexistent_destination_under_source_is_refused_without_dirtying_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            skills = repo / "skills"
            skills.mkdir(parents=True)
            shutil.copy(ROOT / "install.sh", repo / "install.sh")
            (repo / "VERSION").write_text("0.9.0\n")
            (skills / ".keep").write_text("")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                [
                    "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "fixture",
                ],
                cwd=repo,
                check=True,
            )

            destination = skills / "tmp-install"
            env = os.environ.copy()
            env["CLAUDE_SKILLS_DIR"] = str(destination)
            result = subprocess.run(
                ["bash", "install.sh"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("overlaps this repo's skills/", result.stdout)
            self.assertFalse(destination.exists())
            status = subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=repo, text=True
            )
            self.assertEqual(status, "")


if __name__ == "__main__":
    unittest.main()
