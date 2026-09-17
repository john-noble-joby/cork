import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
import orchestrate


class ClassifyTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(orchestrate._classify_preflight(200, "ok"), "ok")

    def test_model_not_supported(self):
        self.assertEqual(orchestrate._classify_preflight(
            400, '{"error":{"code":"model_not_supported"}}'), "model_not_supported")

    def test_integrator(self):
        self.assertEqual(orchestrate._classify_preflight(
            400, 'model "x" is not available for integrator "zed"'),
            "integrator_mismatch")

    def test_auth(self):
        self.assertEqual(orchestrate._classify_preflight(401, "nope"), "auth")

    def test_other(self):
        self.assertEqual(orchestrate._classify_preflight(503, "busy"), "other")


class SelectTest(unittest.TestCase):
    def setUp(self):
        self._resolve = orchestrate._resolve_copilot_auth
        orchestrate._resolve_copilot_auth = lambda: ("TOKEN", "cork", 9_999_999_999.0, True)

    def tearDown(self):
        orchestrate._resolve_copilot_auth = self._resolve

    def test_stops_at_count_and_skips_dead(self):
        rotation = [
            {"provider": "copilot", "model": "dead1"},
            {"provider": "copilot", "model": "good1"},
            {"provider": "copilot", "model": "good2"},
            {"provider": "copilot", "model": "good3"},
        ]
        calls = []
        def fake_probe(provider, model):
            calls.append(model)
            return "ok" if model.startswith("good") else "model_not_supported"
        orig = orchestrate._probe
        orchestrate._probe = fake_probe
        try:
            sel = orchestrate.preflight(rotation, count=2)
        finally:
            orchestrate._probe = orig
        self.assertEqual([s["model"] for s in sel], ["good1", "good2"])
        self.assertEqual(calls, ["dead1", "good1", "good2"])  # stopped, never probed good3

    def test_zero_survivors_exits(self):
        rotation = [{"provider": "copilot", "model": "dead"}]
        orig = orchestrate._probe
        orchestrate._probe = lambda p, m: "model_not_supported"
        try:
            with self.assertRaises(SystemExit):
                orchestrate.preflight(rotation, count=3)
        finally:
            orchestrate._probe = orig

    def test_auth_halts(self):
        orig = orchestrate._probe
        orchestrate._probe = lambda p, m: "auth"
        try:
            with self.assertRaises(SystemExit):
                orchestrate.preflight(
                    [{"provider": "copilot", "model": "m"}], count=1)
        finally:
            orchestrate._probe = orig

    def test_native_only_rotation_does_not_resolve_copilot_auth(self):
        orchestrate._resolve_copilot_auth = lambda: self.fail("must not resolve Copilot")
        orig = orchestrate._probe
        orchestrate._probe = lambda provider, model: "ok"
        try:
            selected = orchestrate.preflight(
                [{"provider": "openai", "model": "gpt-4o"}], count=1)
        finally:
            orchestrate._probe = orig
        self.assertEqual(selected, [{"provider": "openai", "model": "gpt-4o"}])


class AuthVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cork = Path(self.tmp.name) / "auth.json"
        self.opencode = Path(self.tmp.name) / "opencode.json"
        self._cork, self._opencode = orchestrate._CORK_AUTH, orchestrate._OPENCODE_AUTH
        self._probe, self._now = orchestrate._probe, orchestrate._now
        self._env = os.environ.pop("CORK_COPILOT_TOKEN", None)
        orchestrate._CORK_AUTH, orchestrate._OPENCODE_AUTH = self.cork, self.opencode

    def tearDown(self):
        orchestrate._CORK_AUTH, orchestrate._OPENCODE_AUTH = self._cork, self._opencode
        orchestrate._probe, orchestrate._now = self._probe, self._now
        if self._env is not None:
            os.environ["CORK_COPILOT_TOKEN"] = self._env
        self.tmp.cleanup()

    def test_preflight_warns_when_using_opencode_fallback(self):
        orchestrate._now = lambda: 1000.0
        self.opencode.write_text(json.dumps({
            "github-copilot": {
                "access": "EXPIRED",
                "refresh": "FALLBACK",
                "expires": 0,
            }
        }))
        orchestrate._probe = lambda provider, model: "ok"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            selected = orchestrate.preflight(
                [{"provider": "copilot", "model": "gpt-4.1"}], count=1)
        self.assertEqual(selected, [{"provider": "copilot", "model": "gpt-4.1"}])
        text = out.getvalue()
        self.assertIn("WARNING: Copilot token: opencode fallback", text)
        self.assertIn(str(self.opencode), text)
        self.assertIn("no expiry, not refreshable", text)
        self.assertIn(orchestrate._LOGIN_COMMAND, text)
        self.assertLess(text.index("opencode fallback"), text.index("✓ copilot/gpt-4.1"))

    def test_preflight_401_names_token_only_cork_file_and_relogin(self):
        self.cork.write_text(json.dumps({"token": "STALE"}))
        orchestrate._probe = lambda provider, model: "auth"
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            orchestrate.preflight(
                [{"provider": "copilot", "model": "gpt-4.1"}], count=1)
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("token-only cork file", err.getvalue())
        self.assertIn(str(self.cork), err.getvalue())
        self.assertIn("delete it and re-login", err.getvalue())
        self.assertIn(orchestrate._LOGIN_COMMAND, err.getvalue())


