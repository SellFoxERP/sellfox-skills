# 指标口径白皮书（METRIC_FORMULAS.md）

> **模板 + 官方口径说明。** 技能计算与展示指标时**必须**遵循本文件。
> 与财务/业务方共同确认后填写「**___**」处。
>
> **为什么需要它**：避免「技能算的和财务算的对不上」。一旦口径分叉，技能的可信度立即归零。
> 配套：`config_loader.py`（读配置）、`config/thresholds.yaml`（阈值）。

---

## 目录

- [一、利润口径必须显式选择（最重要的口径决策）](#一利润口径必须显式选择最重要的口径决策)
- [二、指标口径表（技能必须遵循）](#二指标口径表技能必须遵循)
- [三、字段来源与数据源](#三字段来源与数据源)
- [四、币种与汇率](#四币种与汇率)
- [五、时间窗与业务日边界](#五时间窗与业务日边界)
- [六、基线对比的正确做法](#六基线对比的正确做法)
- [七、自算指标公式](#七自算指标公式)
- [八、输出中的口径标注（不可省略）](#八输出中的口径标注不可省略)

---

## 一、利润口径必须显式选择（最重要的口径决策）

`get_profit_report_by_*` 都有 **`profit_caliber_type`** 参数（实测确认，见
`当前 MCP 契约与共享字段字典`）：

| 取值 | 含义 | 默认 |
|---|---|---|
| `settlement` | **结算口径**（按实际结算时间归集） | ✅ 默认 |
| `shipment` | **发货口径**（按发货时间归集） | — |

> ⚠️ **同一时间段两者结果不同。** 技能必须在输出中**标注用的是哪个口径**。
> 不标注必然与财务对不上账。

**选择规则**：

- 默认用 `settlement`（与财务对账口径一致）。
- 若用户明确要求「按发货看」，用 `shipment`。
- **同一次报告内不得混用两种口径**；若跨数据源（如自定义报表无 `profit_caliber_type`），
  必须在数据说明中写明这一差异。

```python
# 每次调用显式传参
client.call("get_profit_report_by_shop", {
    "start_date": "20260923", "end_date": "20260929", "date_type": 2,
    "profit_caliber_type": "settlement",     # ← 显式选择
})
```

---

## 二、指标口径表（技能必须遵循）

| 指标 | 来源字段 | 来源工具 | 口径说明 |
|---|---|---|---|
| 销售额 | `productSales` | `get_profit_report_by_*` | 赛狐订单口径；含税/不含税：**___** |
| 销售额（报表） | `salePrice` / `revenueAmount` | `get_custom_report_page_list` | 与 `productSales` 口径是否一致：**___** |
| 订单数 | `orderNum` | `get_custom_report_page_list` | — |
| 销量 | `saleNum` / `salesNum` | 自定义报表 / 利润报表 | 两处字段名不同，勿混 |
| 客单价 | 自算 | — | = 销售额 / 订单数 |
| 毛利 | `grossProfit` | `get_profit_report_by_*` | 口径由 `profit_caliber_type` 决定 |
| 毛利率 | `grossProfitRate` | `get_profit_report_by_shop` / `_msku` | = 毛利 / 销售额 |
| 利润率（报表） | `profitRate` / `profitRateNet` | `get_custom_report_page_list` | 与 `grossProfitRate` 口径差异：**___** |
| 广告花费 | `cpcCost` | `get_profit_report_by_*` | 注意为负数（费用） |
| 广告成本占比 | 自算 | — | = \|`cpcCost`\| / `productSales` |
| ACOS | `acos` / `spAcos` / `sbAcos` / `sdAcos` | `get_custom_report_page_list` | 报表现成口径，不自算 |
| ROAS | `roas` | 广告报表 / 自定义报表 | 指标树中官方口径：`advertising_spend → ROAS` |
| CPC | `cpc` | 广告报表 / 自定义报表 | — |
| 退款率 | `refundRate` | `get_custom_report_page_list` / 利润报表 | 直接采用赛狐算法，不自算 |
| 退款金额 | `refundPrice` / `refundSales` | 自定义报表 / 利润报表 | 注意符号 |
| 周转天数 | `turnoverDaysMonth` | `get_custom_report_page_list` | **直接采用赛狐算法**，不自定义 |
| 周转率 | `turnoverRateMonth` | 自定义报表 | 赛狐算法 |
| ISR | `isrMonth` | 自定义报表 | 赛狐算法 |
| 可售天数 | `availableDays` | `get_custom_report_page_list` | 基于月销速率，赛狐算法 |
| 日均销量 | `salesVelocityMonth` / 30 | 自定义报表 | 若需自算见 §七 |
| 补货建议量 | `suggestNumSum` | `get_custom_report_page_list` | **直接用官方口径** |
| 库龄占比 | `invAge271To365Days` / `totalInventory` 等 | 自定义报表 | 见 §七 |
| 购物车占比 | `buyBoxPercent` | `get_custom_report_page_list` | — |
| 会话数 | `sessions` | `get_custom_report_page_list` | 流量 |
| 转化率 | `conversionRate` / `orderConversionRate` | 自定义报表 | 报表现成口径 |
| 广告转化率 | `adConversionRateCpc` | `get_custom_report_page_list` | 字段字典 06 确认（样例 `2.33`），可用于 ACOS 归因 |
| 广告点击率 | `adClickRate` | 自定义报表 | 素材衰退判定用 |
| 评分 | `rating` | 自定义报表 / 在线产品 | 字符串，需转 float |
| 差评数 | `lowReviewNum` | 自定义报表 | — |
| 差评率/好评率 | `reviewRate` | 自定义报表 | 指标树官方口径：`review_count → 好评率/差评数量` |

> 💡 **选型口诀**：要**广**（指标种类多）用 `get_custom_report_page_list`；
> 要**深**（费用拆解细）用 `get_profit_report_by_msku`；
> 要 **Listing 属性**用 `get_online_product_page_list`。

---

## 三、字段来源与数据源

| 数据源 | 字段数 | 定位 | 列表字段 |
|---|---|---|---|
| `get_custom_report_page_list` | **489** | 全指标宽表（库存/流量/广告/评论/成本/赔偿） | `data.rows` |
| `get_profit_report_by_msku` | **171** | 财务深度（亚马逊费用逐项拆解） | `data.reportList` |
| `get_profit_report_by_shop` | 店铺聚合 | 店铺级利润 | `data.reportList` |
| `get_profit_report_by_order` | 74 | 订单级结算 | `data.reportList` |
| `get_online_product_page_list` | 79 + `inventoryManage`(41) | Listing 属性 + FBA 库存 | `data.rows` |

> ⚠️ **两个报表字段不通用**（实测确认）：
> `reimbursementAmount`、`warehouseLostAmount`、`missingFromInboundAmount`、`deferredAmount`、
> `purchaseCost`、`headTripPrice` **只存在于 `get_custom_report_page_list`**，
> **不在** `get_profit_report_by_msku`。利润诊断技能因此需要**两个数据源**，
> 归因表**每个字段后必须标注来源工具**。

### 归因路径（每条都标注来源）

```
毛利下滑
├─ 销售额跌？    → productSales / saleNum 环比                    【msku报表】
├─ 成本涨？      → purchaseCost / headTripPrice 环比              【自定义报表】
├─ 平台费用涨？  → commission / fbaFulfillmentFee / storageFee    【msku报表】
├─ 广告失控？    → cpcCost / cpcCostRate / acos 环比              【msku报表】
├─ 退货激增？    → refundNum / refundPrice / returnSaleNum        【msku报表】
├─ 仓储费异常？  → storageFee / fbaLongTermStorageFeeAmount /
│                  fbaOverageFeeAmount（长期仓储费=库龄271+天）    【msku + 自定义报表】
├─ 赔偿漏领？    → reimbursementAmount / warehouseLostAmount /
│                  missingFromInboundAmount                      【自定义报表】
├─ 延期结算？    → deferredAmount / deferredReason                【自定义报表】
└─ 汇损？        → rate / currencyList                            【msku报表】
```

> `currencyList` 与 `rate` 经实测确认真实存在于 `get_profit_report_by_msku`（171 字段），可直接使用。

---

## 四、币种与汇率

| 位置 | 币种 | 说明 |
|---|---|---|
| `get_online_product_page_list.currency` | **USD** | 售价口径 |
| `get_online_product_page_list.inventoryManage.currency` | **CNY** | **成本口径** |
| `get_custom_report_page_list.currency` | USD（可配置） | 报表币种 |
| `get_profit_report_by_*` 的 `currency` 参数 | 可选 | 可按指定币种折算 |
| `rate` / `currencyList` | 汇率 | 出现在 `get_profit_report_by_msku` |

**规则**：

1. **售价（USD）与库存成本（CNY）不得直接相减**，必须先折算到同一币种。
2. 折算后必须在输出中标注：**汇率数值、来源、取值日期**。
3. 跨站点聚合时，各站点币种可能不同，须逐站点标注。

---

## 五、时间窗与业务日边界

- 利润报表：`date_type=2`（按日），`start_date=end_date=昨日`，格式 **`yyyyMMdd`**。
- 自定义报表：`start_date` / `end_date`，格式 **`yyyy-MM-dd`**。
- 按月查询：格式 `yyyyMM`（利润报表最大限制 12 个月）；按日查询限制最近 6 个月。

> ⚠️ **「昨日」的业务日边界需显式定义**：多站点（US/EU/JP）时区不同，「昨日」起止不同。
> 技能必须写明采用哪个时区口径，跨站点聚合时标注汇率来源与日期。
> 本方案默认：**以数据源（赛狐 ERP）的 `bizDay` 为准**，格式 `yyyyMMdd`。

---

## 六、基线对比的正确做法

| 方案 | 做法 | 适用 |
|---|---|---|
| **A（推荐）** | 用报表现成窗口字段 `sevenSaleNum` / `thirtySaleNum`，**不需要自己拼时间窗** | 只要环比基线 |
| B | 一次取 30 天，在技能内按日切片 | 需要逐日趋势 |

> 不要为 7 天/30 天基线各发一次调用 —— 那把 3 次调用降到 1 次。
> 注意：服务端分页上限 20，取 30 天数据时**必须按店铺/日期分批**并做 `max_rows` 截断。

---

## 七、自算指标公式

只有当赛狐**没有现成口径**时才自算；有现成字段的一律直接用官方值（避免与业务方对不上账）。

```python
# 客单价
avg_order_value = productSales / orderNum

# 广告成本占比（注意 cpcCost 为负）
ad_cost_rate = abs(cpcCost) / productSales * 100

# 日均销量（补货用）
daily_velocity = salesVelocityMonth / 30.0

# 库龄占比（滞销识别）
inv_271_365_pct = invAge271To365Days / totalInventory * 100
inv_365_plus_pct = invAge365PlusDays / totalInventory * 100

# 达成率（周报/月报，目标值来自 config/targets.yaml）
achievement = actual / target * 100
```

> 规则：**分母为 0 时不得输出 `inf`/`NaN`** —— 标注「无数据」或「不适用」，不写 0。

---

## 八、输出中的口径标注（不可省略）

每份报告**必须**包含数据说明区块：

```markdown
---
### 数据说明
- 利润口径：settlement（结算口径）
- 币种：销售额 USD ｜ 库存成本 CNY（已按 rate=<值> 折算，汇率日期 <日期>）
- 业务日边界：以赛狐 bizDay 为准（yyyyMMdd）
- 数据源：赛狐 ERP（get_custom_report_page_list / get_profit_report_by_msku）
- 数据截止：{日期} 23:59

### ⚠️ 本报告未能覆盖（赛狐 MCP 无对应数据源）
- 账户健康 / 绩效通知 — 请人工登录 Seller Central 核查
- 买家消息 / 待回工单 — MCP 无客服接口
- 政策合规 / 绩效风险 — MCP 无此接口
- 评论原文 — 仅有聚合指标（好评率/差评数）
```

**参数来源标注**（来自 `config_loader.ensure_params`）：

```
| 参数 | 取值 | 来源 |
|---|---|---|
| 预警阈值 | 30% | 配置命中 |
| 到货周期 | 45 天 | 默认值 ⚠️ |
```

> **核心原则**：**默认值必须显式标注，不能混进真实数据里。**
> 使用者一眼能看出哪些结论建立在不确定的假设上，可信度自知。