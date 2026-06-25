import json, os, unittest, tempfile
from pathlib import Path
import orchestrate


class TokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = Path(self.tmp.name) / "auth.json"
        self._orig = orchestrate._CORK_AUTH
        orchestrate._CORK_AUTH = self.auth
        # Save prior env values so we restore (not clobber) the developer's shell.
        self._env = {k: os.environ.pop(k, None)
                     for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}

    def tearDown(self):
        orchestrate._CORK_AUTH = self._orig
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
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


class AnthropicExtractTest(unittest.TestCase):
    def test_extracts_text_blocks(self):
        data = {"content": [{"type": "text", "text": "FILE | LINE | ISSUE"},
                            {"type": "text", "text": " | FIX"}]}
        self.assertEqual(orchestrate._extract_anthropic_text(data),
                         "FILE | LINE | ISSUE | FIX")

    def test_empty_content(self):
        self.assertEqual(orchestrate._extract_anthropic_text({"content": []}), "")


if __name__ == "__main__":
    unittest.main()
