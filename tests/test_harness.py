import inspect, io, json, os, shutil, subprocess, tempfile, unittest
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
        # never let a test create scratch dirs in the real ~/.local/share/code-orchestrator
        self.addCleanup(setattr, orchestrate, "STATE_DIR", orchestrate.STATE_DIR)
        orchestrate.STATE_DIR = Path(self.tmp.name) / "state"
        # ...nor depend on what the developer's real ~/.opencode holds (legacy-dir refusal)
        real_home = os.environ.get("HOME")
        self.addCleanup(lambda: os.environ.update(HOME=real_home) if real_home is not None else os.environ.pop("HOME", None))
        os.environ["HOME"] = str(Path(self.tmp.name) / "home"); (Path(self.tmp.name) / "home").mkdir()
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
        # The protected flag set is spelled out literally: reading it from HARNESSES would
        # let a removed flag update both sides of the assertion and pass.
        self.assertEqual(argv, ["codex", "exec", "-m", "gpt-5.6-sol", "--ephemeral",
                                "--skip-git-repo-check", "-C", "/repo", "--color", "never", "-",
                                "-s", "read-only", "--ignore-user-config",
                                "--disable", "shell_tool", "--disable", "unified_exec",
                                "--disable", "code_mode_host", "--disable", "apps"])  # read-only flags last
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

    def test_claude_high_effort_preserves_read_only_flags(self):
        orchestrate.CONFIG_PATH.write_text('{"rotation":[{"provider":"claude","model":"opus"}],'
                                           '"providers":{"claude":{"enabled":true,'
                                           '"extra_args":["--effort","high"]}}}')
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("claude", "opus", "SYS", "USER", "/repo")
        argv = fake.calls[0][0]
        ro = orchestrate.HARNESSES["claude"]["read_only"]
        self.assertEqual(argv[-len(ro) - 2:], ["--effort", "high", *ro])

    def test_pi_argv_uses_existing_login_high_effort_and_prompt_only(self) -> None:
        orchestrate.CONFIG_PATH.write_text('{"rotation":[{"provider":"pi","model":"openai-codex/gpt-6-sol"}],'
                                           '"providers":{"pi":{"enabled":true,"extra_args":["--thinking","high"]}}}')
        os.environ["CORK_PI_BIN"] = "/opt/pi"
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        result = orchestrate._call_and_extract("pi", "openai-codex/gpt-6-sol", "SYS", "USER", repo="/repo")
        argv, kw = fake.calls[0]
        self.assertEqual(argv, ["/opt/pi", "--print", "--model", "openai-codex/gpt-6-sol",
                                "--system-prompt", "SYS", "--thinking", "high", "--no-tools", "--no-extensions",
                                "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files",
                                "--no-approve", "--no-session", "--append-system-prompt", ""])
        self.assertEqual((kw["cwd"], kw["input"], kw["timeout"]), ("/repo", "USER", 900))
        self.assertEqual(result, (200, fake.out, None))

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

    def test_pi_argv_read_only_prompt_on_stdin(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("pi", "glm-internal/glm-5.3-onprem", "SYS", "USER", "/repo")
        argv, kw = fake.calls[0]
        # Spelled out literally (not read from HARNESSES): pi must have NO tools — its
        # read/find accept absolute paths — and no ambient resources. The prompt travels on
        # stdin, so no `--` terminator and no prompt argv element.
        self.assertEqual(argv, ["pi", "--print", "--model", "glm-internal/glm-5.3-onprem",
                                "--system-prompt", "SYS",
                                "--no-tools", "--no-extensions", "--no-skills", "--no-prompt-templates",
                                "--no-themes", "--no-context-files", "--no-approve", "--no-session",
                                "--append-system-prompt", ""])
        self.assertNotIn("--tools", argv); self.assertNotIn("--", argv)
        self.assertEqual((kw["cwd"], kw["input"]), ("/repo", "USER"))
        self.assertNotIn("stdin", kw)

    def _isolated_state_dir(self):
        return orchestrate.STATE_DIR  # patched per test in HarnessBase.setUp

    def test_opencode_auth_probe_is_hardened_like_the_review_call(self):
        # Preflight runs before _harness_call's refusal check, so the probe must not run from
        # the repo (project plugins) nor with the parent's config variables.
        state = self._isolated_state_dir()
        os.environ["OPENCODE_CONFIG"] = "inherited"; self.addCleanup(os.environ.pop, "OPENCODE_CONFIG", None)
        fake = _FakeRun(out="●  GitHub Copilot oauth\n└  1 credential\n"); orchestrate.subprocess.run = fake
        orchestrate._probe("opencode", "github-copilot/gpt-5")
        kw = fake.calls[0][1]
        self.assertEqual(kw["cwd"], str(state))
        scratch = Path(kw["env"]["XDG_CONFIG_HOME"])
        self.assertEqual(scratch.parent, state)                      # cork-owned, never the repo
        self.assertEqual(kw["env"]["OPENCODE_DB"], str(scratch / "opencode.db"))
        self.assertNotIn("OPENCODE_CONFIG", kw["env"])
        self.assertEqual((kw["encoding"], kw["errors"]), ("utf-8", "replace"))

    def test_opencode_refuses_legacy_home_dir_with_anything_beyond_the_install(self):
        # $HOME/.opencode is loaded in full regardless of XDG_CONFIG_HOME (verified on 1.17.3
        # with a repo outside HOME: config MCP started, tool/*.ts registered, agent/plan.md
        # replaced the agent); the upward scan misses it there. Only OpenCode's own install
        # artifacts are tolerated — the binary itself lives in ~/.opencode/bin by default.
        home = Path(self.tmp.name) / "home"; legacy = home / ".opencode"; legacy.mkdir(parents=True)
        os.environ["HOME"] = str(home); self.addCleanup(os.environ.pop, "HOME", None)
        repo = Path(self.tmp.name) / "elsewhere" / "repo"; repo.mkdir(parents=True); (repo / ".git").mkdir()
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        for name in ("bin", "node_modules"):
            (legacy / name).mkdir()
        for name in ("package.json", "package-lock.json", "bun.lock", ".gitignore"):
            (legacy / name).write_text("x")
        self.assertEqual(orchestrate._harness_call("opencode", "p/m", "S", "U", str(repo))[0], 200)  # default install
        for rel in ("opencode.json", "opencode.jsonc", "plugin", "plugins", "tool/probe.ts",
                    "agent/plan.md", "agents/plan.md", "command/x.md", "skill/x/SKILL.md", "whatever"):
            with self.subTest(rel=rel):
                hit = legacy / rel; hit.parent.mkdir(parents=True, exist_ok=True)
                hit.mkdir() if rel in ("plugin", "plugins") else hit.write_text("{}")
                status, body = orchestrate._harness_call("opencode", "p/m", "S", "U", str(repo))
                self.assertEqual(status, 403)
                self.assertIn(str(legacy), body); self.assertIn(rel.split("/")[0], body)   # names the stray entry
                self.assertIn("~/.config/opencode/", body)
                details = {}
                self.assertEqual(orchestrate._probe("opencode", "gh/m", details), "error")  # probe refuses too
                self.assertIn(str(legacy), details["detail"])
                shutil.rmtree(legacy / rel.split("/")[0]) if (legacy / rel.split("/")[0]).is_dir() else (legacy / rel).unlink()
        self.assertEqual(len(fake.calls), 1)                          # the CLI was never launched while refused
        (legacy / "opencode.json").write_text("{}")
        for lane in ("codex", "claude", "pi"):                        # opencode-specific
            self.assertEqual(orchestrate._harness_call(lane, "p/m", "S", "U", str(repo))[0], 200)
        shutil.rmtree(legacy)
        self.assertEqual(orchestrate._harness_call("opencode", "p/m", "S", "U", str(repo))[0], 200)  # no legacy dir at all

    def test_probe_keeps_the_cause_when_scratch_setup_fails_after_state_dir_exists(self):
        # STATE_DIR exists (mkdir exist_ok passes) but mkdtemp inside it fails: the probe must
        # still say why, not just `unavailable (error)`.
        state = self._isolated_state_dir(); state.mkdir(parents=True)
        orig = orchestrate.tempfile.TemporaryDirectory
        def boom(**kw): raise PermissionError(13, "Permission denied", str(state))
        orchestrate.tempfile.TemporaryDirectory = boom
        self.addCleanup(setattr, orchestrate.tempfile, "TemporaryDirectory", orig)
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        details = {}
        self.assertEqual(orchestrate._probe("opencode", "gh/m", details), "error")
        self.assertIn("Permission denied", details["detail"]); self.assertIn(str(state), details["detail"])
        self.assertEqual(fake.calls, [])

    def test_unwritable_state_dir_makes_the_probe_error_not_crash(self):
        blocker = Path(self.tmp.name) / "blocker"; blocker.write_text("not a dir")
        orchestrate.STATE_DIR = blocker / "state"
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        details = {}
        self.assertEqual(orchestrate._probe("opencode", "gh/m", details), "error")
        self.assertIn(str(orchestrate.STATE_DIR), details["detail"])
        self.assertEqual(fake.calls, [])

    def test_opencode_scratch_is_fresh_per_run_and_removed(self):
        # opencode 1.17.3 scaffolds $XDG_CONFIG_HOME/opencode/opencode.jsonc on every start
        # (verified live), so a persistent "must stay empty" dir failed the second run.
        # Each run gets a fresh dir; what the CLI leaves there is deleted with it.
        state = self._isolated_state_dir()
        seen = []
        def scaffolding_run(argv, **kw):
            home = Path(kw["env"]["XDG_CONFIG_HOME"])
            self.assertEqual(list(home.iterdir()), [])              # fresh and empty at launch
            (home / "opencode").mkdir(); (home / "opencode" / "opencode.jsonc").write_text("{}")
            Path(kw["env"]["OPENCODE_DB"]).write_text("session rows")
            seen.append(home)
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")
        orchestrate.subprocess.run = scaffolding_run
        for _ in range(2):
            self.assertEqual(orchestrate._harness_call("opencode", "p/m", "S", "U", "/repo"), (200, "ok"))
        self.assertEqual(len(set(seen)), 2)                          # never reused
        for home in seen:
            self.assertFalse(home.exists())                          # removed after the run
        self.assertEqual([p for p in state.iterdir()], [])           # nothing accumulates

    def test_opencode_never_shares_or_self_updates(self):
        # OPENCODE_AUTO_SHARE is a runtime flag read independently of config (1.17.3); an
        # inherited one would upload the review prompt. Cleared, plus belt-and-braces:
        # share disabled in the enforced config and via OPENCODE_DISABLE_SHARE.
        os.environ["OPENCODE_AUTO_SHARE"] = "1"; self.addCleanup(os.environ.pop, "OPENCODE_AUTO_SHARE", None)
        os.environ["OPENCODE_DISABLE_SHARE"] = "0"; self.addCleanup(os.environ.pop, "OPENCODE_DISABLE_SHARE", None)
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("opencode", "p/m", "S", "U", "/repo")
        env = fake.calls[0][1]["env"]
        self.assertNotIn("OPENCODE_AUTO_SHARE", env)
        self.assertEqual(env["OPENCODE_DISABLE_SHARE"], "1")
        self.assertEqual(json.loads(env["OPENCODE_CONFIG_CONTENT"])["share"], "disabled")
        self.assertEqual(env["OPENCODE_DISABLE_AUTOUPDATE"], "1")

    def test_opencode_session_state_stays_out_of_the_user_store(self):
        # Verified live on 1.17.3: OPENCODE_DB redirects the session database, and
        # `snapshot: false` (via OPENCODE_CONFIG_CONTENT) stops repo snapshots being written
        # under ~/.local/share/opencode; both are table-owned and replace inherited values.
        self._isolated_state_dir()
        os.environ["OPENCODE_CONFIG_CONTENT"] = '{"mcp":{"evil":{}}}'
        self.addCleanup(os.environ.pop, "OPENCODE_CONFIG_CONTENT", None)
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("opencode", "p/m", "S", "U", "/repo")
        env = fake.calls[0][1]["env"]
        self.assertIs(json.loads(env["OPENCODE_CONFIG_CONTENT"])["snapshot"], False)
        self.assertEqual(Path(env["OPENCODE_DB"]).parent, Path(env["XDG_CONFIG_HOME"]))
        self.assertNotIn("{scratch}", env["OPENCODE_DB"])

    def test_opencode_runs_with_isolated_global_config(self):
        self._isolated_state_dir()
        # ~/.config/opencode/opencode.json (MCP servers, plugins, agents) must not load;
        # login (XDG_DATA_HOME) and the models cache (XDG_CACHE_HOME) are untouched.
        os.environ["XDG_CONFIG_HOME"] = "/home/someone/.config"  # inherited value must be overridden
        self.addCleanup(os.environ.pop, "XDG_CONFIG_HOME", None)
        at_launch = {}
        def recording_run(argv, **kw):
            home = Path(kw["env"]["XDG_CONFIG_HOME"])
            at_launch.update(env=kw["env"], is_dir=home.is_dir(), contents=list(home.iterdir()))
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")
        orchestrate.subprocess.run = recording_run
        orchestrate._harness_call("opencode", "p/m", "S", "U", "/repo")
        env = at_launch["env"]
        self.assertNotEqual(env["XDG_CONFIG_HOME"], "/home/someone/.config")
        self.assertTrue(at_launch["is_dir"]); self.assertEqual(at_launch["contents"], [])  # exists and is empty
        self.assertNotIn("{scratch}", env["XDG_CONFIG_HOME"])
        self.assertIn('"bash":"deny"', env["OPENCODE_PERMISSION"])  # JSON braces survived substitution
        # only CONFIG is redirected: data (login) and cache (models) dirs are inherited untouched
        for k in ("XDG_DATA_HOME", "XDG_CACHE_HOME"):
            self.assertEqual(env.get(k), os.environ.get(k))

    def test_opencode_refuses_repo_that_ships_plugins(self):
        # anomalyco/opencode#49836: .opencode/plugins/*.js runs despite --pure and the
        # project-config switch. Branch-controlled code must never execute — skip the lane.
        repo = Path(self.tmp.name) / "repo"; (repo / ".opencode" / "plugins").mkdir(parents=True)
        (repo / ".opencode" / "plugins" / "evil.js").write_text("process.exit(0)")
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        status, text = orchestrate._harness_call("opencode", "p/m", "S", "U", str(repo))
        self.assertEqual((status, fake.calls), (403, []))  # refused before any exec
        self.assertIn(".opencode/plugins", text); self.assertIn("49836", text)
        self.assertEqual(orchestrate.review("opencode", "p/m", "S", "story", "diff", {}, repo=str(repo)),
                         "[opencode/p/m returned no usable content — skipped]")
        clean = Path(self.tmp.name) / "clean"; clean.mkdir()
        status, _ = orchestrate._harness_call("opencode", "p/m", "S", "U", str(clean))
        self.assertEqual((status, len(fake.calls)), (200, 1))  # a repo without plugins runs
        # the loader scans both spellings: the singular directory is refused too
        single = Path(self.tmp.name) / "single"; (single / ".opencode" / "plugin").mkdir(parents=True)
        (single / ".opencode" / "plugin" / "evil.ts").write_text("")
        status, text = orchestrate._harness_call("opencode", "p/m", "S", "U", str(single))
        self.assertEqual((status, len(fake.calls)), (403, 1)); self.assertIn(".opencode/plugin", text)

    def test_opencode_refusal_scans_up_to_the_worktree_root(self):
        # OpenCode discovers .opencode upward from its cwd, so a repo path naming a
        # subdirectory must still see /repo/.opencode/plugins.
        root = Path(self.tmp.name) / "wt"; (root / "sub" / "deeper").mkdir(parents=True)
        (root / ".git").write_text("gitdir: /elsewhere\n")  # linked-worktree marker
        (root / ".opencode" / "plugins").mkdir(parents=True)
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        status, text = orchestrate._harness_call("opencode", "p/m", "S", "U", str(root / "sub" / "deeper"))
        self.assertEqual((status, fake.calls), (403, []))
        self.assertIn(str(root / ".opencode" / "plugins"), text); self.assertIn("branch-controlled", text)
        # OpenCode walks EVERY ancestor, so a plugins dir above the repo is refused too — and the
        # message says it is the user's environment, not the branch
        outer = Path(self.tmp.name) / "outer"; (outer / ".opencode" / "plugins").mkdir(parents=True)
        inner = outer / "repo"; inner.mkdir(); (inner / ".git").mkdir()
        status, text = orchestrate._harness_call("opencode", "p/m", "S", "U", str(inner))
        self.assertEqual((status, fake.calls), (403, [])); self.assertIn("your own environment", text)

    def test_opencode_probe_applies_the_refusal_from_its_own_cwd(self):
        # ~/.opencode/plugins above cork's state dir would execute during `opencode auth list`
        state = self._isolated_state_dir()                 # <tmp>/state
        (Path(self.tmp.name) / ".opencode" / "plugins").mkdir(parents=True)  # an ancestor of it
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        details = {}
        self.assertEqual(orchestrate._probe("opencode", "github-copilot/gpt-5", details), "error")
        self.assertEqual(fake.calls, [])                   # never launched
        self.assertIn(".opencode/plugins", details["detail"]); self.assertIn("refusing", details["detail"])
        self.assertTrue(state.is_dir())

    def test_arg_prompt_trimming_is_bounded_near_the_size_boundary(self):
        # One file that puts the element just over the limit: proportional shrinking would
        # rebuild the identical prompt thousands of times (files are added whole).
        calls = []
        orig = orchestrate._budget_files
        orchestrate._budget_files = lambda files, budget, *a: calls.append(budget) or orig(files, budget, *a)
        self.addCleanup(setattr, orchestrate, "_budget_files", orig)
        files = {"big.py": "x" * (orchestrate._MAX_ARG_BYTES - 2_000), "tiny.py": "y" * 100}
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            out = orchestrate.review("opencode", "p/m", "S" * 3_000, "story", "diff", files, repo="/repo")
        self.assertEqual(out, fake.out)
        self.assertLess(len(fake.calls[0][0][-1].encode()), orchestrate._MAX_ARG_BYTES)
        self.assertLess(len(calls), 25)  # binary search, not a proportional crawl (was >10k)

    def test_arg_lane_packs_files_by_encoded_size_not_characters(self):
        # 70k chars of é are 140 KB — over the argv limit alone — while 80k ASCII chars fit.
        # Ordering/stopping by characters put the é file first and stopped there, so no
        # budget included the ASCII file; packing by encoded size does.
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        files = {"multibyte.md": "é" * 70_000, "ascii.py": "x" * 80_000}
        orchestrate.review("opencode", "p/m", "", "story", "diff", files, repo="/repo")  # arg-transported lane
        prompt = fake.calls[0][0][-1]
        self.assertIn("### ascii.py", prompt); self.assertNotIn("### multibyte.md", prompt)
        self.assertLess(len(prompt.encode("utf-8")), orchestrate._MAX_ARG_BYTES)

    def test_arg_transported_prompt_is_budgeted_in_bytes_not_chars(self):
        # 100k `é` is 100k chars but 200 KB; a char budget alone would overshoot into a skip.
        files = {"f.py": "é" * 100_000, "g.py": "x" * 50_000}
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            out = orchestrate.review("opencode", "p/m", "S", "story", "small diff", files, repo="/repo")
        self.assertEqual(out, fake.out)
        self.assertLess(len(fake.calls[0][0][-1].encode()), orchestrate._MAX_ARG_BYTES)

    def test_opencode_clears_inherited_explicit_config_variables(self):
        # OPENCODE_CONFIG / _CONFIG_DIR / _CONFIG_CONTENT are honoured regardless of
        # XDG_CONFIG_HOME and would re-introduce MCP/plugin config from the parent shell.
        cleared = ("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_AUTO_SHARE",
                   "OPENCODE_EXPERIMENTAL", "OPENCODE_EXPERIMENTAL_LSP_TOOL",
                   "OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS", "OPENCODE_ENABLE_EXA",
                   "OPENCODE_ENABLE_QUESTION_TOOL")
        for k in cleared:
            os.environ[k] = "inherited"; self.addCleanup(os.environ.pop, k, None)
        os.environ["UNRELATED_VAR"] = "kept"; self.addCleanup(os.environ.pop, "UNRELATED_VAR", None)
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("opencode", "p/m", "S", "U", "/repo")
        env = fake.calls[0][1]["env"]
        for k in cleared:
            self.assertNotIn(k, env)
        self.assertEqual((env["OPENCODE_DISABLE_EXTERNAL_SKILLS"], env["OPENCODE_DISABLE_CLAUDE_CODE"]), ("1", "1"))
        self.assertEqual(env["UNRELATED_VAR"], "kept")          # everything else still inherited
        self.assertIn("OPENCODE_PERMISSION", env)               # overlay still applied

    def test_scratch_dir_is_only_created_for_lanes_that_use_it(self):
        state = self._isolated_state_dir()
        orig = orchestrate.tempfile.TemporaryDirectory
        orchestrate.tempfile.TemporaryDirectory = lambda **kw: self.fail("must not touch the state dir for this lane")
        self.addCleanup(setattr, orchestrate.tempfile, "TemporaryDirectory", orig)
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        for lane in ("codex", "claude", "pi"):
            with self.subTest(lane=lane):
                orchestrate._harness_call(lane, "p/m", "S", "U", "/repo")  # no placeholder in these lanes' env
        self.assertFalse(state.exists())

    def test_config_cannot_override_read_only_or_argv(self):
        orchestrate.CONFIG_PATH.write_text('{"rotation":[{"provider":"codex","model":"m"}],'
                                           '"providers":{"codex":{"enabled":true,"read_only":[],'
                                           '"argv":["exec"],"system_flag":"--x"}}}')
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("codex", "m", "S", "U", "/repo")
        argv = fake.calls[0][0]
        ro = orchestrate.HARNESSES["codex"]["read_only"]
        self.assertEqual(argv[-len(ro):], ro); self.assertIn("--ephemeral", argv)
        self.assertNotIn("--x", argv)

    def test_new_lane_read_only_flags_follow_extra_args(self):
        for lane, marker in (("opencode", "--agent"), ("pi", "--no-tools")):
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
        self.assertEqual(set(denies), {"bash", "edit", "task", "webfetch", "websearch",
                                      "external_directory", "lsp"})
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
        ro = orchestrate.HARNESSES["codex"]["read_only"]
        self.assertEqual(argv[-len(ro) - 1:], ["--foo", *ro])  # extra_args before read-only
        self.assertEqual(kw["timeout"], 30)

    def test_output_decoding_is_lenient(self):
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("codex", "m", "S", "U", "/repo")
        kw = fake.calls[0][1]
        self.assertEqual((kw["encoding"], kw["errors"]), ("utf-8", "replace"))

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

    def test_argv_limit_boundary_counts_the_nul_terminator(self):
        # Linux MAX_ARG_STRLEN (131072) includes the NUL: 131071 usable bytes pass, 131072 do not.
        # Pi's prompt is on stdin; its --system-prompt standards are exactly one argv element,
        # so the boundary is measured there (opencode's prompt element also carries the standards).
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        status, _ = orchestrate._harness_call("pi", "p/m", "x" * (orchestrate._MAX_ARG_BYTES - 1), "U", "/repo")
        self.assertEqual((status, len(fake.calls)), (200, 1))
        status, _ = orchestrate._harness_call("pi", "p/m", "x" * orchestrate._MAX_ARG_BYTES, "U", "/repo")
        self.assertEqual((status, len(fake.calls)), (413, 1))  # refused, no second exec

    def test_oversized_argv_element_is_refused_before_exec(self):
        big = "x" * (orchestrate._MAX_ARG_BYTES + 1)
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        # arg-transported prompt (opencode) -> refused up front, binary never runs
        status, text = orchestrate._harness_call("opencode", "p/m", "S", big, "/repo")
        self.assertEqual(status, 413); self.assertIn("platform limit", text); self.assertEqual(fake.calls, [])
        # --system-prompt standards too large (claude) -> same
        status, _ = orchestrate._harness_call("claude", "m", big, "U", "/repo")
        self.assertEqual(status, 413); self.assertEqual(fake.calls, [])
        # stdin-transported prompt (codex) is not an argv element -> runs normally
        status, _ = orchestrate._harness_call("codex", "m", "S", big, "/repo")
        self.assertEqual(status, 200); self.assertEqual(len(fake.calls), 1)
        # and review() turns the refusal into the usual skip sentinel
        self.assertEqual(orchestrate.review("opencode", "p/m", "S", "story", big, {}, repo="/repo"),
                         "[opencode/p/m returned no usable content — skipped]")

    def test_arg_transported_lanes_budget_files_to_fit_one_argument(self):
        # 192k-char default budget > 128 KiB argv cap: without a harness-aware budget the
        # opencode lane (the only one whose prompt travels as an argument) would skip on
        # ordinary diffs. Files are trimmed to fit instead.
        files = {f"f{i}.py": "x" * 40_000 for i in range(8)}        # 320k chars of file content
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            out = orchestrate.review("opencode", "p/m", "S" * 1000, "story", "small diff", files, repo="/repo")
        self.assertEqual(out, fake.out)                              # ran, not skipped
        prompt = fake.calls[0][0][-1]                                # the single argv element
        self.assertLess(len(prompt.encode()), orchestrate._MAX_ARG_BYTES)
        self.assertIn("small diff", prompt)                          # the diff always travels
        # stdin lanes keep the full budget: codex gets far more of the files
        fake2 = _FakeRun(); orchestrate.subprocess.run = fake2
        with contextlib.redirect_stdout(io.StringIO()):
            orchestrate.review("codex", "m", "S" * 1000, "story", "small diff", files, repo="/repo")
        self.assertGreater(len(fake2.calls[0][1]["input"]), orchestrate._MAX_ARG_BYTES)

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

    def test_nul_byte_in_argument_is_skip_sentinel(self):
        # A branch-controlled standards file containing \0 reaches argv via --system-prompt;
        # subprocess raises ValueError, which must follow the same skip path as OSError.
        orchestrate.subprocess.run = _FakeRun(raise_=ValueError("embedded null byte"))
        self.assertEqual(self._review("claude"), "[claude/m returned no usable content — skipped]")
        orchestrate.subprocess.run = self._run  # real subprocess: rejects the NUL before any exec
        status, text = orchestrate._harness_call("claude", "m", "SYS\0", "U", self.tmp.name)
        self.assertEqual(status, 404)
        self.assertIn("null", text.lower())

    def test_pi_failed_or_empty_review_skips_once(self) -> None:
        for rc, out in ((1, "partial output"), (0, "")):
            with self.subTest(rc=rc):
                fake = _FakeRun(rc=rc, out=out); orchestrate.subprocess.run = fake
                self.assertEqual(self._review("pi"), "[pi/m returned no usable content — skipped]")
                self.assertEqual(len(fake.calls), 1)

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
                         (200, "chat-ok", None))
        self.assertEqual(orchestrate._call_and_extract("anthropic", "claude-x", "S", "U"),
                         (200, "anth-ok", None))

    def test_probe_api_provider_uses_http_probe(self):
        seen = []
        orchestrate._call_and_extract = lambda p, m, s, u, max_out=None, repo="": (
            seen.append((p, m, max_out)) or (200, "ok", None))
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

    def test_validate_timeout_huge_int_fails_cleanly_not_overflow(self):
        # json.loads happily yields 10**400; math.isfinite() raises OverflowError on it.
        with self.assertRaises(SystemExit):
            orchestrate._validate_harness_cfg("codex", {"timeout": 10 ** 400})

    def test_validate_bin_rejects_relative_paths_accepts_bare_and_absolute(self):
        for ok in ("codex", "/opt/codex", "~/bin/codex"):
            with self.subTest(bin=ok):
                orchestrate._validate_harness_cfg("codex", {"bin": ok})
        for bad in ("./tools/codex", "tools/codex", "../codex", "~no-such-user-xyz/bin/codex"):
            with self.subTest(bin=bad), self.assertRaises(SystemExit):
                orchestrate._validate_harness_cfg("codex", {"bin": bad})

    def test_settings_expand_tilde_and_reject_relative_env_bin(self):
        orchestrate.CONFIG_PATH.write_text('{"rotation":[{"provider":"codex","model":"m"}],'
                                           '"providers":{"codex":{"enabled":true,"bin":"~/bin/codex"}}}')
        self.assertEqual(orchestrate._harness_settings("codex")["bin"], str(Path.home() / "bin/codex"))
        os.environ["CORK_CODEX_BIN"] = "./tools/codex"
        with self.assertRaises(SystemExit):
            orchestrate._harness_settings("codex")

    def test_validate_rejects_empty_provider_or_model_component(self):
        for lane in ("opencode", "pi"):
            orchestrate._validate_config({"rotation": [{"provider": lane, "model": "p/m"}]})  # ok
            for bad in ("/model", "provider/", "/", " /m", "p/ ", "no-slash", 3):
                with self.subTest(lane=lane, model=bad), self.assertRaises(SystemExit):
                    orchestrate._validate_config({"rotation": [{"provider": lane, "model": bad}]})

    def test_direct_review_model_ref_is_shape_checked_before_anything_else(self):
        import contextlib, io
        orig = orchestrate._probe
        orchestrate._probe = lambda *a, **k: self.fail("must not probe a malformed ref")
        self.addCleanup(setattr, orchestrate, "_probe", orig)
        for ref in ("opencode/github-copilot", "pi//m", "pi/ /m", "opencode/p/"):
            for validate in (True, False):  # the shape check is not `--skip-validation`'s probe
                with self.subTest(ref=ref, validate=validate):
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                        orchestrate.cmd_review("T", "/nonexistent-repo", "main", ref, validate=validate)
                    self.assertIn("must be <provider>/<model>", err.getvalue())  # not the base-ref failure

    def test_validate_harness_keys_types(self):
        base = {"rotation": [{"provider": "codex", "model": "m"}]}
        orchestrate._validate_config({**base, "providers": {"codex": {
            "enabled": True, "bin": "/opt/codex", "extra_args": ["--a"], "timeout": 60.5}}})
        for bad in ({"timeout": None}, {"timeout": 0}, {"timeout": "30"}, {"timeout": True},
                    {"timeout": float("inf")}, {"timeout": float("nan")},
                    {"extra_args": "--a"}, {"extra_args": [1]}, {"bin": ""}, {"bin": 3}, "str"):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                orchestrate._validate_config({**base, "providers": {"codex": bad}})

    def test_providers_must_be_a_mapping_of_objects(self):
        base = {"rotation": [{"provider": "copilot", "model": "m"}]}
        for bad in (None, [], "x", {"copilot": None}, {"copilot": []}):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                orchestrate._validate_config({**base, "providers": bad})

    def test_enabled_must_be_json_boolean_for_every_provider(self):
        base = {"rotation": [{"provider": "copilot", "model": "m"}]}
        for name in ("copilot", "codex"):
            for bad in ("false", "true", 0, 1, None, []):
                with self.assertRaises(SystemExit, msg=f"{name}={bad!r}"):
                    orchestrate._validate_config({**base, "providers": {name: {"enabled": bad}}})
        orchestrate._validate_config({**base, "providers": {"codex": {"enabled": False}}})

    def test_eligible_rotation_requires_enabled_is_true(self):
        # Belt and braces behind validation: a truthy non-bool must never select a lane.
        orchestrate.shutil.which = lambda b: "/usr/bin/" + b
        cfg = {"providers": {"codex": {"enabled": "false"}},
               "rotation": [{"provider": "codex", "model": "m"}]}
        self.assertEqual(orchestrate._eligible_rotation(cfg), [])

    def test_validate_warns_only_for_unknown_harness_keys_on_stderr(self):
        import contextlib
        base = {"rotation": [{"provider": "opencode", "model": "p/m"}]}
        for extra, expected in (({"env": {}},
                                 "  ⚠ config.providers.opencode: ignoring unknown keys: env (only enabled, bin, extra_args, "
                                 "timeout are configurable; env/unset_env/refuse_paths are cork-enforced)\n"),
                                ({}, "")):
            with self.subTest(extra=extra):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), contextlib.redirect_stderr(err):
                    orchestrate._validate_config({
                        **base, "providers": {"opencode": {"enabled": True, **extra}}})
                self.assertEqual(err.getvalue(), expected)
                self.assertEqual(out.getvalue(), "")  # stdout stays parseable for --json callers

    def test_validate_rejects_option_terminator_in_extra_args(self):
        # `--` in extra_args would demote every protected flag appended after it to a positional.
        for lane in ("pi", "opencode", "codex", "claude"):
            with self.subTest(lane=lane), self.assertRaises(SystemExit):
                orchestrate._validate_harness_cfg(lane, {"extra_args": ["--thinking", "high", "--"]})
        orchestrate._validate_harness_cfg("pi", {"extra_args": ["--thinking", "high"]})  # ok

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

    def test_eligible_rotation_missing_absolute_bin_is_not_reported_as_path_lookup(self):
        orchestrate.CONFIG_PATH.write_text('{"rotation":[{"provider":"codex","model":"m"}],'
                                           '"providers":{"codex":{"enabled":true,"bin":"/opt/codex"}}}')
        orchestrate.shutil.which = lambda b: None
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(orchestrate._eligible_rotation(orchestrate.load_config(quiet=True)), [])
        self.assertIn("/opt/codex", out.getvalue())
        self.assertNotIn("not on PATH", out.getvalue())

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
                called_argv, kw = fake.calls[0]
                self.assertEqual(called_argv, argv)
                self.assertEqual((kw["timeout"], kw["stdin"], kw["capture_output"], kw["text"],
                                  kw["encoding"], kw["errors"]),
                                 (10, subprocess.DEVNULL, True, True, "utf-8", "replace"))
                self.assertEqual(kw["cwd"], str(orchestrate.STATE_DIR))  # never the repo under review

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

    # A slice of opencode's real models.dev cache (~/.cache/opencode/models.json, 1.17.3).
    _MODELS_CACHE = {
        "github-copilot": {"name": "GitHub Copilot", "env": ["GITHUB_TOKEN"]},
        "anthropic": {"name": "Anthropic", "env": ["ANTHROPIC_API_KEY"]},
        "openai": {"name": "OpenAI", "env": ["OPENAI_API_KEY"]},
        "google": {"name": "Google", "env": ["GOOGLE_API_KEY", "GEMINI_API_KEY"]},
        "google-vertex": {"name": "Vertex", "env": ["GOOGLE_VERTEX_PROJECT", "GOOGLE_APPLICATION_CREDENTIALS"]},
        "amazon-bedrock": {"name": "Amazon Bedrock", "env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]},
    }

    def _with_models_cache(self, data):
        import json
        path = Path(self.tmp.name) / "models.json"
        if data is not None:
            path.write_text(json.dumps(data))
        orig = orchestrate._OPENCODE_MODELS_CACHE
        orchestrate._OPENCODE_MODELS_CACHE = path
        self.addCleanup(setattr, orchestrate, "_OPENCODE_MODELS_CACHE", orig)

    def test_opencode_auth_is_provider_aware(self):
        self._with_models_cache(self._MODELS_CACHE)
        # Real `opencode auth list` 1.17.3 shape (ANSI stripped): one bullet per credential.
        listing = "┌  Credentials ~/.local/share/opencode/auth.json\n│\n●  GitHub Copilot oauth\n│\n●  Anthropic oauth\n│\n└  2 credentials\n"
        # env-backed providers are listed separately as "<Display Name> <ENV_VAR>"
        with_env = listing + "┌  Environment\n│\n●  OpenAI OPENAI_API_KEY\n│\n└  1 environment variable\n"
        for output, model, expected in (
            (listing, "github-copilot/gpt-5", "ok"),
            (listing, "anthropic/claude-opus-4.7", "ok"),
            (listing, "openai/gpt-5", "not_logged_in"),          # others' credentials don't count
            (with_env, "openai/gpt-5", "ok"),                    # environment-backed counts for its provider
            (with_env, "google/gemini", "not_logged_in"),        # footers never parse as providers
            # display names are models.dev labels, not ids — resolved through the cache, exactly:
            (with_env + "●  Vertex GOOGLE_APPLICATION_CREDENTIALS\n", "google-vertex/gemini-2.5-pro", "ok"),
            (with_env + "●  Vertex GOOGLE_APPLICATION_CREDENTIALS\n", "google/gemini", "not_logged_in"),  # Vertex is not Google
            (with_env + "●  Google GEMINI_API_KEY\n", "google/gemini", "ok"),
            (with_env + "●  Google GEMINI_API_KEY\n", "google-vertex/gemini", "not_logged_in"),          # Google is not Vertex
            (with_env + "●  Amazon Bedrock AWS_ACCESS_KEY_ID\n", "amazon-bedrock/claude", "ok"),
            ("└  2 credentials\n", "github-copilot/gpt-5", "not_logged_in"),  # aggregate count alone is not auth
            ("└  0 credentials\n", "github-copilot/gpt-5", "not_logged_in"),
            ("●  GitHub Copilot oauth\n1 credential", "github-copilot/gpt-5", "ok"),
            ("●  GitHub Copilot pat\n1 credential", "github-copilot/gpt-5", "ok"),   # unknown method word: name resolves via cache
        ):
            with self.subTest(output=output[-60:], model=model):
                orchestrate.subprocess.run = _FakeRun(out=output)
                self.assertEqual(orchestrate._probe("opencode", model), expected)
        orchestrate.subprocess.run = _FakeRun(rc=1, out=listing)
        self.assertEqual(orchestrate._probe("opencode", "anthropic/claude"), "error")

    def test_models_cache_shape_drift_is_ignored_not_fatal(self):
        self._with_models_cache({"github-copilot": {"name": "GitHub Copilot", "env": 1},
                                 "openai": {"name": 7, "env": ["OPENAI_API_KEY"]}, "bogus": "str"})
        orchestrate.subprocess.run = _FakeRun(out="●  GitHub Copilot oauth\n●  OpenAI OPENAI_API_KEY\n")
        self.assertEqual(orchestrate._probe("opencode", "github-copilot/gpt-5"), "ok")  # by name
        self.assertEqual(orchestrate._probe("opencode", "openai/gpt-5"), "ok")          # by env var

    def test_probe_argv_with_nul_byte_is_error_not_traceback(self):
        orchestrate.subprocess.run = self._run  # real subprocess: rejects the NUL before any exec
        self.assertEqual(orchestrate._probe("pi", "bad\0provider/model"), "error")

    def test_opencode_listing_is_parsed_from_stdout_only(self):
        self._with_models_cache(self._MODELS_CACHE)
        orchestrate.subprocess.run = _FakeRun(out="●  GitHub Copilot oauth\n└  1 credential\n",
                                             err="warning: set OPENAI_API_KEY\n")  # last token IS a known env var
        self.assertEqual(orchestrate._probe("opencode", "github-copilot/gpt-5"), "ok")
        self.assertEqual(orchestrate._probe("opencode", "openai/gpt-5"), "not_logged_in")  # stderr hint is not a credential

    def test_opencode_provider_match_falls_back_to_slug_without_cache(self):
        self._with_models_cache(None)  # no models.json on this machine
        orchestrate.subprocess.run = _FakeRun(out="●  GitHub Copilot oauth\n└  1 credential\n")
        self.assertEqual(orchestrate._probe("opencode", "github-copilot/gpt-5"), "ok")
        self.assertEqual(orchestrate._probe("opencode", "google-vertex/gemini"), "not_logged_in")  # "Vertex" unknowable without the cache

    def test_ansi_auth_output_is_stripped(self):
        completed = subprocess.CompletedProcess(
            ["opencode", "auth", "list"], 0,
            stdout="\x1b[0mGitHub Copilot \x1b[90moauth\x1b[0m\n2 credentials", stderr="",
        )
        self.assertEqual(orchestrate._plain_auth_output(completed),
                         "GitHub Copilot oauth\n2 credentials\n")

    def test_pi_json_is_parsed_from_stdout_even_with_stderr_noise(self):
        # A deprecation/telemetry warning on stderr must not invalidate the stdout JSON.
        for rc, out, err, expected in (
            (0, '{"status":"ready","provider":"openai-codex"}', "warning: config migrated\n", "ok"),
            (1, '{"status":"not_ready","reason":"missing_credentials"}', "note: run /login", "not_logged_in"),
        ):
            with self.subTest(expected=expected):
                orchestrate.subprocess.run = _FakeRun(rc=rc, out=out, err=err)
                details = {}
                self.assertEqual(orchestrate._probe("pi", "openai-codex/gpt-6-sol", details), expected)
        self.assertEqual(details["detail"], "missing_credentials")  # detail still parsed from stdout JSON

    def test_failed_probe_without_structured_reason_has_empty_detail(self):
        # "unavailable (error: glm-internal)" misreported the provider as the failure reason.
        for lane, model in (("pi", "glm-internal/model"), ("opencode", "github-copilot/gpt")):
            with self.subTest(lane=lane):
                orchestrate.subprocess.run = _FakeRun(rc=1, out="garbage", err="boom")
                details = {}
                self.assertEqual(orchestrate._probe(lane, model, details), "error")
                self.assertEqual(details["detail"], "")
        # a structured reason is still preserved on failure
        orchestrate.subprocess.run = _FakeRun(rc=1, out='{"status":"not_ready","reason":"provider_not_found"}')
        details = {}
        orchestrate._probe("pi", "bad/model", details)
        self.assertEqual(details["detail"], "provider_not_found")

    def test_pi_provider_not_found_is_error_with_reason(self):
        orchestrate.subprocess.run = _FakeRun(
            rc=1, out='{"status":"not_ready","provider":"bad","reason":"provider_not_found"}')
        details = {}
        self.assertEqual(orchestrate._probe("pi", "bad/model", details), "error")
        self.assertEqual(details["detail"], "provider_not_found")

    def test_pi_logged_out_requires_rc1_not_ready_json(self):
        cases = (
            (_FakeRun(rc=1, out="", err="provider failed"), "non-JSON rc1"),
            (_FakeRun(rc=0, out="", err="provider failed"), "non-JSON rc0"),
            (_FakeRun(rc=1, out="[]"), "non-object JSON rc1"),
            (_FakeRun(rc=0, out='{"status":"not_ready","reason":"missing_credentials"}'),
             "not_ready rc0"),
            (_FakeRun(rc=1, out='{"status":"ready","provider":"glm-internal"}'),
             "ready rc1"),
        )
        for fake, label in cases:
            with self.subTest(case=label):
                orchestrate.subprocess.run = fake
                self.assertEqual(orchestrate._probe("pi", "glm-internal/model"), "error")

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

    def test_preflight_pi_logged_out_includes_reason(self):
        orchestrate.subprocess.run = _FakeRun(
            rc=1, out='{"status":"not_ready","reason":"missing_credentials"}')
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            orchestrate.preflight([{"provider": "pi", "model": "glm-internal/model"}], 1)
        self.assertIn("pi: logged-out (missing_credentials) — run pi, then /login",
                      buf.getvalue())

    def test_preflight_pi_error_includes_reason(self):
        orchestrate.subprocess.run = _FakeRun(
            rc=1, out='{"status":"not_ready","reason":"provider_not_found"}')
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            orchestrate.preflight([{"provider": "pi", "model": "bad/model"}], 1)
        self.assertIn("pi: unavailable (error: provider_not_found)", buf.getvalue())

    def test_preflight_reports_harness_after_selection_count_is_full(self):
        original = orchestrate._call_and_extract
        orchestrate._call_and_extract = lambda *a, **k: (200, "ok", None)
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


class CodexCopilotPassthroughDocTest(HarnessBase):
    # The README's cork-lane example is the only supported way to give the codex lane the
    # Copilot provider: the lane passes --ignore-user-config, so ~/.codex/config.toml is
    # never read and the whole provider must travel as `-c` overrides in extra_args.
    def _readme_example(self) -> dict:
        import json, re
        text = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        block = re.search(r'```json\n(\{\n  "providers": \{\n    "codex": \{\n      "enabled": true,.*?\n\})\n```', text, re.S)
        self.assertIsNotNone(block, "README cork-lane codex example not found")
        return json.loads(block.group(1))

    def test_readme_example_is_valid_config(self):
        cfg = {**orchestrate.DEFAULT_CONFIG, **self._readme_example()}
        orchestrate._validate_config(cfg)  # no raise

    def test_readme_example_defines_the_whole_provider_via_overrides(self):
        args = self._readme_example()["providers"]["codex"]["extra_args"]
        overrides = [a for i, a in enumerate(args) if i % 2 == 1]
        self.assertEqual(args[::2], ["-c"] * len(overrides))
        keys = [o.split("=", 1)[0] for o in overrides]
        for required in ("model_provider", "model_providers.copilot.base_url",
                         "model_providers.copilot.wire_api", "model_providers.copilot.auth.command",
                         "model_providers.copilot.auth.args"):
            self.assertIn(required, keys)
        self.assertIn("auth print-token", " ".join(overrides))

    def test_readme_example_reaches_codex_before_the_isolation_flags(self):
        import json
        cfg = {**orchestrate.DEFAULT_CONFIG, **self._readme_example()}
        orchestrate.CONFIG_PATH.write_text(json.dumps(cfg))
        fake = _FakeRun(); orchestrate.subprocess.run = fake
        orchestrate._harness_call("codex", "gpt-5.5", "SYS", "USER", "/repo")
        argv = fake.calls[0][0]
        extra = cfg["providers"]["codex"]["extra_args"]
        start = argv.index(extra[0], argv.index("-"))  # after the stdin marker
        self.assertEqual(argv[start:start + len(extra)], extra)
        self.assertLess(start, argv.index("--ignore-user-config"))  # -c overrides precede isolation flags


class SplitRefTest(unittest.TestCase):
    def test_harness_refs(self):
        self.assertEqual(orchestrate._split_model_ref("claude/x"), ("claude", "x"))
        self.assertEqual(orchestrate._split_model_ref("codex/y"), ("codex", "y"))
        self.assertEqual(orchestrate._split_model_ref("pi/glm-internal/glm-5.3-onprem"),
                         ("pi", "glm-internal/glm-5.3-onprem"))
        self.assertEqual(orchestrate._split_model_ref("gpt-4.1"), ("copilot", "gpt-4.1"))
