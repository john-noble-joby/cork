import unittest
import orchestrate


class RemainingTest(unittest.TestCase):
    def _state(self, done):
        return {"rotation": [{"provider": "copilot", "model": "a"},
                             {"provider": "copilot", "model": "b"}],
                "done": done}

    def test_fresh(self):
        r = orchestrate._remaining_work(self._state({}))
        self.assertTrue(r["implement"])
        self.assertEqual(r["models"], ["copilot/a", "copilot/b"])

    def test_partial(self):
        r = orchestrate._remaining_work(self._state({
            "implement": True, "self_review": "x", "self_fix": "y",
            "models": {"copilot/a": {"review": "r", "fix": "f"}},
        }))
        self.assertFalse(r["implement"])
        self.assertEqual(r["models"], ["copilot/b"])  # a fully done, b remains

    def test_model_review_only(self):
        r = orchestrate._remaining_work(self._state({
            "implement": True, "self_review": "x", "self_fix": "y",
            "models": {"copilot/a": {"review": "r"}},  # review yes, fix no
        }))
        self.assertEqual(r["models"], ["copilot/a", "copilot/b"])
        self.assertEqual(r["needs_fix_only"], ["copilot/a"])


if __name__ == "__main__":
    unittest.main()
