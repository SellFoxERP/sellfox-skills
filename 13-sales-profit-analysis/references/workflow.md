# 取数计划与落盘纪律（agent 执行）

> SKILL.md 执行本技能时按本文件取数。调用纪律（发现/契约/重试/分页）以
> `_shared/SELLFOX_CLIENT.md` v2 为准；本文件只写本技能的**计划、参数注意与处置**。
> 字段名与符号方向见 [field-map.md](field-map.md)。

## 一、两个主力，各司其职

| | 用途 | 特点 |
|---|---|---|
| `get_profit_report_by_msku` / `_by_shop` / `_by_sku` | **金额真值**：桥接、费用结构、站点层结论 | 369 字段全返回、不能选列；`_by_msku`/`_by_shop` 有 `show_on_day` 聚合开关 |
| `get_product_analyze_list` | **排名与多维**：ASIN/店铺/站点榜、比率、广告、库存、评分 | 顶层 `fields`/`field_modules` 投影 → 行极小；`dim_type` 1 ASIN/2 父ASIN/3 MSKU/4 SKU/5 SPU/6 店铺/7 业务员；`sub_type` 0不汇总/1 ASIN/2标签/3品牌/4分类；`compare_type_list` 1环比/2同比/3自定义 |

⚠️ **两者毛利润不同基**：实测同店同窗口，两工具毛利率可差 4 倍以上（如 10.6% 对 2.4%）。
一块来源已定位：产品分析的 `adTotalCost` 等于利润报表的 `cpcSpCost`（广告账单口径），而利润报表
`cpcCost` 是结算报告口径（差额挂 `cpcCostAdExpensesDiff`/`cpcCostSettlementDiff`）。
**规则：金额与桥接只用利润报表；产品分析只用于排序方向，两层的数不相加、不同表。**

辅助工具（按需；**入参以当轮契约为准**，首次使用前 `search(tool_ids=[...], detail=true)` 读一次，组合公式已列全在 field-map「契约自带组合」）：

| 工具 | 什么时候用 | 关键注意 |
|---|---|---|
| `get_profit_report_by_order` | 订单级核对、**延期结算/拒付**专项 | 口径词 `caliber_type`（发货=`delivery`）；分页 **string**；`begin`/`end` ≤31 天；独有 `deferredDays` 等；另有十余项弃用字段（field-map §二） |
| `get_order_detail` | 单笔订单核对 | 自带官方加总定义（`totalRevenue`/`totalCost` 公式见 field-map「契约自带组合」）；行含买家信息，PII 脱敏 |
| `get_fba_shipping_head_fee_page_list` | 头程涨价要落到批次时（成本子链专项） | 首次用前读契约 |
| `get_amazon_reimbursement_report` | 赔偿、漏领追回（费用子链专项） | 首次用前读契约 |
| `get_custom_report_page_list` | 需要固定费用等**自定义口径**时 | `report_id` **只能从 Web 界面取**，不可猜；取不到就按内置字段口径并披露 |
| `get_multiplatform_sale_stats` | **别用于利润**——自述 `not_for 利润、毛利、广告归因` | 跨工具不可混算（field-map §四） |

## 二、取数计划表

**时间维度默认=按天**（`date_type=2` + `yyyyMMdd`，默认窗口近 14 天 vs 前 14 天，相邻不重叠）；月粒度 `yyyyMM` 仅在用户以**月语义**指代窗口时使用——「本月/上月/这个月/上个月/X月」都算点名月份（切月），「最近 N 天/上周 vs 这周」仍走日窗口。按序取数；已知 tool_id 直接 `search`（一次 ≤5 个）拿契约再 action。多店铺批次可并行，
每步落盘完整信封并核对 status。

