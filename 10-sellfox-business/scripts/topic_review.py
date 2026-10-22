"""Read-only topic reports over validated MCP envelopes; standard library only."""
import argparse
from pathlib import Path
import sys
import process_data as data
from report_html import write_html_report


def shop_names(plan, base):
    """Best-effort 店铺名映射：数据集输入同目录或其父目录的 shops.json（信封 data.rows 的 id/name）。
    找不到或解析失败仅影响展示（店铺列退回编号），不影响计算。"""
    mapping = {}
    folders = []
    for dataset in plan.get('datasets', {}).values():
        for file in dataset.get('inputs', []):
            folder = (base / file).parent
            for candidate in (folder, folder.parent):
                if candidate not in folders:
                    folders.append(candidate)
    for folder in folders:
        path = folder / 'shops.json'
        try:
            if not path.is_file():
                continue
            rows = data.at(data.unwrap(data.read_json(path))[1], 'data.rows')
        except (ValueError, OSError, TypeError, KeyError):
            continue
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get('id') is not None \
                        and isinstance(row.get('name'), str) and row['name'].strip():
                    mapping.setdefault(str(row['id']), row['name'].strip())
    return mapping


def review(plan, base, topic):
    data.validate(plan)
    spec = plan.get('topic', {})
    if not isinstance(spec, dict):
        raise ValueError('topic 必须为对象')
    sample = plan.get('mode', 'production') == 'sample'
    if sample and not spec.get('sample_reason'):
        raise ValueError('样本模式必须提供 topic.sample_reason，说明用户授权及样本限制')
    if not plan.get('group_by') or not plan.get('dedupe_by'):
        raise ValueError('主题复盘需要显式商品分组键和业务去重键')
    if not spec.get('scope_note'):
        raise ValueError('topic.scope_note 必须说明平台、粒度、口径和来源')
    if topic == 'returns':
        if plan['tool_id'] != 'get_profit_report_by_msku':
            raise ValueError('本版退款计算支持 Amazon MSKU 利润；其他平台先使用通用处理器')
        if not plan.get('date_field'):
            raise ValueError('售后分析必须验证业务日期和查询窗口')
        required = {'refund_units':('sum','refundNum'), 'return_units':('sum','returnNum'),
                    'refund_amount_signed':('sum','refundSales'), 'sales_units':('sum','salesNum')}
        for name, (op, field) in required.items():
            metric = plan['metrics'].get(name, {})
            if metric.get('op') != op or metric.get('field') != field or metric.get('unit') != ('money' if name == 'refund_amount_signed' else 'count'):
                raise ValueError('售后指标映射不符合当前支持口径：' + name)
        if plan['metrics']['refund_amount_signed'].get('unit') != 'money':
            raise ValueError('退款金额必须按币种处理')
    elif topic == 'launch':
        if plan['tool_id'] not in ('get_product_analyze_list','get_online_product_page_list'):
            raise ValueError('新品复盘支持产品分析或在线商品；不同工具不能隐式拼接')
        if not data.day(spec.get('as_of')) or not spec.get('launch_date_field'):
            raise ValueError('新品复盘需要 as_of 和 launch_date_field，日期须已核实为日历日期')
    else:
        raise ValueError('未知主题')
    max_age = spec.get('max_age_days')
    if max_age is not None and (isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0):
        raise ValueError('max_age_days 必须为正整数')
    # Do not project arrays into a single product identity.
    # Never assign multi-MSKU aggregate money to the first SKU.
    for path in plan['group_by'] + plan['dedupe_by']:
        if any(part.isdigit() for part in path.split('.')):
            raise ValueError('主题分组/去重不能取数组首项；使用经验证的标量 ID')
    result = data.process(plan, base)
    result['topic'] = topic
    result['scope_note'] = spec['scope_note']
    result['sample_reason'] = spec.get('sample_reason') if sample else None
    result['findings'] = []
    # 通用说明：逐行 note 与此完全一致；渲染层据此收拢，避免同一句重复 N 遍。
    result['common_note'] = ('退款金额绝对值仅用于观察规模；退款与退货数量不相加；不是净损失，不代表质量原因。' if topic == 'returns'
                             else '已达到的日历观察窗口，不表示数据完整或目标达成；未知/未来上架日期不评阶段。')
    result['shop_names'] = shop_names(plan, base)
    for name, ds in result['datasets'].items():
        if topic == 'returns':
            # 有行的数据集仍强制 period；空数据集（complete 空页）可缺——渲染标「窗口未知（空数据）」，
            # 与 empty→退出码 0 的语义一致，不冒充期间统计。
            if ds['accepted_rows'] and not plan['datasets'][name].get('period'):
                raise ValueError('售后分析必须验证业务日期和查询窗口：数据集 ' + name + ' 有行但未声明 period')
            if not ds['accepted_rows'] and not plan['datasets'][name].get('period'):
                ds['issues'].append(name + ': 窗口未知（空数据）——complete 空页无行可验期间；'
                                    '如需期间统计请补 period 后重跑')
        ds['observed_dates'] = sorted({str(data.at(x['row'], plan.get('date_field'))) for x in ds['records']}) if plan.get('date_field') else []
        if topic == 'returns':
            for group in ds['groups']:
                metrics = group['metrics']
                amount = metrics['refund_amount_signed']['value']
                result['findings'].append({'dataset':name,'product':group['dimensions'],
                    'refund_amount_signed':amount,
                    'refund_amount_magnitude':abs(amount) if amount is not None else None,
                    'refund_units':metrics['refund_units']['value'],'return_units':metrics['return_units']['value'],
                    'note':result['common_note']})
        else:
            as_of = data.day(spec['as_of'])
            for record in ds['records']:
                row = record['row']; launch = data.day(data.at(row,spec['launch_date_field']))
                future = bool(launch and launch > as_of)
                age = (as_of-launch).days+1 if launch and launch <= as_of else None
                note = result['common_note']
                if future:
                    note += '上架日期为未来值，未确认。'
                result['findings'].append({'dataset':name,
                    'product':{key:data.at(row,key) for key in plan['group_by']},
                    'launch_date':str(launch) if launch and not future else None,'launch_date_raw':data.at(row,spec['launch_date_field']),'age_days':age,
                    'new_product_scope':'unknown' if age is None else 'outside' if max_age is not None and age>max_age else 'within' if max_age is not None else 'not_defined',
                    'max_age_days':max_age,
                    'completed_observation_windows':[n for n in (7,14,30,60) if age is not None and age>=n],
                    'sources':record['sources'],
                    'note':note})
    return result


