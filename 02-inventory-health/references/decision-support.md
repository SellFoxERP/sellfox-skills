# 补货情景与货件跟进

沿用原库存体检与官方建议量；新增情景测算应与官方值分列，解释差异，不覆盖原口径。

## 补货情景

商家问“补多少/什么时候下单/占多少钱”时，先确认日均需求、交期、安全天数、复核周期、可用量、到达时间已确认的在途量、包装倍数。缺交期/在途时询问或分别测算明确标注的场景，不把未知设为零。近 7/30 天需求可以作不同场景，不把促销峰值无条件外推。

完整包中的 sellfox-business/scripts/scenarios.py 支持 replenishment 模式。源码对应 10-sellfox-business，安装后对应 sellfox-business。输入字段：mode、source_note、daily_units、lead_days、safety_days、review_days、available_units、confirmed_inbound_units、inbound_timing_note、pack_size；可选 unit_cost、currency（采购预算须同币种）。

计算目标覆盖=交期+安全天数+复核周期；缺口=max(0,日均需求×目标覆盖−可用量−确认在途)，建议量向上取包装倍数。单列当前库存是否撑得过交期；在途将来会到不代表当前不会断货。最晚下单日需按确认的需求/到货批次推导，脚本不支持批次到货排程。不自动下采购单。

## 货件跟进

用当前 get_fba_shipment_page_list 契约按店铺、创建/更新时间及状态查询。发货时间 shippedDate、状态 status、运单号 trackNos 和承运人 carrierName 可用于跟进。updateTime 不是状态变更时间，不能据此计算状态停留天数；货件页没有商品级接收数量，不输出少收结论。

scenarios.py 的 shipment 模式输入 mode、source_note、as_of、shipped_date（已按确认业务时区转换为 YYYY-MM-DD）、status、terminal_statuses（经本次契约确认的结束状态）、followup_days（商家阈值）。输出仅为发货天数及待跟进标记。

两模式均运行 python <组件目录>/scripts/scenarios.py --input <本次输入.json> --output <新输出.json>。输入及结果本地保存；不得用当前快照冒充历史库存轨迹。

quantity与inventoryManage.available可能不同口径，不相加或任取较大值；inboundWorking不是确认到货。测试可明确假设不计在途，但不等于实际在途为零。
