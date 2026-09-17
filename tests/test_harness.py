import inspect, io, json, os, subprocess, tempfile, unittest
from contextlib import redirect_stdout
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
                     for k in ("ANTHROPIC_API_KEY", "CORK_CLAUDE_BIN", "CORK_CODEX_BIN",
                               "CORK_OPENCODE_BIN", "CORK_PI_BIN", "OPENCODE_PERMISSION",
                               "OPENCODE_DISABLE_PROJECT_CONFIG")}

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

    def test_opencode_argv_read_only_prompt_arg_cwd(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("opencode", "github-copilot/gpt-5.5", "SYS", "USER", "/repo")
        argv, kw = fake.calls[0]
        self.assertEqual(argv, ["opencode", "run", "-m", "github-copilot/gpt-5.5",
                                "--agent", "plan", "--format", "default", "--dir", "/repo",
                                "--pure", "--", "SYS\n\n=== END OF REVIEW STANDARDS — REVIEW TASK "
                                "FOLLOWS ===\n\nUSER"])
        self.assertEqual(kw["cwd"], "/repo")
        self.assertNotIn("input", kw)
        self.assertIs(kw["stdin"], subprocess.DEVNULL)

    def test_pi_argv_read_only_prompt_arg_devnull(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("pi", "glm-internal/glm-5.3-onprem", "SYS", "USER", "/repo")
        argv, kw = fake.calls[0]
        self.assertEqual(argv, ["pi", "-p", "--model", "glm-internal/glm-5.3-onprem",
                                "--system-prompt", "SYS", "--tools", "read,grep,find,ls",
                                "--no-session", "--no-context-files", "--no-approve", "--", "USER"])
        self.assertEqual(kw["cwd"], "/repo")
        self.assertNotIn("input", kw)
        self.assertIs(kw["stdin"], subprocess.DEVNULL)

    def test_config_cannot_override_read_only_or_argv(self):
        orchestrate.CONFIG_PATH.write_text('{"rotation":[{"provider":"codex","model":"m"}],'
                                           '"providers":{"codex":{"enabled":true,"read_only":[],'
                                           '"argv":["exec"],"system_flag":"--x"}}}')
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("codex", "m", "S", "U", "/repo")
        argv = fake.calls[0][0]
        self.assertEqual(argv[-2:], ["-s", "read-only"]); self.assertIn("--ephemeral", argv)
        self.assertNotIn("--x", argv)

    def test_new_lane_read_only_flags_follow_extra_args(self):
        for lane, marker in (("opencode", "--agent"), ("pi", "--tools")):
            with self.subTest(lane=lane):
                orchestrate.CONFIG_PATH.write_text(
                    '{"rotation":[{"provider":"' + lane + '","model":"p/m"}],'
                    '"providers":{"' + lane + '":{"enabled":true,"extra_args":["--unsafe"]}}}'
                )
                fake = _FakeRun(); orchestrate.subprocess.run = fake
                orchestrate._harness_call(lane, "p/m", "S", "U", "/repo")
                argv = fake.calls[0][0]
                self.assertLess(argv.index("--unsafe"), argv.index(marker))

    def test_opencode_immutable_environment_overrides_process_and_config(self):
        os.environ["OPENCODE_PERMISSION"] = '{"bash":"allow"}'
        os.environ["OPENCODE_DISABLE_PROJECT_CONFIG"] = "0"
        orchestrate.CONFIG_PATH.write_text(
            '{"rotation":[{"provider":"opencode","model":"p/m"}],'
            '"providers":{"opencode":{"enabled":true,"env":{'
            '"OPENCODE_PERMISSION":"allow","OPENCODE_DISABLE_PROJECT_CONFIG":"0"}}}}'
        )
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("opencode", "p/m", "S", "U", "/repo")
        env = fake.calls[0][1]["env"]
        self.assertEqual(env["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")
        denies = json.loads(env["OPENCODE_PERMISSION"])
        self.assertEqual(set(denies), {"bash", "edit", "write", "patch", "task",
                                      "webfetch", "external_directory"})
        self.assertEqual(set(denies.values()), {"deny"})

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
                                                   {"provider": "codex", "model": "y"},
                                                   {"provider": "opencode", "model": "p/z"},
                                                   {"provider": "pi", "model": "p/q"}]})
        with self.assertRaises(SystemExit):
            orchestrate._validate_config({"rotation": [{"provider": "unknown", "model": "x"}]})

    def test_provider_model_harnesses_reject_slashless_model(self):
        for lane in ("opencode", "pi"):
            with self.subTest(lane=lane), self.assertRaises(SystemExit):
                orchestrate._validate_config({"rotation": [{"provider": lane, "model": "model"}]})

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
        orchestrate.HARNESSES["future"] = {**orchestrate.HARNESSES["codex"], "bin": "future",
                                           "bin_env": "CORK_FUTURE_BIN"}
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b
        try:
            cfg = {"providers": {}, "rotation": [{"provider": "future", "model": "m"}]}
            orchestrate._validate_config(cfg)
            self.assertEqual(orchestrate._eligible_rotation(cfg), [])
            self.assertTrue(orchestrate._provider_token_available("future"))
        finally:
            del orchestrate.HARNESSES["future"]

    def test_token_available_follows_which(self):
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b
        self.assertTrue(orchestrate._provider_token_available("codex"))
        orchestrate.shutil.which = lambda b: None
        self.assertFalse(orchestrate._provider_token_available("codex"))

    def test_eligible_rotation_missing_harness_says_not_installed(self):
        import io
        from contextlib import redirect_stdout
        orchestrate.shutil.which = lambda b: None
        cfg = {"providers": {"codex": {"enabled": True}},
               "rotation": [{"provider": "codex", "model": "m"}]}
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(orchestrate._eligible_rotation(cfg), [])
        self.assertIn("codex: not installed", buf.getvalue())

    def test_preflight_selects_harness_without_http(self):
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b
        orchestrate.subprocess.run = _FakeRun(out="Logged in using ChatGPT")
        orig = orchestrate._http_post_json
        orchestrate._http_post_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP"))
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                sel = orchestrate.preflight([{"provider": "codex", "model": "gpt-5.6-sol"},
                                             {"provider": "claude", "model": "claude-opus-4.7"}], 2)
        finally:
            orchestrate._http_post_json = orig
        self.assertEqual([orchestrate._model_key(s) for s in sel],
                         ["codex/gpt-5.6-sol", "claude/claude-opus-4.7"])
        self.assertIn("codex: live (ChatGPT)", buf.getvalue())


