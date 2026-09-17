import unittest
from unittest.mock import Mock, patch

import orchestrate


class ReviewDiffTest(unittest.TestCase):
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
        base_check = Mock(returncode=1)
        with (
            patch.object(orchestrate, "load_agent_instructions", return_value=("", "")),
            patch.object(orchestrate.subprocess, "run", return_value=base_check) as run,
            patch.object(orchestrate, "git_diff_branch") as diff,
        ):
            with self.assertRaises(SystemExit):
                orchestrate.cmd_review(
                    "TEST-1", "/repo", "missing-base", "copilot/model", validate=False
                )

        run.assert_called_once_with(
            ["git", "rev-parse", "--verify", "--quiet", "missing-base"],
            cwd="/repo",
            capture_output=True,
            text=True,
        )
        diff.assert_not_called()

    def test_review_prompts_use_merge_base_and_fix_spec_findings(self):
        review_prompt = orchestrate.prompt_claude_review("origin/main", "/review.md")
        fix_prompt = orchestrate.prompt_fix("summary", "origin/main", "review", "TEST-1")

        self.assertIn("git diff origin/main...HEAD", review_prompt)
        self.assertIn("git diff origin/main...HEAD", fix_prompt)
        self.assertIn("Spec conformance sections", fix_prompt)
        self.assertIn("do NOT delete behaviour flagged as unrequested", fix_prompt)


if __name__ == "__main__":
    unittest.main()
