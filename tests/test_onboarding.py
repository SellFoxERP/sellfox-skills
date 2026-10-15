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
        self.assertTrue(all(v['auto_discover'] for v in state['reports'].values()))
        self.assertFalse(any(v['prompt'] for v in state['reports'].values()))
        self.assertFalse(self.path.parent.exists())

    def test_defer_persists_without_reprompt(self):
        self.cli('defer', '--kind', 'inventory')
        state = self.cli('status')['reports']
        self.assertFalse(state['inventory']['prompt'])
        self.assertEqual(state['inventory']['status'], 'deferred')
        self.assertFalse(state['inventory']['auto_discover'])
        self.assertTrue(state['profit']['auto_discover'])

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

    def indices(self, name, rows, **changes):
        obj={'tool_id':'get_custom_report_index_page_list','status':'complete',
             'trace_id':'synthetic-discovery','data':{'rows':rows},
             'coverage':{'complete':False},'warnings':[]}
        obj.update(changes)
        path=self.directory/name
        path.write_text(json.dumps(obj))
        return str(path)

    def test_discovery_deduplicates_without_saving_or_selecting(self):
        first=self.indices('first.json',[
            {'indexId':'not-a-report','reportList':[{'reportId':'11','reportName':'经营'}]},
            {'reportList':[{'reportId':'11','reportName':'旧名称'},{'reportId':12,'reportName':'经营'}]},
            {'reportList':None}])
        second=self.indices('second.json',[{'reportList':[{'reportId':'11','reportName':'经营'}]}])
        result=self.cli('candidates','--input',first,second)
        self.assertEqual([r['report_id'] for r in result['candidates']],['11','12'])
        self.assertEqual(result['candidates'][0]['names'],['经营','旧名称'])
        self.assertEqual(result['candidates'][0]['source_pages'],[1,2])
        self.assertFalse(result['report_catalog_complete'])
        self.assertFalse(self.path.parent.exists())

    def test_discovery_keeps_success_with_failed_page(self):
        good=self.indices('good.json',[{'reportList':[{'reportId':'11','reportName':'经营'}]}])
        bad=self.indices('bad.json',[{'reportList':[{'reportId':'unsafe','reportName':'不可信'}]}],
                         status='failed',error_code='UPSTREAM_RESPONSE_INVALID')
        result=self.cli('candidates','--input',good,bad)
        self.assertEqual(len(result['candidates']),1)
        self.assertFalse(result['pages'][1]['usable'])
        self.assertEqual(result['pages'][1]['error_code'],'UPSTREAM_RESPONSE_INVALID')

    def test_discovery_rejects_unknown_and_wrong_tool(self):
        for changes in ({'status':'unknown'},{'tool_id':'get_order_page_list'}):
            path=self.indices('untrusted.json',[{'reportList':[{'reportId':'11'}]}],**changes)
            result=self.cli('candidates','--input',path)
            self.assertEqual(result['status'],'unavailable')
            self.assertEqual(result['candidates'],[])

    def test_discovery_missing_links_are_not_report_ids(self):
        path=self.indices('missing.json',[{'indexId':'index-only','indexName':'收入'},
            {'reportList':[{'id':'wrong-field'},{'reportId':True},'bad-entry']},
            {'reportList':{'reportId':'wrong-shape'}}])
        result=self.cli('candidates','--input',path)
        self.assertEqual(result['status'],'empty')
        self.assertFalse(result['report_catalog_complete'])
        self.assertEqual(result['pages'][0]['invalid_entries'],4)

    def test_discovery_partial_retains_warnings(self):
        path=self.indices('partial.json',[{'reportList':[{'reportId':'11'}]}],
                          status='partial',warnings=['incomplete'],output_validation={'status':'mismatch'})
        result=self.cli('candidates','--input',path)
        self.assertEqual(result['status'],'candidates')
        self.assertEqual(result['pages'][0]['warnings'],['incomplete'])
        self.assertEqual(result['pages'][0]['output_validation'],{'status':'mismatch'})

if __name__ == '__main__':
    unittest.main()
