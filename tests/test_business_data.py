"""Contract-directed generic processing with synthetic MCP envelopes; no network."""
import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'10-sellfox-business/scripts/process_data.py'
spec = importlib.util.spec_from_file_location('business_data', SCRIPT)
business = importlib.util.module_from_spec(spec)
spec.loader.exec_module(business)


class BusinessDataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = {'version':1, 'tool_id':'fixture-tool', 'rows_path':'data.rows',
                     'group_by':['shop'], 'dedupe_by':['shop','id','date','currency'],
                     'currency_field':'currency', 'date_field':'date',
                     'metrics':{'revenue':{'op':'sum','field':'revenue','unit':'money'},
                                'margin':{'op':'ratio','numerator':'profit','denominator':'revenue',
                                          'unit':'percent','currency_sensitive':True}},
                     'datasets':{'current':{'inputs':['page.json'],'period':{'start':'2026-10-14','end':'2026-10-14'}}}}

    def row(self, **changes):
        row={'shop':'shop-a','id':'a','date':'2026-10-14','currency':'USD','revenue':'100','profit':'20'}
        row.update(changes)
        return row

    def page(self, rows, file='page.json', **changes):
        obj={'tool_id':'fixture-tool','status':'complete','trace_id':'synthetic-business',
             'coverage':{'complete':False},'warnings':[],'data':{'rows':rows}}
        obj.update(changes)
        (self.root/file).write_text(json.dumps(obj))
        return obj

    def run_plan(self):
        return business.process(self.plan, self.root)

    def test_currency_groups_and_weighted_ratio(self):
        self.page([self.row(),self.row(id='b',revenue='300',profit='30'),self.row(id='c',currency='EUR',revenue='50')])
        groups=self.run_plan()['datasets']['current']['groups']
        self.assertEqual(len(groups),2)
        usd=next(g for g in groups if g['dimensions']['currency']=='USD')
        self.assertEqual(usd['metrics']['revenue']['value'],Decimal('400'))
        self.assertEqual(usd['metrics']['margin']['value'],Decimal('12.5'))
        self.assertEqual(self.run_plan()['datasets']['current']['coverage'],'not_certified')

    def test_missing_and_invalid_values_do_not_become_zero(self):
        self.page([self.row(),self.row(id='b',revenue=None),self.row(id='c',revenue=True),self.row(id='d',revenue='NaN')])
        metric=self.run_plan()['datasets']['current']['groups'][0]['metrics']['revenue']
        self.assertIsNone(metric['value'])
        self.assertEqual(metric['known_value'],Decimal('100'))
        self.assertEqual(metric['missing_rows'],3)

    def test_missing_currency_cannot_sum_money(self):
        self.plan['dedupe_by']=['id']
        self.page([self.row(currency=None)])
        metric=self.run_plan()['datasets']['current']['groups'][0]['metrics']['revenue']
        self.assertIsNone(metric['known_value'])
        self.assertEqual(metric['status'],'unknown_currency')

    def test_failed_and_unknown_pages_are_not_data(self):
        self.page([self.row()])
        self.page([self.row(id='bad',revenue='999')],'bad.json',status='failed',error_code='FAIL')
        self.page([self.row(id='unknown',revenue='999')],'unknown.json',status='unknown')
        self.plan['datasets']['current']['inputs']+=['bad.json','unknown.json']
        ds=self.run_plan()['datasets']['current']
        self.assertEqual(ds['accepted_rows'],1)
        self.assertEqual(ds['status'],'partial')
        self.assertEqual(ds['pages'][1]['error_code'],'FAIL')

    def test_duplicate_rows_and_conflicting_keys(self):
        self.page([self.row(),self.row(id='b')])
        self.page([self.row(),self.row(id='b',revenue='999'),self.row(id='c')],'second.json')
        self.plan['datasets']['current']['inputs'].append('second.json')
        ds=self.run_plan()['datasets']['current']
        self.assertEqual(ds['accepted_rows'],2)
        self.assertEqual(ds['excluded']['conflict'],1)
        self.assertEqual(ds['excluded']['duplicate'],1)
        self.assertEqual(len(ds['records'][0]['sources']),2)
        self.assertEqual(ds['groups'][0]['metrics']['revenue']['value'],Decimal('200'))

    def test_repeated_page_not_double_counted(self):
        self.page([self.row()])
        self.page([self.row()],'repeated.json')
        self.plan['datasets']['current']['inputs'].append('repeated.json')
        ds=self.run_plan()['datasets']['current']
        self.assertEqual(ds['accepted_rows'],1)
        self.assertTrue(ds['pages'][1]['duplicate_page'])

    def test_date_scope_is_checked_including_integer_dates(self):
        self.page([self.row(date=20261014),self.row(id='old',date=20250115),self.row(id='missing',date=None)])
        ds=self.run_plan()['datasets']['current']
        self.assertEqual(ds['accepted_rows'],1)
        self.assertEqual(ds['excluded']['date'],2)
        self.plan['mode']='sample'
        with self.assertRaises(business.PlanError):
            self.run_plan()  # sample 无授权依据 → 拒绝（与 topic 链强制闸一致）
        self.plan['sample_reason']='用户明确要求的返回样本演示'
        ds=self.run_plan()['datasets']['current']
        # Missing date also prevents use as a dedupe key.
        self.assertEqual(ds['accepted_rows'],2)
        self.assertTrue(any('仅作返回样本' in issue for issue in ds['issues']))

    def test_comparison_missing_groups_and_zero_baseline(self):
        self.page([self.row(),self.row(id='b',shop='new')])
        self.page([self.row(revenue='0',profit='0')],'base.json')
        self.plan['datasets']['baseline']={'inputs':['base.json']}
        self.plan['compare']={'current':'current','baseline':'baseline'}
        result=self.run_plan()['comparison']['groups']
        for group in result:
            self.assertIsNone(group['metrics']['revenue']['change_percent'])
        missing=next(g for g in result if g['dimensions']['shop']=='new')
        self.assertIsNone(missing['metrics']['revenue']['difference'])

    def test_envelopes_nested_fields_and_request_scope(self):
        env=self.page([self.row()])
        (self.root/'page.json').write_text(json.dumps({'request':{'params':{'shop_id':'a'}},'response':{'structuredContent':env}}))
        self.plan['datasets']['current']['request_match']={'shop_id':'a'}
        self.assertEqual(self.run_plan()['datasets']['current']['accepted_rows'],1)
        self.plan['datasets']['current']['request_match']={'shop_id':'b'}
        self.assertEqual(self.run_plan()['datasets']['current']['status'],'unavailable')
        self.assertEqual(business.at({'a':{'b':3}},'a.b'),3)
        self.assertEqual(business.at({'mskuList':['SKU-A']},'mskuList.0'),'SKU-A')
        self.assertIsNone(business.at({'mskuList':[]},'mskuList.0'))

    def test_warning_preservation_and_empty_not_zero(self):
        self.page([],warnings=['scope unknown'],output_validation={'mismatch':True})
        ds=self.run_plan()['datasets']['current']
        self.assertEqual(ds['status'],'empty')
        self.assertEqual(ds['groups'],[])
        self.assertEqual(ds['pages'][0]['warnings'],['scope unknown'])
        self.assertIn('scope unknown',business.render(self.run_plan()))

    def test_wrong_tool_and_required_money_mapping(self):
        self.page([self.row()],tool_id='wrong-tool')
        self.assertEqual(self.run_plan()['datasets']['current']['status'],'unavailable')
        self.plan.pop('currency_field')
        with self.assertRaises(business.PlanError):self.run_plan()

    def test_standalone_cli_outputs_and_refuses_overwrite(self):
        # The skill can be moved without _shared or the repository.
        standalone=self.root/'skill'
        shutil.copytree(SCRIPT.parent,standalone/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        self.page([self.row()])
        plan=self.root/'plan.json';plan.write_text(json.dumps(self.plan))
        out=self.root/'result'
        cmd=[sys.executable,str(standalone/'scripts/process_data.py'),'--plan',str(plan),'--out-dir',str(out)]
        first=subprocess.run(cmd,text=True,capture_output=True)
        self.assertEqual(first.returncode,0,first.stderr)
        self.assertEqual(json.loads((out/'result.json').read_text())['datasets']['current']['groups'][0]['metrics']['revenue']['value'],'100')
        original=(out/'report.md').read_bytes()
        self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,1)
        self.assertEqual((out/'report.md').read_bytes(),original)

    def test_unavailable_cli_writes_diagnostics_and_nonzero(self):
        self.page([self.row()],status='failed')
        plan=self.root/'plan.json';plan.write_text(json.dumps(self.plan))
        out=self.root/'unavailable'
        run=subprocess.run([sys.executable,str(SCRIPT),'--plan',str(plan),'--out-dir',str(out)],capture_output=True)
        self.assertEqual(run.returncode,2)
        self.assertIn('unavailable',(out/'report.md').read_text())

    def test_real_failure_shape_preserves_upstream_diagnostics(self):
        # Failure envelopes may omit tool_id and contract_version altogether.
        response={'status':'failed','error_code':'UPSTREAM_HTTP_ERROR','retryable':False,
                  'trace_id':'synthetic-failure','data':None,
                  'upstream':{'stage':'business','http_status':400,'upstream_code':40019},
                  'message':'upstream failure','field_errors':[], 'suggested_action':'inspect trace'}
        (self.root/'page.json').write_text(json.dumps({'request':{'tool_id':'fixture-tool','params':{}},'response':response}))
        result=self.run_plan();ds=result['datasets']['current']
        self.assertEqual(ds['status'],'unavailable')
        self.assertEqual(ds['pages'][0]['upstream']['upstream_code'],40019)
        self.assertFalse(ds['pages'][0]['retryable'])
        self.assertIn('40019',business.render(result))
        self.assertIn('UPSTREAM_HTTP_ERROR',ds['pages'][0]['rejection'])


if __name__=='__main__':unittest.main()
