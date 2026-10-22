#!/usr/bin/env python3
"""Offline MCP data processing. Python 3.10+, standard library only, no credentials."""
from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys

try:
    from report_html import write_html_report
except ImportError:  # 以任意模块名 spec 加载本文件时，按文件位置定位同目录厂商化副本
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('report_html', Path(__file__).resolve().with_name('report_html.py'))
    _rh = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_rh)
    write_html_report = _rh.write_html_report


class PlanError(ValueError):
    pass


# Demo/sample data fingerprints. Kept in sync with DEMO_FINGERPRINTS in
# _shared/scripts/sellfox_offline.py (single source: the shared module).
# This skill is deliberately standalone with zero _shared imports, so the
# list is duplicated here on purpose — update both places together.
DEMO_FINGERPRINTS = ('MOCK-', 'mock.example.com', 'demo.example.com', 'SHOP-DEMO-', 'B0DEMO', 'DPARENT', '演示')


def demo_fingerprints_in(value):
    """Collect demo fingerprints hit by any recursive string value (canonical order, deduped)."""
    hit = set()

    def walk(node):
        if isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, str):
            for fingerprint in DEMO_FINGERPRINTS:
                if fingerprint in node:
                    hit.add(fingerprint)

    walk(value)
    return [fingerprint for fingerprint in DEMO_FINGERPRINTS if fingerprint in hit]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), parse_float=Decimal)


def dump(value):
    # Decimal values remain exact strings rather than silently losing precision.
    return json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False)


def at(value, path):
    if not isinstance(path, str) or not path:
        return None
    for key in path.split('.'):
        if isinstance(value, list) and key.isdigit():
            index = int(key)
            if index >= len(value):
                return None
            value = value[index]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    return value


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        result = Decimal(str(value).strip())
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def day(value):
    if isinstance(value, bool) or value is None:
        return None
    value = str(value)
    try:
        if re.fullmatch(r'\d{8}', value):
            return datetime.strptime(value, '%Y%m%d').date()
        # No timezone/date truncation assumptions for timestamps.
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value)
    except ValueError:
        pass
    return None


def signature(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False)


def unwrap(raw):
    request = raw.get('request') if isinstance(raw, dict) else None
    value = raw.get('response', raw) if isinstance(raw, dict) else raw
    if isinstance(value, dict) and 'structuredContent' in value:
        value = value['structuredContent']
    elif isinstance(value, dict) and 'content' in value and 'status' not in value:
        blocks = [x for x in value['content'] if isinstance(x, dict) and x.get('type') == 'text']
        if len(blocks) != 1:
            raise ValueError('MCP text 包装必须包含唯一业务 JSON')
        value = json.loads(blocks[0]['text'], parse_float=Decimal)
    if not isinstance(value, dict):
        raise ValueError('业务信封必须为对象')
    return request, value


def sample_reason_of(plan):
    """sample 模式授权依据：顶层 sample_reason，或 topic 链的 topic.sample_reason（同语义）。"""
    reason = plan.get('sample_reason')
    if not (isinstance(reason, str) and reason.strip()):
        topic = plan.get('topic')
        reason = topic.get('sample_reason') if isinstance(topic, dict) else None
    return reason if isinstance(reason, str) and reason.strip() else None


