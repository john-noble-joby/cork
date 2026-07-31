import json, os, unittest, tempfile
from pathlib import Path
import orchestrate


class AuthRefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cork = Path(self.tmp.name) / "auth.json"
        self.oc = Path(self.tmp.name) / "opencode.json"
        self._cork, self._oc = orchestrate._CORK_AUTH, orchestrate._OPENCODE_AUTH
        self._post, self._now = orchestrate._post_form, orchestrate._now
        orchestrate._CORK_AUTH, orchestrate._OPENCODE_AUTH = self.cork, self.oc
        self._env = os.environ.pop("CORK_COPILOT_TOKEN", None)

    def tearDown(self):
        orchestrate._CORK_AUTH, orchestrate._OPENCODE_AUTH = self._cork, self._oc
        orchestrate._post_form, orchestrate._now = self._post, self._now
        if self._env is not None:
            os.environ["CORK_COPILOT_TOKEN"] = self._env
        self.tmp.cleanup()

    def test_payload_persists_refresh_and_expiry(self):
        orchestrate._now = lambda: 1000.0
        out = orchestrate._auth_payload_from_token_response(
            {"access_token": "A", "refresh_token": "R", "expires_in": 28800})
        self.assertEqual(out, {"token": "A", "refresh_token": "R", "expires_at": 1000 + 28800})

    def test_payload_without_refresh_is_token_only(self):
        out = orchestrate._auth_payload_from_token_response({"access_token": "A"})
        self.assertEqual(out, {"token": "A"})

    def test_unexpired_returns_token_without_refresh(self):
        orchestrate._now = lambda: 1000.0
        self.cork.write_text(json.dumps(
            {"token": "A", "refresh_token": "R", "expires_at": 9_999_999_999}))
        orchestrate._post_form = lambda *a, **k: self.fail("must not refresh an unexpired token")
        self.assertEqual(orchestrate._copilot_token(), "A")

    def test_expired_refreshes_rewrites_and_returns_new(self):
        orchestrate._now = lambda: 10000.0
        self.cork.write_text(json.dumps(
            {"token": "OLD", "refresh_token": "R1", "expires_at": 5000}))
        calls = []
        def fake_post(url, fields, timeout=15):
            calls.append(fields)
            return {"access_token": "NEW", "refresh_token": "R2", "expires_in": 28800}
        orchestrate._post_form = fake_post
        self.assertEqual(orchestrate._copilot_token(), "NEW")
        saved = json.loads(self.cork.read_text())
        self.assertEqual(saved["token"], "NEW")
        self.assertEqual(saved["refresh_token"], "R2")
        self.assertEqual(saved["expires_at"], 10000 + 28800)
        self.assertEqual(calls[0]["grant_type"], "refresh_token")
        self.assertEqual(calls[0]["refresh_token"], "R1")

    def test_legacy_token_only_is_used_as_is(self):
        self.cork.write_text(json.dumps({"token": "A"}))
        orchestrate._post_form = lambda *a, **k: self.fail("no refresh for a legacy token-only file")
        self.assertEqual(orchestrate._copilot_token(), "A")

    def test_refresh_invalid_grant_exits_with_guidance(self):
        orchestrate._now = lambda: 10000.0
        self.cork.write_text(json.dumps(
            {"token": "OLD", "refresh_token": "DEAD", "expires_at": 1}))
        orchestrate._post_form = lambda *a, **k: {"error": "invalid_grant"}
        with self.assertRaises(SystemExit):
            orchestrate._copilot_token()

    def test_opencode_fallback_reads_access_not_refresh(self):
        # No cork file → fall through to opencode; must read `access`, not `refresh`.
        self.oc.write_text(json.dumps(
            {"github-copilot": {"access": "OC_ACCESS", "refresh": "OC_REFRESH",
                                "expires": 9_999_999_999_000}}))
        self.assertEqual(orchestrate._copilot_token(), "OC_ACCESS")

    def test_opencode_falls_back_to_refresh_when_access_expired(self):
        # If opencode's access is stale (cork can't run opencode's refresh flow),
        # fall back to the non-expiring refresh token — never worse than before.
        orchestrate._now = lambda: 2000.0  # → 2_000_000 ms
        self.oc.write_text(json.dumps(
            {"github-copilot": {"access": "STALE", "refresh": "GHO_REFRESH",
                                "expires": 1_000_000}}))  # 1_000_000 ms < now
        self.assertEqual(orchestrate._copilot_token(), "GHO_REFRESH")


if __name__ == "__main__":
    unittest.main()
