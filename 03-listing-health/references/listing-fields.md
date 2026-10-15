# 字段字典核对（listing-fields.md）

> 何时读：需要核对某字段属于哪个工具、能否取值、或写归因表的来源标注时。
> 权威依据：`_shared/FIELD_DICTIONARY.md`。
> 机器可读版：`config/field-map.yaml`（脚本读取它，本文件供人读）。

## 目录

- [1. 三个数据源与列表字段名](#1-三个数据源与列表字段名)
- [2. 字段 → 数据源对照表](#2-字段--数据源对照表)
- [3. 只在宽表的字段（无 report_id 即降级）](#3-只在宽表的字段无-report_id-即降级)
- [4. 拿不到的字段](#4-拿不到的字段)

## 1. 三个数据源与列表字段名

| 工具 | 列表字段 | 分页 | 备注 |
|---|---|---|---|
| `get_online_product_page_list` | `data.rows` | 页码 `page_no`，服务端上限 20 | 79 字段，Listing 体检主源 |
| `get_custom_report_page_list` | `data.rows` | 页码 `page_no`，需 `report_id` | 489 字段宽表 |
| `get_product_analyze_list` | `data.rows` | 页码 `page_no` | 字段集合取决于 `fields` 入参 |
| `get_product_by_label` | `data.rows` | 页码 **`page_num`** | 标签→产品 |

> ⚠️ 不要统一按 `rows` 解析**所有**工具：利润报表是 `reportList`，广告类是 `itemList`+`nextToken`。本技能不涉及后两类，但共用 `_shared` 客户端时须按工具区分。

## 2. 字段 → 数据源对照表

| 字段 | online | custom | analyze | 用途 |
|---|:--:|:--:|:--:|---|
| `onlineStatus` | ✅ | | | 不可售/删除判定（P0） |
| `listingId` | ✅ | | | 去重主键 |
| `buyBoxWinner` | ✅ | | | 丢购物车（退化布尔信号） |
| `buyBoxPercent` | | ✅ | | 丢购物车占比（P1） |
| `listingPrice` | ✅ | ✅ | | 价格（数组 `[{"key":...}]`） |
| `standardPrice` / `salePrices` | ✅ | | | 价格 |
| `rating` / `ratingCount` | ✅ | ✅ | | 评分下滑（`rating` 为字符串） |
| `lowReviewNum` | | ✅ | | 差评数 |
| `bsrRank[]` | ✅ | | ✅ | BSR 名次（`rank`/`bigCategory`/`categoryName`） |
| `bigCategoryRanking` / `smallCategoryRanking` | | ✅ | | 类目名次 |
| `sessions` / `pageViews` | | ✅ | | 无流量 / 转化 |
| `conversionRate` | | ✅ | | 有流量无转化 |
| `day7SaleNum` / `day30SaleNum` / `saleNum` | ✅ | | | 销量衰退基线（现成窗口字段） |
| `quantity` | ✅ | | | 可售库存（退化断货信号） |
| `availableDays` | | ✅ | | 断货可用天数 |
| `labels[]` / `productLabelList[]` | ✅ | ✅ | | 标签缺失 |
| `categoryNameList` | | | ✅ | 类目名 |
| `fbmAvailableDays` | | | ✅ | FBM 可用天数（analyze 也有） |
| `currency`（顶层） | ✅ | ✅ | ✅ | USD |
| `inventoryManage.currency` | ✅ | | | **CNY**（跨币种须显式处理） |

## 3. 只在宽表的字段（无 report_id 即降级）

`sessions`、`pageViews`、`conversionRate`、`buyBoxPercent`、`buyboxPrice`、`lowReviewNum`、`bigCategoryRanking`、`smallCategoryRanking`、`availableDays`、`orderConversionRate`、`naturalConversionRate`。

以上均**只出现在 `get_custom_report_page_list`**（06 字段字典已核对）。无 `report_id` 时按 `references/workflow.md` §4 降级并标注「需人工核查」。

> 注：`buyBoxPercent` **不在** `get_online_product_page_list`——该表只有 `buyBoxWinner` 布尔，勿混用。

## 4. 拿不到的字段

竞品 BuyBox 价 / 竞品售价、类目平均转化率基准、账户健康/绩效通知/政策合规、评论原文、BuyBox 竞品卖家身份、抑制原因。

一律标 **⚠️ 需人工核查**，绝不编造；空数据标「无数据」，不写 0。