def validate(plan):
    if not isinstance(plan, dict) or plan.get('version') != 1:
        raise PlanError('处理计划必须为 version=1 的对象')
    for key in ('tool_id', 'rows_path'):
        if not isinstance(plan.get(key), str) or not plan[key]:
            raise PlanError(f'需要显式声明 {key}')
    if plan.get('row_mode', 'list') not in ('list', 'object'):
        raise PlanError('row_mode 只支持 list/object')
    for key in ('group_by', 'dedupe_by'):
        if not isinstance(plan.get(key), list) or any(not isinstance(x, str) or not x for x in plan[key]):
            raise PlanError(f'{key} 必须显式为字段路径数组（允许空数组）')
    metrics = plan.get('metrics')
    if not isinstance(metrics, dict) or not metrics:
        raise PlanError('需要至少一项 metrics')
    for name, metric in metrics.items():
        if not isinstance(metric, dict) or metric.get('op') not in ('sum', 'count', 'ratio'):
            raise PlanError(f'{name}: op 只支持 sum/count/ratio')
        op = metric['op']
        units = {'sum': ('money', 'number', 'count'), 'count': ('count',), 'ratio': ('ratio', 'percent')}
        if metric.get('unit') not in units[op]:
            raise PlanError(f'{name}: 需要明确且匹配操作的 unit')
        fields = ('field',) if op == 'sum' else ('numerator', 'denominator') if op == 'ratio' else ()
        if any(not isinstance(metric.get(k), str) or not metric[k] for k in fields):
            raise PlanError(f'{name}: 缺少字段路径')
        if op == 'ratio' and not isinstance(metric.get('currency_sensitive'), bool):
            raise PlanError(f'{name}: 比率须声明 currency_sensitive')
    if any(m['unit'] == 'money' or m.get('currency_sensitive') for m in metrics.values()):
        if not isinstance(plan.get('currency_field'), str) or not plan['currency_field']:
            raise PlanError('金额/货币比率必须声明 currency_field，禁止隐式币种')
    if plan.get('mode', 'production') not in ('production', 'sample'):
        raise PlanError('mode 只支持 production/sample')
    if plan.get('mode', 'production') == 'sample' and not sample_reason_of(plan):
        raise PlanError('sample 模式必须提供 sample_reason（计划顶层字段，或 CLI --sample-reason；'
                        'topic 链用 topic.sample_reason），说明用户授权与样本限制；'
                        '未经用户明确演示授权请用 mode=production')
    datasets = plan.get('datasets')
    if not isinstance(datasets, dict) or not datasets:
        raise PlanError('datasets 必须为非空对象')
    for name, ds in datasets.items():
        if not isinstance(ds, dict) or not isinstance(ds.get('inputs'), list) or not ds['inputs']:
            raise PlanError(f'{name}: 需要 inputs 文件数组')
        if any(not isinstance(x, str) or not x for x in ds['inputs']):
            raise PlanError(f'{name}: inputs 必须为路径字符串')
        if ds.get('period') is not None:
            period = ds['period']
            if not isinstance(period, dict) or not day(period.get('start')) or not day(period.get('end')) or day(period['start']) > day(period['end']):
                raise PlanError(f'{name}: period 无效')
            if not plan.get('date_field'):
                raise PlanError('有统计窗口时须声明 date_field；缺日期不能认证窗口')
        if not isinstance(ds.get('request_match', {}), dict):
            raise PlanError('request_match 必须为对象')
    compare = plan.get('compare')
    if compare is not None:
        if not isinstance(compare, dict) or any(compare.get(k) not in datasets for k in ('current', 'baseline')) or compare['current'] == compare['baseline']:
            raise PlanError('compare 必须引用两个不同 dataset')
    if not isinstance(plan.get('filters', {}), dict) or any(not isinstance(v, list) or not v for v in plan.get('filters', {}).values()):
        raise PlanError('filters 必须为 字段路径:允许值数组')


