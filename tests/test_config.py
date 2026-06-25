import json, os, unittest, tempfile
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


if __name__ == "__main__":
    unittest.main()
