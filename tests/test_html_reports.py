"""HTML 报告出口测试：共享转换器子集、厂商化副本一致性、渲染冒烟（技能 10/13）。全部合成数据，零网络。"""
import contextlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL10 = ROOT / '10-sellfox-business' / 'scripts'
SHARED_HTML = ROOT / '_shared' / 'scripts' / 'report_html.py'
for path in (str(SKILL10),):
    if path not in sys.path:
        sys.path.insert(0, path)

import report_html as vendored_html          # 10-sellfox-business/scripts/report_html.py（厂商化副本）
import process_data
import topic_review

_spec = importlib.util.spec_from_file_location('report_html_shared', SHARED_HTML)
shared_html = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shared_html)

SAMPLE_MD = """# 退款退货损失分析

> ⚠️ 演示/样本数据指纹（MOCK-）：结果不作为经营结论。

## 概览

范围仅已读取记录，**不含**未验证字段；工具 `get_profit_report_by_msku`。

| 数据集 | 商品 | 观察结果 |
|---|---|---|
| current | fixture | 退款 -40 |

- 无序项 **加粗**
- 无序项 `code`

1. 有序项
2、中文序号项

[文档](https://example.com/docs?a=1)

---

```
<b>raw & tag</b>
```
"""


@contextlib.contextmanager
def argv(*args):
    old = sys.argv
    sys.argv = list(args)
    try:
        yield
    finally:
        sys.argv = old


