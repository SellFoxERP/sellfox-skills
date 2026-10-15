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
| GMV / 销售额 | `productSales` | `get_profit_report_by_shop` / `_msku` | `reportList` | 赛狐订单口径 |
| 销售额（报表宽表） | `salePrice` | `get_custom_report_page_list` | `rows` | 与 `productSales` 口径是否一致需业务确认 |
| 销量 | `salesNum` | `get_profit_report_by_*` | `reportList` | 宽表侧字段名是 `saleNum`，**勿混** |
| 订单数 | `orderNum` | `get_custom_report_page_list` | `rows` | 需 report_id |
| 毛利 | `grossProfit` | `get_profit_report_by_*` | `reportList` | 口径由 `profit_caliber_type` 决定 |
| 毛利率 | `grossProfitRate` | `get_profit_report_by_shop` / `_msku` | `reportList` | 小数（0.2364） |
| 广告花费 | `cpcCost` | `get_profit_report_by_*` | `reportList` | **为负数**；展示取绝对值 |
| 广告成本占比 | 自算 | — | — | `|cpcCost| / productSales` |
| ACOS | `acos`（报表现成）/ 自算 | `get_custom_report_page_list` | `rows` | 优先自算 `|adCost| / salePrice`（单位明确，用于阈值判定）；报表现成 `acos` 单位待核实，仅展示 |
| 广告转化率 | `adConversionRateCpc` | `get_custom_report_page_list` | `rows` | ACOS 归因用；`06` 确认。`adOrderConversionRate` 亦为真实字段，可替代使用 |
| 广告订单数 | `adOrderNum` | `get_custom_report_page_list` | `rows` | `06` 确认 |
| 广告点击率 | `adClickRate` | `get_custom_report_page_list` | `rows` | 素材衰退判定 |
| 退款率 | `refundRate` | `get_custom_report_page_list` / `get_profit_report_by_*` | `rows` / `reportList` | 直接用赛狐算法 |
| 会话数 | `sessions` | `get_custom_report_page_list` | `rows` | 流量 |
| 可售天数 | `availableDays` | `get_custom_report_page_list` | `rows` | 赛狐算法 |
| 评分 | `rating` | `get_custom_report_page_list` | `rows` | 字符串，需转 float |
| 差评数 | `lowReviewNum` | `get_custom_report_page_list` | `rows` | — |
| 汇率 | `rate` | `get_profit_report_by_msku` | `reportList` | `rate` 在利润报表中（`06` 备注）；`currencyList` 未在 `01/06` 确认，勿用 |
| 标识（宽表） | `msku` / `asin` / `sku` / `title` | `get_custom_report_page_list` | `rows` | 宽表榜单展示；`asin`/`parentAsin` 形如 `[{"key":"..."}]` |
| 标识（MSKU 利润报表） | **字段名未确认** | `get_profit_report_by_msku` | `reportList` | ⚠️ `06` 的 `msku`/`asin`/`sku`/`title`「出现于」均不含该工具，`01` 亦未列；须用真实响应核实后回填 `weekly.yaml → rank_identifiers`，取不到即标「需人工核查」 |

> ⚠️ **`cvr` 不是宽表字段**：`06` 中 `cvr` 只出现在 walmart/temu 报表。
> 亚马逊侧的转化率请用报表现成的 `conversionRate` / `orderConversionRate`（宽表），
> 或自算 `orderNum / sessions`；**不要臆造 `cvr` 字段**。

## 二、列表字段名（因工具而异）

| 类型 | 列表字段 | 示例 |
|---|---|---|
| 分页类 | `data.rows` | `get_shop_page_list`、`get_custom_report_page_list`、`get_product_analyze_list` |
| 利润报表 | `data.reportList` | `get_profit_report_by_shop` / `_msku` / `_sku` / `_order` |
| 广告类（游标） | `data.itemList` + `data.nextToken` | `get_sp_campaign_list` 等 |

> 用 `rows` 解析利润报表会拿到**空结果且不报错** —— 这是最常见的隐性错误。
> 本技能在 `call_paged(..., list_key="reportList")` 中显式指定，避免踩坑。

## 三、币种与汇率

| 位置 | 币种 |
|---|---|
| `get_online_product_page_list.currency` | **USD**（售价口径） |
| `get_online_product_page_list.inventoryManage.currency` | **CNY**（成本口径） |
| `get_custom_report_page_list.currency` | 报表币种，可配置 |
| `get_profit_report_by_*` 的 `currency` 参数 | 可选，按指定币种折算 |

规则：
1. 售价（USD）与库存成本（CNY）**不得直接相减**，须先折算到同一币种。
2. 跨站点聚合前，配置 `weekly.yaml → currency.aggregate_to`；未配置时**按币种分组**，
   禁止直接相加，并在报告中告警。
3. 折算后标注：**汇率数值、来源、取值日期**（`rate` 在 `get_profit_report_by_msku`）。

## 四、时间窗与基线

- 利润报表：`date_type=2`（按日），`start_date/end_date` 格式 **`yyyyMMdd`**，按日查询限最近 6 个月。
- 自定义报表：`start_date/end_date` 格式 **`yyyy-MM-dd`**。
- 业务日边界以赛狐 `bizDay`（`yyyyMMdd`）为准；多站点时区不同，「昨日/本周」边界会不同。
- 基线优先用**现成窗口字段** `sevenSaleNum`（近 7 天）/ `thirtySaleNum`（近 30 天）
  （`get_custom_report_page_list`，`06` 确认）—— 不需要自己拼时间窗；需要逐日趋势时才拉一次多天数据本地切片。
  > ✅ `threeSaleNum` 是真实字段（近 3 天窗口，见 `_shared/FIELD_DICTIONARY.md`），可用作短基线。
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
