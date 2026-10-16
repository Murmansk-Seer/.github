import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'update_report', Path(__file__).resolve().parents[1] / '.github/actions/report-update/report.py'
)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def test_outcomes(self):
        for expected in ('unchanged', 'updated', 'published', 'dry_run', 'blocked'):
            evidence = {} if expected == 'unchanged' else {expected: True}
            self.assertEqual(report.outcome({'build': {'result': 'success'}}, evidence), expected)

    def test_failure_and_cancellation_never_become_success(self):
        for result, expected in [('failure', 'failed'), ('cancelled', 'cancelled')]:
            self.assertEqual(report.outcome({'build': {'result': result}}, {'published': True}), expected)

    def test_dispatch_is_not_publication(self):
        self.assertEqual(report.outcome({}, {'dispatch': 'success'}), 'unchanged')


if __name__ == '__main__':
    unittest.main()
