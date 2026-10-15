"""Read-only topic reports over validated MCP envelopes; standard library only."""
import argparse
from pathlib import Path
import sys
import process_data as data


def review(plan, base, topic):
    data.validate(plan)
    spec = plan.get('topic', {})
    if not isinstance(spec, dict):
        raise ValueError('topic 必须为对象')
    if plan.get('mode', 'production') != 'production':
        raise ValueError('主题复盘需要 production 模式；样本演示请直接使用通用处理器')
    if not plan.get('group_by') or not plan.get('dedupe_by'):
        raise ValueError('主题复盘需要显式商品分组键和业务去重键')
    if not spec.get('scope_note'):
        raise ValueError('topic.scope_note 必须说明平台、粒度、口径和来源')
    if topic == 'returns':
        if plan['tool_id'] != 'get_profit_report_by_msku':
            raise ValueError('本版退款计算支持 Amazon MSKU 利润；其他平台先使用通用处理器')
        if not plan.get('date_field') or any(not x.get('period') for x in plan['datasets'].values()):
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
    # Array projections are allowed only when cardinality is exactly one.
    # Never assign multi-MSKU aggregate money to the first SKU.
    for path in plan['group_by'] + plan['dedupe_by']:
        if any(part.isdigit() for part in path.split('.')):
            raise ValueError('主题分组/去重不能取数组首项；使用经验证的标量 ID')
    result = data.process(plan, base)
    result['topic'] = topic
    result['scope_note'] = spec['scope_note']
    result['findings'] = []
    for name, ds in result['datasets'].items():
        if topic == 'returns':
            for group in ds['groups']:
                metrics = group['metrics']
                amount = metrics['refund_amount_signed']['value']
                result['findings'].append({'dataset':name,'product':group['dimensions'],
                    'refund_amount_signed':amount,
                    'refund_amount_magnitude':abs(amount) if amount is not None else None,
                    'refund_units':metrics['refund_units']['value'],'return_units':metrics['return_units']['value'],
                    'note':'退款金额绝对值仅用于观察规模；退款与退货数量不相加；不是净损失，不代表质量原因。'})
        else:
            as_of = data.day(spec['as_of'])
            for record in ds['records']:
                row = record['row']; launch = data.day(data.at(row,spec['launch_date_field']))
                age = (as_of-launch).days+1 if launch and launch <= as_of else None
                result['findings'].append({'dataset':name,
                    'product':{key:data.at(row,key) for key in plan['group_by']},
                    'launch_date':str(launch) if launch else None,'age_days':age,
                    'completed_observation_windows':[n for n in (7,14,30,60) if age is not None and age>=n],
                    'sources':record['sources'],
                    'note':'已达到的日历观察窗口，不表示数据完整或目标达成；未知/未来上架日期不评阶段。'})
    return result


def render(result):
    title = '退款退货损失分析' if result['topic']=='returns' else '新品上市复盘'
    lines = ['# '+title,'',data.cell(result['scope_note']),'',
             '仅基于已取得记录，不认证全量覆盖。目标达成、原因判断和行动建议须结合证据另行分析。','',
             '| 数据集 | 商品 | 观察结果 |','|---|---|---|']
    for item in result['findings']:
        detail={k:v for k,v in item.items() if k not in ('dataset','product')}
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
        for filename,content in [('result.json',data.dump(result)),('report.md',render(result)),('plan.json',data.dump(plan))]:
            (args.out_dir/filename).write_text(content+'\n',encoding='utf-8')
        print(data.dump({'output':str(args.out_dir),'statuses':{k:v['status'] for k,v in result['datasets'].items()}}))
        return 2 if all(v['status']=='unavailable' for v in result['datasets'].values()) else 0
    except (ValueError,OSError,TypeError,KeyError) as exc:
        print('复盘未完成：'+str(exc),file=sys.stderr);return 1
