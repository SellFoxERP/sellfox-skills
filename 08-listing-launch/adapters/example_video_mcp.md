# 示例适配器：生视频 MCP

> 以通用「图生视频 / 文生视频」MCP 为例，展示如何声明映射。**以你实际安装的 MCP 为准**。

```yaml
mcp_name: "<你的生视频 MCP>"
direction: "video"
tool_map:
  主图视频: "generate_video"
param_map:
  prompt: "prompt"
  duration_s: "duration"
  aspect: "aspect_ratio"
  image_url: "image_url"       # 图生视频时传主图/商品图
cost_key: "cost_per_video"
```

## 调用方式

- **整体脚本**：把 `asset.prompt` 作为总提示词，`spec.duration_s` 控制时长；
- **逐镜生成**：按 `asset.storyboard` 的每个 `shot` 分别生成，再拼接；
- 建议用**商品原图**做图生视频，减少商品变形（保持 LISTING 主图一致性）。

## 主图视频规格（来自配置，勿硬编码）

| 项 | 配置键 |
|---|---|
| 时长 | `listing_spec.assets.video.duration_s` |
| 比例 | `listing_spec.assets.video.aspect` |
| 大小上限 | `listing_spec.assets.video.max_mb` |
| 格式 | `listing_spec.assets.video.formats` |

> ⚠️ 亚马逊主图视频要求本次未验证，请按官方最新要求核对。

## 成本提示

生视频通常比生图贵得多。`estimator.cost_per_video` 未配置时，
成本闸会标「需人工核查」，且**不会**放行批量生成。

## 常见坑

- 视频时长/码率不符平台要求 → 生成后转码；
- 含音乐/字体可能侵权 → 用平台授权素材或免版权素材；
- AI 合成内容可能需披露 → 交给合规环节。