def load_rows(plan, dataset, base):
    pages, records, issues = [], [], []
    seen_pages = set()
    dataset_fingerprints = []
    for file in dataset['inputs']:
        page = {'file': file, 'accepted': False}
        pages.append(page)
        try:
            document = read_json(base / file)
            fingerprints = demo_fingerprints_in(document)
            if fingerprints:
                page['demo_fingerprint'] = fingerprints
                for fingerprint in fingerprints:
                    if fingerprint not in dataset_fingerprints:
                        dataset_fingerprints.append(fingerprint)
            request, env = unwrap(document)
            page.update({k: env.get(k) for k in ('tool_id', 'trace_id', 'status', 'coverage', 'pagination', 'warnings', 'output_validation', 'error_code', 'scope', 'contract_version', 'period', 'currency', 'upstream', 'message', 'field_errors', 'retryable', 'suggested_action')})
            if isinstance(request, dict):
                page['request_params'] = request.get('params')
                page['request_tool_id'] = request.get('tool_id')
            if env.get('status') not in ('complete', 'partial') or env.get('error_code'):
                raise ValueError('上游调用不可用：' + str(env.get('error_code') or env.get('status')))
            if env.get('tool_id') != plan['tool_id']:
                raise ValueError('工具不匹配：仅接受指定工具的数据')
            if plan.get('contract_version') and env.get('contract_version') != plan['contract_version']:
                raise ValueError('契约版本不匹配')
            for key, expected in dataset.get('request_match', {}).items():
                if at(request, 'params.' + key) != expected:
                    raise ValueError(f'请求范围不匹配或缺 request: {key}')
            rows = at(env, plan['rows_path'])
            if plan.get('row_mode', 'list') == 'object':
                rows = [rows] if isinstance(rows, dict) else None
            if not isinstance(rows, list):
                raise ValueError('rows_path 未指向约定的列表/单对象')
            fingerprint = signature(rows)
            if rows and fingerprint in seen_pages:
                page['duplicate_page'] = True
                issues.append(f'{file}: 疑似重复页未再次计入，需核对分页依据')
                continue
            seen_pages.add(fingerprint)
            page['accepted'] = True
            page['row_count'] = len(rows)
            for row_no, row in enumerate(rows, 1):
                if not isinstance(row, dict):
                    issues.append(f'{file}:{row_no}: 行不是对象，已排除')
                    continue
                records.append({'row': row, 'sources': [{'file': file, 'row': row_no, 'trace_id': env.get('trace_id')}]})
        except (ValueError, OSError, TypeError, KeyError) as exc:
            page['rejection'] = str(exc)
            issues.append(f'{file}: {exc}')
    if not plan['dedupe_by']:
        issues.append('未指定业务去重键；仅去除完全重复页，不认证跨页无重叠')
    selected, seen, conflicts = [], {}, set()
    excluded = {'filter': 0, 'date': 0, 'missing_key': 0, 'conflict': 0, 'duplicate': 0}
    out_of_window = []
    for record in records:
        row = record['row']
        if any(at(row, path) not in allowed for path, allowed in plan.get('filters', {}).items()):
            excluded['filter'] += 1
            continue
        period = dataset.get('period')
        if period:
            raw_date = at(row, plan['date_field'])
            actual = day(raw_date)
            if actual is None or not day(period['start']) <= actual <= day(period['end']):
                if plan.get('mode') == 'sample':
                    issues.append(f"{record['sources'][0]['file']}:{record['sources'][0]['row']}: 日期不匹配，仅作返回样本")
                else:
                    excluded['date'] += 1
                    out_of_window.append((raw_date, actual))
                    continue
        if not plan['dedupe_by']:
            selected.append(record)
            continue
        keys = [at(row, key) for key in plan['dedupe_by']]
        if any(x is None or isinstance(x, (dict, list)) or x == '' for x in keys):
            excluded['missing_key'] += 1
            continue
        key = signature(keys)
        if key in seen:
            previous = seen[key]
            previous['sources'].extend(record['sources'])
            if previous['row'] != row:
                conflicts.add(key)
            else:
                excluded['duplicate'] += 1
        else:
            seen[key] = record
    if plan['dedupe_by']:
        selected = [record for key, record in seen.items() if key not in conflicts]
        excluded['conflict'] = len(conflicts)
    if any(excluded.values()):
        issues.append('排除/去重计数：' + signature(excluded))
    excluded_dates = None
    if excluded['date']:
        # Out-of-window rows keep their real date values visible instead of a bare count.
        parsed = sorted({value for _, value in out_of_window if value is not None})
        samples = []
        for value, _ in out_of_window:
            text = '缺失' if value is None else str(value)
            if text not in samples:
                samples.append(text)
            if len(samples) == 3:
                break
        excluded_dates = {'field': plan['date_field'], 'count': excluded['date'],
                          'min': str(parsed[0]) if parsed else None,
                          'max': str(parsed[-1]) if parsed else None,
                          'samples': samples}
        issues.append('有 ' + str(excluded['date']) + ' 行日期（字段 ' + str(plan['date_field'])
                      + '）落在窗口外：' + (str(parsed[0]) if parsed else '未知')
                      + '~' + (str(parsed[-1]) if parsed else '未知')
                      + '，样例 ' + '、'.join(samples) + '。')
    # Full dataset coverage requires contract-specific proof outside this generic processor.
    return selected, pages, issues, excluded, excluded_dates, dataset_fingerprints


