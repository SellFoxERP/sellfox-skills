# 异常判定与归因规则（anomaly-rules.md）

> SKILL.md 判定红线、生成归因线索时读本文件。
> **阈值一律读 `_shared/config/thresholds.yaml`，绝不硬编码。**

## 目录

- [一、判定原则](#一判定原则)
- [二、各指标阈值（对应 thresholds.yaml）](#二各指标阈值对应-thresholdsyaml)
- [三、归因路径（每条标注来源工具）](#三归因路径每条标注来源工具)
- [四、判定条件禁区](#四判定条件禁区)
- [五、红线选取](#五红线选取)

---

## 一、判定原则

1. **对比基线，不做孤立判定**：所有核心指标以「昨日 / 近 7 天均值 / 近 30 天均值 / 偏离度」呈现。
2. **偏离度公式**：`(昨日 - 基线) / |基线| × 100%`。分母为 0/None → 标「不适用」，**不输出 inf/NaN**。
3. **双阈值方向**：销售额/订单/CVR/毛利看**下跌**（`deviation <= -阈值`）；
   ACOS/退款率/成本看**上涨**（`deviation >= 阈值` 或绝对值超上限）。
4. **异常必须带归因线索**，不能只报数字。
5. 数据缺失 → 「需人工核查」；空数据 → 「无数据」；**都不写 0**。

---

## 二、各指标阈值（对应 thresholds.yaml）

| 分组 | 键 | 含义 | 默认 |
|---|---|---|---|
| `sales` | `drop_pct_vs_7d` | 销售额低于近 7 天均值 % | 30 |
| `sales` | `drop_pct_vs_30d` | 销售额低于近 30 天均值 % | 25 |
| `orders` | `drop_pct_vs_7d` | 订单量低于近 7 天均值 % | 30 |
| `conversion` | `drop_pct_vs_30d` | CVR 低于近 30 天均值 % | 25 |
| `conversion` | `sessions_zero_days` | 连续 N 天会话为 0 | 7 |
| `ad` | `acos_abs_max` | ACOS 绝对值上限 % | 35 |
| `ad` | `acos_rise_pct_vs_7d` | ACOS 高于近 7 天均值 % | 50 |
| `ad` | `acos_target` | 目标 ACOS %（优质词判定） | 25 |
| `ad` | `roas_min` | ROAS 下限 | 2.0 |
| `ad` | `click_rate_drop_pct` | `adClickRate` 环比下降 %（素材衰退） | 30 |
| `ad` | `high_cost_zero_order_days` / `zero_order_cost_min` | 高花费零转化天数 / 金额下限 | 7 / 30 |
| `inventory` | `available_days_min` / `available_days_critical` | 可售天数下限 / 紧急线 | 30 / 15 |
| `inventory` | `inv_age_271_365_pct_max` / `inv_age_365_plus_pct_max` | 库龄占比上限 % | 10 / 5 |
| `inventory` | `turnover_days_max` | 周转天数上限 | 120 |
| `listing` | `buybox_pct_min` | 购物车占比下限 % | 90 |
| `after_sales` | `refund_rate_rise_pct_vs_30d` | 退款率高于近 30 天均值 % | 50 |
| `after_sales` | `low_review_daily_max` | 单日新增差评上限 | 3 |
| `profit` | `gross_profit_rate_min` / `drop_pct_vs_30d` | 毛利率下限 % / 毛利下跌 % | 0 / 25 |
| `redline` | `max_items` | 「今日红线」最多条数 | 5 |

> 技能若发现某项阈值缺失，**不得静默用内置值** —— 按 `config_loader.ensure_params`
> 一次性批量询问补全，并在输出标注来源（配置命中 / 本次填写 / 默认值）。

---

## 三、归因路径（每条标注来源工具）

销售额跌（STEP 02）：

```
销售额跌
├─ 拆：订单量跌 vs 客单价跌            → saleNum / orderNum / avgSalePrice  【自定义报表】
├─ 再拆店铺 / ASIN                     → shopId 分组 → msku 下钻          【自定义报表】
├─ 是 Sessions 跌还是 CVR 跌？         → sessions / conversionRate         【自定义报表】
└─ 若店铺级也要看，看利润报表           → productSales / salesNum         【店铺利润报表】
```

CVR 跌（STEP 05）：

```
├─ 丢购物车？   → buyBoxPercent（<90% 触发）              【自定义报表】
├─ 差评拉低？   → lowReviewNum / reviewRate / rating       【自定义报表】
└─ 价格变动？   → listingPrice / buyboxPrice               【自定义报表/在线产品】
```

ACOS 涨（STEP 03）：

```
├─ 竞价涨？     → cpc                                      【自定义报表】
├─ 素材衰退？   → adClickRate（环比下降 30%）              【自定义报表】
└─ 转化跌？     → adConversionRateCpc（字段字典 06 确认）  【自定义报表 ⭐】
```

> `adConversionRateCpc` 经字段字典 06 确认存在于 `get_custom_report_page_list`（489 字段，样例 2.33），
> 可直接作为 ACOS 上涨的归因线索。`adOrderConversionRate`（广告订单转化率）同为真实字段，可交叉验证。

库存预警（STEP 04）：

```
├─ 可售天数   → availableDays（<30 预警，<15 紧急）
├─ 在途能否救急？ → inboundWorking / inboundShipped / inTransit  【自定义报表】
└─ 补货建议量 → suggestNumSum（赛狐官方口径，不自算）             【自定义报表】
```

毛利下滑（延伸；本技能只做提示，深度归因属利润诊断技能）：

```
├─ 销售额跌？    → productSales / saleNum                     【店铺/MSKU 利润报表】
├─ 成本涨？      → purchaseCost / headTripPrice               【自定义报表】
├─ 平台费用涨？  → commission / fbaFulfillmentFee / storageFee【MSKU 利润报表】
├─ 广告失控？    → cpcCost / cpcCostRate                      【MSKU 利润报表】
└─ 汇损？        → rate / currencyList                        【MSKU 利润报表】
```

> ⚠️ `reimbursementAmount` / `warehouseLostAmount` / `missingFromInboundAmount` / `deferredAmount` /
> `purchaseCost` / `headTripPrice` **只存在于 `get_custom_report_page_list`**，
> **不在 `get_profit_report_by_msku`**。归因时逐字段标注所属工具。

---

## 四、判定条件禁区

| 字段 | 规则 |
|---|---|
| `state`（广告活动） | 仅用实测确认的 **`enabled` / `paused` / `archived`** |
| `servingStatus` | 字段真实存在（实测 `CAMPAIGN_PAUSED`），但取值 **`BUDGET_CAPPED` 未经实测验证，不得作为诊断规则的判定条件**（仅可作人工提示） |
| `get_online_product_page_list` 币种 | 顶层 `currency`=USD，`inventoryManage.currency`=CNY，**跨币种不得直接相减** |

---

## 五、红线选取

1. 收集全部触发项（销售额/订单/ACOS/库存紧急…）+ 其 `|偏离度|`。
2. 按 `|偏离度|` 降序排列。
3. 取前 `thresholds.redline.max_items`（默认 **≤5**）条进入「🔴 今日红线」。
4. 库存**紧急**项（`availableDays < available_days_critical`）即使无偏离度也优先补位。
5. 每条红线**必须**附归因线索（见上表），不能只报数字。