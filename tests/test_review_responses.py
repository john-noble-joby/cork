import io, unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import orchestrate


class ResponsesCompletionTest(unittest.TestCase):
    def setUp(self):
        for target, value in (("_provider_headers", {}), ("load_config", {"responses_effort": "high"})):
            patched = patch.object(orchestrate, target, return_value=value)
            patched.start()
            self.addCleanup(patched.stop)
        patched = patch.object(orchestrate, "_http_post_json")
        self.http = patched.start()
        self.addCleanup(patched.stop)

    def review(self, provider: str = "copilot") -> str:
        with redirect_stdout(io.StringIO()):
            return orchestrate.review(provider, "gpt-6-sol", "standards", "story", "diff", {})

    def test_incomplete_reviews_are_skipped_once_even_with_partial_text(self):
        for provider in ("copilot", "openai"):
            for reason in ("max_output_tokens", "content_filter"):
                for text in ("", "Partial findings"):
                    with self.subTest(provider=provider, reason=reason, text=text):
                        self.http.reset_mock()
                        self.http.return_value = (200, {"status": "incomplete", "output_text": text,
                                                       "incomplete_details": {"reason": reason}})
                        self.assertEqual(self.review(provider),
                                         f"[{provider}/gpt-6-sol review incomplete ({reason}) — skipped]")
                        self.assertEqual(self.http.call_count, 1)
                        payload = self.http.call_args.args[2]
                        self.assertEqual(payload["reasoning"], {"effort": "high"})
                        self.assertEqual(payload["max_output_tokens"], 32_000)

    def test_incomplete_without_reason_still_fails_closed(self):
        for details in ({}, {"incomplete_details": None}, {"incomplete_details": {}},
                        {"incomplete_details": {"reason": ""}}):
            with self.subTest(details=details):
                self.http.reset_mock()
                self.http.return_value = (200, {"status": "incomplete", "output_text": "Partial", **details})
                self.assertEqual(self.review(),
                                 "[copilot/gpt-6-sol review incomplete (unknown reason) — skipped]")
                self.assertEqual(self.http.call_count, 1)

    def test_failed_reviews_discard_partial_output_and_skip_once(self):
        for provider in ("copilot", "openai"):
            for text in ("", "Partial findings"):
                with self.subTest(provider=provider, text=text):
                    self.http.reset_mock()
                    self.http.return_value = (200, {"status": "failed", "output_text": text,
                                                   "error": {"code": "server_error", "message": "fixture failure"}})
                    self.assertEqual(self.review(provider),
                                     f"[{provider}/gpt-6-sol review failed (fixture failure) — skipped]")
                    self.assertEqual(self.http.call_count, 1)

    def test_failed_review_diagnostic_fallbacks(self):
        for fields, expected in (({}, "unknown reason"), ({"error": None}, "unknown reason"),
                                 ({"error": {}}, "unknown reason"),
                                 ({"error": {"message": "", "code": ""}}, "unknown reason"),
                                 ({"error": {"message": None, "code": "server_error"}}, "server_error")):
            with self.subTest(fields=fields):
                self.http.reset_mock()
                self.http.return_value = (200, {"status": "failed", "output_text": "Partial", **fields})
                self.assertEqual(self.review(),
                                 f"[copilot/gpt-6-sol review failed ({expected}) — skipped]")
                self.assertEqual(self.http.call_count, 1)

    def test_malformed_diagnostic_containers_skip_without_breaking_probes(self):
        for provider in ("copilot", "openai"):
            for state, key in (("failed", "error"), ("incomplete", "incomplete_details")):
                for details in ("rate limited", 42, True, [], ["reason"], None):
                    with self.subTest(provider=provider, state=state, details=details):
                        self.http.reset_mock()
                        self.http.return_value = (200, {"status": state, key: details, "output_text": "Partial"})
                        self.assertEqual(self.review(provider),
                                         f"[{provider}/gpt-6-sol review {state} (unknown reason) — skipped]")
                        self.assertEqual(self.http.call_count, 1)
                        self.http.reset_mock()
                        self.assertEqual(orchestrate._probe(provider, "gpt-6-sol"), "ok")
                        self.assertEqual(self.http.call_count, 1)

    def test_other_explicit_noncompleted_states_fail_closed(self):
        for state in ("cancelled", "queued", "in_progress", "future_state"):
            with self.subTest(state=state):
                self.http.reset_mock()
                self.http.return_value = (200, {"status": state, "output_text": "Partial findings"})
                self.assertEqual(self.review(),
                                 f"[copilot/gpt-6-sol review {state} (unknown reason) — skipped]")
                self.assertEqual(self.http.call_count, 1)

    def test_malformed_status_values_skip_once_but_probes_remain_available(self):
        for provider in ("copilot", "openai"):
            for state in (False, 0, 0.0, [], {}, True, 1, ["completed"], {"status": "completed"}):
                for text in ("", "Partial findings"):
                    with self.subTest(provider=provider, state=state, text=text):
                        self.http.reset_mock()
                        self.http.return_value = (200, {"status": state, "output_text": text})
                        self.assertEqual(self.review(provider),
                                         f"[{provider}/gpt-6-sol review {state} (unknown reason) — skipped]")
                        self.assertEqual(self.http.call_count, 1)
                        self.http.reset_mock()
                        self.assertEqual(orchestrate._probe(provider, "gpt-6-sol"), "ok")
                        self.assertEqual(self.http.call_count, 1)

    def test_high_effort_probe_accepts_http_200_with_no_output(self):
        for body in ({}, {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
                     {"status": "failed", "error": {"message": "fixture failure"}},
                     {"status": "queued"}):
            for provider in ("copilot", "openai"):
                with self.subTest(body=body, provider=provider):
                    self.http.reset_mock()
                    self.http.return_value = (200, body)
                    self.assertEqual(orchestrate._probe(provider, "gpt-6-sol"), "ok")
                    self.assertEqual(self.http.call_count, 1)
                    payload = self.http.call_args.args[2]
                    self.assertEqual(payload["max_output_tokens"], orchestrate._PROBE_MAX_OUT)
                    self.assertEqual(payload["reasoning"], {"effort": "high"})

    def test_complete_and_statusless_responses_ignore_stale_error_metadata(self):
        for fields in ({"status": "completed"}, {}, {"status": None}, {"status": ""}):
            with self.subTest(fields=fields):
                self.http.reset_mock()
                self.http.return_value = (200, {**fields, "output_text": "Findings",
                                               "error": {"message": "stale error"},
                                               "incomplete_details": {"reason": "max_output_tokens"}})
                self.assertEqual(self.review(), "Findings")
                self.assertEqual(self.http.call_count, 1)

    def test_ordinary_empty_response_still_retries(self):
        self.http.return_value = (200, {"status": "completed", "output": []})
        self.assertEqual(self.review(), "[copilot/gpt-6-sol returned no usable content — skipped]")
        self.assertEqual(self.http.call_count, 3)

    def test_chat_completion_is_not_interpreted_as_a_responses_result(self):
        for state in ("incomplete", "failed"):
            with self.subTest(state=state):
                self.http.return_value = (200, {"status": state, "incomplete_details": {"reason": "test"},
                                               "error": {"message": "test"},
                                               "choices": [{"message": {"content": "Chat findings"}}]})
                self.assertEqual(orchestrate._call_and_extract("copilot", "gpt-4.1", "S", "U"),
                                 (200, "Chat findings", None))

    def test_http_error_preserves_transient_retry_behavior(self):
        self.http.side_effect = [(500, {"status": "incomplete", "incomplete_details": {"reason": "test"}}),
                                 (200, {"status": "completed", "output_text": "Recovered"})]
        with patch.object(orchestrate, "_retry_wait") as wait:
            self.assertEqual(self.review(), "Recovered")
        self.assertEqual(self.http.call_count, 2)
        wait.assert_called_once()

    def test_native_anthropic_error_preserves_status_and_body(self):
        with patch.object(orchestrate, "_anthropic_call", return_value=(401, "unauthorized")):
            self.assertEqual(orchestrate._call_and_extract("anthropic", "claude-x", "S", "U"),
                             (401, "unauthorized", None))
        self.http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
