import os, subprocess, tempfile, unittest
from pathlib import Path
import orchestrate


class _FakeRun:
    def __init__(self, rc=0, out="FILE: a.py | LINE: 1 | ISSUE: x | FIX: y", err="", raise_=None):
        self.rc, self.out, self.err, self.raise_, self.calls = rc, out, err, raise_, []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.raise_:
            raise self.raise_
        return subprocess.CompletedProcess(argv, self.rc, stdout=self.out, stderr=self.err)


class HarnessBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._cfg = orchestrate.CONFIG_PATH
        orchestrate.CONFIG_PATH = Path(self.tmp.name) / "config.json"  # -> DEFAULT_CONFIG
        self._run, self._which = orchestrate.subprocess.run, orchestrate.shutil.which
        self._env = {k: os.environ.pop(k, None)
                     for k in ("ANTHROPIC_API_KEY", "CORK_CLAUDE_BIN", "CORK_CODEX_BIN")}

    def tearDown(self):
        orchestrate.CONFIG_PATH = self._cfg
        orchestrate.subprocess.run, orchestrate.shutil.which = self._run, self._which
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()


class ArgvTest(HarnessBase):
    def test_codex_argv_read_only_stdin_cwd(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        status, text = orchestrate._harness_call("codex", "gpt-5.6-sol", "SYS", "USER", "/repo")
        argv, kw = fake.calls[0]
        self.assertEqual(argv, ["codex", "exec", "-m", "gpt-5.6-sol", "--ephemeral",
                                "--skip-git-repo-check", "-C", "/repo", "--color", "never", "-",
                                "-s", "read-only"])  # sandbox flag last
        self.assertEqual(kw["cwd"], "/repo")
        self.assertEqual(kw["timeout"], 900)
        # no system flag -> standards prepended to the stdin body with a separator
        self.assertTrue(kw["input"].startswith("SYS\n\n=== END OF REVIEW STANDARDS"))
        self.assertTrue(kw["input"].endswith("USER"))
        self.assertEqual((status, text), (200, fake.out))

    def test_claude_argv_read_only_system_flag_no_bash(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("claude", "claude-opus-4.7", "SYS", "USER", "/repo")
        argv, kw = fake.calls[0]
        self.assertEqual(argv, ["claude", "-p", "--no-session-persistence", "--output-format",
                                "text", "--model", "claude-opus-4.7", "--system-prompt", "SYS",
                                "--safe-mode", "--restricted", "--tools", "Read,Grep,Glob",
                                "--permission-mode", "plan"])
        self.assertEqual(kw["input"], "USER")  # system NOT duplicated into the body
        self.assertNotIn("--bare", argv)       # --bare refuses OAuth logins; --safe-mode keeps auth
        self.assertNotIn("Bash", ",".join(argv))

    def test_config_cannot_override_read_only_or_argv(self):
        orchestrate.CONFIG_PATH.write_text('{"rotation":[{"provider":"codex","model":"m"}],'
                                           '"providers":{"codex":{"enabled":true,"read_only":[],'
                                           '"argv":["exec"],"system_flag":"--x"}}}')
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("codex", "m", "S", "U", "/repo")
        argv = fake.calls[0][0]
        self.assertEqual(argv[-2:], ["-s", "read-only"]); self.assertIn("--ephemeral", argv)
        self.assertNotIn("--x", argv)

    def test_bin_env_and_extra_args_and_timeout_from_config(self):
        os.environ["CORK_CODEX_BIN"] = "/opt/codex"
        orchestrate.CONFIG_PATH.write_text('{"rotation":[{"provider":"codex","model":"m"}],'
                                           '"providers":{"codex":{"enabled":true,'
                                           '"extra_args":["--foo"],"timeout":30}}}')
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("codex", "m", "S", "U", "/repo")
        argv, kw = fake.calls[0]
        self.assertEqual(argv[0], "/opt/codex")
        self.assertEqual(argv[-3:], ["--foo", "-s", "read-only"])  # extra_args before read-only
        self.assertEqual(kw["timeout"], 30)

    def test_explicit_timeout_overrides_config(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("codex", "m", "S", "U", "/repo", timeout=7)
        self.assertEqual(fake.calls[0][1]["timeout"], 7)

    def test_empty_system_still_passes_system_flag(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("claude", "m", "", "U", "/repo")
        self.assertIn("--system-prompt", fake.calls[0][0])

    def test_empty_repo_fails_before_running(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        with self.assertRaises(SystemExit):
            orchestrate._harness_call("codex", "m", "S", "U", "")
        self.assertEqual(fake.calls, [])

    def test_prompt_via_arg_path(self):
        orchestrate.HARNESSES["argtool"] = {**orchestrate.HARNESSES["codex"], "prompt_via": "arg"}
        try:
            fake = _FakeRun(); orchestrate.subprocess.run = fake
            orchestrate._harness_call("argtool", "m", "S", "U", "/repo")
            argv, kw = fake.calls[0]
            self.assertTrue(argv[-1].endswith("U"))
            self.assertNotIn("input", kw)
            self.assertIs(kw["stdin"], subprocess.DEVNULL)
        finally:
            del orchestrate.HARNESSES["argtool"]


class FailurePathTest(HarnessBase):
    def _review(self, provider="codex"):
        return orchestrate.review(provider, "m", "STANDARDS", "story", "diff", {"a.py": "x"},
                                  repo="/repo")

    def test_nonzero_exit_is_skip_sentinel(self):
        orchestrate.subprocess.run = _FakeRun(rc=2, out="", err="boom")
        self.assertEqual(self._review(), "[codex/m returned no usable content — skipped]")

    def test_nonzero_exit_status_and_stderr(self):
        orchestrate.subprocess.run = _FakeRun(rc=2, out="", err="boom")
        status, text = orchestrate._harness_call("codex", "m", "S", "U", "/repo")
        self.assertNotEqual(status, 200); self.assertIn("boom", text)

    def test_timeout_is_skip_sentinel(self):
        orchestrate.subprocess.run = _FakeRun(raise_=subprocess.TimeoutExpired("codex", 900))
        self.assertEqual(self._review(), "[codex/m returned no usable content — skipped]")

    def test_missing_binary_is_skip_sentinel(self):
        orchestrate.subprocess.run = _FakeRun(raise_=FileNotFoundError("codex"))
        self.assertEqual(self._review("claude"), "[claude/m returned no usable content — skipped]")

    def test_empty_stdout_is_skip_not_retry(self):
        fake = _FakeRun(rc=0, out="   "); orchestrate.subprocess.run = fake
        self.assertEqual(self._review(), "[codex/m returned no usable content — skipped]")
        self.assertEqual(len(fake.calls), 1)  # harnesses get exactly one attempt

    def test_success_returns_stdout_once(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        self.assertEqual(self._review(), fake.out)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0][1]["cwd"], "/repo")


class ApiRoutingUnaffectedTest(HarnessBase):
    # The false arm of every new `provider in HARNESSES` gate: API providers must
    # never touch subprocess.run or shutil.which.
    def setUp(self):
        super().setUp()
        orchestrate.subprocess.run = lambda *a, **k: self.fail("harness path taken")
        orchestrate.shutil.which = lambda b: self.fail("harness path taken")
        self._calls = (orchestrate._openai_compatible_call, orchestrate._anthropic_call,
                       orchestrate._call_and_extract, orchestrate._provider_token_available)

    def tearDown(self):
        (orchestrate._openai_compatible_call, orchestrate._anthropic_call,
         orchestrate._call_and_extract, orchestrate._provider_token_available) = self._calls
        super().tearDown()

    def test_call_and_extract_copilot_and_anthropic(self):
        orchestrate._openai_compatible_call = lambda *a, **k: (
            200, {"choices": [{"message": {"content": "chat-ok"}}]})
        orchestrate._anthropic_call = lambda *a, **k: (
            200, {"content": [{"type": "text", "text": "anth-ok"}]})
        self.assertEqual(orchestrate._call_and_extract("copilot", "gpt-4.1", "S", "U"),
                         (200, "chat-ok"))
        self.assertEqual(orchestrate._call_and_extract("anthropic", "claude-x", "S", "U"),
                         (200, "anth-ok"))

    def test_probe_api_provider_uses_http_probe(self):
        seen = []
        orchestrate._call_and_extract = lambda p, m, s, u, max_out=None, repo="": (
            seen.append((p, m, max_out)) or (200, "ok"))
        self.assertEqual(orchestrate._probe("copilot", "gpt-4.1"), "ok")
        self.assertEqual(seen, [("copilot", "gpt-4.1", 16)])

    def test_eligible_rotation_api_missing_token_wording(self):
        import io
        from contextlib import redirect_stdout
        orchestrate._provider_token_available = lambda p: False
        cfg = {"providers": {}, "rotation": [{"provider": "copilot", "model": "m"}]}
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(orchestrate._eligible_rotation(cfg), [])
        self.assertIn("no copilot token", buf.getvalue())
        self.assertNotIn("binary", buf.getvalue())


class ConfigAndProbeTest(HarnessBase):
    def test_validate_accepts_harness_and_rejects_unknown(self):
        orchestrate._validate_config({"rotation": [{"provider": "claude", "model": "x"},
                                                   {"provider": "codex", "model": "y"}]})
        with self.assertRaises(SystemExit):
            orchestrate._validate_config({"rotation": [{"provider": "opencode", "model": "x"}]})

    def test_validate_harness_keys_types(self):
        base = {"rotation": [{"provider": "codex", "model": "m"}]}
        orchestrate._validate_config({**base, "providers": {"codex": {
            "enabled": True, "bin": "/opt/codex", "extra_args": ["--a"], "timeout": 60.5}}})
        for bad in ({"timeout": None}, {"timeout": 0}, {"timeout": "30"}, {"timeout": True},
                    {"timeout": float("inf")}, {"timeout": float("nan")},
                    {"extra_args": "--a"}, {"extra_args": [1]}, {"bin": ""}, {"bin": 3}, "str"):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                orchestrate._validate_config({**base, "providers": {"codex": bad}})

    def test_default_config_has_harnesses_disabled(self):
        for h in orchestrate.HARNESSES:
            self.assertFalse(orchestrate.DEFAULT_CONFIG["providers"][h]["enabled"])

    def test_harness_absent_from_providers_is_not_eligible(self):
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b
        cfg = {"providers": {}, "rotation": [{"provider": "codex", "model": "m"},
                                             {"provider": "claude", "model": "m"}]}
        self.assertEqual(orchestrate._eligible_rotation(cfg), [])
        cfg["providers"] = {"codex": {"enabled": True}}
        self.assertEqual(orchestrate._eligible_rotation(cfg), [{"provider": "codex", "model": "m"}])

    def test_table_only_lane_is_data_only(self):
        # A future lane added ONLY to HARNESSES must validate and default to disabled.
        orchestrate.HARNESSES["opencode"] = {**orchestrate.HARNESSES["codex"], "bin": "opencode",
                                             "bin_env": "CORK_OPENCODE_BIN"}
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b
        try:
            cfg = {"providers": {}, "rotation": [{"provider": "opencode", "model": "m"}]}
            orchestrate._validate_config(cfg)
            self.assertEqual(orchestrate._eligible_rotation(cfg), [])
            self.assertTrue(orchestrate._provider_token_available("opencode"))
        finally:
            del orchestrate.HARNESSES["opencode"]

    def test_token_available_and_probe_follow_which(self):
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b
        self.assertTrue(orchestrate._provider_token_available("codex"))
        self.assertEqual(orchestrate._probe("claude", "m"), "ok")
        orchestrate.shutil.which = lambda b: None
        self.assertFalse(orchestrate._provider_token_available("codex"))
        self.assertEqual(orchestrate._probe("claude", "m"), "no binary")

    def test_probe_never_runs_binary_or_http(self):
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b
        orchestrate.subprocess.run = _FakeRun(raise_=AssertionError("must not run"))
        orig = orchestrate._http_post_json
        orchestrate._http_post_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP"))
        try:
            self.assertEqual(orchestrate._probe("codex", "m"), "ok")
        finally:
            orchestrate._http_post_json = orig

    def test_eligible_rotation_skip_line_names_binary(self):
        import io
        from contextlib import redirect_stdout
        orchestrate.shutil.which = lambda b: None
        cfg = {"providers": {"codex": {"enabled": True}},
               "rotation": [{"provider": "codex", "model": "m"}]}
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(orchestrate._eligible_rotation(cfg), [])
        self.assertIn("binary not on PATH", buf.getvalue())

    def test_preflight_selects_harness_without_http(self):
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b
        orig = orchestrate._http_post_json
        orchestrate._http_post_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP"))
        try:
            sel = orchestrate.preflight([{"provider": "codex", "model": "gpt-5.6-sol"},
                                         {"provider": "claude", "model": "claude-opus-4.7"}], 2)
        finally:
            orchestrate._http_post_json = orig
        self.assertEqual([orchestrate._model_key(s) for s in sel],
                         ["codex/gpt-5.6-sol", "claude/claude-opus-4.7"])


class SplitRefTest(unittest.TestCase):
    def test_harness_refs(self):
        self.assertEqual(orchestrate._split_model_ref("claude/x"), ("claude", "x"))
        self.assertEqual(orchestrate._split_model_ref("codex/y"), ("codex", "y"))
        self.assertEqual(orchestrate._split_model_ref("pi/glm-internal/glm-5.3-onprem"),
                         ("pi", "glm-internal/glm-5.3-onprem"))
        self.assertEqual(orchestrate._split_model_ref("gpt-4.1"), ("copilot", "gpt-4.1"))
