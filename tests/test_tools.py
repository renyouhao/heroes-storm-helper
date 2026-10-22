from pathlib import Path
import unittest
from tools.evaluate_samples import evaluate
from tools.measure_runtime import summarize

class ToolTests(unittest.TestCase):
    def test_evaluation_dry_run_never_opens_cloud(self):
        def forbidden():raise AssertionError('unexpected network initialization')
        result=evaluate([{'text':'hello','source':'auto','target':'zh'}],engine_factory=forbidden)
        self.assertEqual(result['cloud_calls'],0)
        self.assertEqual(result['items'][0]['grade'],'未联网／待实译')
    def test_cpu_normalized_and_short_measurement_not_acceptance(self):
        result=summarize([(0,0,100),(10,1,120)],16)
        self.assertAlmostEqual(result['cpu_percent_all_cores'],.625)
        self.assertFalse(result['ten_minute_window'])
