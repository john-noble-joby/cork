import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallSafetyTest(unittest.TestCase):
    def _guard_fixture(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
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
        return repo, skills

    def _run(
        self, repo: Path, destination: Path, **env_overrides: str
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(env_overrides)
        env["CLAUDE_SKILLS_DIR"] = str(destination)
        env.setdefault("CORK_HOME", str(repo))
        return subprocess.run(
            ["bash", "install.sh"],
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )

    def _run_real_install(
        self, destination: Path, **env_overrides: str
    ) -> subprocess.CompletedProcess[str]:
        home = destination.parent / "home"
        home.mkdir(exist_ok=True)
        return self._run(
            ROOT,
            destination,
            HOME=str(home),
            CORK_HOME=str(ROOT),
            **env_overrides,
        )

    def _assert_clean_fixture(self, repo: Path) -> None:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo, text=True
        )
        self.assertEqual(status, "")

    def _assert_no_install_artifacts(self, destination: Path) -> None:
        self.assertFalse((destination / ".cork-install.lock").exists())
        self.assertEqual(list(destination.glob(".*.tmp.*")), [])
        self.assertEqual(list(destination.glob(".*.prev.*")), [])

    def test_nonexistent_destination_under_source_is_refused_without_dirtying_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, skills = self._guard_fixture(Path(tmp))
            destination = skills / "tmp-install"

            result = self._run(repo, destination)

            self.assertEqual(result.returncode, 1)
            self.assertIn("overlaps this repo's skills/", result.stdout)
            self.assertFalse(destination.exists())
            self._assert_clean_fixture(repo)

    def test_dangling_symlink_path_is_refused_without_creating_source_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, skills = self._guard_fixture(root)
            missing_target = skills / "not-yet-created"
            link = root / "skills-link"
            link.symlink_to(missing_target, target_is_directory=True)

            result = self._run(repo, link / "new")

            self.assertEqual(result.returncode, 1)
            self.assertIn("path traverses a dangling symlink", result.stderr)
            self.assertFalse(missing_target.exists())
            self._assert_clean_fixture(repo)

    def test_direct_and_resolved_symlink_source_destinations_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, skills = self._guard_fixture(root)
            link = root / "skills-link"
            link.symlink_to(skills, target_is_directory=True)

            for destination in (skills, link):
                with self.subTest(destination=destination):
                    result = self._run(repo, destination)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("overlaps this repo's skills/", result.stdout)
                    self._assert_clean_fixture(repo)

    def test_preexisting_lock_refuses_install_without_touching_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "skills"
            marker = destination / "coding-standards" / "PREVIOUS_INSTALL"
            marker.parent.mkdir(parents=True)
            marker.write_text("old\n")
            lock = destination / ".cork-install.lock"
            lock.mkdir()
            before = sorted(
                str(path.relative_to(destination))
                for path in destination.rglob("*")
            )

            result = self._run_real_install(destination)

            after = sorted(
                str(path.relative_to(destination))
                for path in destination.rglob("*")
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("another cork install is running", result.stdout)
            self.assertEqual(after, before)
            self.assertEqual(marker.read_text(), "old\n")

    def test_upgrade_replaces_stale_files_sweeps_leftovers_and_removes_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "skills"
            first = self._run_real_install(destination)
            self.assertEqual(first.returncode, 0, first.stderr)
            stale_file = destination / "coding-standards" / "references" / "OLD.md"
            stale_file.write_text("stale\n")
            (destination / ".cork.tmp.dead").mkdir()
            (destination / ".cork.prev.dead").mkdir()

            second = self._run_real_install(destination)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout.count("installed (stamp v0.9.0)"), 5)
            self.assertNotIn("⚠", second.stdout)
            self.assertFalse(stale_file.exists())
            self._assert_no_install_artifacts(destination)

    def test_failed_staged_move_restores_previous_copy_and_cleans_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "skills"
            first = self._run_real_install(destination)
            self.assertEqual(first.returncode, 0, first.stderr)
            marker = destination / "coding-standards" / "PREVIOUS_INSTALL"
            marker.write_text("old\n")
            wrapper_dir = root / "bin"
            wrapper_dir.mkdir()
            real_mv = shutil.which("mv")
            self.assertIsNotNone(real_mv)
            if real_mv is None:
                self.fail("mv executable not found")
            wrapper = wrapper_dir / "mv"
            wrapper.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *".coding-standards.tmp."*"/coding-standards") exit 1 ;;\n'
                "esac\n"
                f"exec {shlex.quote(real_mv)} \"$@\"\n"
            )
            wrapper.chmod(0o755)

            result = self._run_real_install(
                destination, PATH=f"{wrapper_dir}:{os.environ['PATH']}"
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("restoring previous copy", result.stderr)
            self.assertEqual(marker.read_text(), "old\n")
            self._assert_no_install_artifacts(destination)


if __name__ == "__main__":
    unittest.main()