class EligibleRotationTest(unittest.TestCase):
    def _patch_token_available(self, return_val_map: dict[str, bool]):
        # return_val_map: provider -> bool; missing providers default to True
        def fake(provider: str) -> bool:
            return return_val_map.get(provider, True)
        orig = orchestrate._provider_token_available
        orchestrate._provider_token_available = fake
        return orig

    def test_drops_disabled_provider(self):
        cfg = {
            "providers": {"openai": {"enabled": False}},
            "rotation": [
                {"provider": "openai", "model": "gpt-4o"},
                {"provider": "copilot", "model": "gpt-5.5"},
            ],
        }
        orig = self._patch_token_available({"openai": True, "copilot": True})
        try:
            result = orchestrate._eligible_rotation(cfg)
        finally:
            orchestrate._provider_token_available = orig
        self.assertEqual(result, [{"provider": "copilot", "model": "gpt-5.5"}])

    def test_drops_entry_with_no_token(self):
        cfg = {
            "providers": {},
            "rotation": [
                {"provider": "anthropic", "model": "claude-opus-4"},
                {"provider": "copilot",   "model": "gpt-5.5"},
            ],
        }
        orig = self._patch_token_available({"anthropic": False, "copilot": True})
        try:
            result = orchestrate._eligible_rotation(cfg)
        finally:
            orchestrate._provider_token_available = orig
        self.assertEqual(result, [{"provider": "copilot", "model": "gpt-5.5"}])

    def test_keeps_enabled_tokened_entries_in_rank_order(self):
        cfg = {
            "providers": {
                "copilot":   {"enabled": True},
                "openai":    {"enabled": True},
                "anthropic": {"enabled": True},
            },
            "rotation": [
                {"provider": "copilot",   "model": "gpt-5.5"},
                {"provider": "openai",    "model": "gpt-4o"},
                {"provider": "anthropic", "model": "claude-opus-4"},
            ],
        }
        orig = self._patch_token_available(
            {"copilot": True, "openai": True, "anthropic": True}
        )
        try:
            result = orchestrate._eligible_rotation(cfg)
        finally:
            orchestrate._provider_token_available = orig
        self.assertEqual(result, [
            {"provider": "copilot",   "model": "gpt-5.5"},
            {"provider": "openai",    "model": "gpt-4o"},
            {"provider": "anthropic", "model": "claude-opus-4"},
        ])

    def test_provider_absent_from_providers_map_defaults_enabled(self):
        # no "providers" key at all — should keep the entry if token available
        cfg = {
            "rotation": [{"provider": "copilot", "model": "gpt-5.5"}],
        }
        orig = self._patch_token_available({"copilot": True})
        try:
            result = orchestrate._eligible_rotation(cfg)
        finally:
            orchestrate._provider_token_available = orig
        self.assertEqual(result, [{"provider": "copilot", "model": "gpt-5.5"}])

    def test_disabled_takes_priority_over_token_presence(self):
        cfg = {
            "providers": {"copilot": {"enabled": False}},
            "rotation": [{"provider": "copilot", "model": "gpt-5.5"}],
        }
        orig = self._patch_token_available({"copilot": True})
        try:
            result = orchestrate._eligible_rotation(cfg)
        finally:
            orchestrate._provider_token_available = orig
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
