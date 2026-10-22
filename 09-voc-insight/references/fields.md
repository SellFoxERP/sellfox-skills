# 字段与数据源说明（fields.md）

> 何时读：需要核对某字段属于哪个工具、或判断「这个字段能不能取到」时读。
> **权威依据**：`_shared/FIELD_DICTIONARY.md`（字段 → 含义 → 所属工具）。抽查字段是否存在一律以它为准，**不得凭记忆**。

## 目录

- [1. 评论相关字段的存在性（关键）](#1-评论相关字段的存在性关键)
- [2. 数据源的列表字段名](#2-数据源的列表字段名)
- [3. 辅助维度字段](#3-辅助维度字段)
- [4. 取不到的字段（需人工核查）](#4-取不到的字段需人工核查)
- [5. 外部评论 MCP 契约](#5-外部评论-mcp-契约)

## 1. 评论相关字段的存在性（关键）

| 字段 | `get_custom_report_page_list` | `get_online_product_page_list` | 本技能是否使用 |
|---|---|---|---|
| `rating` | ✅ | ✅ | ✅ online 快照（主源） |
| `ratingCount` | ✅ | ✅ | ✅ online 快照（小样本下界） |
| `lowReviewNum` | ✅ | ❌ | ❌ 已由 custom-report-analysis 承接 |
| `reviewNum` | ✅ | ❌ | ❌ 已由 custom-report-analysis 承接 |
| `reviewRate` | ✅ | ❌ | ❌ 已由 custom-report-analysis 承接 |

> ⚠️ **最常见的隐性错误**：以为 online 侧也有 `lowReviewNum`。
> 实际取不到时**不报错**，会让「差评数」恒为空而无人察觉。
> 需要差评数/好评率及其环比时转 `custom-report-analysis` 技能（489 宽表）。

> ⚠️ **勿把真实字段误判为不存在**（历史上发生过，已被纠正）：
> `adOrderConversionRate` / `threeSaleNum` 经一手实测确认为**真实字段**。
> 判断字段是否存在，一律查 _shared/FIELD_DICTIONARY.md 与当前 MCP 契约，不凭记忆。

## 2. 数据源的列表字段名

列表字段名**因工具而异**，用错会拿到空结果且不报错（铁律 5）：

| 类型 | 列表字段 | 本技能涉及 |
|---|---|---|
| 分页类 | `data.rows` | `get_online_product_page_list`（本技能主源） |
| 利润报表 | `data.rows`（标准化分页） | （本技能不用） |
| 广告类（游标） | `data.rows`（标准化分页）+ 游标 | （本技能不用） |

本技能工具用 `rows`；仍要求显式声明 `list_key="rows"`，避免未来换工具时踩坑。

## 3. 辅助维度字段

| 字段 | 数据源 | 用途 |
|---|---|---|
| `asin` | online（本技能）/ 两者 | VOC 归因主键（外部评论按 ASIN 拉） |
| `parentAsin` | 两者 | 变体聚合；外部源常按父 ASIN，注意粒度差异 |
| `sku` | online（本技能）/ 两者 | 变体标识 |
| `shopId` | 两者 | 店铺（告警路由） |
| `marketplaceId` | 两者 | 站点（外部源分市场，口径须对齐） |
| `title` | 两者 | 辅助判断「描述不符」 |
| `mainImage` | 两者 | 辅助判断「色差/与图不符」 |
| `saleNum` | online 侧为 `day7SaleNum` 等；宽表侧 `saleNum` 已不在本技能 | 影响面排序（有销量的低分优先深挖） |

> `msku` / `buyBoxPercent` / `saleNum`（宽表侧）只在 custom 报表，已由
> `custom-report-analysis` 承接，本技能不取。

完整映射见 `config/field-map.yaml`。

## 4. 取不到的字段（需人工核查）

赛狐 MCP **拿不到**（一律标「⚠️ 需人工核查」，绝不编造）：

- **评论原文** —— 只有聚合指标；原文来自外部 MCP。
- **评论者信息** —— 同上。
- **类目平均差评率基准** —— 无此字段，无法判断「高不高」。
- **变更/退货原因文本** —— 退款类字段是数字，无原因文本。
- **账户健康 / 绩效通知 / 政策合规**。

## 5. 外部评论 MCP 契约

**至少**要能提供（缺一即不合格 → 降级）：

| 字段 | 用途 |
|---|---|
| `id` | 去重、数独立评论 |
| `asin` | 归属 |
| `rating` | 筛差评 |
| `text` | 归因输入 |

可选：`title` `date` `reviewer_id` `reviewer_name`(PII) `marketplace` `verified` `helpful_votes`。

`reviewer_name` 写入任何对外输出前**必须掩码**；`reviewer_id` 用于数独立评论者。
详见 `config/field-map.yaml → external_review_contract` 与 `adapters/README.md`。
