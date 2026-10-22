# 广告字段核对表（field-dictionary.md）

> 何时读：写诊断规则、构造写工具参数、或需要确认某字段是否真实存在时。
> 所有字段名核对自 `_shared/FIELD_DICTIONARY.md`，不凭记忆书写；工具参数以网关当次契约为准。

## 目录

- [一、读接口字段](#一读接口字段)
- [二、写接口参数](#二写接口参数)
- [三、绩效字段（下载任务 xlsx）](#三绩效字段下载任务-xlsx)
- [四、字段确认状态](#四字段确认状态)
- [五、列表字段与分页形态](#五列表字段与分页形态)

---

## 一、读接口字段

### `get_sp_campaign_list`（游标分页，`itemList` + `nextToken`）

请求：`shop_id`(必填) / `state` / `campaign_id` / `portfolio_id` / `page_size` / `next_token`

返回每项：`shopId`、`state`（enabled/paused/archived）、`portfolioId`、`campaignId`、`name`、
`targetingType`（manual/auto）、`budget`、`servingStatus`、`offAmazonBudgetControlStrategy`、
`campaignSite`、`strategy`、`adjustments`、`startDate`、`endDate`、`creationDate`、`lastUpdatedDate`。

### `get_sp_keyword_list`（游标分页，`itemList` + `nextToken`）

请求：`shop_id`(必填) / `state` / `campaign_id` / `group_id` / `page_size` / `next_token`

返回每项：`shopId`、`state`、`campaignId`、`groupId`、`keywordId`、`keywordText`、`bid`、
`matchType`（exact/phrase/broad）、`servingStatus`。

### `get_sp_ad_product_list`（游标分页）

返回每项：`shopId`、`state`、`campaignId`、`groupId`、`adId`、`asin`、`sku`、`servingStatus`。

### `get_sp_product_targeting_list`（游标分页）

返回每项：`shopId`、`state`、`campaignId`、`groupId`、`targetId`、`bid`、`servingStatus`、
`expressionType`、`expression`、`resolvedExpression`。

### `get_sp_negative_keyword_list`（游标分页，用于 read-back）

返回每项：`shopId`、`state`、`campaignId`、`groupId`、`keywordId`、`keywordText`、
`matchType`（`negativeExact`/`negativePhrase`）、`servingStatus`。

> ⚠️ 否定关键词的 `matchType` 取值是 `negativeExact`/`negativePhrase`，
> 与正向关键词的 `exact`/`phrase`/`broad` **不同**，不要混用。

---

## 二、写接口参数

| 工具 | 请求 | items 必填字段 |
|---|---|---|
| `edit_sp_campaign` | `shop_id`、`items` | `campaignId`；可选 `state`/`budget`/`bidStrategy`/`placement*`/`startDate`/`endDate` |
| `edit_sp_ad_group` | `shop_id`、`items` | `groupId`；可选 `state`/`defaultBid` |
| `edit_sp_targeting` | `shop_id`、`items` | `targetId`、`targetingType`(keyword/product)；可选 `state`/`bid` |
| `edit_sp_ad_product` | `shop_id`、`items` | `adId`、`state` |
| `create_sp_negative_keyword_targeting` | `shop_id`、`items` | `campaignId`、`adGroupId`、`keywordText`、`matchType`(exact/phrase) |
| `create_sp_negative_product_targeting` | `shop_id`、`items` | `campaignId`、`adGroupId`、`asin` |
| `close_sp_negative_targeting` | `shop_id`、`items` | `targetId`、`targetingType`(keyword/product) |
| `create_sp_keyword_targeting` | `shop_id`、`items` | `campaignId`、`adGroupId`、`keywordText`、`matchType`、`bid`、`state` |

- `edit_sp_campaign.state` 取值**仅用实测确认的** `enabled` / `paused` / `archived`。
- 接口单次上限 100 条；**本技能限 20 条**（`thresholds.approval.max_items_per_batch`）。
- 写返回：`data.successIds` / `data.failList`（批量编辑类）；
  `create_*` 返回 `data.successList` / `data.failList`。

---

## 三、绩效字段（下载任务 xlsx）

`create_ad_download_task`（L1）参数：
`shop_ids`(必填)、`ad_type_code`(sp/sb/sd)、`report_type_code`、`time_unit`(daily/summary)、
`report_start_date`、`report_end_date`。

`report_type_code` 可选：`adCampaignReport`（活动）、`adGroupReport`（广告组）、
`adProductReport`（广告产品）、`adSpaceReport`（广告位，sp/sb）、`adTargetReport`（投放，sp/sb/sd）、
`adSearchTermReport`（搜索词，sp/sb）、`adPurchasedItemReport`、`amazonBusinessReport`（sp）、
`adCampaignMatchedTargetReport`（sd）。

进度查询 `get_ad_download_task_page_list`（**分页类，列表字段 `data.rows`**）：
`task_ids` / `task_name` / `ad_type_code_list` / `report_type_code_list` / `create_time_*` / `shop_ids`；
返回 `id`、`taskName`、`shopId`、`adTypeCode`、`reportTypeCode`、`reportState`、`downloadUrl`、
`createTime`、`reportCycle`、`timeUnit`。

> ⚠️ **xlsx 的列名未在 01 清单中枚举**，因此不做臆造：列名→规范名的映射写在
> `config/optimizer.yaml` 的 `perf_columns`，以实际导出为准，匹配不到就标「无数据」。

---

## 四、字段确认状态

| 字段/取值 | 状态 | 用法约束 |
|---|---|---|
| `state` = enabled/paused/archived | 实测确认 | 可直接用于判定与写操作 |
| `servingStatus` | 字段实测存在（见 `CAMPAIGN_PAUSED`） | 仅作人工提示 |
| `servingStatus` = `BUDGET_CAPPED` | **未经实测验证** | **禁止**作为自动判定条件 |
| `rate` / `currencyList` | 实测确认存在（`get_profit_report_by_msku`，171 字段） | 汇损归因；本技能不涉及利润报表 |
| `edit_sp_targeting.targetId` 与关键词 `keywordId` 是否等价 | **未实测确认** | 默认不自动执行关键词竞价写 |

---

## 五、列表字段与分页形态

| 类型 | 列表字段 | 翻页 | 涉及工具 |
|---|---|---|---|
| 广告类（游标） | `data.itemList` + `data.nextToken` | `next_token` ← `nextToken` | `get_sp_*` / `get_sb_*` |
| 分页类 | `data.rows` | `page_no` 递增 | `get_ad_download_task_page_list` |
| 利润报表 | `data.reportList` | `page_no` | `get_profit_report_by_*`（本技能不涉及） |

> 用 `data.rows` 解析广告列表会拿到空结果且不报错；历史测试常见 20 条/页（当前分页以契约为准），
> 不能靠 `page_size` 控量，必须客户端 `max_rows` 截断并披露。游标超时时间 1 分钟。