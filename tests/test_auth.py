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

    def test_expires_in_zero_persists_immediate_expiry(self):
        # A zero-lifetime response must store expires_at (→ refresh next time), not
        # be treated as "no expiry" (which would pin an unusable token forever).
        orchestrate._now = lambda: 1000.0
        out = orchestrate._auth_payload_from_token_response(
            {"access_token": "A", "expires_in": 0})
        self.assertEqual(out.get("expires_at"), 1000)

    def test_malformed_file_fails_before_consuming_refresh_token(self):
        # If the locked re-read finds a malformed file, fail BEFORE the network
        # exchange — otherwise a one-use refresh token is burned then unsaveable.
        orchestrate._now = lambda: 10000.0
        self.cork.write_text("{ corrupt,,,")
        orchestrate._post_form = lambda *a, **k: self.fail(
            "must not exchange the refresh token when the file is malformed")
        stale = {"token": "OLD", "refresh_token": "R1", "expires_at": 1}
        with self.assertRaises(SystemExit):
            orchestrate._cork_access_token(stale)

    def test_concurrent_writes_preserve_keys_and_stay_valid(self):
        import threading
        self.cork.write_text(json.dumps({"openai": "OA"}))
        def w(i):
            orchestrate._write_cork_auth({"token": f"T{i}"})
        ts = [threading.Thread(target=w, args=(i,)) for i in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        saved = json.loads(self.cork.read_text())  # must remain valid JSON
        self.assertEqual(saved["openai"], "OA")     # never lost to a racing writer
        self.assertTrue(saved["token"].startswith("T"))

    def test_non_object_json_auth_file_fails_loudly(self):
        # A valid-JSON-but-non-object file (list/string) is not a legitimate auth
        # file — fail like malformed rather than silently resetting to {}.
        self.cork.write_text(json.dumps(["not", "a", "dict"]))
        with self.assertRaises(SystemExit):
            orchestrate._copilot_token()

    def test_refreshed_token_is_stripped(self):
        orchestrate._now = lambda: 10000.0
        self.cork.write_text(json.dumps(
            {"token": "OLD", "refresh_token": "R1", "expires_at": 5000}))
        orchestrate._post_form = lambda *a, **k: {
            "access_token": "  NEW  ", "refresh_token": "R2", "expires_in": 28800}
        self.assertEqual(orchestrate._copilot_token(), "NEW")

    def test_opencode_non_object_json_fails_loudly(self):
        # A valid-JSON-but-non-object opencode file must fail with a clear message,
        # not crash with AttributeError inside _opencode_access_token.
        self.oc.write_text(json.dumps(["not", "a", "dict"]))
        with self.assertRaises(SystemExit):
            orchestrate._copilot_token()

    def test_expired_token_without_refresh_fails_with_guidance(self):
        # expires_at is past and there's no refresh_token — returning the token
        # guarantees a downstream 401; fail early with re-login guidance instead.
        orchestrate._now = lambda: 10000.0
        self.cork.write_text(json.dumps({"token": "OLD", "expires_at": 5000}))
        with self.assertRaises(SystemExit):
            orchestrate._copilot_token()

    def test_non_utf8_cork_file_fails_loudly(self):
        # Non-UTF8 bytes raise UnicodeDecodeError (not OSError) — must fail cleanly,
        # not surface as a traceback.
        self.cork.write_bytes(b"\xff\xfe not utf8")
        with self.assertRaises(SystemExit):
            orchestrate._read_cork_auth()

    def test_non_utf8_opencode_file_fails_loudly(self):
        self.oc.write_bytes(b"\xff\xfe not utf8")
        with self.assertRaises(SystemExit):
            orchestrate._copilot_token()

    def test_opencode_fallback_reads_access_not_refresh(self):
        # No cork file → fall through to opencode; must read `access`, not `refresh`.
        self.oc.write_text(json.dumps(
            {"github-copilot": {"access": "OC_ACCESS", "refresh": "OC_REFRESH",
                                "expires": 9_999_999_999_000}}))
        self.assertEqual(orchestrate._copilot_token(), "OC_ACCESS")

    def test_refresh_preserves_other_provider_keys(self):
        # auth.json also holds openai/anthropic tokens — a Copilot refresh must
        # not clobber them (it rewrites the whole file).
        orchestrate._now = lambda: 10000.0
        self.cork.write_text(json.dumps(
            {"openai": "OA", "anthropic": "AN",
             "token": "OLD", "refresh_token": "R1", "expires_at": 5000}))
        orchestrate._post_form = lambda *a, **k: {
            "access_token": "NEW", "refresh_token": "R2", "expires_in": 28800}
        self.assertEqual(orchestrate._copilot_token(), "NEW")
        saved = json.loads(self.cork.read_text())
        self.assertEqual(saved["openai"], "OA")
        self.assertEqual(saved["anthropic"], "AN")
        self.assertEqual(saved["token"], "NEW")
        self.assertEqual(saved["refresh_token"], "R2")

    def test_refresh_uses_already_refreshed_file_without_exchanging(self):
        # Concurrency: another process already refreshed the on-disk file while our
        # in-memory snapshot was stale. We must use the fresh token, not re-exchange
        # the (now one-use-consumed) refresh token.
        orchestrate._now = lambda: 10000.0
        self.cork.write_text(json.dumps(
            {"token": "FRESH", "refresh_token": "R2", "expires_at": 99999}))
        stale = {"token": "OLD", "refresh_token": "R1", "expires_at": 1}
        orchestrate._post_form = lambda *a, **k: self.fail(
            "must not exchange when the on-disk token is already fresh")
        self.assertEqual(orchestrate._cork_access_token(stale), "FRESH")

    def test_written_auth_file_is_chmod_600(self):
        orchestrate._write_cork_auth({"token": "X"})
        self.assertEqual(self.cork.stat().st_mode & 0o777, 0o600)

    def test_write_refuses_to_clobber_malformed_existing_file(self):
        # A hand-corrupted file may still hold openai/anthropic secrets — refuse
        # to silently overwrite it (symmetric with _copilot_token's hard fail).
        self.cork.write_text("{ not valid json,,,")
        before = self.cork.read_text()
        with self.assertRaises(SystemExit):
            orchestrate._write_cork_auth({"token": "X"})
        self.assertEqual(self.cork.read_text(), before)

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
