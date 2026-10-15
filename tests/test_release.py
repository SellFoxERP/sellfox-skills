"""离线发布验收：在临时副本中运行，不读取商家凭证、不访问网络。"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SKILLS=sorted(p for p in ROOT.glob('[0-9][0-9]-*') if p.is_dir())

def envelope(rows, key='rows', status='complete', **extra):
    obj={'status':status,'data':{key:rows},'coverage':{'complete':True,'fetched_count':len(rows),'total_count':len(rows),'truncated':False},'warnings':[],'trace_id':'synthetic-release-test'}
    obj.update(extra)
    return obj

PRODUCT={'id':'fixture-listing','listingId':'fixture-listing','shopId':'SHOP-DEMO-1','asin':'B0DEMO00001','msku':'SKU-TEST','sku':'SKU-TEST','title':'测试产品','onlineStatus':'Inactive','quantity':0,'currency':'USD','rating':3.0,'ratingCount':10,'day7SaleNum':14,'day30SaleNum':60,'listingPrice':20,'inventoryManage':{'availableQuantity':0,'totalInventory':0,'inboundReceiving':0,'currency':'USD'}}
PROFIT={'shopId':'SHOP-DEMO-1','shopName':'演示店','msku':'SKU-TEST','asin':'B0DEMO00001','productSales':1000,'salesNum':50,'orderNum':40,'grossProfit':200,'grossProfitRate':20,'cpcCost':-100,'refundNum':2,'currency':'USD'}

class ReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='sellfox-test-')
        cls.root=Path(cls.tmp.name)/'source'
        shutil.copytree(ROOT,cls.root,ignore=shutil.ignore_patterns('_internal','dist','.git','__pycache__','out','logs','.venv'))
        cls.env=dict(os.environ)
        cls.env.pop('SELLFOX_SHARED_ROOT',None)
        cls.env['PYTHONIOENCODING']='utf-8'
        cls.env['SELLFOX_USER_CONFIG_DIR']=str(Path(cls.tmp.name)/'user-config')
        # 通过审计钩子阻断测试子进程网络，避免可选外发被意外开启。
        guard=Path(cls.tmp.name)/'guard';guard.mkdir()
        (guard/'sitecustomize.py').write_text("import sys\ndef guard(event,args):\n    if event in ('socket.connect','socket.getaddrinfo'):\n        raise RuntimeError('offline test: network forbidden')\nsys.addaudithook(guard)\n")
        cls.env['PYTHONPATH']=str(guard)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def skill(self,n):return next(self.root.glob(f'{n:02d}-*'))
    def run_script(self,skill,script,*args,expected=0):
        p=Path(skill)
        result=subprocess.run([sys.executable,str(p/'scripts'/script),*map(str,args)],cwd=p,env=self.env,text=True,capture_output=True,timeout=40)
        self.assertEqual(result.returncode,expected,result.stdout+'\n'+result.stderr)
        return result.stdout+result.stderr
    def raw(self,skill,name,obj):
        p=skill/'out/raw'/f'{name}.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
    def test_01_daily_report(self):
        p=self.skill(1)
        self.raw(p,'shops',envelope([{'id':'SHOP-DEMO-1','name':'演示店'}]))
        self.raw(p,'profit_shop',envelope([PROFIT],'reportList'))
        self.run_script(p,'run_daily_report.py','--date','2026-10-14')
        report=(p/'out/daily_report.md').read_text()
        self.assertIn('演示',report);self.assertIn('1,000',report);self.assertIn('降级',report)
    def test_02_inventory(self):
        p=self.skill(2);self.raw(p,'online_products_1',envelope([PRODUCT]))
        out=p/'out/inventory.md'
        self.run_script(p,'run_inventory_health.py','--date','2026-10-14','--out',out)
        report=out.read_text();self.assertIn('演示',report);self.assertIn('B0DEMO00001',report)
        self.assertTrue('断货' in report or '需人工核查' in report)
    def test_03_listing(self):
        p=self.skill(3);self.raw(p,'online_1',envelope([PRODUCT]))
        self.run_script(p,'fetch_listing_data.py','--out','out/data.json')
        self.run_script(p,'diagnose.py','--in','out/data.json','--out','out/problems.json')
        self.run_script(p,'render_report.py','--in','out/problems.json','--out','out/report.md')
        report=(p/'out/report.md').read_text();self.assertIn('B0DEMO00001',report);self.assertIn('不可售',report)
    def test_04_weekly(self):
        p=self.skill(4)
        for step in ('profit_shop_current','profit_shop_prev','profit_shop_prev_month','profit_shop_mtd','profit_shop_prev_year','profit_msku'):
            self.raw(p,step,envelope([PROFIT],'reportList'))
        self.run_script(p,'fetch_weekly_data.py','--anchor','2026-10-12','--out','out/data.json')
        self.run_script(p,'compute_metrics.py','--in','out/data.json','--out','out/computed.json')
        self.run_script(p,'render_report.py','--in','out/computed.json','--out','out/report.md')
        self.assertIn('演示',(p/'out/report.md').read_text())
        self.run_script(p,'test_regression.py')
    def test_05_profit(self):self.assertIn('全部通过',self.run_script(self.skill(5),'selfcheck.py'))
    def test_06_ad(self):self.assertIn('自检通过',self.run_script(self.skill(6),'optimize.py','selftest'))
    def test_07_governance(self):
        p=self.skill(7);self.raw(p,'custom_indices_1',envelope([]))
        output=self.run_script(p,'run_governance.py')
        self.assertTrue('人工' in output or '待确认' in output or '未确认' in output,output)
        self.assertFalse(list((p/'out').glob('execute_manifest*')))
    def test_08_launch(self):
        p=self.skill(8);self.raw(p,'online_product',envelope([PRODUCT]))
        self.run_script(p,'listing_launch.py','--search-content','B0DEMO00001','--name','测试产品','--out','out/package.md')
        report=(p/'out/package.md').read_text();self.assertIn('演示',report);self.assertIn('需人工',report)
    def test_09_voc(self):
        p=self.skill(9);self.raw(p,'online_products',envelope([PRODUCT]))
        self.run_script(p,'voc_insight.py','--days','7')
        report=(p/'out/voc-report.md').read_text();self.assertIn('演示',report)
        self.assertTrue('无原文' in report or '未接入' in report or '无评论' in report,report)
    def test_10_bulk_install_and_update(self):
        dest=Path(self.tmp.name)/'installed skills'
        self.run_script(self.root,'install.py','--skills-dir',dest)
        self.assertEqual(len(list(dest.glob('*/SKILL.md'))),9)
        marker=dest/'_sellfox_shared/config/reports.yaml';marker.write_text('reports: {daily_ops: {id: "TEST-REPORT"}}\n')
        out=dest/'daily-ops-report/out/keep.txt';out.parent.mkdir();out.write_text('local report')
        self.run_script(self.root,'install.py','--skills-dir',dest,expected=1)
        self.run_script(self.root,'install.py','--skills-dir',dest,'--update')
        self.assertIn('TEST-REPORT',marker.read_text());self.assertEqual(out.read_text(),'local report')
        entries=['run_daily_report.py','run_inventory_health.py','fetch_listing_data.py','fetch_weekly_data.py','run_profit_diagnosis.py','optimize.py','run_governance.py','listing_launch.py','voc_insight.py']
        for skill,entry in zip(SKILLS,entries):
            self.run_script(dest/skill.name[3:],entry,'--help')
    def test_11_unknown_status_not_consumed(self):
        sys.path.insert(0,str(self.root/'_shared/scripts'))
        from sellfox_offline import RawEnvelope
        for status in ('failed','unknown',''):
            env=RawEnvelope(Path('synthetic.json'),envelope([PRODUCT],status=status))
            self.assertEqual(env.rows(),[],status)
    def test_12_governance_incomplete_blocked(self):
        p=self.skill(7)
        code='''import index_governor as g\nfrom pathlib import Path\nimport json,tempfile\nfor status in ('partial','unknown','failed','complete'):\n with tempfile.TemporaryDirectory() as d:\n  p=Path(d)/'custom_indices_1.json'\n  p.write_text(json.dumps({'status':status,'data':{'rows':[]},'coverage':{'complete':False,'truncated':False}}))\n  assert not g.load_existing_indices(Path(d),{}).reliable,status\n'''
        self.run_code(p,code)
    def test_13_voc_stops_on_failed_step(self):
        code="""import argparse
