import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import beta
import telemetry
from common import write


class Measurements(unittest.TestCase):
    def test_phase_and_nested_load_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'timing.jsonl'
            with patch.dict(os.environ, CJH_TIMING_FILE=str(target)):
                telemetry.phase('analysis')
                with telemetry.measure('whisper'):
                    time.sleep(.002)
                telemetry.event('cache', 'analysis_reused', count=1)
                telemetry.phase('done')
            data = telemetry.summary(target)
            self.assertEqual(data['cacheCounts']['analysis_reused'], 1)
            self.assertGreaterEqual(data['phaseSeconds']['analysis'], data['modelLoadSecondsIncluded']['whisper'])

    def test_failure_closes_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            @telemetry.workflow
            def fail(source):
                telemetry.phase('boundaries')
                raise ValueError('fixture')
            with self.assertRaises(ValueError):
                fail(Path(tmp)/'input.json')
            self.assertIn('boundaries', telemetry.summary(Path(tmp)/'timing.jsonl')['phaseSeconds'])
            self.assertNotIn('CJH_TIMING_FILE', os.environ)

    def test_sanitized_report_and_feedback_validation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(beta, 'DATA', Path(tmp)):
            folder = Path(tmp)/'jobs/test'
            folder.mkdir(parents=True)
            write(folder/'input.json', {'assets': [{'path': '/private/secret.mov', 'name': 'secret.mov', 'size': 123}],
                                      'editingScript': 'private dialogue', 'voiceProfile': {'embedding': [0.5]}})
            job = {'id': 'test', 'status': 'failed', 'created': 10, 'finished': 20,
                   'message': '/secret.log', 'command': ['private-token'], 'result': {},
                   'attemptHistory': [{'seconds': 8, 'status': 'failed', 'attempt': 1}]}
            report = beta.report(job)
            text = json.dumps(report)
            for private in ('secret.mov', 'private dialogue', 'embedding', 'private-token', 'secret.log'):
                self.assertNotIn(private, text)
            self.assertEqual(report['timing']['activeSeconds'], 8)
            self.assertEqual(report['timing']['elapsedSeconds'], 10)
            for body in ({'reviewMinutes': float('nan')}, {'issues': ['invalid']}, {'note': 'x'*2001}):
                with self.assertRaises(ValueError):
                    beta.feedback(job, body)
            beta.feedback(job, {'issues': ['食物匹配错误'], 'draftOpened': 'no', 'reviewMinutes': 2})
            self.assertEqual(beta.report(job)['feedback']['reviewMinutes'], 2)

    def test_legacy_does_not_invent_metrics(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(beta, 'DATA', Path(tmp)):
            report = beta.report({'id': 'old', 'status': 'succeeded', 'created': 0, 'finished': 10})
            self.assertFalse(report['timing']['measured'])
            self.assertIsNone(report['timing']['activeSeconds'])


if __name__ == '__main__':
    unittest.main()
