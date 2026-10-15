# 亚马逊 Listing 字段规范（如何填写与核对）

> 当需要生成/校验标题、五点、搜索词，或维护字符限制配置时读本文件。
> 机器可读的值在 `config/listing_spec.yaml`，本文件说明**字段含义、校验口径与填写方法**。

---

## ⚠️ 最重要的一条：字符限制不要照抄，要核对

本次调研**未能验证**亚马逊各品类的确切字符限制（检索失败）。因此：

- 技能**绝不写死**任何字符/字节数字；
- `config/listing_spec.yaml` 默认全部为 `null`，运行时一律标「⚠️ 需人工核查」；
- 使用者登录 Seller Central，按**目标站点 + 类目**的最新官方要求填写，改完即生效。

> 参考量级（**仅为方向，不得作为判定依据**）：标题常见 200 字符、部分类目更短；
> 五点通常 5 条；搜索词常按 250 字节计。**具体数值请以官方为准。**

---

## 一、配置项含义

`config/listing_spec.yaml`：

| 键 | 含义 | 单位 |
|---|---|---|
| `title_max_chars` | 标题上限 | 字符 |
| `bullet_count` | 五点数量 | 条 |
| `bullet_max_chars` | 每条五点上限 | 字符 |
| `search_terms_max_bytes` | 搜索词上限 | **字节**（非字符） |
| `description_max_chars` | 商品描述上限 | 字符 |

覆盖优先级：`by_category` > `by_marketplace` > `default`。
未命中的项保持 `null` → 标「需人工核查」。

### 素材规格 `assets`

| 键 | 含义 |
|---|---|
| `main_image.bg` | 主图背景要求（如纯白 `pure_white_rgb_255`） |
| `main_image.min_px` | 建议最小边长 |
| `main_image.product_ratio` | 商品主体占比下限 |
| `scene_image`/`aplus_module` | 场景图 / A+ 模块图规格 |
| `video.duration_s`/`aspect`/`max_mb`/`formats` | 主图视频规格 |

---

## 二、文案写作要点

- **标题**：品牌 + 核心关键词 + 属性/卖点 + 适用场景；避免堆砌、避免绝对化表述。
- **五点**：每条聚焦一个卖点，前置最重要信息；可含尺寸/材质/使用场景/售后。
- **搜索词**：只放关键词，不重复标题、不加标点、不堆品牌；注意**字节**上限。
- **描述/A+**：补充使用说明与信任状；A+ 图文模块另配素材。
- **语言**：按 `marketplace_locale` 使用目标站点语言（US→en-US，DE→de-DE，JP→ja-JP…）。

生成后**必须**跑 `check_compliance.validate_copy_against_spec` 做字符/字节校验。

---

## 三、与合规的衔接

文案还要过 `references/compliance-rules.md` 的禁词/商标/品类审核检查。
文案里的高风险表述会同时命中合规闸与字符校验，两者都要在包中披露。

---

## 四、维护建议

1. 首次使用：至少填好目标站点的 `default`，避免大量「需人工核查」；
2. 多类目运营：把差异项放到 `by_category`；
3. 平台政策变化时只改本配置，**不改代码**；
4. 修改后无需重启，脚本每次执行重新读取。