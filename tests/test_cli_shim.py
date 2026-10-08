import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "bin" / "cork"


def _run(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True)


class CorkShimTest(unittest.TestCase):
    def test_shim_is_executable_and_runs_this_checkouts_orchestrate(self):
        self.assertTrue(os.access(SHIM, os.X_OK), f"{SHIM} must be executable (git mode 755)")
        direct = _run("python3", ROOT / "orchestrate.py", "--version")
        via_shim = _run(SHIM, "--version")
        self.assertEqual(via_shim.returncode, 0, via_shim.stderr)
        self.assertEqual(via_shim.stdout, direct.stdout)
        self.assertTrue(via_shim.stdout.startswith("cork "))

    def test_symlink_chain_with_relative_link_resolves_to_the_source_clone(self):
        # ~/.local/bin/cork → ../tools/cork-link → <clone>/bin/cork: the shim must follow
        # both hops (one relative) and still run the clone's orchestrate.py.
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp) / "tools"; tools.mkdir()
            bindir = Path(tmp) / "bin"; bindir.mkdir()
            (tools / "cork-link").symlink_to(SHIM)
            (bindir / "cork").symlink_to(Path("..") / "tools" / "cork-link")
            direct = _run("python3", ROOT / "orchestrate.py", "--version")
            result = _run(bindir / "cork", "--version", cwd=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, direct.stdout)

    def test_arguments_and_cwd_pass_through_unchanged(self):
        # `standards status <dir>` reads the target repo, so matching output proves argv and
        # cwd reach orchestrate.py intact; a bad verb must surface orchestrate.py's own error.
        with tempfile.TemporaryDirectory() as tmp:
            direct = _run("python3", ROOT / "orchestrate.py", "standards", "status", ".", cwd=Path(tmp))
            via_shim = _run(SHIM, "standards", "status", ".", cwd=Path(tmp))
            self.assertEqual(via_shim.returncode, direct.returncode)
            self.assertEqual(via_shim.stdout, direct.stdout)
            direct = _run("python3", ROOT / "orchestrate.py", "config", "get")
            via_shim = _run(SHIM, "config", "get")
            self.assertNotEqual(via_shim.returncode, 0)
            self.assertEqual(via_shim.returncode, direct.returncode)
            self.assertEqual(via_shim.stderr, direct.stderr)


