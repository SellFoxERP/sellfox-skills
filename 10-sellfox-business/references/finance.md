# 财务与结算（finance）

用于利润、费用、赔偿、收款及平台结算。调用遵循 [MCP 流程](mcp-workflow.md)。

## 先确定口径

沿用用户已明确的结算/发货口径、日期、店铺和汇总币种；缺失且影响统计时再确认。不同平台可能没有同一口径枚举，不将 Amazon 参数照搬到 Temu 或 TikTok。返回币种与日期需再次核对。

## 工具选择

| 需求 | 已知工具线索 |
|---|---|
| 店铺、订单、MSKU、SKU 粒度利润 | `get_profit_report_by_shop`、`get_profit_report_by_order`、`get_profit_report_by_msku`、`get_profit_report_by_sku` |
| Amazon 赔偿 | `get_amazon_reimbursement_report` |
| 收款单 | `get_receipt_order_page_list` |
| TikTok 利润和账单 | `get_tiktok_profit_report_by_shop`、`get_tiktok_profit_report_by_msku`、`get_tiktok_statement_detail` |
| Temu 全托/半托 | `get_temu_full_managed_profit_by_shop`、`get_temu_semi_managed_profit_by_shop`；产品、结算及具体费用项再 sf_discover 发现 |
| MercadoLibre、SHEIN 结算 | `get_mercadolibre_settlement_page_list`、`get_shein_agent_settlement_summary_page_list`、`get_shein_half_settlement_page_list` |

问句示例：“查询 Temu 半托管这家店上月利润，并查看售后退款和物流费用明细。”先按平台和经营模式选汇总，再从当前 sf_discover 发现对应费用工具。工具列表不是全量清单，不按前后缀拼造工具 ID。

## 分析与交付

- 先读取用户要求的粒度；有异常再下钻到商品/订单和费用，不把店铺汇总与明细相加。
- 成本、广告、退款、赔偿的正负号和是否已包含在利润中以当前定义为准。费用明细用于解释差异，不能重复扣费。
- 不同币种分开呈现；同币种同口径才比较或汇总。利润率使用汇总利润/相应收入，不平均各行利润率。
- 对比窗口须一致；有本期无基期只展示本期。利润下降可能同时受销量、售价和费用影响，缺少对应明细时不能确定归因。
- 内置利润查询无需 report_id。需要自定义报表宽表口径时转 custom-report-analysis 技能（已安装）；未安装则按内置字段口径分析并披露该缺口。

输出收入、利润与已获取的费用证据，注明未返回/未计入的成本。业务报表分析不替代审计或税务口径认证。无赔偿记录不代表全部可索赔款项均已追回。

## 数据处理脚本

映射实际收入/利润字段为 money，利润率用 ratio（总利润/总收入，percent，currency_sensitive=true）。按店铺或商品维度分别处理，基期和本期保持同一口径，用 compare 对照，不把利润表与费用明细拼接重复扣费。 按 [Python 数据处理](data-processing.md) 生成本次计划，运行 `scripts/process_data.py`，保留逐页来源和警告。
