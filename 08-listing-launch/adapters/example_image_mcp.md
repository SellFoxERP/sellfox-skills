# 示例适配器：生图 MCP

> 以通用生图 MCP 为例，展示如何声明映射。具体工具名/入参**以你实际安装的 MCP 为准**。

```yaml
mcp_name: "<你的生图 MCP>"
direction: "image"
tool_map:
  主图: "text_to_image"
  场景图: "text_to_image"
  A+模块图: "text_to_image"
  信息图: "text_to_image"
  生活方式图: "text_to_image"
param_map:
  prompt: "prompt"
  negative_prompt: "negative_prompt"
  aspect: "aspect_ratio"
  min_px: "size"          # 部分 MCP 用 "1024x1024" 之类的尺寸串
cost_key: "cost_per_image"
```

## 调用示例（由 AI 助手 执行）

对 asset-brief.json 中每个 `type == "image"` 的资产：

```
tool = tool_map[asset.role]
args = {
  param_map.prompt: asset.prompt,
  param_map.negative_prompt: asset.negative_prompt,
  param_map.aspect: asset.spec.aspect,
}
result = call_image_mcp(tool, args)     # 串行，逐个
record(cost, result.url)
```

## 主图特别要求

- 纯白背景（`spec.bg = pure_white_rgb_255`）；
- `spec.min_px`（建议 ≥1600）以支持放大；
- 商品主体占比 ≥ `spec.product_ratio`（0.85）；
- 不要出现文字/水印/边框/可识别 logo（`negative_prompt` 已列）。

> ⚠️ 以上数值来自 `config/listing_spec.yaml`，请按目标站点最新官方要求核对后再用。

## 常见坑

- 有的 MCP 不支持 `negative_prompt` → `param_map` 里去掉该项，把否定词合并进正向提示词；
- 有的 MCP 只接受固定尺寸 → 用最接近的比例，生成后再统一裁剪；
- 生成结果的版权/可商用性由所选 MCP 的许可决定，**刊登前需人工确认**。