class ConverterSubsetTest(unittest.TestCase):
    """共享转换器 Markdown 子集 → HTML。"""

    def convert(self, md):
        return shared_html.markdown_to_html(md)

    def test_heading_levels(self):
        html = self.convert('# 标题一\n### 标题三\n###### 标题六')
        self.assertIn('<h1>标题一</h1>', html)
        self.assertIn('<h3>标题三</h3>', html)
        self.assertIn('<h6>标题六</h6>', html)

    def test_table_header_and_body(self):
        md = '| 数据集 | 商品 |\n|---|---|\n| current | fixture |'
        html = self.convert(md)
        self.assertIn('<table><thead><tr><th>数据集</th><th>商品</th></tr></thead>'
                      '<tbody><tr><td>current</td><td>fixture</td></tr></tbody></table>', html)

    def test_unordered_and_ordered_lists(self):
        html = self.convert('- 甲\n- 乙')
        self.assertIn('<ul><li>甲</li><li>乙</li></ul>', html)
        html = self.convert('1. 甲\n2、 乙')
        self.assertIn('<ol><li>甲</li><li>乙</li></ol>', html)

    def test_bold_inline_code_and_escaping(self):
        html = self.convert('**加粗** 与 `行内码` 及 <script>alert(1)</script>')
        self.assertIn('<strong>加粗</strong>', html)
        self.assertIn('<code>行内码</code>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertNotIn('<script>', html)

    def test_blockquote_watermark_is_warning_bar(self):
        html = self.convert('> ⚠️ 演示/样本数据指纹（MOCK-）：结果不作为经营结论')
        self.assertIn('<blockquote><p>⚠️ 演示/样本数据指纹（MOCK-）：结果不作为经营结论</p></blockquote>', html)
        doc = shared_html.render_html_document('> 水印')
        self.assertIn('blockquote', doc)          # 引用块即警示条（水印落点）
        self.assertIn('#d46b08', doc)             # 警示条边框色

    def test_fenced_code_is_escaped(self):
        html = self.convert('```\n<b>raw & tag</b>\n```')
        self.assertIn('<pre><code>&lt;b&gt;raw &amp; tag&lt;/b&gt;</code></pre>', html)

    def test_link_becomes_anchor(self):
        html = self.convert('[文档](https://example.com/docs?a=1)')
        self.assertIn('<a href="https://example.com/docs?a=1">文档</a>', html)

    def test_orphan_table_row_fallback_terminates(self):
        """孤立表格行（无分隔行）必须被兜底消费，不能死循环（子进程限时防挂起回归）。"""
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / 'orphan.md'
            md_path.write_text('| 孤立 | 表格行 |\n正文段落\n', encoding='utf-8')
            run = subprocess.run([sys.executable, str(SHARED_HTML), str(md_path)],
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            html_path = md_path.with_suffix('.html')
            self.assertTrue(html_path.is_file())
            content = html_path.read_text(encoding='utf-8')
            self.assertNotIn('<table>', content)
            self.assertIn('<p>', content)
            self.assertIn('正文段落', content)


class ConsistencyTest(unittest.TestCase):
    def test_vendored_copy_matches_shared_output(self):
        """10 的厂商化副本与 _shared 版对同一段样本 md 输出必须逐字一致。"""
        self.assertEqual(vendored_html.markdown_to_html(SAMPLE_MD),
                         shared_html.markdown_to_html(SAMPLE_MD))
        self.assertEqual(vendored_html.render_html_document(SAMPLE_MD, title='一致性'),
                         shared_html.render_html_document(SAMPLE_MD, title='一致性'))

    def test_write_html_report_same_name_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / 'sub' / 'report.md'
            html_path = shared_html.write_html_report('# 标题 A\n\n正文\n', md_path)
            self.assertEqual(html_path, md_path.with_suffix('.html'))
            self.assertTrue(html_path.is_file())
            content = html_path.read_text(encoding='utf-8')
            self.assertTrue(content.startswith('<!doctype html>'))
            self.assertIn('<title>标题 A</title>', content)


class RenderSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_skill13_render_report_smoke_html(self):
        """13 的 render（合成 analysis）→ .html 产出且含 doctype。"""
        scripts = ROOT / '13-sales-profit-analysis' / 'scripts'
        boot_spec = importlib.util.spec_from_file_location('_bootstrap', scripts / '_bootstrap.py')
        boot = importlib.util.module_from_spec(boot_spec)
        boot_spec.loader.exec_module(boot)
        saved_boot = sys.modules.get('_bootstrap')
        sys.modules['_bootstrap'] = boot           # 避免与其他技能同名模块串包
        try:
            spec = importlib.util.spec_from_file_location('html_smoke_render_report', scripts / 'render_report.py')
            render_report = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(render_report)
        finally:
            if saved_boot is None:
                sys.modules.pop('_bootstrap', None)
            else:
                sys.modules['_bootstrap'] = saved_boot
        analysis = {
            'demo_fingerprint': ['MOCK-'],
            'currencies': ['USD'],
            'meta': {'caliber': 'settlement', 'granularity': 'MSKU', 'cost_layer': 'settlement',
                     'current': {'start': '2026-10-01', 'end': '2026-10-07'}},
            'periods': {
                'current': {'rows': 3, 'gp': 120.5, 'margin': 0.12,
                            'bridge': {'销售额': 500.0, '退款': -30.0},
                            'bridge_total': 470.0, 'residual': 0.5},
                'base': {'rows': 0, 'gp': None, 'margin': None, 'bridge': {}},
            },
            'delta': {'gp': 20.0, 'margin_pp': 1.2},
            'coverage': [],
        }
        md = render_report.render(analysis)
        html_path = shared_html.write_html_report(md, self.base / 'report.md')
        self.assertTrue(html_path.is_file())
        content = html_path.read_text(encoding='utf-8')
        self.assertTrue(content.startswith('<!doctype html>'))
        self.assertIn('<table>', content)
        self.assertIn('<blockquote>', content)   # 演示指纹水印 → 警示条
        self.assertIn('演示/样本数据指纹', content)

    def test_topic_review_main_writes_html_report(self):
        """10 的 topic render（11/12 的同一出口）→ report.md 留档 + report.html 主报告。"""
        plan = json.loads((ROOT / '11-returns-loss' / 'references' / 'plan-example.json').read_text(encoding='utf-8'))
        row = {'shopId': 'fixture', 'id': 'item-a', 'bizDay': '2026-10-02', 'currency': 'USD',
               'salesNum': 10, 'refundNum': 2, 'returnNum': 1, 'refundSales': -40}
        container = plan['rows_path'].split('.')[-1]
        (self.base / 'current.json').write_text(json.dumps(
            {'tool_id': plan['tool_id'], 'status': 'complete',
             'data': {container: [row]}, 'trace_id': 'synthetic-html',
             'coverage': {'complete': False}}), encoding='utf-8')
        (self.base / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
        out = self.base / 'review'
        with argv('run_review.py', '--plan', str(self.base / 'plan.json'), '--out-dir', str(out)):
            code = topic_review.main('returns')
        self.assertIn(code, (0, 2))
        self.assertTrue((out / 'report.md').is_file())
        html_path = out / 'report.html'
        self.assertTrue(html_path.is_file())
        content = html_path.read_text(encoding='utf-8')
        self.assertTrue(content.startswith('<!doctype html>'))
        self.assertIn('<title>退款退货损失分析</title>', content)
        self.assertIn('<table>', content)

    def test_process_data_main_writes_html_report(self):
        """10 的 process_data（Markdown 输出分支）→ report.html 主报告，JSON 输出不变。"""
        plan = {'version': 1, 'tool_id': 'fixture-tool', 'rows_path': 'data.rows',
                'group_by': ['shopId'], 'dedupe_by': ['shopId', 'bizDay'],
                'metrics': {'记录数': {'op': 'count', 'unit': 'count'}},
                'datasets': {'current': {'inputs': ['raw/page1.json']}}}
        (self.base / 'raw').mkdir()
        (self.base / 'raw' / 'page1.json').write_text(json.dumps(
            {'tool_id': 'fixture-tool', 'status': 'complete',
             'data': {'rows': [{'shopId': 'fixture', 'bizDay': '2026-10-01'}]},
             'trace_id': 'synthetic-html', 'coverage': {'complete': False}}), encoding='utf-8')
        (self.base / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
        out = self.base / 'processed'
        with argv('process_data.py', '--plan', str(self.base / 'plan.json'), '--out-dir', str(out)):
            code = process_data.main()
        self.assertEqual(code, 0)
        self.assertTrue((out / 'report.md').is_file())
        html_path = out / 'report.html'
        self.assertTrue(html_path.is_file())
        content = html_path.read_text(encoding='utf-8')
        self.assertTrue(content.startswith('<!doctype html>'))
        self.assertIn('<title>赛狐业务数据处理结果</title>', content)
        self.assertTrue((out / 'result.json').is_file())
        self.assertTrue((out / 'plan.json').is_file())


if __name__ == '__main__':
    unittest.main()
