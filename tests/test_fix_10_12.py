"""Fix verification for skills 10/11/12 (live eval 2026-10-16 findings); synthetic data only."""
import ast
import importlib.util
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT/'10-sellfox-business/scripts'
spec = importlib.util.spec_from_file_location('fix_business', SCRIPTS/'process_data.py')
business = importlib.util.module_from_spec(spec)
spec.loader.exec_module(business)
sys.path.insert(0, str(SCRIPTS))
import topic_review


class FixTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def envelope(self, rows, file='page.json', tool_id='fixture-tool', **changes):
        obj = {'tool_id': tool_id, 'status': 'complete', 'trace_id': 'synthetic-fix',
               'coverage': {'complete': False}, 'data': {'rows': rows}}
        obj.update(changes)
        (self.base/file).write_text(json.dumps(obj, ensure_ascii=False))
        return obj

    def plan(self, **changes):
        obj = {'version': 1, 'tool_id': 'fixture-tool', 'rows_path': 'data.rows',
               'group_by': ['id'], 'dedupe_by': ['id'], 'metrics': {'units': {'op': 'count', 'unit': 'count'}},
               'datasets': {'current': {'inputs': ['page.json']}}}
        obj.update(changes)
        return obj

    def test_fingerprint_scan_hits_nested_values_only(self):
        payload = {'request': {'params': {'q': 'B0DEMO001'}}, 'response': {'data': {'rows': [
            {'sku': 'MOCK-SKU-1', 'shop': 'SHOP-DEMO-1001', 'img': 'https://mock.example.com/a.png'},
            {'title': '演示店铺商品', 'n': 3, 'ok': True}]}}}
        self.assertEqual(business.demo_fingerprints_in(payload),
                         ['MOCK-', 'mock.example.com', 'SHOP-DEMO-', 'B0DEMO', '演示'])
        self.assertEqual(business.demo_fingerprints_in({'data': {'rows': [{'sku': 'REAL-SKU-9', 'n': 12}]}}), [])
        self.assertEqual(business.demo_fingerprints_in('MOCK-plain-string'), ['MOCK-'])
        self.assertEqual(business.DEMO_FINGERPRINTS[0], 'MOCK-')
        self.assertIn('DPARENT', business.DEMO_FINGERPRINTS)

    def test_demo_fingerprint_recorded_and_reported_in_production(self):
        self.envelope([{'id': 'a', 'shop': 'MOCK-SHOP-1'}])
        result = business.process(self.plan(), self.base)
        self.assertEqual(result['demo_fingerprint'], ['MOCK-'])
        self.assertEqual(result['datasets']['current']['demo_fingerprint'], ['MOCK-'])
        self.assertEqual(result['datasets']['current']['pages'][0]['demo_fingerprint'], ['MOCK-'])
        report = business.render(result)
        self.assertIn('演示/样本数据指纹（MOCK-）：结果不作为经营结论', report)
        self.envelope([{'id': 'a', 'shop': 'real-shop'}], file='clean.json')
        clean = business.process(self.plan(datasets={'current': {'inputs': ['clean.json']}}), self.base)
        self.assertEqual(clean['demo_fingerprint'], [])
        self.assertNotIn('演示/样本数据指纹', business.render(clean))

    def test_topic_report_carries_demo_watermark_in_both_modes(self):
        plan = {'version': 1, 'tool_id': 'get_online_product_page_list', 'rows_path': 'data.rows',
                'group_by': ['id'], 'dedupe_by': ['id'],
                'metrics': {'units': {'op': 'count', 'unit': 'count'}},
                'topic': {'scope_note': 'synthetic launch scope', 'as_of': '2026-10-16', 'launch_date_field': 'openDate'},
                'datasets': {'snapshot': {'inputs': ['page.json']}}}
        self.envelope([{'id': 'a', 'openDate': '2026-10-01'}], tool_id='get_online_product_page_list')
        result = topic_review.review(plan, self.base, 'launch')
        self.assertEqual(result['demo_fingerprint'], [])
        self.envelope([{'id': 'a', 'openDate': '2026-10-01', 'sku': 'MOCK-SKU-1'}], tool_id='get_online_product_page_list')
        result = topic_review.review(plan, self.base, 'launch')
        self.assertEqual(result['demo_fingerprint'], ['MOCK-'])
        self.assertIn('演示/样本数据指纹（MOCK-）：结果不作为经营结论', topic_review.render(result))
        plan['mode'] = 'sample'
        with self.assertRaises(ValueError):
            topic_review.review(plan, self.base, 'launch')
        plan['topic']['sample_reason'] = 'Synthetic demonstration with authorization note'
        sample = topic_review.review(plan, self.base, 'launch')
        report = topic_review.render(sample)
        self.assertIn('返回样本演示：不能作为当前经营结论', report)
        self.assertIn('演示/样本数据指纹（MOCK-）：结果不作为经营结论', report)

    def test_excluded_dates_visible_with_min_max_and_samples(self):
        self.envelope([{'id': 'ok', 'date': '2026-10-14'}, {'id': 'old', 'date': 20250115},
                       {'id': 'older', 'date': '2024-12-31'}, {'id': 'none', 'date': None}])
        plan = self.plan(date_field='date', metrics={'units': {'op': 'count', 'unit': 'count'}},
                         datasets={'current': {'inputs': ['page.json'], 'period': {'start': '2026-10-14', 'end': '2026-10-14'}}})
        result = business.process(plan, self.base)
        ds = result['datasets']['current']
        self.assertEqual(ds['excluded']['date'], 3)
        self.assertEqual(ds['excluded_dates'], {'field': 'date', 'count': 3, 'min': '2024-12-31',
                                                'max': '2025-01-15', 'samples': ['20250115', '2024-12-31', '缺失']})
        report = business.render(result)
        self.assertIn('有 3 行日期（字段 date）落在窗口外：2024-12-31~2025-01-15', report)
        self.assertIn('样例 20250115、2024-12-31、缺失', report)
        plan['mode'] = 'sample'
        with self.assertRaises(ValueError):
            business.process(plan, self.base)  # sample 无授权依据 → 通用处理器同样拒绝
        plan['sample_reason'] = 'Synthetic demonstration authorized for this test'
        ds = business.process(plan, self.base)['datasets']['current']
        self.assertIsNone(ds['excluded_dates'])
        self.assertEqual(ds['excluded']['date'], 0)

    def test_snapshot_compare_without_periods_warns(self):
        self.envelope([{'id': 'a', 'n': 5}], file='a.json')
        self.envelope([{'id': 'a', 'n': 5}], file='b.json')
        plan = self.plan(datasets={'current': {'inputs': ['a.json']}, 'baseline': {'inputs': ['b.json']}},
                         compare={'current': 'current', 'baseline': 'baseline'})
        result = business.process(plan, self.base)
        self.assertEqual(result['comparison']['snapshot_warning'],
                         '快照对比（无期间信息）：差异为 0 不代表无变化，需核对两窗口是否同源。')
        self.assertIn('快照对比（无期间信息）', business.render(result))
        plan['date_field'] = 'date'
        for name in ('current', 'baseline'):
            plan['datasets'][name]['period'] = {'start': '2026-10-14', 'end': '2026-10-14'}
        self.envelope([{'id': 'a', 'n': 5, 'date': '2026-10-14'}], file='a.json')
        self.envelope([{'id': 'a', 'n': 5, 'date': '2026-10-14'}], file='b.json')
        comparison = business.process(plan, self.base)['comparison']
        self.assertIsNone(comparison.get('snapshot_warning'))
        # One side missing period metadata is still a snapshot compare.
        plan['datasets']['baseline'].pop('period')
        self.assertIn('快照对比（无期间信息）', business.process(plan, self.base)['comparison']['snapshot_warning'])

    def test_future_launch_date_null_with_raw_preserved(self):
        plan = {'version': 1, 'tool_id': 'get_online_product_page_list', 'rows_path': 'data.rows',
                'group_by': ['id'], 'dedupe_by': ['id'],
                'metrics': {'units': {'op': 'count', 'unit': 'count'}},
                'topic': {'scope_note': 'synthetic', 'as_of': '2026-10-16', 'launch_date_field': 'openDate'},
                'datasets': {'snapshot': {'inputs': ['page.json']}}}
        self.envelope([{'id': 'future', 'openDate': '2027-06-01'}, {'id': 'past', 'openDate': '2026-10-10'}], tool_id='get_online_product_page_list')
        findings = {f['product']['id']: f for f in topic_review.review(plan, self.base, 'launch')['findings']}
        self.assertIsNone(findings['future']['launch_date'])
        self.assertEqual(findings['future']['launch_date_raw'], '2027-06-01')
        self.assertIsNone(findings['future']['age_days'])
        self.assertEqual(findings['future']['new_product_scope'], 'unknown')
        self.assertIn('上架日期为未来值，未确认', findings['future']['note'])
        self.assertEqual(findings['past']['launch_date'], '2026-10-10')
        self.assertEqual(findings['past']['age_days'], 7)
        self.assertNotIn('上架日期为未来值', findings['past']['note'])

    def test_skill_scripts_parse_under_python_39_grammar(self):
        for script in (SCRIPTS/'process_data.py', SCRIPTS/'topic_review.py', SCRIPTS/'scenarios.py',
                       ROOT/'11-returns-loss/scripts/run_review.py', ROOT/'12-new-product-review/scripts/run_review.py'):
            ast.parse(script.read_text(encoding='utf-8'), filename=str(script), feature_version=(3, 9))


if __name__ == '__main__':
    unittest.main()
