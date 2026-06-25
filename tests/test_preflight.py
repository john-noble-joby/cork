import unittest
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
