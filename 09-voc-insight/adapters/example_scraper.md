# 示例适配器：通用抓取型评论源（Scraper 型）

> 通用抓取源字段更全（常含图片、变体、有用票），但**合规风险更高** ——
> 抓取 Amazon 页面可能触及平台 ToS 与当地法律，接入前请自行确认合规性。
> 契约见 `README.md`。

```yaml
mcp_name: "amazon-scraper-v2"            # 参考生态：apiguru-app/amazon-scraper-v2
source_kind: "scraper"
tool_map:
  取评论: "amazon_reviews"
input_map:
  asin: "asin"
  marketplace: "domain"                  # 抓取源常用域名，如 "amazon.com"
  max_reviews: "max_pages"               # ⚠️ 注意：抓取源可能是「页」而非「条」——换算后填入
  min_rating: "filter_by_star"           # 例 "critical"（仅差评）
output_map:
  id: "reviewId"
  rating: "rating"
  text: "reviewText"
  date: "reviewDate"
  reviewer_id: "reviewerId"
  reviewer_name: "reviewerName"          # PII → 掩码
  verified: "isVerifiedPurchase"
  helpful_votes: "helpfulVotes"
cost_key: "cost_per_request"
pagination: "page"
compliance_note: "页面抓取型；可能违反 Amazon ToS，且全量/高频抓取有封禁与法律风险。仅小样本抽查使用，勿用于长期批量监控。"
```

## 与 API 型源的差异（接入时注意）

| 项 | API 型（示例 1） | 抓取型（本示例） |
|---|---|---|
| 计费 | 按条 | 常按请求/页（**需换算**） |
| 合规 | 服务商已授权 | **自行承担 ToS/法律风险** |
| 稳定性 | 契约稳定 | 页面改版即失效 |
| 建议用途 | 常规 VOC | 仅少量人工抽查 |

## 兜底

抓取源失败/被封时，技能自动回落到 `voc.yaml → degradation.no_external_reviews` 的
聚合指标模式，并在报告标注「外部评论源不可用」。
