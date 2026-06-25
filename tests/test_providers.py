import json, os, unittest, tempfile
from pathlib import Path
import orchestrate


class TokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = Path(self.tmp.name) / "auth.json"
        self._orig = orchestrate._CORK_AUTH
        orchestrate._CORK_AUTH = self.auth
        os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self):
        orchestrate._CORK_AUTH = self._orig
        os.environ.pop("OPENAI_API_KEY", None)
        self.tmp.cleanup()

    def test_env_var_wins(self):
        os.environ["OPENAI_API_KEY"] = "env-key"
        self.auth.write_text(json.dumps({"openai": "file-key"}))
        self.assertEqual(orchestrate._provider_token("openai"), "env-key")

    def test_auth_file_fallback(self):
        self.auth.write_text(json.dumps({"openai": "file-key"}))
        self.assertEqual(orchestrate._provider_token("openai"), "file-key")

    def test_missing_token_fails(self):
        self.auth.write_text(json.dumps({}))
        with self.assertRaises(SystemExit):
            orchestrate._provider_token("anthropic")


if __name__ == "__main__":
    unittest.main()