class InstallLinksShimTest(unittest.TestCase):
    def _install(self, home: Path, destination: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(env_overrides)
        env["HOME"] = str(home)
        env["CLAUDE_SKILLS_DIR"] = str(destination)
        env["CORK_HOME"] = str(ROOT)
        return subprocess.run(["bash", "install.sh"], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)

    def test_install_links_cork_into_local_bin_and_hints_when_not_on_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir()
            link = home / ".local" / "bin" / "cork"
            first = self._install(home, Path(tmp) / "skills")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue(link.is_symlink())
            self.assertTrue(os.path.samefile(link, SHIM))
            self.assertIn("✓ cork command linked", first.stdout)
            self.assertIn("not on PATH", first.stdout)
            # the hint is informational, not an install warning
            self.assertNotIn("⚠", first.stdout)
            # idempotent: a second run reports the existing link and keeps it
            second = self._install(home, Path(tmp) / "skills")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("cork command already linked", second.stdout)
            self.assertTrue(os.path.samefile(link, SHIM))
            # on PATH: no hint
            on_path = self._install(home, Path(tmp) / "skills", PATH=f"{link.parent}{os.pathsep}{os.environ['PATH']}")
            self.assertEqual(on_path.returncode, 0, on_path.stderr)
            self.assertNotIn("not on PATH", on_path.stdout)

    def test_install_prints_a_hook_snippet_that_carries_a_non_default_bin_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir(); dest = Path(tmp) / "skills"
            out = self._install(home, dest).stdout
            # default bin dir → no CORK_BIN_DIR prefix; the (non-default) skills dir of this install is carried
            self.assertIn(f"\"command\": \"CLAUDE_SKILLS_DIR='{dest}' '{SHIM}' doctor\"", out)
            self.assertNotIn("CORK_BIN_DIR=", out)
            home2 = Path(tmp) / "home2"; home2.mkdir()
            custom = Path(tmp) / "my bin"            # a space: the snippet must stay one shell word per path
            out = self._install(home2, dest, CORK_BIN_DIR=str(custom)).stdout
            self.assertTrue((custom / "cork").is_symlink())
            # both non-default locations travel inside the hook command and the update command
            self.assertIn(f"\"command\": \"CORK_BIN_DIR='{custom}' CLAUDE_SKILLS_DIR='{dest}' '{SHIM}' doctor\"", out)
            self.assertIn(f"Update later with: CORK_BIN_DIR='{custom}' CLAUDE_SKILLS_DIR='{dest}' '{SHIM}' update", out)
            self.assertFalse((home2 / ".local" / "bin" / "cork").exists())   # the default dir was not touched
            # a relative override is resolved against the installer's cwd once, so the printed hook (run from
            # any cwd later) checks the same link that was created
            home3 = Path(tmp) / "home3"; home3.mkdir()
            out = self._install(home3, dest, CORK_BIN_DIR="rel bin").stdout
            self.assertTrue((ROOT / "rel bin" / "cork").is_symlink(), out)
            self.assertIn(f"CORK_BIN_DIR='{ROOT / 'rel bin'}' ", out)
            import shutil as _sh; _sh.rmtree(ROOT / "rel bin")
            # a single quote in a path is closed, escaped and reopened — still one shell word
            quoted = Path(tmp) / "it's bin"
            out = self._install(home2, dest, CORK_BIN_DIR=str(quoted)).stdout
            self.assertIn("CORK_BIN_DIR='" + str(quoted).replace("'", "'\\''") + "' ", out)
            import shlex
            cmd = next(l for l in out.splitlines() if "Update later with:" in l).split("Update later with: ", 1)[1].split("   (")[0]
            self.assertEqual(shlex.split(cmd)[:2], [f"CORK_BIN_DIR={quoted}", f"CLAUDE_SKILLS_DIR={dest}"])

    def test_install_repoints_cork_links_but_leaves_foreign_files_and_links_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            bindir = home / ".local" / "bin"; bindir.mkdir(parents=True)
            link = bindir / "cork"
            # a symlink to another clone's bin/cork — present or already deleted — is cork's to fix
            other = Path(tmp) / "old-clone" / "bin" / "cork"; other.parent.mkdir(parents=True); other.write_text("#!/bin/sh\n")
            for stale in (other, Path(tmp) / "deleted-clone" / "bin" / "cork"):
                with self.subTest(stale=stale):
                    if link.is_symlink(): link.unlink()
                    link.symlink_to(stale)
                    result = self._install(home, Path(tmp) / "skills")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("repointed", result.stdout)
                    self.assertTrue(os.path.samefile(link, SHIM))
            # a user's own `cork` symlink to some other tool — live or dangling — is not ours: warn, keep it
            tool = Path(tmp) / "other-tool"; tool.write_text("#!/bin/sh\necho not ours\n")
            for foreign in (tool, Path(tmp) / "gone"):
                with self.subTest(foreign=foreign):
                    link.unlink(); link.symlink_to(foreign)
                    result = self._install(home, Path(tmp) / "skills")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(os.readlink(link), str(foreign))
                    self.assertIn("not a cork clone", result.stdout)
                    self.assertNotIn("repointed", result.stdout)
            # a regular file named cork is someone else's too — warn, don't clobber
            link.unlink(); link.write_text("#!/bin/sh\necho not ours\n")
            result = self._install(home, Path(tmp) / "skills")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(link.is_symlink())
            self.assertEqual(link.read_text(), "#!/bin/sh\necho not ours\n")
            self.assertIn("is not a symlink", result.stdout)


if __name__ == "__main__":
    unittest.main()
