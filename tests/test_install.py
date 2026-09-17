import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()
SKILLS_LINE = next(
    line for line in (ROOT / "install.sh").read_text().splitlines()
    if line.startswith("SKILLS=(")
)
SKILLS = tuple(SKILLS_LINE.removeprefix("SKILLS=(").removesuffix(")").split())


class InstallSafetyTest(unittest.TestCase):
    def _guard_fixture(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        skills = repo / "skills"
        skills.mkdir(parents=True)
        shutil.copy(ROOT / "install.sh", repo / "install.sh")
        (repo / "VERSION").write_text(f"{VERSION}\n")
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

    def _install_fixture(self, root: Path) -> Path:
        repo, skills = self._guard_fixture(root)
        for skill in SKILLS:
            shutil.copytree(ROOT / "skills" / skill, skills / skill)
        shutil.copy(ROOT / "statusline.py", repo / "statusline.py")
        shutil.copy(ROOT / "orchestrate.py", repo / "orchestrate.py")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            [
                "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                "commit", "-qm", "complete install fixture",
            ],
            cwd=repo,
            check=True,
        )
        return repo

    def _run(
        self, repo: Path, destination: Path | str, **env_overrides: str
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(env_overrides)
        env["CLAUDE_SKILLS_DIR"] = str(destination)
        env.setdefault("CORK_HOME", str(repo))
        env.setdefault("HOME", str(repo.parent / "home"))
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
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

    def _write_failing_coding_standards_mv(self, wrapper_dir: Path) -> None:
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

    def test_identity_guard_refuses_alias_when_string_guard_is_removed(self):
        # Kills deletion of either -ef check: this fixture removes the string
        # guard, leaving filesystem identity as the only refusal path.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, skills = self._guard_fixture(root)
            skills_alias = root / "skills-alias"
            skills_alias.symlink_to(skills, target_is_directory=True)
            repo_alias = root / "repo-alias"
            repo_alias.symlink_to(repo, target_is_directory=True)
            script = repo / "install.sh"
            contents = script.read_text()
            string_guard = '''case "$dest_root/" in
  "$src_root/"*|"$repo_root/")
    echo "✗ refusing to install into $DEST — it overlaps this repo's skills/ (source tree)"
    exit 1
    ;;
esac
'''
            self.assertIn(string_guard, contents)
            script.write_text(contents.replace(string_guard, "", 1))
            subprocess.run(["git", "add", "install.sh"], cwd=repo, check=True)
            subprocess.run(
                [
                    "git", "-c", "user.name=Test", "-c",
                    "user.email=test@example.com", "commit", "-qm",
                    "test fixture without string overlap guard",
                ],
                cwd=repo,
                check=True,
            )

            for alias in (skills_alias, repo_alias):
                with self.subTest(alias=alias):
                    result = self._run(repo, alias)

                    self.assertEqual(result.returncode, 1)
                    self.assertIn("overlaps this repo's skills/", result.stdout)
                    self._assert_clean_fixture(repo)

    def test_repo_root_with_or_without_trailing_slashes_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = self._guard_fixture(Path(tmp))

            for destination in (repo, f"{repo}///"):
                with self.subTest(destination=destination):
                    result = self._run(repo, destination)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("overlaps this repo's skills/", result.stdout)
                    self._assert_clean_fixture(repo)

    def test_dotdot_paths_are_refused_without_creating_source_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, skills = self._guard_fixture(root)
            symlink_target = skills / "sub"
            symlink_target.mkdir()
            link = root / "link"
            link.symlink_to(symlink_target, target_is_directory=True)
            destinations = (
                (Path(f"{link}/../x"), skills / "x"),
                (Path(f"{skills}/nope/../../../outside"), skills / "nope"),
            )

            for destination, forbidden in destinations:
                with self.subTest(destination=destination):
                    result = self._run(repo, destination)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("'..' components are not allowed", result.stderr)
                    self.assertFalse(forbidden.exists())
                    self._assert_clean_fixture(repo)

    def test_regular_file_ancestor_fails_resolution_without_creating_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = self._guard_fixture(root)
            regular_file = root / "not-a-directory"
            regular_file.write_text("file\n")

            result = self._run(repo, regular_file / "new")

            self.assertEqual(result.returncode, 1)
            self.assertIn("could not resolve destination", result.stderr)
            self.assertFalse((regular_file / "new").exists())
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
            self.assertTrue(
                (
                    destination
                    / "coding-standards"
                    / "references"
                    / "review-checklist.md"
                ).is_file()
            )
            stale_file = destination / "coding-standards" / "references" / "OLD.md"
            stale_file.write_text("stale\n")
            (destination / ".cork.tmp.dead").mkdir()
            (destination / ".cork.prev.dead").mkdir()

            second = self._run_real_install(destination)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                second.stdout.count(f"installed (stamp v{VERSION})"), len(SKILLS)
            )
            self.assertNotIn("⚠", second.stdout)
            self.assertFalse(stale_file.exists())
            self._assert_no_install_artifacts(destination)

    def test_statusline_is_installed_next_to_logical_symlinked_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            logical_parent = home / ".claude"
            logical_parent.mkdir(parents=True)
            physical_parent = root / "physical"
            physical_destination = physical_parent / "skills"
            physical_destination.mkdir(parents=True)
            logical_destination = logical_parent / "skills"
            logical_destination.symlink_to(physical_destination, target_is_directory=True)

            result = self._run(
                ROOT,
                logical_destination,
                HOME=str(home),
                CORK_HOME=str(ROOT),
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((logical_parent / "statusline.py").is_file())
            self.assertFalse((physical_parent / "statusline.py").exists())

    def test_trailing_dot_destination_keeps_statusline_outside_skills(self):
        for ending in (".", "./"):
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                claude_dir = root / ".claude"
                skills = claude_dir / "skills"
                destination = f"{skills}/{ending}"

                result = self._run(
                    ROOT,
                    destination,
                    HOME=str(root / "home"),
                    CORK_HOME=str(ROOT),
                )

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue((claude_dir / "statusline.py").is_file())
                self.assertFalse((skills / "statusline.py").exists())

    def test_statusline_copy_is_skipped_when_source_and_destination_are_same_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._install_fixture(Path(tmp))
            destination = repo / "build-skills"
            statusline = repo / "statusline.py"
            before_mtime = statusline.stat().st_mtime_ns

            result = self._run(repo, destination)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                result.stdout.count(f"installed (stamp v{VERSION})"), len(SKILLS)
            )
            self.assertIn("statusline.py already at", result.stdout)
            self.assertEqual(statusline.stat().st_mtime_ns, before_mtime)
            self._assert_no_install_artifacts(destination)

    def test_dangling_skill_symlink_yields_to_orphan_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "skills"
            destination.mkdir()
            orphan = destination / ".coding-standards.prev.123"
            orphan.mkdir()
            (orphan / "PREVIOUS_INSTALL").write_text("old\n")
            (destination / "coding-standards").symlink_to(
                destination / "missing-skill-target", target_is_directory=True
            )

            result = self._run_real_install(destination)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(
                "↩ coding-standards: recovered previous copy from interrupted install",
                result.stdout,
            )
            self.assertIn(
                f"⚠ coding-standards: {destination}/coding-standards is a dangling "
                "symlink — removing it",
                result.stdout,
            )
            installed = destination / "coding-standards"
            self.assertTrue(installed.is_dir())
            self.assertFalse(installed.is_symlink())
            self.assertTrue((installed / "SKILL.md").is_file())
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
            self._write_failing_coding_standards_mv(wrapper_dir)

            result = self._run_real_install(
                destination, PATH=f"{wrapper_dir}:{os.environ['PATH']}"
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("restoring previous copy", result.stderr)
            self.assertEqual(marker.read_text(), "old\n")
            self._assert_no_install_artifacts(destination)

    def test_orphaned_previous_copy_is_recovered_before_normal_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "skills"
            orphan = destination / ".coding-standards.prev.123"
            orphan.mkdir(parents=True)
            (orphan / "PREVIOUS_INSTALL").write_text("old\n")

            result = self._run_real_install(destination)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "recovered previous copy from interrupted install", result.stdout
            )
            installed_skill = destination / "coding-standards" / "SKILL.md"
            self.assertIn(f"**Version:** {VERSION}", installed_skill.read_text())
            self._assert_no_install_artifacts(destination)

    def test_orphaned_previous_copy_survives_failed_staged_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "skills"
            orphan = destination / ".coding-standards.prev.123"
            orphan.mkdir(parents=True)
            (orphan / "PREVIOUS_INSTALL").write_text("old\n")
            wrapper_dir = root / "bin"
            wrapper_dir.mkdir()
            self._write_failing_coding_standards_mv(wrapper_dir)

            result = self._run_real_install(
                destination, PATH=f"{wrapper_dir}:{os.environ['PATH']}"
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn(
                "recovered previous copy from interrupted install", result.stdout
            )
            marker = destination / "coding-standards" / "PREVIOUS_INSTALL"
            self.assertEqual(marker.read_text(), "old\n")
            self._assert_no_install_artifacts(destination)


if __name__ == "__main__":
    unittest.main()
