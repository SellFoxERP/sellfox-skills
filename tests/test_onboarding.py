"""Persistent merchant setup regression tests; synthetic evidence, no network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '_shared/scripts/merchant_setup.py'

class OnboardingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.env = dict(os.environ, SELLFOX_USER_CONFIG_DIR=str(self.directory / 'config'))
        self.env.pop('SELLFOX_SHARED_ROOT', None)
        self.path = self.directory / 'config/reports.json'

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env, cwd=self.directory, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return json.loads(result.stdout) if ok else result.stderr

    def proof(self, **changes):
        obj = {'request': {'tool_id': 'get_custom_report_page_list', 'contract_version': 'test-v1', 'params': {'report_id': 'fixture-report'}},
               'response': {'tool_id': 'get_custom_report_page_list', 'contract_version': 'test-v1', 'status': 'complete', 'trace_id': 'synthetic-onboarding', 'data': {'rows': []}}}
        obj['response'].update(changes)
        p = self.directory / 'evidence.json'
        p.write_text(json.dumps(obj))
        return str(p)

    def test_status_does_not_create_config(self):
        state = self.cli('status')
        self.assertTrue(all(v['prompt'] for v in state['reports'].values()))
        self.assertFalse(self.path.parent.exists())

    def test_defer_persists_without_reprompt(self):
        self.cli('defer', '--kind', 'inventory')
        state = self.cli('status')['reports']
        self.assertFalse(state['inventory']['prompt'])
        self.assertEqual(state['inventory']['status'], 'deferred')
        self.assertTrue(state['profit']['prompt'])

    def test_save_merge_backup_and_voc(self):
        self.cli('defer', '--kind', 'inventory')
        state = self.cli('save', '--kind', 'daily_ops', '--id', 'fixture-report', '--evidence', self.proof())
        self.assertTrue(state['reports']['daily_ops']['empty'])
        self.assertEqual(state['reports']['inventory']['status'], 'deferred')
        self.assertTrue(self.path.with_suffix('.json.bak').exists())
        code = "import sys;sys.path.insert(0,sys.argv[1]);import voc_common;print(voc_common.resolve_report_id('daily_ops'))"
        result = subprocess.run([sys.executable, '-c', code, str(ROOT/'09-voc-insight/scripts')], env=self.env, cwd=self.directory, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'fixture-report')

    def test_failed_validation_preserves_previous_state(self):
        self.cli('defer', '--kind', 'profit')
        old = self.path.read_bytes()
        for changes in ({'status':'failed'}, {'status':'partial'}, {'status':'unknown'}, {'scope':{'report_id':'wrong'}}, {'scope':[],'data':None}, {'contract_version':'wrong'}, {'trace_id':None}):
            self.cli('save', '--kind', 'profit', '--id', 'fixture-report', '--evidence', self.proof(**changes), ok=False)
            self.assertEqual(self.path.read_bytes(), old)

    def test_corrupt_config_not_overwritten(self):
        self.path.parent.mkdir()
        self.path.write_text('{broken')
        self.cli('defer', '--kind', 'profit', ok=False)
        self.assertEqual(self.path.read_text(), '{broken')

    def test_different_package_copy_reads_same_user_state(self):
        import shutil
        self.cli('save', '--kind', 'profit', '--id', 'fixture-report', '--evidence', self.proof())
        old = self.path.read_bytes()
        copy = self.directory / 'new-version/_shared'
        shutil.copytree(ROOT/'_shared', copy, ignore=shutil.ignore_patterns('__pycache__'))
        result = subprocess.run([sys.executable, str(copy/'scripts/merchant_setup.py'), 'status'], env=self.env, cwd=self.directory, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['reports']['profit']['id'], 'fixture-report')
        self.assertEqual(self.path.read_bytes(), old)

    def test_concurrent_write_refused(self):
        self.cli('defer', '--kind', 'profit')
        old = self.path.read_bytes()
        (self.path.parent/'.reports.lock').touch()
        self.cli('defer', '--kind', 'inventory', ok=False)
        self.assertEqual(self.path.read_bytes(), old)

if __name__ == '__main__':
    unittest.main()