class AuthProbeTest(HarnessBase):
    def setUp(self):
        super().setUp()
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b

    def test_probe_argv_per_lane_and_pi_provider_extraction(self):
        cases = {
            "claude": ("x", ["claude", "auth", "status", "--text"],
                       "Login method: OAuth", "OAuth"),
            "codex": ("x", ["codex", "login", "status"], "", "ChatGPT"),
            "opencode": ("github-copilot/gpt", ["opencode", "auth", "list"],
                         "GitHub Copilot oauth\n1 credential", "github-copilot"),
            "pi": ("glm-internal/glm-5.3/onprem",
                   ["pi", "auth", "check", "--provider", "glm-internal", "--json",
                    "--no-refresh"], '{"status":"ready","provider":"glm-internal"}',
                   "glm-internal"),
        }
        for lane, (model, argv, output, detail) in cases.items():
            with self.subTest(lane=lane):
                fake = _FakeRun(out=output, err="Logged in using ChatGPT" if lane == "codex" else "")
                orchestrate.subprocess.run = fake
                details = {}
                self.assertEqual(orchestrate._probe(lane, model, details), "ok")
                self.assertEqual(details["status"], "ok")
                self.assertEqual(details["detail"], detail)
                self.assertEqual(fake.calls, [(argv, {"timeout": 10, "stdin": subprocess.DEVNULL,
                                                      "capture_output": True, "text": True})])

    def test_outcome_mapping(self):
        for fake, expected in ((_FakeRun(rc=1), "not_logged_in"),
                               (_FakeRun(rc=2), "error"),
                               (_FakeRun(raise_=subprocess.TimeoutExpired("codex", 10)), "timeout"),
                               (_FakeRun(raise_=FileNotFoundError("codex")), "missing_binary"),
                               (_FakeRun(raise_=PermissionError("codex")), "error")):
            with self.subTest(expected=expected):
                orchestrate.subprocess.run = fake
                self.assertEqual(orchestrate._probe("codex", "m"), expected)
        orchestrate.shutil.which = lambda b: None
        self.assertEqual(orchestrate._probe("codex", "m"), "missing_binary")

    def test_opencode_credential_count(self):
        for output, expected in (
            ("GitHub Copilot oauth\n1 credential", "ok"),
            ("0 credentials", "not_logged_in"),
            ("2 credentials", "ok"),
        ):
            with self.subTest(output=output):
                orchestrate.subprocess.run = _FakeRun(out=output)
                self.assertEqual(orchestrate._probe("opencode", "provider/model"), expected)
        orchestrate.subprocess.run = _FakeRun(rc=1, out="0 credentials")
        self.assertEqual(orchestrate._probe("opencode", "anthropic/claude"), "error")

    def test_ansi_auth_output_is_stripped(self):
        completed = subprocess.CompletedProcess(
            ["opencode", "auth", "list"], 0,
            stdout="\x1b[0mGitHub Copilot \x1b[90moauth\x1b[0m\n2 credentials", stderr="",
        )
        self.assertEqual(orchestrate._plain_auth_output(completed),
                         "GitHub Copilot oauth\n2 credentials\n")

    def test_pi_provider_not_found_is_error_with_reason(self):
        orchestrate.subprocess.run = _FakeRun(
            rc=1, out='{"status":"not_ready","provider":"bad","reason":"provider_not_found"}')
        details = {}
        self.assertEqual(orchestrate._probe("pi", "bad/model", details), "error")
        self.assertEqual(details["detail"], "provider_not_found")

    def test_each_lane_logged_out_and_not_installed(self):
        for lane in orchestrate.HARNESSES:
            with self.subTest(lane=lane, state="logged_out"):
                rc, output = {
                    "opencode": (0, "0 credentials"),
                    "pi": (1, '{"status":"not_ready","reason":"missing_credentials"}'),
                }.get(lane, (1, ""))
                orchestrate.subprocess.run = _FakeRun(rc=rc, out=output)
                self.assertEqual(orchestrate._probe(lane, "provider/model"), "not_logged_in")
            with self.subTest(lane=lane, state="not_installed"):
                orchestrate.shutil.which = lambda b: None
                self.assertEqual(orchestrate._probe(lane, "provider/model"), "missing_binary")
                orchestrate.shutil.which = lambda b: "/usr/bin/" + b

    def test_claude_env_key_is_presence_only(self):
        os.environ["ANTHROPIC_API_KEY"] = ""
        orchestrate.subprocess.run = _FakeRun(rc=1)
        details = {}
        self.assertEqual(orchestrate._probe("claude", "m", details), "not_logged_in")
        self.assertIs(details["env_key"], True)
        self.assertEqual(details["env_flag"], "ANTHROPIC_API_KEY")

    def test_preflight_logged_out_prints_login_and_never_calls_http(self):
        orchestrate.subprocess.run = _FakeRun(rc=1)
        original = orchestrate._http_post_json
        orchestrate._http_post_json = lambda *a, **k: self.fail("HTTP probe called")
        try:
            buf = io.StringIO()
            with redirect_stdout(buf), self.assertRaises(SystemExit):
                orchestrate.preflight([{"provider": "codex", "model": "m"}], 1)
        finally:
            orchestrate._http_post_json = original
        self.assertIn("codex: logged-out — run codex login", buf.getvalue())

    def test_preflight_claude_timeout_notes_env_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "invalid"
        orchestrate.subprocess.run = _FakeRun(raise_=subprocess.TimeoutExpired("claude", 10))
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            orchestrate.preflight([{"provider": "claude", "model": "m"}], 1)
        self.assertIn("claude: unavailable (timeout; ANTHROPIC_API_KEY set)", buf.getvalue())

    def test_preflight_reports_harness_after_selection_count_is_full(self):
        original = orchestrate._call_and_extract
        orchestrate._call_and_extract = lambda *a, **k: (200, "ok")
        orchestrate.subprocess.run = _FakeRun(out="Logged in using ChatGPT")
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                selected = orchestrate.preflight([
                    {"provider": "copilot", "model": "gpt"},
                    {"provider": "codex", "model": "review"},
                ], 1)
        finally:
            orchestrate._call_and_extract = original
        self.assertEqual(selected, [{"provider": "copilot", "model": "gpt"}])
        self.assertIn("codex: live (ChatGPT) (not selected — count reached)", buf.getvalue())

    def test_eligible_rotation_silences_disabled_harness(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(orchestrate._eligible_rotation({
                "providers": {"pi": {"enabled": False}},
                "rotation": [{"provider": "pi", "model": "p/m"}],
            }), [])
        self.assertEqual(buf.getvalue(), "")

    def test_lane_names_are_not_enumerated_in_generic_code_paths(self):
        generic = "\n".join(inspect.getsource(fn) for fn in (
            orchestrate._harness_call, orchestrate._harness_auth_probe,
            orchestrate._probe, orchestrate._eligible_rotation,
            orchestrate._provider_token_available, orchestrate.preflight,
        ))
        for lane in orchestrate.HARNESSES:
            self.assertNotIn(f'provider == "{lane}"', generic)
        preflight_source = inspect.getsource(orchestrate.preflight)
        for spec in orchestrate.HARNESSES.values():
            env_names = [*spec.get("env", {}), spec["auth_probe"].get("env_flag")]
            for env_name in filter(None, env_names):
                self.assertNotIn(env_name, preflight_source)


class SplitRefTest(unittest.TestCase):
    def test_harness_refs(self):
        self.assertEqual(orchestrate._split_model_ref("claude/x"), ("claude", "x"))
        self.assertEqual(orchestrate._split_model_ref("codex/y"), ("codex", "y"))
        self.assertEqual(orchestrate._split_model_ref("pi/glm-internal/glm-5.3-onprem"),
                         ("pi", "glm-internal/glm-5.3-onprem"))
        self.assertEqual(orchestrate._split_model_ref("gpt-4.1"), ("copilot", "gpt-4.1"))
