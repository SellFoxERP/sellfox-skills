# 字段来源速查（field-sources.md）

核对「某个字段属于哪个工具」时读本文件。**权威表是 `_shared/FIELD_DICTIONARY.md`**
（字段 → 含义 → 所属工具）；本文件是**本技能用到的字段**的精选子集。

## 目录

- [1. 数据源分工](#1-数据源分工)
- [2. msku 报表用到的字段](#2-msku-报表用到的字段171)
- [3. 只在自定义报表的字段（已移交 custom-report-analysis）](#3-只在自定义报表的字段已移交-custom-report-analysis禁止归给-msku-报表)
- [4. 广告转化字段并存](#4-广告转化字段并存按需选用)
- [5. 销量窗口字段并存](#5-销量窗口字段并存按需选用)
- [6. 需人工核查的项](#6-需人工核查的项)

---

## 1. 数据源分工

| 数据源 | 字段数 | 定位 | 本技能是否使用 |
|---|---|---|---|
| `get_profit_report_by_msku` | **171** | 财务深度（亚马逊费用逐项拆解）：`productSales` / `grossProfit` / `commission` / `fbaFulfillmentFee` / `cpcCost` / `storageFee` / `refundRate` / `roi` / `rate` / `currencyList` | ✅ 归因主源（全部因子） |
| `get_custom_report_page_list` | **489** | 广度 + 成本/赔偿/延期结算：`purchaseCost` / `headTripPrice` / `reimbursementAmount` / `deferredAmount` / `sessions` / `availableDays` | ❌ 已移交 `custom-report-analysis` 技能承接 |
| `get_amazon_reimbursement_report` | — | 赔偿条目清单 | ✅ 赔偿**条数级**核对（非自定义报表） |

## 2. msku 报表用到的字段（171）

| 规范名 | 原始字段 | 说明 |
|---|---|---|
| `product_sales` | `productSales` | 销售额（归因主因子） |
| `sales_num` | `salesNum` | 销量 |
| `gross_profit` | `grossProfit` | 毛利（**归因的分母/目标**） |
| `gross_profit_rate` | `grossProfitRate` | 毛利率 |
| `roi` | `roi` | 投资回报率 |
| `commission` | `commission` | 平台佣金（负数） |
| `fba_fulfillment_fee` | `fbaFulfillmentFee` | FBA 配送费（负数） |
| `storage_fee` | `storageFee` | 仓储费（负数） |
| `cpc_cost` | `cpcCost` | 广告花费（负数） |
| `cpc_cost_rate` | `cpcCostRate` | 广告成本占比 |
| `refund_sales` | `refundSales` | 退款销售额 |
| `refund_num` / `refund_rate` | `refundNum` / `refundRate` | 退款数 / 退款率 |
| `purchase_pc_fee` | `purchasePcFee` | 采购成本**汇总费用**（非明细，慎用） |
| `head_trip_pc_fee` | `headTripPcFee` | 头程**汇总费用**（非明细，慎用） |
| `compensation_fee` | `compensationFee` | 赔偿**汇总** |
| `rate` / `currency_list` | `rate` / `currencyList` | 汇率 / 币种列表（跨币种提示用） |

## 3. 只在自定义报表的字段（已移交 custom-report-analysis；⚠️ 禁止归给 msku 报表）

以下 6 个字段经一手实测确认**只存在于 `get_custom_report_page_list`**，**不在** `get_profit_report_by_msku`：

```
purchaseCost / headTripPrice / reimbursementAmount
warehouseLostAmount / missingFromInboundAmount / deferredAmount
```

早期曾把它们错误归到 msku 报表，导致归因永远取不到值、静默落空。**本技能已不再取数
自定义报表**：这些因子的金额归因由 `custom-report-analysis` 技能承接（固定分工，
报告固定披露）；本技能归因因子全部来自 msku 报表，逐字段标注来源工具。

宽表侧其余字段（`commodityCost` / `storageFeeAmount` / `fbaLongTermStorageFeeAmount` /
`fbaOverageFeeAmount` / `sessions` / `orderConversionRate` / `buyBoxPercent` /
`invAge271To365Days` / `invAge365PlusDays` / `profitPrice` / `profitRate` 等）同样不在
本技能取数范围，需要时改用 `custom-report-analysis`。

> ⚠️ `deferredReason`（延期原因）**不在**自定义报表 —— 实测它属于 `get_profit_report_by_order`（74 字段）。
> 本技能不含订单级明细，因此**不呈现延期原因**，需看原因时引导用户改用订单利润报表。

## 4. 广告转化字段并存（按需选用）

这几个字段**都真实存在**（均在 `get_custom_report_page_list`），指代不同，不得宣称某个不存在：
需要它们时改用 `custom-report-analysis` 技能。

| 字段 | 口径 |
|---|---|
| `adConversionRateCpc` | 广告转化率（按 CPC 口径） |
| `adConversionRateVcpm` | 广告转化率（按 vCPM 口径） |
| `adOrderConversionRate` | 广告订单转化率（广告侧） |
| `orderConversionRate` | 订单转化率（订单侧） |
| `conversionRate` | 会话转化率（会话侧） |

广告侧转化问题交给技能 6。

## 5. 销量窗口字段并存（按需选用）

两套窗口字段，分属不同工具，**不可互套**：

| 字段 | 归属 |
|---|---|
| `threeSaleNum` / `sevenSaleNum` / `fourteenSaleNum` / `thirtySaleNum` / `sixtySaleNum` / `ninetySaleNum` | **自定义报表**（本技能不取） |
| `day7SaleNum` / `day14SaleNum` / `day30SaleNum` | **在线产品**（`get_online_product_page_list`） |

本技能主用 msku 报表的 `salesNum`（利润报表口径的销量）。

## 6. 需人工核查的项

以下内容**赛狐 MCP 拿不到**，一律标「⚠️ 需人工核查」，**绝不编造**：

- **账户健康 / 绩效通知 / 政策合规**：无接口。
- **买家消息 / 工单**：无接口。
- **评论原文**：本技能不做（技能 9 走外部评论 MCP）。
- **税费细节 / 汇兑损益的完整重构**：仅用报表自带 `rate` / `currencyList` 提示，不重构。
- **`deferredReason`（延期原因）**：属于订单利润报表，本技能不含；需原因时改用 `get_profit_report_by_order`。
- **缺失的字段值**：标「无数据」（不写 0）+「需人工核查」，并注明所属工具。
