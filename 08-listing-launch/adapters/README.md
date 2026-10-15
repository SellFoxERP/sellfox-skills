# 生图 / 生视频适配器（可插拔）

> 本目录说明如何把资产需求单接到**你选的**生图/生视频 MCP。
> 契约细节见 `../references/asset-brief-spec.md`。技能与具体模型解耦：换模型只改 adapter。

---

## 为什么是「适配器」而不是「内置生成」

生图/生视频 MCP 生态变化快、各家入参不同、且**按次收费**。把生成绑死在技能里，
换模型就要改技能。因此本技能只产出**需求单（prompt + 规格）**，
生成交给**声明式适配器**描述的 MCP 来执行。

---

## 一个适配器要声明什么

```yaml
mcp_name: "MiniMax"                     # 外部 MCP 名称
direction: "image"                      # image | video
tool_map:                               # 角色 → MCP 工具名
  主图: "text_to_image"
  场景图: "text_to_image"
  A+模块图: "text_to_image"
param_map:                              # asset 字段 → MCP 入参字段
  prompt: "prompt"
  negative_prompt: "negative_prompt"
  aspect: "aspect_ratio"
  duration_s: "duration"
cost_key: "cost_per_image"              # 对应 launch.yaml → estimator
```

---

## 执行约定

1. **先过成本闸**（阶段 5）：未 `--confirm-assets` 不得生成；
2. **串行调用**：除非确认外部 MCP 支持并发；同 session 并发会挂起（赛狐如此，外部亦须确认）；
3. **记录花费**与产出 URL，回填上架包区块 4；
4. **AI 披露**：结果标注为 AI 生成，供合规环节披露。

---

## 示例

- `example_image_mcp.md` —— 生图（主图/场景图/A+/去背景/商品场景化）
- `example_video_mcp.md` —— 生视频（图生视频/带货短视频）

---

## 未配置适配器怎么办

技能仍会产出 `asset-brief.json`。使用者可：
- 手动把 `prompt` 贴到任意生图工具（Midjourney / 即梦 / 通义万相等）；
- 或在 `config/launch.yaml → adapter` 填入所选 MCP 名称后由技能调用。

> 无论哪条路径，**最后一公里（刊登）都由人工在赛狐/亚马逊后台完成** —— 本技能不做一键上架。