| # | 落盘文件 | 工具 | 关键参数（类型注意） | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/shops.json` | `get_shop_page_list` | `page_no`/`page_size` 为 **string**（`page_size=100`） | `rows` | 105 | 按 `totalPage` 翻完；`status` 枚举以当轮契约与实测为准（线上见过 `active`/`inactive` 与数字码两种形态）——授权失效的店铺别当"没生意"；行可能重复，按唯一店铺 ID 去重 |
| 2 | `out/raw/agg_current.json` / `agg_base.json` | `get_profit_report_by_msku`（或 `_by_shop`） | `date_type=2`；`start_date`/`end_date` `yyyyMMdd`；`show_on_day=false`；`profit_caliber_type` **string**；`page_no`/`page_size` **integer** | `reportList` | 5 | 窗口聚合 1 行，本期/基期各一次（总额与桥接用）；`page_size=5` 足够 |
| 3 | `out/raw/daily_current/<seg>.json`、`daily_base/<seg>.json`（每段一个） | 同上 | `show_on_day=true`，**每段 ≤7 天** | `reportList` | 7×段数 | 变化归因类默认做（**两期都要**：基期贷项剔除与停投对照都靠它）；纯快照或已确认无贷项/停投可省 |
| 4 | `out/raw/rank_current.json` / `rank_base.json` | `get_product_analyze_list` | 顶层 `fields`（字段常量 ≤199）/ `field_modules`（模块 ≤14）——**anyOf 至少一项非空**（投影后行极小；**参数以当轮契约为准**，实测没有 options 投影嵌套参数）；`dim_type` 1/6；`merge_asin` 显式 false；分页 **integer**；店铺参数名 `shop_id_list`（**string 数组**，与利润工具不同） | `rows` | 200 | 赚钱榜/亏损榜各发一次 desc=true/false（排序常量以当轮契约为准——fields 字典的利润列常量是 `profitPrice`，无 `gross_profit`）；**亏损榜必须再发一次 `desc=false`**（负毛利在降序尾部，从 desc 结果筛 `<0` ≈ 空集） |
| 5（可选） | `out/raw/order_current.json` | `get_profit_report_by_order` | `begin`/`end` `yyyy-MM-dd`，≤31 天；`caliber_type`；分页 **string** | `reportList` | 60 | 延期结算/拒付核对；行含买家信息，**渲染前 PII 脱敏** |

**窗口聚合成一行（本技能最重要的取数技巧）**：

```
date_type=2 + show_on_day=false + start_date/end_date（yyyyMMdd）→ 整个区间返回 1 行，含全部费用字段
→ 一次拿到窗口级支出结构，免去逐日翻页；page_size 给 5 足够
反例：date_type=2 + show_on_day=true + page_size=14|100 → RESULT_BUDGET_EXCEEDED（行数×369 字段超返回预算）
日分段：show_on_day=true 时每段 ≤7 天为安全默认；返回预算上限随数据行宽波动（曾实测 14 行被拒、亦有 14 天一次请求通过），超限按 RESULT_BUDGET_EXCEEDED 处置（§四三步）；更长窗口按 7 天切片多次取，
        用聚合行做交叉校验（日分段各项加总应 == 聚合行同字段，容差 ≤0.01，不等先修取数再谈结论）
```

**取数边界**：月 `yyyyMM` ≤12 个月；日 `yyyyMMdd` 限最近 6 个月；order ≤31 天。
只能报毛利：利润族只有 `grossProfit` `grossProfitRate` `roi`（旁支 `grossInvestment`/`grossIncome`；
order 侧同义 `grossProfitMargin`/`returnOnInvestment`），全表无净利字段。
null/省略 ≠ 0。时间键按粒度二选一：月粒度 `bizDate`（`bizDay=0`），日粒度 `bizDay`（`bizDate=0`），`year` 恒 0 别用。

## 三、params 关键差异速查