from unittest.mock import patch
import voc_insight as v
args=argparse.Namespace(days=7,report_id=None,reviews_json=None,adapter_file=None,confirm_external=False,llm_themes=None,with_optimization=False,feishu=False)
with patch.object(v,'check_raw',return_value=0), patch.object(v,'_run',return_value=2) as run:
 assert v.run(args)==2
 assert run.call_count==1
"""
        self.run_code(self.skill(9),code)
    def test_14_voc_independent_evidence(self):
        code="""import analyze_themes as a
reviews=[{'id':str(i),'reviewer_id':str(i),'text':'broken item','rating':1} for i in range(3)]
def classify(rows):
 return a._classify(a._to_records(a._cluster(rows,{'quality':['broken']},{}),{}),{})[0]
assert classify(reviews)['level']=='pattern'
assert classify([reviews[0]]*3)['level']=='mention'
assert a._negative_threshold([{'id':'missing','text':'broken'}],3)==[]
"""
        self.run_code(self.skill(9),code)
    def test_15_currency_requires_source_and_date(self):
        code="""from estimate_pricing import _convert
from _common import Notes
assert _convert(70,'CNY','USD',{'rate':7,'base':'USD','quote':'CNY','source':'synthetic','as_of':'2026-10-15'},Notes(),'test')==10
for cfg in ({'rate':0},{'rate':7},{'rate':float('nan')}):
 cfg.update(base='USD',quote='CNY')
 assert _convert(70,'CNY','USD',cfg,Notes(),'test') is None
