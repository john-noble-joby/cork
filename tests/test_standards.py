import json, os, unittest, tempfile
from pathlib import Path
import orchestrate


class LayeringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"; self.repo.mkdir()
        # isolate config + the shipped default
        self._cfg = orchestrate.CONFIG_PATH
        orchestrate.CONFIG_PATH = self.root / "config.json"
        self._std = orchestrate._DEFAULT_STANDARDS
        orchestrate._DEFAULT_STANDARDS = self.root / "standards.md"
        orchestrate._DEFAULT_STANDARDS.write_text("UNIVERSAL")

    def tearDown(self):
        orchestrate.CONFIG_PATH = self._cfg
        orchestrate._DEFAULT_STANDARDS = self._std
        self.tmp.cleanup()

    def _project(self, text="PROJECT"):
        d = self.repo / "code-review"; d.mkdir(exist_ok=True)
        (d / "AGENTS.md").write_text(text)

    def test_default_on_plus_project(self):
        self._project()
        text, label = orchestrate.load_agent_instructions(str(self.repo))
        self.assertIn("UNIVERSAL", text); self.assertIn("PROJECT", text)

    def test_default_on_no_project(self):
        text, _ = orchestrate.load_agent_instructions(str(self.repo))
        self.assertEqual(text, "UNIVERSAL")

    def test_sentinel_opts_out_default(self):
        self._project()
        (self.repo / "code-review" / ".cork-standards-off").write_text("")
        text, _ = orchestrate.load_agent_instructions(str(self.repo))
        self.assertNotIn("UNIVERSAL", text); self.assertIn("PROJECT", text)

    def test_global_off(self):
        orchestrate.CONFIG_PATH.write_text(json.dumps({
            "rotation": [{"provider": "copilot", "model": "gpt-4.1"}],
            "default_standards": False}))
        self._project()
        text, _ = orchestrate.load_agent_instructions(str(self.repo))
        self.assertNotIn("UNIVERSAL", text); self.assertIn("PROJECT", text)

    def test_nothing_applies(self):
        (self.repo / "code-review").mkdir()
        (self.repo / "code-review" / ".cork-standards-off").write_text("")
        text, label = orchestrate.load_agent_instructions(str(self.repo))
        self.assertEqual((text, label), ("", ""))


class StandardsCmdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self._cfg = orchestrate.CONFIG_PATH
        orchestrate.CONFIG_PATH = self.repo / "config.json"

    def tearDown(self):
        orchestrate.CONFIG_PATH = self._cfg
        self.tmp.cleanup()

    def test_init_scaffolds_project_file(self):
        orchestrate.cmd_standards_init(str(self.repo))
        f = self.repo / "code-review" / "AGENTS.md"
        self.assertTrue(f.exists())
        self.assertIn("project-specific", f.read_text().lower())

    def test_init_refuses_overwrite(self):
        d = self.repo / "code-review"; d.mkdir()
        (d / "AGENTS.md").write_text("mine")
        with self.assertRaises(SystemExit):
            orchestrate.cmd_standards_init(str(self.repo))
        self.assertEqual((d / "AGENTS.md").read_text(), "mine")

    def test_init_opt_out_writes_sentinel(self):
        orchestrate.cmd_standards_init(str(self.repo), opt_out=True)
        self.assertTrue(orchestrate._repo_opted_out(str(self.repo)))

    def test_status_reports_missing_default_as_off(self):
        # status must mirror load_agent_instructions: a missing default file is OFF.
        import io
        from contextlib import redirect_stdout
        orig = orchestrate._DEFAULT_STANDARDS
        orchestrate._DEFAULT_STANDARDS = self.repo / "does-not-exist.md"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                orchestrate.cmd_standards_status(str(self.repo))
            out = buf.getvalue()
            self.assertIn("universal default: OFF", out)
            self.assertIn("missing", out)
        finally:
            orchestrate._DEFAULT_STANDARDS = orig


if __name__ == "__main__":
    unittest.main()
