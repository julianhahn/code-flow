import unittest
from review_summary import checks_summary, purpose_excerpt


class SummaryTests(unittest.TestCase):
    def test_checks_are_bounded(self):
        checks = [{'conclusion': 'SUCCESS', 'name': 'Long job name'}] * 200
        checks += [{'conclusion': 'SKIPPED'}]
        self.assertEqual(checks_summary(checks), 'Checks: no failures · 1 skipped')

    def test_not_all_green_when_pending_or_unknown(self):
        self.assertEqual(checks_summary([{'state': 'PENDING'}, {'conclusion': 'FAILURE'}, {}]),
                         'Checks: 1 failed · 1 pending · 1 unknown')
        self.assertEqual(checks_summary([]), 'Checks: none reported')

    def test_author_excerpt_ignores_bot_block(self):
        body = '## Description\r\n\r\nGive the editor one issue shape.\r\n\r\n---\r\n\r\n> Bot summary'
        self.assertEqual(purpose_excerpt(body), 'Give the editor one issue shape.')

    def test_long_or_missing_description(self):
        self.assertLessEqual(len(purpose_excerpt('word ' * 1000)), 360)
        self.assertIn('GitHub', purpose_excerpt('> Bot summary only'))
        self.assertEqual(purpose_excerpt('Use `one` **shape**.'), 'Use one shape.')