def merchant_summary(result):
    """商家摘要表：正文最前的可读层；金额保留原币种标注，note 收成表下一句话，不逐行重复。"""
    names = result.get('shop_names') or {}
    findings = result['findings']
    lines = ['## 摘要', '']
    if not findings:
        return lines + ['（无可用摘要行：没有通过校验的记录；各数据集状态见文末“赛狐业务数据处理结果”。）']
    unmapped = False

    def parts(item):
        product = item['product']
        shop_keys = [k for k in product if 'shop' in str(k).lower()]
        currency_keys = [k for k in product if 'currency' in str(k).lower()]
        label_keys = [k for k in product if k not in shop_keys and k not in currency_keys]
        label = ' / '.join(data.cell(product[k]) for k in label_keys) if label_keys else '未知商品'
        shop_id = product[shop_keys[0]] if shop_keys else None
        name = names.get(str(shop_id)) if shop_id is not None else None
        return label, data.cell(name if name is not None else shop_id), \
            name is None and shop_id is not None, product[currency_keys[0]] if currency_keys else None

    if result['topic'] == 'returns':
        rows = []
        for item in findings:
            label, shop, missing, currency = parts(item)
            unmapped = unmapped or missing
            amount = item.get('refund_amount_magnitude')
            amount_cell = data.cell(amount)+' '+data.cell(currency) if amount is not None and currency is not None else data.cell(amount)
            rows.append((amount, [label, shop, amount_cell,
                                  data.cell(item.get('refund_units')), data.cell(item.get('return_units'))]))
        rows.sort(key=lambda row: (row[0] is None, -row[0] if row[0] is not None else 0))
        lines += ['| MSKU | 店铺 | 退款金额 | 退款量 | 退货量 |', '|---|---|---|---|---|']
        lines += ['| '+' | '.join(cells)+' |' for _, cells in rows]
        lines += ['', '说明：'+result.get('common_note', '')+'金额保留原币种标注，未折算汇率。']
    else:
        rank = {'within': 0, 'not_defined': 1, 'outside': 2, 'unknown': 3}
        rows = []
        for item in findings:
            label, shop, missing, _ = parts(item)
            unmapped = unmapped or missing
            age, scope, window = item.get('age_days'), item.get('new_product_scope'), item.get('max_age_days')
            if scope == 'within':
                stage = '观察中（第 '+str(age)+' 天/窗口 '+str(window)+' 天）'
            elif scope == 'outside':
                stage = '超龄（第 '+str(age)+' 天/窗口 '+str(window)+' 天）'
            elif scope == 'not_defined':
                stage = '上市 '+str(age)+' 天（未定义窗口）'
            else:
                stage = '日期不明'
            rows.append(((rank.get(scope, 4), -(age or 0)),
                         [label, shop, data.cell(item.get('launch_date') or '未知'),
                          data.cell(age if age is not None else '未知'), stage]))
        rows.sort(key=lambda row: row[0])
        lines += ['| MSKU | 店铺 | 上架日期 | 上市天数 | 阶段 |', '|---|---|---|---|---|']
        lines += ['| '+' | '.join(cells)+' |' for _, cells in rows]
        lines += ['', '说明：'+result.get('common_note', '')]
        if any(item.get('note') != result.get('common_note') for item in findings):
            lines.append('个别行的上架日期为未来值，未确认（见复核明细）。')
    if unmapped:
        lines.append('店铺名未知时显示编号：未在数据目录找到 shops.json 店铺名录，或该店铺不在名录内。')
    return lines


