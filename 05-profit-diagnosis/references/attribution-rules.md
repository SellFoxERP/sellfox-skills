# 利润归因规则（attribution-rules.md）

把 171 + 489 字段**收敛成 3~5 条归因**的方法论与公式。计算前必读。

## 目录

- [1. 为什么不能做字段排行榜](#1-为什么不能做字段排行榜)
- [2. 归因公式](#2-归因公式)
- [3. 符号（profit_sign）为什么必须显式声明](#3-符号profit_sign为什么必须显式声明)
- [4. 归因门槛](#4-归因门槛)
- [5. 因子清单与归属](#5-因子清单与归属)
- [6. 利润口径的强制标注](#6-利润口径的强制标注)
- [7. 两条「易漏的钱」专线](#7-两条易漏的钱专线)
- [8. 边界：算术归因 ≠ 因果推断](#8-边界算术归因--因果推断)

---

## 1. 为什么不能做字段排行榜

171 个字段里，**绝大多数是别的字段的比率/占比/分项**（如 `cpcCostRate` = `cpcCost`/`productSales`）。
直接对全部字段做「环比变化排序」，会得出一堆**同源结论**（销售额跌 → 佣金跌、广告销售额跌、
促销分摊跌…），看似 10 条发现，实则 1 个事实。

正确做法是**先把因子收敛成互相独立、金额可加的一组**（本技能取 10 条，见 `config/diagnosis.yaml
→ attribution.factors`），再算每条对毛利的**贡献额**。贡献额可加，因此能回答
「毛利少赚的这 8 万，分别是谁吃掉的」—— 这才是归因，而不是罗列。

## 2. 归因公式

对每个 MSKU、每条因子：

```
贡献额 contribution_to_drop = profit_sign × (基期值 − 本期值)
占跌幅 share_pct            = 贡献额 / 毛利跌幅 × 100%
其中 毛利跌幅 = 基期 grossProfit − 本期 grossProfit（正 = 下滑）
```

**贡献额为正 = 该因子把毛利往下拉。** 按贡献额绝对值降序取前 N（默认 5）条即为归因清单。

> 只列**贡献额 > 0** 的因子：贡献额为负的是「利好」（抵消了部分跌幅），不进异常归因，
> 但会计入毛利差额的自然结果（因为跌幅本身已扣掉了它们的贡献）。

## 3. 符号（profit_sign）为什么必须显式声明

**赛狐报表里费用字段的存储符号不统一**，靠猜必反向：

| 报表 | 费用/成本符号 | 实测样例 | 推论 | profit_sign |
|---|---|---|---|---|
| `get_profit_report_by_msku` | **负数** | `commission=-768.1`、`fbaFulfillmentFee=-624.3`、`cpcCost=-286.5` | 费用变多 = 值**变小** = 不利 | `+1` |
| `get_custom_report_page_list` | **正数** | `purchaseCost=3102.5`、`headTripPrice=135` | 成本变多 = 值**变大** = 不利 | `-1` |

因此 `config/diagnosis.yaml → attribution.factors[].profit_sign` 是**每个因子必填项**，
含义为「该字段值越大，对利润越有利（+1）还是越不利（-1）」。

> ⚠️ 新增因子时必须填对符号，否则归因会恰好反向 —— 而且**不会报错**，只会静默给出错误结论。
> 不确定的因子（如 `deferredAmount`，递延的利弊取决于公司确认口径）应在注释里写明
> 「需与财务确认后固定此符号」。

## 4. 归因门槛

两条门槛**取或**（满足任一即纳入），避免小额噪声与巨头效应各占一边：

| 配置键 | 默认 | 含义 |
|---|---|---|
| `attribution.min_share_pct` | 20 | 因子贡献额 / 毛利跌幅 ≥ 20% |
| `attribution.min_amount` | 50 | 或因子贡献额绝对值 ≥ 50（报表币种） |
| `attribution.max_items` | 5 | 最多输出 3~5 条（按贡献额绝对值降序） |
| `attribution.min_gross_profit_drop` | 0 | 毛利跌幅低于此值不触发归因（滤噪声） |

## 5. 因子清单与归属

⚠️ **字段归属不可混**（实测确认，权威表见 `_shared/FIELD_DICTIONARY.md 与当前 MCP 契约`）。
下表是**唯一事实来源**，与 `fetch_profit.FIELD_SOURCES` 一致：

| 因子 key | 字段 | 归属工具 | profit_sign |
|---|---|---|---|
| `sales` | `productSales` | **msku 报表** | +1 |
| `purchase_cost` | `purchaseCost` | **自定义报表** ⚠️ | -1 |
| `head_trip` | `headTripPrice` | **自定义报表** ⚠️ | -1 |
| `commission` | `commission` | **msku 报表** | +1 |
| `fba_fulfillment` | `fbaFulfillmentFee` | **msku 报表** | +1 |
| `storage_fee` | `storageFee` | **msku 报表** | +1 |
| `ad_cost` | `cpcCost` | **msku 报表** | +1 |
| `refund` | `refundSales` | **msku 报表** | +1 |
| `deferred` | `deferredAmount` | **自定义报表** ⚠️ | -1 |
| `reimbursement` | `reimbursementAmount` | **自定义报表** ⚠️ | +1 |

> ⚠️ **严禁把 `purchaseCost` / `headTripPrice` / `reimbursementAmount` / `warehouseLostAmount` /
> `missingFromInboundAmount` / `deferredAmount` 归到 msku 报表** —— 实测它们**只存在于**
> `get_custom_report_page_list`（489 字段），不在 `get_profit_report_by_msku`（171 字段）。

msku 报表侧**有同名概念的汇总字段**，但口径不同、不可替代：
`purchasePcFee` / `headTripPcFee`（成本汇总费用）、`compensationFee`（赔偿汇总）。

**仓储费细分**（长期仓储费 = 库龄 271+ 天，与技能 2 联动）也只在自定义报表：
`storageFeeAmount` / `fbaLongTermStorageFeeAmount` / `fbaOverageFeeAmount`。

## 6. 利润口径的强制标注

`settlement`（结算口径，默认）vs `shipment`（发货口径）。**同一时间段两者结果不同。**

- 每次调用显式传 `profit_caliber_type`，默认读 `config/diagnosis.yaml → caliber.default`。
- **报告首部必须标注本报告用的是哪个口径**，否则必然与财务对不上账。
- 若需一期两口径对比，置 `caliber.compare_both: true`（会翻倍取数，注意 300s 总超时）。

## 7. 两条「易漏的钱」专线

这两类因子**最容易在「降本增效」的直觉下被当成本砍掉**，但性质不同，必须单列（报告有独立区块）：

1. **延期结算 `deferredAmount`**：钱**没有丢**，只是被推迟到未来期间确认。
   它与「采购成本上涨」的处置方式**完全相反** —— 一个要等，一个要谈。误当成本砍会砍错方向。
   同期口径对比时要注意：延期结算会让本期毛利「看起来」变差，但未来期会回补。
2. **赔偿/仓损 `reimbursementAmount` / `warehouseLostAmount` / `missingFromInboundAmount`**：
   这是**应该收回来但可能漏领**的钱。用 `get_amazon_reimbursement_report` 交叉核对是否漏领。
   金额若持续为 0 或缺失，标「无数据 / 需人工核查」，**不写 0** —— 「没拿到」与「真的是 0」是两回事。

## 8. 边界：算术归因 ≠ 因果推断

本技能只做**算术贡献度**计算。因子间存在**相关关系**（如销售额跌会连带佣金跌、广告销售额跌），
贡献额会同时体现为多条正贡献，加起来可能**超过 100%**（见示例的 167%）。

因此：
- 输出按贡献额降序，但**不声称某条是「根本原因」**。
- 贡献额合计 > 100% 是正常现象（因子相关），**不是计算错误**。
- 真正的因果判断（是降价导致销量跌，还是销量跌被迫降价）需要业务知识，交给人。
- 相关因子在「建议动作」中提示联动查看（如佣金与销售额联动）。
