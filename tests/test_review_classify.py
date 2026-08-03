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
                         "state=NONE tc=0 verdict=none suppressed=0")

    def test_ignores_null_author(self):
        self.assertEqual(orchestrate._classify_reviews([{"author": None, "state": "COMMENTED"}]),
                         "state=NONE tc=0 verdict=none suppressed=0")

    def test_approved_state(self):
        self.assertEqual(orchestrate._classify_reviews([_cop(state="APPROVED")]),
                         "state=APPROVED tc=0 verdict=approve suppressed=0")

    def test_ready_to_approve_body_anchored(self):
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body="### 🟢 Ready to approve\n...")]),
            "state=COMMENTED tc=0 verdict=approve suppressed=0")

    def test_not_ready_to_approve_with_inline(self):
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body="### 🟡 Not ready to approve", tc=2)]),
            "state=COMMENTED tc=2 verdict=block suppressed=0")

    def test_suppressed_only(self):
        self.assertEqual(
            orchestrate._classify_reviews(
                [_cop(body="### 🟡 Not ready to approve\n### Suppressed comments (3)")]),
            "state=COMMENTED tc=0 verdict=block suppressed=3")

    def test_mixed_inline_and_suppressed(self):
        self.assertEqual(
            orchestrate._classify_reviews(
                [_cop(body="### 🟡 Not ready to approve\n### Suppressed comments (2)", tc=1)]),
            "state=COMMENTED tc=1 verdict=block suppressed=2")

    def test_misleading_phrase_is_not_approve(self):
        # 'not quite ready to approve' must not false-positive into approve
        self.assertEqual(
            orchestrate._classify_reviews([_cop(body="### 🟠 Not quite ready to approve yet")]),
            "state=COMMENTED tc=0 verdict=none suppressed=0")

    def test_null_body(self):
        self.assertEqual(
            orchestrate._classify_reviews([{"author": {"login": "copilot-pull-request-reviewer[bot]"},
                                            "state": "COMMENTED", "body": None,
                                            "comments": {"totalCount": 0}}]),
            "state=COMMENTED tc=0 verdict=none suppressed=0")

    def test_uses_latest_copilot_review(self):
        nodes = _nodes(_cop(body="### 🟡 Not ready to approve", tc=1),
                       _cop(state="APPROVED"))
        self.assertEqual(orchestrate._classify_reviews(nodes),
                         "state=APPROVED tc=0 verdict=approve suppressed=0")


    def test_author_object_without_login(self):
        nodes = [{"author": {}, "state": "COMMENTED"},
                 {"author": {"login": None}, "state": "COMMENTED"}]
        self.assertEqual(orchestrate._classify_reviews(nodes),
                         "state=NONE tc=0 verdict=none suppressed=0")


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


if __name__ == "__main__":
    unittest.main()
