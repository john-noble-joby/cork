import json, os, unittest, tempfile
from pathlib import Path
from unittest.mock import patch
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


class CopilotRoutingTest(unittest.TestCase):
    def test_routes_and_extracts_each_model_family(self):
        cases = [
            ("gpt-6-sol", True),
            ("gpt-6-astra", True),
            ("gpt-5.6-sol", True),
            ("gpt-5.5", True),
            ("codex-mini", True),
            ("claude-opus-5.5", False),
            ("gpt-4.1", False),
        ]
        for model, responses in cases:
            with self.subTest(model=model):
                body = ({"output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "review findings"}]}]}
                    if responses else {"choices": [
                        {"message": {"content": "review findings"}}]})
                with patch.object(orchestrate, "_provider_headers", return_value={}), \
                     patch.object(orchestrate, "_http_post_json",
                                  return_value=(200, body)) as post:
                    result = orchestrate._call_and_extract(
                        "copilot", model, "standards", "diff", max_out=16)
                self.assertEqual(result, (200, "review findings"))
                url, _, payload, _ = post.call_args.args
                endpoint = "/responses" if responses else "/chat/completions"
                self.assertEqual(url, orchestrate.COPILOT_BASE + endpoint)
                self.assertEqual(payload["model"], model)
                if responses:
                    self.assertEqual(payload["input"], "diff")
                    self.assertEqual(payload["max_output_tokens"], 16)
                    self.assertEqual(payload["reasoning"], {"effort": "high"})
                else:
                    self.assertEqual(payload["messages"][-1]["content"], "diff")
                    self.assertEqual(payload["max_tokens"], 16)
                    if model == "claude-opus-5.5":
                        self.assertEqual(payload["reasoning_effort"], "high")
                    else:
                        self.assertNotIn("reasoning_effort", payload)

    def test_opus_chat_effort_is_copilot_specific(self):
        with patch.object(orchestrate, "_provider_headers", return_value={}), \
             patch.object(orchestrate, "_http_post_json", return_value=(200, {})) as post:
            orchestrate._openai_compatible_call(
                "openai", "claude-opus-5.5", "standards", "diff")
        self.assertNotIn("reasoning_effort", post.call_args.args[2])

    def test_review_sized_calls_request_high_effort(self):
        for model in ("gpt-6-sol", "claude-opus-5.5", "gpt-6-astra"):
            with self.subTest(model=model), \
                 patch.object(orchestrate, "_provider_headers", return_value={}), \
                 patch.object(orchestrate, "_http_post_json", return_value=(200, {})) as post:
                orchestrate._openai_compatible_call("copilot", model, "standards", "diff")
                payload = post.call_args.args[2]
                if model == "claude-opus-5.5":
                    self.assertEqual(payload["reasoning_effort"], "high")
                else:
                    self.assertEqual(payload["reasoning"], {"effort": "high"})


if __name__ == "__main__":
    unittest.main()
