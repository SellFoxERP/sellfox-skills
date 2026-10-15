# 促销与定价情景测算

用户问活动能否盈利或保本价格时，从 get_online_product_page_list 取得售价、采购成本、头程、费用及各自币种，按实际维护情况核实。平台预估费用不是实际结算，也不是任意售价下固定不变的费用。

完整包的 sellfox-business/scripts/scenarios.py（源码目录 10-sellfox-business）提供 pricing 模式。由助手生成输入：mode、source_note、currency、price、discount_rate、variable_fee_rate、fixed_unit_cost、ad_unit_cost、activity_fixed_cost。比例用 0–1 小数；discount_rate 为折扣减免比例（0.2 表示减免20%）。所有成本与售价必须同币种；没有成本不能以零代替，确实为零须有依据。

fixed_unit_cost 是已核对的采购/头程/每件履约等固定单件成本；variable_fee_rate 是确认的收入比例费；ad_unit_cost 是单独假设的单件广告成本，避免与其他成本重复。优惠券门槛费、税和最低佣金等非线性规则若未被输入覆盖，应声明模型不适用或另建经过验证的公式，不能用简单模型宣称净利润。

计算折后价、单件贡献、保本挂牌价、活动固定成本对应的保本销量。单件贡献<=0 时不输出可盈利的保本销量。运行 python <组件目录>/scripts/scenarios.py --input <输入.json> --output <新输出.json>，输出是情景假设，不是销量预测。可分别算保守/基准/乐观场景，情景依据须写明。

默认只出测算，不修改价格或创建促销；当前目录未提供对应写能力。新品投入判断可转新品上市复盘；售后损失可转退款退货损失分析。

售价currency、feeCurrency与inventoryManage.currency分别核验。假设汇率只作演示，不称实时汇率；不将totalFee与组成项重复相加。未确认费用符号时，绝对值仅作模型假设。模型未模拟平台金额舍入，不直接用于改价。
