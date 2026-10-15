# asset-brief.json 契约与适配器接口

> 当需要产出/校验「素材需求单」，或接入一个新的生图/生视频 MCP 时读本文件。
> 生成逻辑实现：`scripts/build_asset_brief.py`。

---

## 一、设计原则：需求单 + 可插拔执行器

本技能与生图/生视频模型**完全解耦**。技能只产出**需求描述 + 提示词**（JSON），
生成由外部 MCP 完成。好处：换模型只改 adapter，SKILL.md 与流程不变；
也支持人工用其它工具出图（把 `prompt` 贴过去）。

---

## 二、契约（asset-brief.json）

```json
{
  "product": {
    "name": "折叠收纳凳",
    "asin_ref": "B0MOCKA001",
    "category": "家居",
    "marketplace": "US"
  },
  "assets": [
    {
      "id": "main-01",
      "type": "image",
      "role": "主图",
      "spec": { "bg": "pure_white_rgb_255", "min_px": 1600, "product_ratio": 0.85, "aspect": "1:1" },
      "prompt": "专业电商主图，纯白背景，居中展示 折叠收纳凳……",
      "negative_prompt": "文字, 水印, 边框, logo, 人物肖像, 侵权图案, 变形, 模糊"
    },
    {
      "id": "scene-01",
      "type": "image",
      "role": "场景图",
      "spec": { "aspect": "1:1", "min_px": 1000 },
      "prompt": "折叠收纳凳 的真实使用场景，自然光与暖色调……",
      "negative_prompt": "……"
    },
    {
      "id": "video-01",
      "type": "video",
      "role": "主图视频",
      "spec": { "duration_s": 30, "aspect": "16:9", "max_mb": 500, "formats": ["mp4", "mov"] },
      "prompt": "折叠收纳凳 的 15~30 秒带货短视频脚本，保持商品原图不变形",
      "storyboard": [
        { "t": "0-3s", "shot": "产品特写", "prompt": "……" },
        { "t": "3-10s", "shot": "核心卖点", "prompt": "……" },
        { "t": "10-20s", "shot": "使用场景", "prompt": "……" },
        { "t": "20-30s", "shot": "收尾/规格", "prompt": "……" }
      ]
    }
  ]
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `product.name` | ✅ | 产品名 |
| `product.asin_ref` | ✖ | 参考 ASIN（竞品或自家） |
| `product.category` | ✖ | 类目（影响审核提示） |
| `product.marketplace` | ✅ | 站点 |
| `assets[].id` | ✅ | 唯一标识（如 `main-01`） |
| `assets[].type` | ✅ | `image` / `video` |
| `assets[].role` | ✅ | 主图/场景图/A+模块图/信息图/生活方式图/主图视频 |
| `assets[].spec` | ✅ | 规格，来自 `config/listing_spec.yaml`；为 `null` 的项标「需人工核查」 |
| `assets[].prompt` | ✅ | 正向提示词（模板起点，由 AI 助手 结合卖点补全） |
| `assets[].negative_prompt` | ✖ | 负向提示词（图片） |
| `assets[].storyboard` | ✖ | 视频分镜，每项含 `t` / `shot` / `prompt` |

> ⚠️ **规格不硬编码**：`min_px` / `product_ratio` / `duration_s` 等全部来自配置。
> 本次调研未验证亚马逊确切要求，请按目标站点最新官方要求维护 `config/listing_spec.yaml`。

---

## 三、适配器接口契约（如何接入你的生图/生视频 MCP）

适配器是**声明式**的：描述「用哪个 MCP 工具、怎么把 asset 映射成调用入参」。
真正的 MCP 调用由 AI 助手（Agent）执行，脚本不直接发起外部 MCP 调用。

一个适配器需要提供：

```yaml
# adapters/<name>.md 中声明的关键信息
mcp_name: "MiniMax"                 # 外部 MCP 名称
direction: "image" | "video"
tool_map:
  主图: "text_to_image"
  场景图: "text_to_image"
  A+模块图: "text_to_image"
  主图视频: "generate_video"
param_map:
  prompt: "prompt"                  # asset.prompt → MCP 入参字段
  negative_prompt: "negative_prompt"
  aspect: "aspect_ratio"
  duration_s: "duration"
cost:
  per_call_key: "cost_per_image"    # 对应 launch.yaml → estimator
```

**调用约定**：
1. 逐个 asset 串行调用（除非确认外部 MCP 支持并发）；
2. 每次调用前已在阶段 5 完成成本确认（`--confirm-assets`）；
3. 记录每次调用的结果 URL/文件与花费；
4. 结果回填上架包区块 4，并标注「AI 生成」用于合规披露。

---

## 四、降级路径

- **未配置适配器**（`launch.yaml → adapter.image/video` 为空）：
  输出需求单，提示「把 prompt 贴到你的生图工具，或配置适配器后由技能调用」。
- **成本未知**（`estimator` 单价为空）：标「需人工核查」，仍输出数量清单，不生成。

---

## 五、内置适配器示例

- `adapters/example_image_mcp.md` —— 生图（含去背景/场景化）
- `adapters/example_video_mcp.md` —— 生视频（图生视频/带货短视频）