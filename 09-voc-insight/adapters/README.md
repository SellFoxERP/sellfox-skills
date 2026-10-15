# 外部评论 MCP 适配器（可插拔）

> 本目录说明如何把「取评论原文」接到**你选的**评论数据源。
> 技能与具体实现解耦：换评论源只改 adapter，SKILL.md 与流程不变。

---

## 为什么是「适配器」而不是「内置评论抓取」

赛狐**官方** MCP 拿不到评论原文（只有聚合指标 `rating`/`ratingCount`/`lowReviewNum`/`reviewNum`/`reviewRate`）。
评论原文来自**外部**数据源，而外部源生态变化快、各家鉴权与入参不同，且**多为付费/需授权**。
把某个源绑死在技能里，换源就要改技能，也会掩盖「可用性取决于你是否接入」这一事实。

因此本技能**只定义接口契约**，真正调用由 AI 助手 按所选适配器执行。

---

## 接口契约（一个适配器要声明什么）

适配器是一个 `*.md` 文件，用最简结构声明「怎么把 ASIN 变成评论列表」：

```yaml
mcp_name: "voc-amazon-reviews"        # 外部 MCP 名称（人类可读）
source_kind: "review_api"             # review_api | scraper | marketplace_api
tool_map:                             # 角色 → MCP 工具名
  按asin取评论: "get_reviews_by_asin"
  按市场取评论: "get_reviews_by_marketplace"
input_map:                            # 本技能参数 → MCP 入参字段
  asin: "asin"
  marketplace: "marketplace"
  max_reviews: "limit"
  min_rating: "rating_min"            # 差评筛选下界
  max_rating: "rating_max"
output_map:                           # MCP 返回字段 → 契约字段（config/field-map.yaml）
  id: "review_id"
  rating: "star_rating"
  text: "body"
  date: "review_date"
  reviewer_id: "reviewer_hash"        # ⚠️ 用稳定 ID，不要用昵称做去重键
  reviewer_name: "reviewer_name"      # PII，输出前掩码
cost_key: "cost_per_review"           # 计费口径；对应 voc.yaml → external_reviews
pagination: "cursor"                  # cursor | page | none
compliance_note: "..."                # 该源的 ToS / 授权 / 数据合规要点
```

### 最小必需输出字段（`config/field-map.yaml → external_review_contract.required`）

| 字段 | 用途 | 缺失后果 |
|---|---|---|
| `id` | 去重、数「独立评论」条数 | 无法做多源印证计数 → 无法报「模式」 |
| `asin` | 归属到问题 ASIN | 无法关联 |
| `rating` | 筛差评（≤ `negative_max_rating`） | 无法筛差评 |
| `text` | 归因分析的输入 | **本技能核心失效** |

> 缺失任一必需字段 → 该源视为**不合格**，技能走降级路径（只用聚合指标），不猜。

---

## 执行约定

1. **先过成本闸**：`voc.yaml → external_reviews.require_confirmation: true` 时，
   调用前必须报「预计调用次数 / 条数 / 成本」并等确认（`scripts/fetch_reviews.py` 会输出闸门状态）。
2. **串行调用**：外部评论 MCP 的并发能力未知（可能限流/计费叠加）—— 未知时一律串行。
   （赛狐侧取数走 agent 元工具，同 session 并发安全，与本条无关。）
3. **次数上限**：`external_reviews.max_calls_per_run` 是硬上限，超限即停并披露。
4. **PII 脱敏**：`reviewer_name` 等写入任何外部文档前掩码（`scripts/voc_common.py → mask_reviewer`）。
5. **记录来源**：报告须标注每条结论来自哪个评论源（可用性取决于接入，不是官方能力）。

---

## 示例

- `example_voc_reviews.md` —— 按 ASIN 取评论原文（本技能的主用例）。
- `example_scraper.md` —— 通用抓取型源（字段更全但合规风险更高）。

---

## 未配置适配器怎么办（降级路径）

`external_reviews.adapter` 为空 / `enabled: false` / 调用失败 / 超预算时，技能**不崩**，
而是产出**聚合指标版 VOC 报告**：

- ✅ 仍做：按 `lowReviewNum` 环比 / `rating` 下降 / `reviewRate` 下滑 **定位问题 ASIN**；
  输出「需人工核查评论原文」清单与建议抽查方向。
- ❌ 不做（并显式声明）：差评主题聚类、高频问题词、基于原文的归因与行动建议。
- 报告顶部横幅：`voc.yaml → degradation.no_external_reviews.banner`。

> 换句话说：**没有外部源，你仍能知道「哪些 ASIN 出了问题」，但不知道「为什么」。**
> 本技能绝不会在没有原文时编造主题分布。
