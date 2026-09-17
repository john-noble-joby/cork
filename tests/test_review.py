import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, call, patch

import orchestrate


class ReviewDiffTest(unittest.TestCase):
    def _minimal_config(self):
        return {
            "count": 1,
            "providers": {"copilot": {"enabled": True}},
            "rotation": [{"provider": "copilot", "model": "model"}],
        }

    def test_git_diff_branch_uses_merge_base(self):
        with patch.object(orchestrate.subprocess, "check_output", return_value="diff") as check:
            self.assertEqual(orchestrate.git_diff_branch("/repo", "origin/main"), "diff")

        check.assert_called_once_with(
            ["git", "diff", "origin/main...HEAD"], cwd="/repo", text=True
        )

    def test_changed_files_branch_uses_merge_base(self):
        with patch.object(orchestrate.subprocess, "check_output", return_value="") as check:
            self.assertEqual(orchestrate.changed_files_branch("/repo", "origin/main"), {})

        check.assert_called_once_with(
            ["git", "diff", "origin/main...HEAD", "--name-only"],
            cwd="/repo",
            text=True,
        )

    def test_cmd_review_rejects_unresolved_base_before_diff(self):
        base_check = Mock(returncode=1, stderr="")
        with (
            patch.object(orchestrate, "_probe", return_value="ok") as probe,
            patch.object(
                orchestrate, "load_agent_instructions", return_value=("", "")
            ) as load_instructions,
            patch.object(orchestrate.subprocess, "run", return_value=base_check) as run,
            patch.object(orchestrate, "git_diff_branch") as diff,
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit):
                orchestrate.cmd_review(
                    "TEST-1", "/repo", "missing-base", "copilot/model", validate=True
                )

        run.assert_called_once_with(
            [
                "git",
                "rev-parse",
                "--verify",
                "--quiet",
                "missing-base^{commit}",
            ],
            cwd="/repo",
            capture_output=True,
            text=True,
        )
        probe.assert_not_called()
        load_instructions.assert_not_called()
        diff.assert_not_called()

    def test_cmd_review_rejects_empty_diff_before_probe(self):
        with (
            patch.object(orchestrate, "require_base_ref"),
            patch.object(orchestrate, "git_diff_branch", return_value="\n"),
            patch.object(orchestrate, "_probe") as probe,
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit):
                orchestrate.cmd_review(
                    "TEST-1", "/repo", "origin/main", "copilot/model", validate=True
                )

        probe.assert_not_called()

    def test_require_base_ref_rejects_missing_merge_base(self):
        checks = [
            Mock(returncode=0, stderr=""),
            Mock(returncode=1, stderr="fatal: Not a valid object name HEAD\n"),
        ]
        error = io.StringIO()
        with (
            patch.object(orchestrate.subprocess, "run", side_effect=checks) as run,
            redirect_stderr(error),
        ):
            with self.assertRaises(SystemExit):
                orchestrate.require_base_ref("/repo", "origin/main")

        self.assertEqual(
            run.call_args_list,
            [
                call(
                    [
                        "git",
                        "rev-parse",
                        "--verify",
                        "--quiet",
                        "origin/main^{commit}",
                    ],
                    cwd="/repo",
                    capture_output=True,
                    text=True,
                ),
                call(
                    ["git", "merge-base", "origin/main", "HEAD"],
                    cwd="/repo",
                    capture_output=True,
                    text=True,
                ),
            ],
        )
        self.assertIn("fatal: Not a valid object name HEAD", error.getvalue())

    def test_require_base_ref_omits_empty_merge_base_reason(self):
        checks = [
            Mock(returncode=0, stderr=""),
            Mock(returncode=1, stderr=""),
        ]
        error = io.StringIO()
        with (
            patch.object(orchestrate.subprocess, "run", side_effect=checks),
            redirect_stderr(error),
        ):
            with self.assertRaises(SystemExit):
                orchestrate.require_base_ref("/repo", "origin/main")

        self.assertIn("No merge base between 'origin/main' and HEAD\n", error.getvalue())
        self.assertNotIn("HEAD:", error.getvalue())

    def test_main_requires_base_before_step_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            argv = [
                "orchestrate.py",
                "TEST-1",
                str(repo),
                "--base-branch",
                "origin/main",
                "--reset",
                "--skip-validation",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(orchestrate, "STATE_DIR", Path(tmp) / "state"),
                patch.object(
                    orchestrate, "load_config", return_value=self._minimal_config()
                ),
                patch.object(
                    orchestrate, "require_base_ref", side_effect=SystemExit(1)
                ) as require,
                patch.object(orchestrate, "clear_state") as clear_state,
                patch.object(orchestrate, "run_claude") as run_claude,
            ):
                with self.assertRaises(SystemExit):
                    orchestrate.main()

        require.assert_called_once_with(str(repo.resolve()), "origin/main")
        clear_state.assert_not_called()
        run_claude.assert_not_called()

    def test_main_requires_base_before_seed_only_git_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            argv = [
                "orchestrate.py",
                "TEST-1",
                str(repo),
                "--base-branch",
                "origin/main",
                "--seed-only",
                "--skip-validation",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(orchestrate, "STATE_DIR", Path(tmp) / "state"),
                patch.object(
                    orchestrate, "load_config", return_value=self._minimal_config()
                ),
                patch.object(
                    orchestrate, "require_base_ref", side_effect=SystemExit(1)
                ) as require,
                patch.object(orchestrate.subprocess, "check_output") as git_log,
            ):
                with self.assertRaises(SystemExit):
                    orchestrate.main()

        require.assert_called_once_with(str(repo.resolve()), "origin/main")
        git_log.assert_not_called()

    def test_review_prompts_use_merge_base_and_fix_spec_findings(self):
        review_prompt = orchestrate.prompt_claude_review(
            "origin/main", "/review.md", "Implement the requested widget"
        )
        fix_prompt = orchestrate.prompt_fix("summary", "origin/main", "review", "TEST-1")

        self.assertIn(
            "## Story / Task\nImplement the requested widget\n\n", review_prompt
        )
        self.assertIn("git diff origin/main...HEAD", review_prompt)
        self.assertIn("git diff origin/main...HEAD", fix_prompt)
        self.assertIn("Spec conformance sections", fix_prompt)
        self.assertIn("do NOT delete behaviour flagged as unrequested", fix_prompt)

    def test_review_system_prompt_carries_spec_axis_with_custom_instructions(self):
        for instructions in ("Custom project rules", ""):
            with self.subTest(instructions=instructions):
                with patch.object(
                    orchestrate, "_call_and_extract", return_value=(200, "review output")
                ) as call_api:
                    result = orchestrate.review(
                        "copilot", "model", instructions,
                        "Implement the widget", "diff", {}
                    )

                self.assertEqual(result, "review output")
                system = call_api.call_args.args[2]
                self.assertIn("## Spec conformance", system)
                self.assertIn("no spec available", system)
                if instructions:
                    self.assertIn(instructions, system)
                else:
                    self.assertIn("For each issue in the main list", system)


if __name__ == "__main__":
    unittest.main()
