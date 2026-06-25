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
    def test_stops_at_count_and_skips_dead(self, ):
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


if __name__ == "__main__":
    unittest.main()