assert _convert(70,None,'USD',{},Notes(),'test') is None
"""
        self.run_code(self.skill(8),code)
    def test_16_unknown_validator_fails(self):
        p=self.skill(7);self.raw(p,'unknown',envelope([PRODUCT],status='unknown'))
        self.run_script(self.root/'_shared','validate_raw.py','--dir',p/'out/raw','--step','unknown:rows',expected=2)
    def test_17_release_archive_excludes_private_files(self):
        self.run_script(self.root,'build_release.py')
        import zipfile
        with zipfile.ZipFile(self.root/'dist/sellfox-skills.zip') as z:
            paths=z.namelist()
            self.assertEqual(sum(n.endswith('/SKILL.md') for n in paths),9)
            for n in paths:
                self.assertFalse({'out','logs','_internal','__pycache__'} & set(Path(n).parts),n)
            self.assertIn('sellfox-skills/scripts/install.py',paths)
            self.assertEqual(sum(n.endswith('/_shared/config/reports.yaml') for n in paths),1)
    def test_22_marketplace_bundle(self):
        import zipfile
        self.run_script(self.root,'build_release.py')
        dest=Path(self.tmp.name)/'market plugin cache'
        with zipfile.ZipFile(self.root/'dist/sellfox-skills.zip') as z:
            z.extractall(dest)
        root=dest/'sellfox-skills'
        market=json.loads((root/'.codebuddy-plugin/marketplace.json').read_text())
        self.assertEqual(len(market['plugins']),1)
        entry=market['plugins'][0]
        plugin_root=(root/entry['source']).resolve()
        self.assertEqual(plugin_root,root.resolve())
        plugin=json.loads((plugin_root/'.codebuddy-plugin/plugin.json').read_text())
        self.assertEqual(entry['name'],plugin['name'])
        self.assertEqual(entry['version'],plugin['version'])
        self.assertEqual(len(set(plugin['skills'])),9)
        self.assertEqual(set(plugin['skills']),{'./'+p.relative_to(self.root).as_posix()+'/SKILL.md' for p in sorted(self.root.glob('0[1-9]-*'))})
        entries=['run_daily_report.py','run_inventory_health.py','fetch_listing_data.py','fetch_weekly_data.py','run_profit_diagnosis.py','optimize.py','run_governance.py','listing_launch.py','voc_insight.py']
        for path,script in zip(plugin['skills'],entries):
            skill=(plugin_root/path).parent
            self.assertTrue((skill/'SKILL.md').is_file())
            self.run_script(skill,script,'--help')
        self.assertTrue((plugin_root/'_shared/scripts/config_loader.py').is_file())
        # 在模拟插件缓存路径执行实际日报，验证不依赖源码绝对路径。
        skill=(plugin_root/plugin['skills'][0]).parent
        self.raw(skill,'shops',envelope([{'id':'SHOP-DEMO-1','name':'演示店'}]))
        self.raw(skill,'profit_shop',envelope([PROFIT],'reportList'))
        self.run_script(skill,'run_daily_report.py','--date','2026-10-14')
        self.assertIn('1,000',(skill/'out/daily_report.md').read_text())

    def test_18_voc_default_keywords_without_pyyaml(self):
        code="""from pathlib import Path