def render(result):
    title = '退款退货损失分析' if result['topic']=='returns' else '新品上市复盘'
    banner = '返回样本演示：不能作为当前经营结论。原因：'+data.cell(result.get('sample_reason')) if result['mode']=='sample' else '生产模式：仍需核验业务范围和覆盖。'
    lines = ['# '+title,'',banner]
    fingerprints = result.get('demo_fingerprint') or []
    if fingerprints:
        # Demo-fingerprint watermark applies in both sample and production modes.
        lines += ['','演示/样本数据指纹（'+'、'.join(fingerprints)+'）：结果不作为经营结论']
    lines += ['',data.cell(result['scope_note']),'',
              '仅基于已取得记录，不认证全量覆盖。目标达成、原因判断和行动建议须结合证据另行分析。']
    # 两层结构：商家摘要表在前；原明细降级为机器证据，供 AI 复核与追溯。
    lines += [''] + merchant_summary(result)
    lines += ['','## 复核明细（机器证据）','',
              '通用说明：'+result.get('common_note', '')+'（原逐行 note 收拢至此一次性说明；下表保留行级原始字段与来源，与通用说明不同的行仍保留各自 note。）','',
              '| 数据集 | 商品 | 观察结果 |','|---|---|---|']
    for item in result['findings']:
        detail={k:v for k,v in item.items() if k not in ('dataset','product','note')}
        if item.get('note') != result.get('common_note'):
            detail['note']=item['note']
        lines.append('| '+data.cell(item['dataset'])+' | '+data.cell(item['product'])+' | '+data.cell(detail)+' |')
    return '\n'.join(lines)+'\n\n'+data.render(result)


def main(topic):
    parser=argparse.ArgumentParser(description='从完整 MCP 信封生成主题分析，计划由助手生成')
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--out-dir',type=Path,required=True)
    args=parser.parse_args()
    try:
        plan=data.read_json(args.plan)
        result=review(plan,args.plan.resolve().parent,topic)
        args.out_dir.mkdir(parents=True,exist_ok=False)
        report_md=render(result)
        for filename,content in [('result.json',data.dump(result)),('report.md',report_md),('plan.json',data.dump(plan))]:
            (args.out_dir/filename).write_text(content+'\n',encoding='utf-8')
        write_html_report(report_md,args.out_dir/'report.md')  # HTML 主报告，.md 留档
        print(data.dump({'output':str(args.out_dir),'statuses':{k:v['status'] for k,v in result['datasets'].items()}}))
        return 2 if all(v['status']=='unavailable' for v in result['datasets'].values()) else 0
    except (ValueError,OSError,TypeError,KeyError) as exc:
        print('复盘未完成：'+str(exc),file=sys.stderr);return 1