def aggregate(plan, records):
    groups = {}
    for record in records:
        row = record['row']
        dimensions = {field: at(row, field) for field in plan['group_by']}
        currency = at(row, plan.get('currency_field'))
        if plan.get('currency_field'):
            currency = currency.strip().upper() if isinstance(currency, str) and currency.strip() else None
            dimensions[plan['currency_field']] = currency
        if any(isinstance(v, (list, dict)) for v in dimensions.values()):
            raise PlanError('分组字段包含数组/对象；请先明确业务粒度，不能隐式拆分')
        key = signature(dimensions)
        group = groups.setdefault(key, {'dimensions': dimensions, 'rows': [], 'currency': currency})
        group['rows'].append(row)
    result = []
    for group in groups.values():
        rows, metrics = group['rows'], {}
        for name, metric in plan['metrics'].items():
            op, valid, missing = metric['op'], 0, 0
            total, denominator = Decimal(0), Decimal(0)
            for row in rows:
                n = Decimal(1) if op == 'count' else number(at(row, metric.get('field') if op == 'sum' else metric['numerator']))
                d = number(at(row, metric['denominator'])) if op == 'ratio' else Decimal(1)
                if n is None or d is None:
                    missing += 1
                else:
                    valid += 1
                    total += n
                    denominator += d
            unknown_currency = (metric['unit'] == 'money' or metric.get('currency_sensitive')) and group['currency'] is None
            known = None
            if valid and not unknown_currency:
                if op != 'ratio':
                    known = total
                elif denominator != 0:
                    known = total / denominator * (100 if metric['unit'] == 'percent' else 1)
            metrics[name] = {'value': known if not missing else None, 'known_value': known,
                             'valid_rows': valid, 'missing_rows': missing, 'unit': metric['unit'],
                             'status': 'unknown_currency' if unknown_currency else 'missing_values' if missing else 'zero_denominator' if op == 'ratio' and denominator == 0 else 'ok'}
        result.append({'dimensions': group['dimensions'], 'row_count': len(rows), 'metrics': metrics})
    return result


def compare_groups(plan, datasets):
    spec = plan.get('compare')
    if not spec:
        return None
    left, right = datasets[spec['current']], datasets[spec['baseline']]
    lm = {signature(g['dimensions']): g for g in left['groups']}
    rm = {signature(g['dimensions']): g for g in right['groups']}
    output = []
    for key in sorted(lm.keys() | rm.keys()):
        current, baseline = lm.get(key), rm.get(key)
        metrics = {}
        for name, rule in plan['metrics'].items():
            a = current['metrics'][name]['value'] if current else None
            b = baseline['metrics'][name]['value'] if baseline else None
            metrics[name] = {'current': a, 'baseline': b,
                             'difference': a - b if a is not None and b is not None else None,
                             'change_percent': (a - b) / abs(b) * 100 if a is not None and b not in (None, 0) else None,
                             'difference_unit': 'percentage_points' if rule['unit'] == 'percent' else rule['unit']}
        output.append({'dimensions': (current or baseline)['dimensions'], 'metrics': metrics})
    result = {'current': spec['current'], 'baseline': spec['baseline'],
              'scope': 'observed_rows_only', 'groups': output,
              'note': '仅对已取有效数据比较；缺基期组或零分母不算增长率，不能据此认定真实业务变化。'}
    if not (left.get('period') and right.get('period')):
        # Snapshot compare without verified periods: zero difference is not evidence of no change.
        result['snapshot_warning'] = '快照对比（无期间信息）：差异为 0 不代表无变化，需核对两窗口是否同源。'
    return result


def process(plan, base):
    validate(plan)
    datasets = {}
    for name, dataset in plan['datasets'].items():
        records, pages, issues, excluded, excluded_dates, fingerprints = load_rows(plan, dataset, base)
        groups = aggregate(plan, records)
        incomplete = any(not p['accepted'] or p.get('status') == 'partial' for p in pages)
        incomplete = incomplete or any(excluded[k] for k in ('date', 'missing_key', 'conflict'))
        empty = bool(pages) and all(p['accepted'] and p.get('row_count') == 0 for p in pages)
        state = ('partial' if incomplete else 'usable') if records else ('empty' if empty else 'unavailable')
        datasets[name] = {'status': state,
                          'period': dataset.get('period'), 'request_match': dataset.get('request_match'),
                          'coverage': 'not_certified', 'pages': pages, 'issues': issues,
                          'excluded': excluded, 'excluded_dates': excluded_dates,
                          'demo_fingerprint': [f for f in DEMO_FINGERPRINTS if f in fingerprints],
                          'accepted_rows': len(records), 'records': records, 'groups': groups}
    demo_fingerprint = [f for f in DEMO_FINGERPRINTS
                        if any(f in ds['demo_fingerprint'] for ds in datasets.values())]
    return {'version': 1, 'mode': plan.get('mode', 'production'), 'tool_id': plan['tool_id'],
            'context': plan.get('context', {}), 'metrics': plan['metrics'], 'datasets': datasets,
            'sample_reason': sample_reason_of(plan) if plan.get('mode', 'production') == 'sample' else None,
            'demo_fingerprint': demo_fingerprint,
            'comparison': compare_groups(plan, datasets)}


