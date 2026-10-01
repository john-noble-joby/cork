import io
import sys
import unittest
import orchestrate


def _nodes(*reviews):
    return list(reviews)


def _cop(state="COMMENTED", body="", tc=0):
    return {"author": {"login": "copilot-pull-request-reviewer[bot]"},
            "state": state, "body": body, "comments": {"totalCount": tc}}


class ClassifyReviewsTest(unittest.TestCase):
    def test_no_copilot_review(self):
        self.assertEqual(orchestrate._classify_reviews([]),
                         "state=NONE tc=0 verdict=none suppressed=0 missed=0")

    def test_ignores_null_author(self):
        self.assertEqual(orchestrate._classify_reviews([{"author": None, "state": "COMMENTED"}]),
                         "state=NONE tc=0 verdict=none suppressed=0 missed=0")

    def test_approved_state(self):
        self.assertEqual(orchestrate._classify_reviews([_cop(state="APPROVED")]),
                         "state=APPROVED tc=0 verdict=approve suppressed=0 missed=0")

    def test_ready_to_approve_body_anchored(self):
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body="### 🟢 Ready to approve\n...")]),
            "state=COMMENTED tc=0 verdict=approve suppressed=0 missed=0")

    def test_not_ready_to_approve_with_inline(self):
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body="### 🟡 Not ready to approve", tc=2)]),
            "state=COMMENTED tc=2 verdict=block suppressed=0 missed=0")

    def test_suppressed_only(self):
        self.assertEqual(
            orchestrate._classify_reviews(
                [_cop(body="### 🟡 Not ready to approve\n### Suppressed comments (3)")]),
            "state=COMMENTED tc=0 verdict=block suppressed=3 missed=0")

    def test_mixed_inline_and_suppressed(self):
        self.assertEqual(
            orchestrate._classify_reviews(
                [_cop(body="### 🟡 Not ready to approve\n### Suppressed comments (2)", tc=1)]),
            "state=COMMENTED tc=1 verdict=block suppressed=2 missed=0")

    def test_misleading_phrase_is_not_approve(self):
        # 'not quite ready to approve' must not false-positive into approve
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body="### 🟠 Not quite ready to approve yet")]),
            "state=COMMENTED tc=0 verdict=none suppressed=0 missed=0")

    def test_null_body(self):
        self.assertEqual(
            orchestrate._classify_reviews([{"author": {"login": "copilot-pull-request-reviewer[bot]"},
                                            "state": "COMMENTED", "body": None,
                                            "comments": {"totalCount": 0}}]),
            "state=COMMENTED tc=0 verdict=none suppressed=0 missed=0")

    def test_uses_latest_copilot_review(self):
        nodes = _nodes(_cop(body="### 🟡 Not ready to approve", tc=1),
                       _cop(state="APPROVED"))
        self.assertEqual(orchestrate._classify_reviews(nodes),
                         "state=APPROVED tc=0 verdict=approve suppressed=0 missed=0")


    def test_author_object_without_login(self):
        nodes = [{"author": {}, "state": "COMMENTED"},
                 {"author": {"login": None}, "state": "COMMENTED"}]
        self.assertEqual(orchestrate._classify_reviews(nodes),
                         "state=NONE tc=0 verdict=none suppressed=0 missed=0")


def _fixture(name):
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / name).read_text()


class CcrOverviewV2Test(unittest.TestCase):
    # Real review bodies captured from cork PRs (2026-09-30): the ccr-overview-v2 format
    # uses emoji verdict headings and a 'Previously missed (N)' section that the older
    # patterns did not see (issue #18) — three consecutive passes classified verdict=none.

    def test_changes_recommended_is_block_and_counts_previously_missed(self):
        # cork #13 @ cc75057: 'Changes recommended', Open (inline) items, Previously missed body notes
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body=_fixture("copilot-body-changes-recommended.md"), tc=3)]),
            "state=COMMENTED tc=3 verdict=block suppressed=0 missed=1")

    def test_needs_a_closer_look_is_not_approve_but_body_notes_count(self):
        # cork #21 @ 35f2ce8: 'Needs a closer look', Findings None, yet real body-level notes
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body=_fixture("copilot-body-closer-look.md"), tc=0)]),
            "state=COMMENTED tc=0 verdict=none suppressed=0 missed=1")

    def test_approval_recommended_is_approve(self):
        # cork #15 @ e444061: the only genuinely clean pass shape
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body=_fixture("copilot-body-approval-recommended.md"), tc=0)]),
            "state=COMMENTED tc=0 verdict=approve suppressed=0 missed=0")

    def test_new_verdicts_are_line_anchored(self):
        # prose mentioning the phrases mid-line must not flip the verdict
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body="Earlier passes said approval recommended; this one does not.")]),
            "state=COMMENTED tc=0 verdict=none suppressed=0 missed=0")
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body="No changes recommended here, but see the notes.")]),
            "state=COMMENTED tc=0 verdict=none suppressed=0 missed=0")
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body="### 🟡 Changes recommended\n\n### Previously missed (2)")]),
            "state=COMMENTED tc=0 verdict=block suppressed=0 missed=2")


class ReviewClassifyCliTest(unittest.TestCase):
    def _run_with_stdin(self, text):
        orig = sys.stdin
        sys.stdin = io.StringIO(text)
        try:
            orchestrate.cmd_review_classify()
        finally:
            sys.stdin = orig

    def test_bad_stdin_fails_cleanly(self):
        with self.assertRaises(SystemExit):
            self._run_with_stdin("not json at all")

    def test_wrong_shape_fails_cleanly(self):
        with self.assertRaises(SystemExit):
            self._run_with_stdin('{"message": "API rate limit exceeded"}')

    def test_null_nodes_fails_cleanly(self):
        # Shape is correct but reviews.nodes is null → must fail, not TypeError in _classify.
        with self.assertRaises(SystemExit):
            self._run_with_stdin(
                '{"data":{"repository":{"pullRequest":{"reviews":{"nodes":null}}}}}')


if __name__ == "__main__":
    unittest.main()
