# 指标口径与字段来源（metrics-and-fields.md）

> 从 SKILL.md 引用。**当需要确认某个指标用哪个字段、哪个工具、什么口径时读本文件。**
> 所有字段名均已对照 `当前 MCP 契约与共享字段字典` 与 `当前 MCP 契约与共享字段字典` 核对；
> 不得凭记忆书写。完整口径说明见 `_shared/METRIC_FORMULAS.md`。

## 目录

- [一、指标 → 字段 → 工具对照表](#一指标--字段--工具对照表)
- [二、列表字段名（因工具而异）](#二列表字段名因工具而异)
- [三、币种与汇率](#三币种与汇率)
- [四、时间窗与基线](#四时间窗与基线)
- [五、达成率口径](#五达成率口径)
- [六、自算公式](#六自算公式)

---

## 一、指标 → 字段 → 工具对照表

| 指标 | 字段名 | 来源工具 | 列表字段 | 备注 |
|---|---|---|---|---|
| GMV / 销售额 | `productSales` | `get_profit_report_by_shop` / `_msku` | `rows`（标准化分页） | 赛狐订单口径 |
| 销量 | `salesNum` | `get_profit_report_by_*` | `rows`（标准化分页） | — |
| 毛利 | `grossProfit` | `get_profit_report_by_*` | `rows`（标准化分页） | 口径由 `profit_caliber_type` 决定 |
| 毛利率 | `grossProfitRate` | `get_profit_report_by_shop` / `_msku` | `rows`（标准化分页） | 小数（0.2364） |
| 广告花费 | `cpcCost` | `get_profit_report_by_*` | `rows`（标准化分页） | **为负数**；展示取绝对值 |
| 广告成本占比 | 自算 | — | — | `|cpcCost| / productSales` |
| 退款率 | `refundRate` | `get_profit_report_by_*` | `rows`（标准化分页） | 直接用赛狐算法 |
| 汇率 | `rate` | `get_profit_report_by_msku` | `rows`（标准化分页） | `rate` 在利润报表中（`06` 备注）；`currencyList` 未在 `01/06` 确认，勿用 |
| 标识（MSKU 利润报表） | **字段名未确认** | `get_profit_report_by_msku` | `rows`（标准化分页） | ⚠️ `06` 的 `msku`/`asin`/`sku`/`title`「出现于」均不含该工具，`01` 亦未列；须用真实响应核实后回填 `weekly.yaml → rank_identifiers`，取不到即标「需人工核查」 |

> 宽表专属指标（订单数 `orderNum`、ACOS `acos`、广告转化率 `adConversionRateCpc`、
> 广告点击率 `adClickRate`、会话数 `sessions`、可售天数 `availableDays`、评分 `rating`、
> 差评数 `lowReviewNum` 等）来自 `get_custom_report_page_list`，已不在本技能取数范围，
> 由 `custom-report-analysis` 技能承接。
> ⚠️ **`cvr` 不是宽表字段**：`06` 中 `cvr` 只出现在 walmart/temu 报表；
> 亚马逊侧的转化率请用报表现成的 `conversionRate` / `orderConversionRate`，
> 或自算 `orderNum / sessions`；**不要臆造 `cvr` 字段**。

## 二、列表字段名（因工具而异）

| 类型 | 列表字段 | 示例 |
|---|---|---|
| 分页类 | `data.rows` | `get_shop_page_list`、`get_custom_report_page_list`、`get_product_analyze_list` |
| 利润报表 | `data.rows`（标准化分页） | `get_profit_report_by_shop` / `_msku` / `_sku` / `_order` |
| 广告类（游标） | `data.rows`（标准化分页）+ 游标 | `get_sp_campaign_list` 等 |

> 用 `rows` 解析利润报表会拿到**空结果且不报错** —— 这是最常见的隐性错误。
> 组装层用共享库自动识别列表键（rows/reportList/itemList），网关默认标准化分页根字段为 `rows`。

## 三、币种与汇率

| 位置 | 币种 |
|---|---|
| `get_online_product_page_list.currency` | **USD**（售价口径） |
| `get_online_product_page_list.inventoryManage.currency` | **CNY**（成本口径） |
| `get_profit_report_by_*` 的 `currency` 参数 | 可选，按指定币种折算 |

规则：
1. 售价（USD）与库存成本（CNY）**不得直接相减**，须先折算到同一币种。
2. 跨站点聚合前，配置 `weekly.yaml → currency.aggregate_to`；未配置时**按币种分组**，
   禁止直接相加，并在报告中告警。
3. 折算后标注：**汇率数值、来源、取值日期**（`rate` 在 `get_profit_report_by_msku`）。

## 四、时间窗与基线

- 利润报表：`date_type=2`（按日），`start_date/end_date` 格式 **`yyyyMMdd`**，按日查询限最近 6 个月。
- 业务日边界以赛狐 `bizDay`（`yyyyMMdd`）为准；多站点时区不同，「昨日/本周」边界会不同。
- **不要**为 7 天/30 天基线各发一次调用；用 `get_profit_report_by_shop` 一次取回窗口内数据本地聚合。
- 对比方式：`get_product_analyze_list` 的 `compare_type_list`：**1 环比 / 2 同比 / 3 自定义**。

## 五、达成率口径

- 目标值一律读 `_shared/config/targets.yaml`（月/季/年 三档）。
- 月报：本月实际 ÷ 月目标。
- 周报：**无周目标配置**时，用「本月累计 ÷ 月目标」并在报告中注明这是口径降级，
  **不得**把月目标按天数摊派成周目标（会引入未经验证的假设）。
- 未配置目标 → 输出「未配置目标值，无法计算达成率」，**不编造目标**。
- 评级分级线读 `targets.yaml.achievement`（`excellent_min` / `warning_min`）。

## 六、自算公式

```python
avg_order_value = gmv / orders            # 分母为 0 → 「不适用」，不写 inf/NaN
ad_cost_rate    = abs(cpcCost) / gmv * 100
achievement     = actual / target * 100   # 目标来自 targets.yaml
```

> 只有赛狐**没有现成口径**时才自算；有现成字段的一律用官方值，避免与业务方对不上账。