| | msku / shop / sku | order | 产品分析 |
|---|---|---|---|
| 日期 | `start_date`/`end_date`（`yyyyMM` 或 `yyyyMMdd`）+ `date_type` 1月/2日 + `show_on_month`/`show_on_day` | `begin`/`end`（`yyyy-MM-dd`，≤31 天） | `start_date`/`end_date`（`yyyy-MM-dd`） |
| 口径 | `profit_caliber_type` | `caliber_type` | —（另核） |
| 排序 | `order_by` **snake_case**（`gross_profit`/`product_sales`/`gross_profit_rate`/`roi`/`sales_num`；一级费用字段皆可排）+ `desc` | `order_field` + `order_value` | `order_by` + `desc` |
| 分页 | `page_no`/`page_size` **integer**，≤200 | **string** | integer；`rows`/`totalSize` |
| 店铺 | `shop_ids` **integer 数组** | `shop_ids` | `shop_id_list` **string 数组** |
| 其他 | `search_type`×`search_mode`×`search_contents`、`label_ids`、`marketplace_ids`（要用再查契约） | `fulfillment_channel`、`settlement_status`、`transaction_type` | `fields`/`field_modules`（anyOf 至少一项非空；**fields 只收字段常量码，不认中文名**）、`merge_asin`（显式 false）、`is_new_or_moving_or_in_stock` |

长尾费用项名**先 `search(detail=true)` 拿字典再取，别猜**。

## 四、返回体积、覆盖与 action 状态处置

**RESULT_BUDGET_EXCEEDED 是体积问题不是参数问题**（`retryable: true`，提示 reduce page_size
or select published fields）。处置顺序：① 改 `show_on_day=false` 聚合 ② 换有投影的工具
（产品分析）③ 降 `page_size`。

**覆盖判定优先级**（本技能实测补充，与 `_shared` §4 配合）：
`coverage` 优先；但实测利润工具 `coverage.complete=false` 与 `pagination.has_more=false`
可能**同时出现**——此时以 `data.totalPage`/`pageNo` 翻完为准，并在口径行披露
「coverage 与分页标记矛盾，按 totalPage 判定」。两者一致时按 `_shared` 常规规则。

| action 返回 | 处置 |
|---|---|
| `INVALID_PARAMS` + `field_errors`（无 `upstream` 段） | 本地就被挡，没发上游 → 按 `suggested_action` 修类型/枚举后重试一次 |
| `RESULT_BUDGET_EXCEEDED`（`retryable: true`） | 体积超限 → 上面三步 |
| `UPSTREAM_BUSINESS_ERROR` 且 `upstream.stage=business` | 已过本地校验、被上游拒绝（鉴权、授权范围、数据权限）。调用方改参数无用；`retryable=false` 就不要再试；把 `trace_id` 交上游排查。凭证与连接配置属运维，不在本技能范围 |
| `CONTRACT_VERSION_MISMATCH` | 重 search 一次、按新 schema 重建参数、只重试一次 |
| `output_validation: documented_schema_mismatch` | 字段差异保留原值不转类型（实测 `shopId`、`commodityInfos` 等恒 null）；空值与省略都不是零 |

**能连上 ≠ 有权限**：网关在 `initialize` 阶段不校验凭证，未授权也能拿到工具目录；判据只有
action 的返回。`contract_version` 一律取当轮 `help`/`search` 的值（help 候选里就带，不必每次
search；要用没把握的字段名才 `search(detail=true)`——不带它就没有字段描述）。

## 五、落盘纪律（对齐 _shared §6/§7）

- 每步完整信封（status/data/coverage/pagination/warnings/trace_id）原样落盘 `out/raw/`；
  不在对话中复述行级数据，分析交给脚本；
- 空数据（`data` 空 + complete）= 无数据 → 落盘保留，报告标「无数据（非 0）」，不重试；
- `status=failed` 的信封保留 error_code，不进计算，渲染层如实披露；
- 演示指纹（MOCK-/SHOP-DEMO-/「演示」）由计算面扫描，报告加水印，结论不作为经营依据；
- 达到 max_rows 停止并保留 coverage 供披露；只有实际按指标排序后才称 Top N。
