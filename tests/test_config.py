import io, json, unittest, tempfile
from contextlib import redirect_stdout
from pathlib import Path
import orchestrate


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self._orig = orchestrate.CONFIG_PATH
        orchestrate.CONFIG_PATH = self.path

    def tearDown(self):
        orchestrate.CONFIG_PATH = self._orig
        self.tmp.cleanup()

    def test_missing_config_returns_default(self):
        cfg = orchestrate.load_config()
        self.assertEqual(cfg, orchestrate.DEFAULT_CONFIG)

    def test_default_is_valid(self):
        orchestrate._validate_config(orchestrate.DEFAULT_CONFIG)  # no raise

    def test_init_writes_default(self):
        orchestrate.cmd_config_init()
        self.assertTrue(self.path.exists())
        self.assertEqual(json.loads(self.path.read_text()), orchestrate.DEFAULT_CONFIG)

    def test_loads_written_config(self):
        self.path.write_text(json.dumps({
            "version": 1, "count": 2,
            "providers": {"copilot": {"enabled": True}},
            "rotation": [{"provider": "copilot", "model": "gpt-4.1"}],
        }))
        cfg = orchestrate.load_config()
        self.assertEqual(cfg["count"], 2)

    def test_unknown_provider_fails(self):
        self.path.write_text(json.dumps({
            "rotation": [{"provider": "bogus", "model": "x"}],
        }))
        with self.assertRaises(SystemExit):
            orchestrate.load_config()

    def test_duplicate_rotation_fails(self):
        self.path.write_text(json.dumps({
            "rotation": [
                {"provider": "copilot", "model": "gpt-4.1"},
                {"provider": "copilot", "model": "gpt-4.1"},
            ],
        }))
        with self.assertRaises(SystemExit):
            orchestrate.load_config()


class ConfigGetSetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self._orig = orchestrate.CONFIG_PATH
        orchestrate.CONFIG_PATH = self.path

    def tearDown(self):
        orchestrate.CONFIG_PATH = self._orig
        self.tmp.cleanup()

    def test_get_interactive_review_defaults_true_when_no_file(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            orchestrate.cmd_config_get("interactive_review")
        self.assertEqual(buf.getvalue().strip(), "true")

    def test_get_interactive_review_defaults_true_when_key_absent(self):
        self.path.write_text(json.dumps({
            "rotation": [{"provider": "copilot", "model": "gpt-4.1"}],
        }))
        buf = io.StringIO()
        with redirect_stdout(buf):
            orchestrate.cmd_config_get("interactive_review")
        self.assertEqual(buf.getvalue().strip(), "true")

    def test_set_then_get_roundtrip(self):
        self.path.write_text(json.dumps({
            "rotation": [{"provider": "copilot", "model": "gpt-4.1"}],
        }))
        orchestrate.cmd_config_set("interactive_review", "false")
        self.assertFalse(json.loads(self.path.read_text())["interactive_review"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            orchestrate.cmd_config_get("interactive_review")
        self.assertEqual(buf.getvalue().strip(), "false")

    def test_set_creates_file_from_default(self):
        orchestrate.cmd_config_set("interactive_review", "false")
        self.assertTrue(self.path.exists())
        cfg = json.loads(self.path.read_text())
        self.assertFalse(cfg["interactive_review"])
        self.assertIn("rotation", cfg)   # seeded from DEFAULT_CONFIG

    def test_set_rejects_unknown_key(self):
        self.path.write_text(json.dumps({"rotation": [{"provider": "copilot", "model": "gpt-4.1"}]}))
        before = self.path.read_text()
        with self.assertRaises(SystemExit):
            orchestrate.cmd_config_set("count", "5")
        self.assertEqual(self.path.read_text(), before)   # unchanged

    def test_set_rejects_non_bool_interactive_review(self):
        with self.assertRaises(SystemExit):
            orchestrate.cmd_config_set("interactive_review", "maybe")

    def test_set_accepts_case_insensitive_bool(self):
        orchestrate.cmd_config_set("interactive_review", "TRUE")
        self.assertTrue(json.loads(self.path.read_text())["interactive_review"])

    def test_get_unknown_key_fails(self):
        with self.assertRaises(SystemExit):
            orchestrate.cmd_config_get("nope")

    def test_load_rejects_non_bool_interactive_review(self):
        self.path.write_text(json.dumps({
            "rotation": [{"provider": "copilot", "model": "gpt-4.1"}],
            "interactive_review": "true",   # string, not a JSON bool
        }))
        with self.assertRaises(SystemExit):
            orchestrate.load_config()

    def test_get_default_standards_defaults_true(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            orchestrate.cmd_config_get("default_standards")
        self.assertEqual(buf.getvalue().strip(), "true")

    def test_set_default_standards_roundtrip(self):
        orchestrate.cmd_config_set("default_standards", "false")
        self.assertFalse(json.loads(self.path.read_text())["default_standards"])

    def test_set_default_standards_rejects_non_bool(self):
        with self.assertRaises(SystemExit):
            orchestrate.cmd_config_set("default_standards", "yes")


if __name__ == "__main__":
    unittest.main()
