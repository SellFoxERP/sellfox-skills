# 售后分析流程

1. 确认 Amazon 店铺、商品范围、统计窗口与财务口径。用户已有选择直接沿用；没有选择时先发现能力，财务取数前明确口径。不把结算期退款率解释为同批订单的最终退款概率。
2. 使用 get_profit_report_by_msku 读取本期和等长基期。核对 data.reportList、bizDay/bizDate 等实际结构，优先日维度；月维度必须先确认日期代表的期间。只查询必要窗口，按分页证据收齐。
3. 分别分析销量、退款量 refundNum、退货量 returnNum、销售额退回 refundSales。refundNum 和 returnNum 可能覆盖同一事件，禁止相加为售后总量。
4. 可追加 refundManagementFee、customerReturnHrrUnitFee、fbaReturnFee 及退货成本，但逐项核对费用符号和包含关系。默认不生成“总净损失”；不能同时加 refund 汇总与其组成费用，也不能把已在利润中扣过的费用再次扣除。可售退回库存不是全额商品报废。
5. 在同币种内排列退款金额规模与变化，结合退款/退货率及样本量给整改优先级。小销量高比例单列，阈值来自商家或明确披露的分析假设。退款原因缺失时列为待核查，不凭费用或评分推断质量问题。
6. 按下面模板交付，并在原始记录可追溯时列出商品与证据。人工确认的质量问题交产品负责人；Listing 预期差异交运营；履约线索交物流。不要编造负责人姓名。

输出：商品、销量、退款量、退货量、退款金额（原始符号）、退款金额规模、费项、同比较窗口差异、证据、待核实问题、建议动作。多个 MSKU 的聚合行作为组合呈现，不能将金额归给数组首项。

## MCP 与数据约束

使用客户端实际连接的 Sellfox-MCP help/search/action。未知工具先用完整业务问句 help；已知工具缺当前契约再 search（每次最多 5 个，detail=true），已有完整契约直接 action。help 的 limit 为 1–10，版本来自本次连接。响应 failed/unknown 不进入计算，retryable=false 不重试。

店铺 ID 来自实际店铺列表；分页按当前契约继续，单页 complete 不证明全量。保留请求、response、trace_id、coverage 和 output_validation。原始文件存本地 out，不能进 GitHub。金额按币种分组，不将省略值补零。不要求商家提供 report_id；本主题优先内置接口，确需宽表时由助手自动发现并验证。
