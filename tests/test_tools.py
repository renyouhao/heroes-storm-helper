from pathlib import Path
import unittest
from tools.evaluate_samples import evaluate
from tools.measure_runtime import summarize
from types import SimpleNamespace

class ToolTests(unittest.TestCase):
    def test_live_evaluation_translates_without_usage_ledger_and_closes_engine(self):
        calls=[]
        closed=[]
        engine=SimpleNamespace(translate=lambda job: calls.append(job.text) or SimpleNamespace(translated='你好'),
                               close=lambda: closed.append(True))
        result=evaluate([{'text':'hello','source':'en','target':'zh'}],live=True,engine_factory=lambda:engine)
        self.assertEqual(calls,['hello'])
        self.assertEqual(result['items'][0]['translation'],'你好')
        self.assertEqual(result['cloud_calls'],1)
        self.assertEqual(closed,[True])

    def test_evaluation_dry_run_never_opens_cloud(self):
        def forbidden():raise AssertionError('unexpected network initialization')
        result=evaluate([{'text':'hello','source':'auto','target':'zh'}],engine_factory=forbidden)
        self.assertEqual(result['cloud_calls'],0)
        self.assertEqual(result['items'][0]['grade'],'未联网／待实译')
    def test_cpu_normalized_and_short_measurement_not_acceptance(self):
        result=summarize([(0,0,100),(10,1,120)],16)
        self.assertAlmostEqual(result['cpu_percent_all_cores'],.625)
        self.assertFalse(result['ten_minute_window'])
