# 示例适配器：按 ASIN 取评论原文（VOC API 型）

> 复制本文件为 `adapters/<你的源>.md` 并改成你的 MCP 入参字段名，即完成接入。
> 契约见 `README.md`；字段含义见 `../config/field-map.yaml → external_review_contract`。

```yaml
mcp_name: "voc-amazon-reviews"          # 参考生态：mguozhen/voc-amazon-reviews（agent-native VOC）
source_kind: "review_api"
tool_map:
  按asin取评论: "get_reviews_by_asin"     # ← 改成你 MCP 的真实工具名
  按csv取评论:  "get_reviews_by_csv"
input_map:
  asin: "asin"
  marketplace: "marketplace"             # 例 "US"；该源支持 10 个市场
  max_reviews: "limit"
  min_rating: "rating_min"               # 差评筛选：传 1
  max_rating: "rating_max"               # 差评筛选：传 3（≤3 星为差评，按需调）
output_map:
  id: "review_id"
  rating: "star_rating"
  text: "body"
  date: "review_date"
  reviewer_id: "reviewer_hash"           # ⚠️ 稳定 ID；不要用昵称做去重键
  reviewer_name: "reviewer_name"         # PII → 输出前掩码
  verified: "verified_purchase"
  helpful_votes: "helpful_count"
cost_key: "cost_per_review"
pagination: "cursor"
compliance_note: "经第三方 VOC OpenAPI 获取；需该服务授权；数据再分发受其 ToS 约束。"
```

## 调用示例（由 AI 助手 执行，串行）

```
1) 输入：问题 ASIN 列表（来自赛狐聚合指标）+ 站点 + 差评星级上界
2) 对每个 ASIN 调 get_reviews_by_asin(asin=..., marketplace=..., limit=max_reviews,
   rating_max=3)，按分页游标串行翻页直到 max_reviews 或末页
3) 用 output_map 把返回字段规范化为契约字段
4) 调用次数不得超过 voc.yaml → external_reviews.max_calls_per_run
5) 结果落盘供 analyze_themes.py 聚类；nickname 掩码后进报告
```

## 注意事项

- **口径差异**：多数评论源是**父 ASIN**维度，赛狐宽表可能按**子 ASIN/msku**。
  归因时须声明用的是哪种粒度，不要把父 ASIN 的差评归到某个子变体头上。
- **时效**：优先取近 `stat_windows.short_days`/`long_days` 内评论；过老评论可能已被修复。
- **成本**：按条计费时，`max_reviews` 直接决定花费 —— 这是成本闸的关键输入。