def cell(value):
    if value is None:
        return '缺失'
    if isinstance(value, (dict, list)):
        value = signature(value)
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '\\|').replace('\n', ' ')


def render(result):
    lines = ['# 赛狐业务数据处理结果', '', '范围：仅已读取并通过校验的记录；脚本不认证全量覆盖或业务口径。', '',
             '模式：' + ('返回样本演示，不能作为真实经营结论。' if result['mode'] == 'sample' else '业务数据处理。'), '',
             '工具：' + cell(result['tool_id']), '', '业务上下文：' + cell(result['context'])]
    if result['mode'] == 'sample' and result.get('sample_reason'):
        lines += ['样本授权与限制：' + cell(result['sample_reason'])]
    if result.get('demo_fingerprint'):
        lines += ['', '演示/样本数据指纹（' + '、'.join(result['demo_fingerprint']) + '）：结果不作为经营结论。']
    for name, ds in result['datasets'].items():
        lines += ['', '## ' + cell(name), '', f"状态：{cell(ds['status'])}；有效记录：{ds['accepted_rows']}；覆盖：未认证。",
                  '', '请求窗口：' + cell(ds['period']), '', '| 分组 | 指标 | 值 | 单位 | 有效/缺失行 | 状态 |', '|---|---|---|---|---|---|']
        for group in ds['groups']:
            for metric, value in group['metrics'].items():
                lines.append(f"| {cell(group['dimensions'])} | {cell(metric)} | {cell(value['value'])} | {cell(value['unit'])} | {value['valid_rows']}/{value['missing_rows']} | {cell(value['status'])} |")
        lines += ['', '已排除/去重：' + cell(ds['excluded']), '', '数据说明：']
        lines += ['- ' + cell(issue) for issue in ds['issues']] or ['- 无额外处理异常；仍需检查上游覆盖信息。']
        for page in ds['pages']:
            lines += ['- ' + cell({key: page.get(key) for key in ('file','status','trace_id','coverage','pagination','warnings','output_validation','error_code','upstream','message','retryable','field_errors','suggested_action','demo_fingerprint','rejection')})]
    if result['comparison']:
        lines += ['', '## 对比（已取记录）', '', result['comparison']['note']]
        if result['comparison'].get('snapshot_warning'):
            lines += [result['comparison']['snapshot_warning']]
        lines += ['', '| 分组 | 指标 | 本期 | 基期 | 差值 | 差值单位 | 变化率% |', '|---|---|---|---|---|---|---|']
        for group in result['comparison']['groups']:
            for name, m in group['metrics'].items():
                lines.append('| ' + ' | '.join(cell(v) for v in (group['dimensions'],name,m['current'],m['baseline'],m['difference'],m['difference_unit'],m['change_percent'])) + ' |')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True, help='助手根据当前契约生成的本地 JSON 处理计划')
    parser.add_argument('--out-dir', type=Path, required=True, help='本次任务的新输出目录，拒绝覆盖已有结果')
    parser.add_argument('--sample-reason', help='sample 模式授权依据（说明用户授权与样本限制）；也可写进计划的 sample_reason 字段')
    args = parser.parse_args()
    try:
        plan = read_json(args.plan)
        if args.sample_reason and not plan.get('sample_reason'):
            plan['sample_reason'] = args.sample_reason
        result = process(plan, args.plan.resolve().parent)
        dest = args.out_dir.resolve()
        # Compute before writing, and never mix a failed run with old outputs.
        dest.mkdir(parents=True, exist_ok=False)
        (dest/'result.json').write_text(dump(result) + '\n', encoding='utf-8')
        report_md = render(result)
        md_path = dest/'report.md'
        md_path.write_text(report_md, encoding='utf-8')
        # HTML 主报告：与 .md 同名写出；.md 留档，JSON 输出不变。
        write_html_report(report_md, md_path)
        (dest/'plan.json').write_text(dump(plan) + '\n', encoding='utf-8')
        print(dump({'output': str(dest), 'datasets': {k: {'status':v['status'],'accepted_rows':v['accepted_rows']} for k,v in result['datasets'].items()}}))
        if all(v['status'] == 'unavailable' for v in result['datasets'].values()):
            return 2
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print('处理未完成：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
