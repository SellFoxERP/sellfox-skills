"""Deterministic decision scenarios; inputs are explicit assumptions, not MCP certification."""
import argparse
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import sys
import process_data as data


def calculate(spec):
    mode=spec['mode']
    if not spec.get('source_note'):
        raise ValueError('必须说明输入来源、时间和假设 source_note')
    def n(key, nonnegative=True):
        value=data.number(spec.get(key))
        if value is None or (nonnegative and value<0):
            raise ValueError('缺失或无效参数：'+key)
        return value
    result={'mode':mode,'source_note':spec['source_note'],'scope':'情景测算，不是预测或业务执行'}
    if mode=='replenishment':
        daily=n('daily_units');lead=n('lead_days');safety=n('safety_days');cycle=n('review_days')
        available=n('available_units');inbound=n('confirmed_inbound_units');pack=n('pack_size')
        if pack<=0 or pack!=int(pack):raise ValueError('pack_size 必须为正整数')
        if not spec.get('inbound_timing_note'):raise ValueError('须说明在途是否在需求前到达；不确定时不要将其算作可用')
        gap=max(Decimal(0),daily*(lead+safety+cycle)-available-inbound)
        quantity=(gap/pack).to_integral_value(rounding=ROUND_CEILING)*pack
        result.update(gap_units=gap,suggested_units=quantity,
                      current_stock_days=available/daily if daily else None,
                      stockout_before_lead=available<daily*lead,
                      note='当前库存能否撑到交期单列；在途时间仍需逐批核对，不把所有在途视为立即可用。')
        cost=data.number(spec.get('unit_cost'))
        if cost is not None and cost>=0 and spec.get('currency'):
            result.update(purchase_budget=quantity*cost,currency=spec['currency'])
    elif mode=='pricing':
        price=n('price');discount=n('discount_rate');fee=n('variable_fee_rate');fixed=n('fixed_unit_cost');ad=n('ad_unit_cost')
        if discount>=1 or fee>=1:raise ValueError('折扣和费率须为 [0,1) 小数')
        if not spec.get('currency'):raise ValueError('定价测算需要统一且已核实的币种')
        net=price*(1-discount);unit=net*(1-fee)-fixed-ad
        activity=n('activity_fixed_cost')
        result.update(currency=spec['currency'],effective_price=net,unit_contribution=unit,
                      break_even_list_price=(fixed+ad)/((1-discount)*(1-fee)),
                      break_even_activity_units=(activity/unit).to_integral_value(rounding=ROUND_CEILING) if unit>0 else None,
                      note='固定单件成本含已确认采购/头程/配送等，不含已单列的比例费和广告；活动总固定费单列。不是销量预测。')
    elif mode=='shipment':
        as_of=data.day(spec.get('as_of'));shipped=data.day(spec.get('shipped_date'))
        if not as_of or not shipped or shipped>as_of:raise ValueError('需要有效且无未来发货的日历日期')
        threshold=n('followup_days')
        terminal=spec.get('terminal_statuses')
        if not isinstance(terminal,list) or not spec.get('status'):raise ValueError('需要当前状态及已核实的结束状态列表')
        age=(as_of-shipped).days
        result.update(days_since_ship=age,needs_followup=spec['status'] not in terminal and age>threshold,
                      note='仅为按发货天数的跟进线索，不代表实际运输超时、状态停留天数或少收。')
    elif mode=='sales_change':
        a=n('current_sales');b=n('baseline_sales')
        if n('current_days')!=n('baseline_days') or n('current_days')==0:
            raise ValueError('销售变化需等长非空窗口')
        result.update(difference=a-b,change_percent=(a-b)/b*100 if b else None,
                      note='仅定位变化；流量、转化、缺货和广告解释必须另取同对象同窗口证据。')
    else:raise ValueError('未知情景')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    try:
        result=calculate(data.read_json(a.input));a.output.parent.mkdir(parents=True,exist_ok=True)
        with a.output.open('x',encoding='utf-8') as f:f.write(data.dump(result)+'\n')
        print(data.dump(result));return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('测算未完成：'+str(exc),file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
