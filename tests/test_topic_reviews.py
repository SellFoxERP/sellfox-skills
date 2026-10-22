"""Synthetic financial and launch edge cases; no live data or network."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'10-sellfox-business/scripts'))
import topic_review
import scenarios

class TopicTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
    def plan(self,folder):
        return json.loads((ROOT/folder/'references/plan-example.json').read_text())
    def page(self,plan,rows,status='complete'):
        ds=next(iter(plan['datasets'].values()));filename=ds['inputs'][0]
        container=plan['rows_path'].split('.')[-1]
        (self.base/filename).write_text(json.dumps({'tool_id':plan['tool_id'],'status':status,'data':{container:rows},'trace_id':'synthetic-topic','coverage':{'complete':False}}))
    def row(self,**kwargs):
        return dict({'shopId':'fixture','id':'item-a','bizDay':20261002,'currency':'USD','salesNum':10,'refundNum':2,'returnNum':1,'refundSales':-40},**kwargs)
    def test_returns_preserves_sign_and_separate_events(self):
        p=self.plan('11-returns-loss');self.page(p,[self.row()])
        r=topic_review.review(p,self.base,'returns');f=r['findings'][0]
        self.assertEqual(f['refund_amount_signed'],Decimal(-40));self.assertEqual(f['refund_amount_magnitude'],Decimal(40))
        self.assertEqual(f['refund_units'],2);self.assertEqual(f['return_units'],1)
        self.assertNotIn('net_loss',f)
    def test_returns_currency_and_missing_amount(self):
        p=self.plan('11-returns-loss');self.page(p,[self.row(),self.row(id='item-b',currency='EUR',refundSales=None)])
        r=topic_review.review(p,self.base,'returns');self.assertEqual(len(r['findings']),2)
        self.assertIsNone(r['findings'][1]['refund_amount_magnitude'])
    def test_merchant_summary_layer_and_note_once(self):
        p=self.plan('11-returns-loss');self.page(p,[self.row(),self.row(id='item-b',refundSales=-25)])
        (self.base/'shops.json').write_text(json.dumps({'tool_id':'get_shop_page_list','status':'complete',
                                                        'data':{'rows':[{'id':'fixture','name':'示例店铺'}]}}))
        report=topic_review.render(topic_review.review(p,self.base,'returns'))
        self.assertIn('| MSKU | 店铺 | 退款金额 | 退款量 | 退货量 |',report)
        self.assertIn('| item-a | 示例店铺 | 40 USD | 2 | 1 |',report)  # 原币种标注 + 店铺名映射 + 金额降序
        self.assertNotIn('{"currency"',report.split('## 复核明细')[0])   # 摘要层无裸 JSON dict
        self.assertEqual(report.count('不是净损失，不代表质量原因。'),2)  # 摘要表下一句话 + 复核明细开头各一次
        self.assertNotIn('"note"',report.split('## 复核明细（机器证据）')[1].split('# 赛狐业务数据处理结果')[0])
    def test_launch_summary_stage_words(self):
        p=self.plan('12-new-product-review');p['topic']['max_age_days']=30
        self.page(p,[{'shopId':'fixture','id':'in','openDate':'2026-10-10','day7SaleNum':1,'day30SaleNum':1},
                     {'shopId':'fixture','id':'old','openDate':'2026-01-20','day7SaleNum':1,'day30SaleNum':1},
                     {'shopId':'fixture','id':'ts','openDate':'2026-10-01T00:00:00Z','day7SaleNum':1,'day30SaleNum':1}])
        report=topic_review.render(topic_review.review(p,self.base,'launch'))
        self.assertIn('| MSKU | 店铺 | 上架日期 | 上市天数 | 阶段 |',report)
        self.assertIn('观察中（第 6 天/窗口 30 天）',report)
        self.assertIn('超龄（第 ',report);self.assertIn('日期不明',report)
        self.assertIn('店铺名未知时显示编号',report)  # 无 shops.json → 编号 + 注
    def test_failed_response_does_not_become_zero(self):
        p=self.plan('11-returns-loss');self.page(p,[self.row()],status='failed')
        r=topic_review.review(p,self.base,'returns');self.assertEqual(r['findings'],[])
        self.assertEqual(r['datasets']['current']['status'],'unavailable')
    def test_array_first_item_and_wrong_mapping_refused(self):
        p=self.plan('11-returns-loss');p['group_by']=['mskuList.0']
        with self.assertRaises(ValueError):topic_review.review(p,self.base,'returns')
        p=self.plan('11-returns-loss');p['metrics']['refund_units']['field']='returnNum'
        with self.assertRaises(ValueError):topic_review.review(p,self.base,'returns')
    def test_launch_age_and_immature_window(self):
        p=self.plan('12-new-product-review');self.page(p,[{'shopId':'fixture','id':'a','openDate':'2026-10-10','day7SaleNum':3,'day30SaleNum':3}])
        r=topic_review.review(p,self.base,'launch');self.assertEqual(r['findings'][0]['age_days'],6)
        self.assertEqual(r['findings'][0]['completed_observation_windows'],[])
    def test_launch_future_and_unparsed_timestamp_unknown(self):
        p=self.plan('12-new-product-review');self.page(p,[{'shopId':'fixture','id':str(i),'openDate':v} for i,v in enumerate(['2026-10-16','2026-10-01T00:00:00Z',None])])
        r=topic_review.review(p,self.base,'launch');self.assertTrue(all(x['age_days'] is None for x in r['findings']))
    def test_replenishment_pack_rounding_and_early_shortage(self):
        x=scenarios.calculate({'mode':'replenishment','source_note':'synthetic','daily_units':3,'lead_days':10,'safety_days':2,'review_days':3,'available_units':1,'confirmed_inbound_units':30,'inbound_timing_note':'synthetic confirmed arrival','pack_size':6})
        self.assertEqual(x['suggested_units'],18);self.assertTrue(x['stockout_before_lead'])
    def test_pricing_nonpositive_contribution_no_break_even_volume(self):
        x=scenarios.calculate({'mode':'pricing','source_note':'synthetic','currency':'USD','price':10,'discount_rate':0,'variable_fee_rate':0.1,'fixed_unit_cost':9,'ad_unit_cost':1,'activity_fixed_cost':50})
        self.assertEqual(x['unit_contribution'],-1);self.assertIsNone(x['break_even_activity_units'])
    def test_missing_assumptions_not_zero(self):
        with self.assertRaises(ValueError):scenarios.calculate({'mode':'pricing','source_note':'synthetic'})
    def test_shipment_closed_and_future(self):
        p={'mode':'shipment','source_note':'synthetic','as_of':'2026-10-15','shipped_date':'2026-09-01','status':'CLOSED','terminal_statuses':['CLOSED'],'followup_days':30}
        self.assertFalse(scenarios.calculate(p)['needs_followup'])
        p['shipped_date']='2026-10-16'
        with self.assertRaises(ValueError):scenarios.calculate(p)
    def test_sales_zero_baseline_and_unequal_periods(self):
        p={'mode':'sales_change','source_note':'synthetic','current_sales':2,'baseline_sales':0,'current_days':7,'baseline_days':7}
        self.assertIsNone(scenarios.calculate(p)['change_percent']);p['current_days']=3
        with self.assertRaises(ValueError):scenarios.calculate(p)
    def test_sample_requires_reason_and_preserves_date_mismatch(self):
        p=self.plan('11-returns-loss');self.page(p,[self.row(bizDay=20250115)])
        self.assertEqual(topic_review.review(p,self.base,'returns')['datasets']['current']['status'],'unavailable')
        p['mode']='sample'
        with self.assertRaises(ValueError):topic_review.review(p,self.base,'returns')
        p['topic']['sample_reason']='Explicit synthetic capability demonstration'
        r=topic_review.review(p,self.base,'returns')
        self.assertEqual(r['datasets']['current']['observed_dates'],['20250115'])
        self.assertEqual(r['findings'][0]['refund_units'],2)
        self.assertIn('返回样本演示',topic_review.render(r))
        self.assertTrue(any('日期不匹配' in x for x in r['datasets']['current']['issues']))

    def test_sample_never_accepts_failed_data(self):
        p=self.plan('11-returns-loss');p['mode']='sample';p['topic']['sample_reason']='Synthetic demonstration'
        self.page(p,[self.row()],status='failed')
        self.assertEqual(topic_review.review(p,self.base,'returns')['findings'],[])

    def test_old_launch_outside_new_product_scope(self):
        p=self.plan('12-new-product-review');p['topic']['max_age_days']=60
        self.page(p,[{'shopId':'fixture','id':'old','openDate':'2025-01-20'}])
        r=topic_review.review(p,self.base,'launch')
        self.assertEqual(r['findings'][0]['new_product_scope'],'outside')
        self.assertEqual(r['findings'][0]['launch_date_raw'],'2025-01-20')
        p['topic']['max_age_days']=True
        with self.assertRaises(ValueError):topic_review.review(p,self.base,'launch')

    def test_installed_topic_cli_and_upgrade_from_ten(self):
        dest=self.base/'installed'
        subprocess.run([sys.executable,str(ROOT/'scripts/install.py'),'--skills-dir',str(dest)],check=True,capture_output=True)
        import shutil
        marker=dest/'.sellfox-install.json';state=json.loads(marker.read_text())
        for name in ('returns-loss','new-product-review'):
            shutil.rmtree(dest/name);state['skills'].remove(name)
        marker.write_text(json.dumps(state))
        subprocess.run([sys.executable,str(ROOT/'scripts/install.py'),'--skills-dir',str(dest),'--update'],check=True,capture_output=True)
        for folder,name,rows in [('11-returns-loss','returns-loss',[self.row()]),('12-new-product-review','new-product-review',[{'shopId':'fixture','id':'a','openDate':'2026-10-01','day7SaleNum':3,'day30SaleNum':3}])]:
            plan=self.plan(folder);self.page(plan,rows);path=self.base/(name+'.json');path.write_text(json.dumps(plan))
            cmd=[sys.executable,str(dest/name/'scripts/run_review.py'),'--plan',str(path),'--out-dir',str(self.base/name),'--keep-md']
            run=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(run.returncode,0,run.stderr)
            self.assertTrue((self.base/name/'report.html').is_file())
            self.assertTrue((self.base/name/'report.md').is_file())  # --keep-md 才额外留档 .md
            self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,1)
        # 默认（无 --keep-md）仅写 report.html，不产出 report.md
        default_out=self.base/'default-only'
        run=subprocess.run([sys.executable,str(dest/'returns-loss/scripts/run_review.py'),'--plan',str(self.base/'returns-loss.json'),'--out-dir',str(default_out)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertTrue((default_out/'report.html').is_file())
        self.assertFalse((default_out/'report.md').exists())

if __name__=='__main__':unittest.main()