import voc_common as v
from config_loader import _mini_yaml_load
cfg=_mini_yaml_load(Path('config/voc.yaml').read_text())
compiled=v.compile_theme_keywords(cfg['themes'])
assert '破损' in compiled['quality']
assert v.match_themes('the item is broken',compiled)==['quality']
assert v.match_themes('尺寸偏小',compiled)==['sizing']
assert v.match_themes('a neutral comment',compiled)==['other']
"""
        self.run_code(self.skill(9),code)
    def test_19_daily_unknown_profit_is_not_revenue(self):
        p=self.skill(1)
        self.raw(p,'shops',envelope([{'id':'SHOP-DEMO-1','name':'演示店'}]))
        bad=dict(PROFIT,productSales=987654321)
        self.raw(p,'profit_shop',envelope([bad],'reportList',status='unknown'))
        output=self.run_script(p,'run_daily_report.py','--date','2026-10-14',expected=2)
        bundle=json.loads((p/'out/bundle.json').read_text())
        self.assertIsNone(bundle['data']['store_profit'])
        self.assertNotIn('完成。日报',output)
        self.assertIn('不可用',output)
    def test_20_inventory_gap_and_unknown(self):
        code="""from types import SimpleNamespace
from replenishment import compute
class Loader:
 def section(self,*args):return {}
 def resolve_lead_time(self,**kwargs):return SimpleNamespace(days=30,source='本次填写',layer='by_sku')
base={'msku':'fixture','thirty_sale_num':300,'available_days':10,'inbound_total':50}
r=compute([base],loader=Loader(),skill_cfg={}).rows[0]
assert r.urgency=='P1' and r.velocity==10 and r.gap==150 and r.suggested==150
r=compute([dict(base,inbound_total=300)],loader=Loader(),skill_cfg={}).rows[0]
assert r.gap==0 and r.suggested==0 and r.urgency!='P1'
r=compute([dict(base,inbound_total=None)],loader=Loader(),skill_cfg={}).rows[0]
assert r.urgency=='未知' and r.suggested is None
"""
        self.run_code(self.skill(2),code)
    def test_21_governance_add_update_and_missing_id(self):
        code="""import index_governor as g
wanted=[g.DesiredIndex(name='new',formula='a/b',formula_status='verified'),g.DesiredIndex(name='change',formula='a/c',formula_status='verified'),g.DesiredIndex(name='pending')]
plan=g.build_plan([{'id':'fixture-id','indexName':'change','indexFormula':'a/b'}],wanted)
assert len(plan.adds)==1 and len(plan.updates)==1 and len(plan.pending)==1
proposal=g.build_proposal(plan)
assert proposal.dry_run and len(proposal.items)==2
assert proposal.items[1].before=='a/b' and proposal.items[1].after=='a/c'
assert proposal.items[1].target_id=='fixture-id'
unsafe=g.build_plan([{'indexName':'change','indexFormula':'a/b'}],wanted)
assert len(unsafe.updates)==0
"""
        self.run_code(self.skill(7),code)
    def run_code(self,p,code):
        result=subprocess.run([sys.executable,'-c',"import sys;sys.path.insert(0,'scripts');"+code],cwd=p,env=self.env,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main(verbosity=2)
