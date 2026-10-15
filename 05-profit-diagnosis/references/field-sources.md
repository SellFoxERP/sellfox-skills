# 字段来源速查（field-sources.md）

核对「某个字段属于哪个工具」时读本文件。**权威表是 `_shared/FIELD_DICTIONARY.md`**
（字段 → 含义 → 所属工具）；本文件是**本技能用到的字段**的精选子集。

## 目录

- [1. 两个主源的分工](#1-两个主源的分工)
- [2. msku 报表用到的字段](#2-msku-报表用到的字段171)
- [3. 自定义报表用到的字段](#3-自定义报表用到的字段489)
- [4. 只在自定义报表的字段](#4-只在自定义报表的字段禁止归给-msku-报表)
- [5. 广告转化字段并存](#5-广告转化字段并存按需选用)
- [6. 销量窗口字段并存](#6-销量窗口字段并存按需选用)
- [7. 需人工核查的项](#7-需人工核查的项)

---

## 1. 两个主源的分工

| 数据源 | 字段数 | 定位 | 典型字段 |
|---|---|---|---|
| `get_profit_report_by_msku` | **171** | 财务深度（亚马逊费用逐项拆解） | `productSales` / `grossProfit` / `commission` / `fbaFulfillmentFee` / `cpcCost` / `storageFee` / `refundRate` / `roi` / `rate` / `currencyList` |
| `get_custom_report_page_list` | **489** | 广度 + 成本/赔偿/延期结算 | `purchaseCost` / `headTripPrice` / `reimbursementAmount` / `warehouseLostAmount` / `missingFromInboundAmount` / `deferredAmount` / `sessions` / `availableDays` |

> 💡 **选型口诀**：要**深**（费用拆解细）用 msku 利润报表；要**成本与赔偿明细**用自定义报表。

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

## 3. 自定义报表用到的字段（489）

| 规范名 | 原始字段 | 说明 |
|---|---|---|
| `purchase_cost` | `purchaseCost` | 采购成本（⚠️ 只在自定义报表） |
| `head_trip_price` | `headTripPrice` | 头程运费（⚠️ 只在自定义报表） |
| `commodity_cost` | `commodityCost` | 商品成本 |
| `reimbursement_amount` | `reimbursementAmount` | 亚马逊赔偿（⚠️ 只在自定义报表） |
| `warehouse_lost_amount` | `warehouseLostAmount` | 仓库丢失（⚠️ 只在自定义报表） |
| `missing_from_inbound_amount` | `missingFromInboundAmount` | 入库缺失（⚠️ 只在自定义报表） |
| `warehouse_damage_amount` | `warehouseDamageAmount` | 仓库损坏 |
| `deferred_amount` | `deferredAmount` | 延期结算金额（⚠️ 只在自定义报表） |
| `storage_fee_amount` | `storageFeeAmount` | 仓储费（宽表口径） |
| `fba_long_term_storage_fee_amount` | `fbaLongTermStorageFeeAmount` | 长期仓储费（库龄 271+ 天） |
| `fba_overage_fee_amount` | `fbaOverageFeeAmount` | 超量仓储费 |
| `session` | `sessions` | 会话数（归因上下文） |
| `conversion_rate` | `orderConversionRate` / `conversionRate` | 订单转化率 / 会话转化率 |
| `buy_box_percent` | `buyBoxPercent` | 购物车占比 |
| `return_sale_num` | `returnSaleNum` | 退货销量 |
| `refund_price` | `refundPrice` | 退款金额 |
| `inv_age_271_365` | `invAge271To365Days` | 库龄 271~365 天（长期仓储费风险） |
| `inv_age_365_plus` | `invAge365PlusDays` | 库龄 365 天以上（长期仓储费红线） |
| `profit_price` / `profit_rate` | `profitPrice` / `profitRate` | 宽表口径利润/利润率（交叉核对用，**非**归因主口径） |

> ⚠️ `deferredReason`（延期原因）**不在**自定义报表 —— 实测它属于 `get_profit_report_by_order`（74 字段）。
> 本技能免费报告不含订单级明细，因此**不呈现延期原因**，需看原因时引导用户改用订单利润报表。

## 4. 只在自定义报表的字段（⚠️ 禁止归给 msku 报表）

以下 6 个字段经一手实测确认**只存在于 `get_custom_report_page_list`**，**不在** `get_profit_report_by_msku`：

```
purchaseCost / headTripPrice / reimbursementAmount
warehouseLostAmount / missingFromInboundAmount / deferredAmount
```

早期曾把它们错误归到 msku 报表，导致归因永远取不到值、静默落空。**本技能两个源都用**，
归因表逐字段标注来源工具；`report_id` 缺失时这些因子一律标「⚠️ 需人工核查」。

## 5. 广告转化字段并存（按需选用）

这几个字段**都真实存在**（均在 `get_custom_report_page_list`），指代不同，不得宣称某个不存在：

| 字段 | 口径 |
|---|---|
| `adConversionRateCpc` | 广告转化率（按 CPC 口径） |
| `adConversionRateVcpm` | 广告转化率（按 vCPM 口径） |
| `adOrderConversionRate` | 广告订单转化率（广告侧） |
| `orderConversionRate` | 订单转化率（订单侧） |
| `conversionRate` | 会话转化率（会话侧） |

本技能归因「销售额下滑」时读 `orderConversionRate`（订单侧），因为要回答的是
「流量在，但下单变少了没有」。**广告侧转化问题交给技能 6**。

## 6. 销量窗口字段并存（按需选用）

两套窗口字段，分属不同工具，**不可互套**：

| 字段 | 归属 |
|---|---|
| `threeSaleNum` / `sevenSaleNum` / `fourteenSaleNum` / `thirtySaleNum` / `sixtySaleNum` / `ninetySaleNum` | **自定义报表** |
| `day7SaleNum` / `day14SaleNum` / `day30SaleNum` | **在线产品**（`get_online_product_page_list`） |

本技能主用 msku 报表的 `salesNum`（利润报表口径的销量）；宽表窗口字段仅作上下文补充。

## 7. 需人工核查的项

以下内容**赛狐 MCP 拿不到**，一律标「⚠️ 需人工核查」，**绝不编造**：

- **账户健康 / 绩效通知 / 政策合规**：无接口。
- **买家消息 / 工单**：无接口。
- **评论原文**：本技能不做（技能 9 走外部评论 MCP）。
- **税费细节 / 汇兑损益的完整重构**：仅用报表自带 `rate` / `currencyList` 提示，不重构。
- **`deferredReason`（延期原因）**：属于订单利润报表，本技能不含；需原因时改用 `get_profit_report_by_order`。
- **缺失的字段值**：标「无数据」（不写 0）+「需人工核查」，并注明所属工具。
