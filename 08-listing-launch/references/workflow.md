# 上架素材包 · 执行工作流详解（v3：agent 取数 + 脚本计算）

> 本文件是 `SKILL.md` 执行流程的展开版。当需要逐步落地、排障或向使用者解释每一步时读它。
> SKILL.md 里的每一步都能在这里找到更细的操作、字段与判据。
>
> **v3 分工**：取数面 = agent 调客户端配置的 `mcp__Sellfox-MCP__{help,search,action}`，
> 按阶段 2 的「取数计划」把每步**完整信封**落盘 `out/raw/`；计算面 =
> `fetch_baseline.py`（校验+组装）→ 文案/需求单/合规/测算/渲染，全部零网络。
> 调用纪律（契约版本/参数类型/错误处理/并发/落盘）见 `_shared/SELLFOX_CLIENT.md`。

---

## 目录

- [阶段 0：前置校验](#阶段-0前置校验)
- [阶段 1：确认输入](#阶段-1确认输入)
- [阶段 2：取数与基线组装（agent 落盘 + 脚本校验）](#阶段-2取数与基线组装agent-落盘--脚本校验)
- [阶段 3：文案生成](#阶段-3文案生成)
- [阶段 4：素材需求单](#阶段-4素材需求单)
- [阶段 5：成本闸（生成前确认）](#阶段-5成本闸生成前确认)
- [阶段 6：合规检查（永不写入）](#阶段-6合规检查永不写入)
- [阶段 7：定价与利润测算](#阶段-7定价与利润测算)
- [阶段 8：渲染与交付](#阶段-8渲染与交付)
- [阶段 9：外部 MCP 生成（可插拔）](#阶段-9外部-mcp-生成可插拔)
- [降级路径](#降级路径)

---

## 阶段 0：前置校验

技能运行前先做四项校验，任一不满足要**显式告知**而不是硬跑：

1. **客户端 MCP 就绪**：会话内能调 `mcp__Sellfox-MCP__*`（Key 只在客户端 MCP 配置里，
   **脚本不需要也不接受 `SELLFOX_MCP_KEY`**，计算面零网络）。若数据源为演示/沙箱环境，
   产出物会带演示水印并默认阻断写飞书。
2. **落盘就绪**：`out/raw/online_product.json` 与 `out/raw/product_analyze.json` 是否已由
   agent 按取数计划落盘。缺落盘 → 不阻断，走「离线骨架」：实时字段标
   「⚠️ 需人工核查」，骨架照出。
3. **演示环境指纹**（`MOCK-` / `demo.example.com` / `SHOP-DEMO-` / `B0DEMO` /
   店铺名含「演示」等，由 `fetch_baseline.py` 扫描落盘信封）。命中 → 上架包顶部加水印，
   **默认阻断写飞书**。
4. **配置完整性**：`config/listing_spec.yaml`（字符/素材规格）、`config/fx.yaml`（汇率）、
   `config/launch.yaml`（素材计划/单价）—— 未配置的项一律标「需人工核查」，**不编造**。

> 「客户端 MCP 就绪 + 落盘完成」是**可运行的前置条件**，不是文档里的一句提醒。

---

## 阶段 1：确认输入

| 参数 | 默认 | 说明 |
|---|---|---|
| 产品名 | 必填 | 用于文案与素材提示词 |
| 站点 marketplace | `launch.yaml → default_marketplace`（US） | 影响语言与字符规范 |
| 搜索类型 / 内容 | `asin` / 必填 | `asin`/`parentAsin`/`msku`/`sku`/`title`/`fnsku`/`commodityName` |
| 类目 | 无 | 命中 `by_category` 覆盖时才需要 |
| 店铺范围 | 全部 | `shop_ids`（分批，每批 ≤20 行） |
| 素材计划 | `launch.yaml → asset_plan` | 主图/场景图/A+/视频数量 |

---

## 阶段 2：取数与基线组装（agent 落盘 + 脚本校验）

### 取数计划（agent 执行）

本技能只用两个赛狐只读工具；已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再
`action`。参数类型以当次契约 inputSchema 为准（分页 string/integer 因工具而异）。
两步**可并行发起**（新网关同 session 并发安全），但每步都要落盘并核对 status。

| # | 落盘文件 | 工具 | 关键参数（类型注意） | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/online_product.json` | `get_online_product_page_list` | `search_type`/`search_content`（asin/parentAsin/msku/sku/title/fnsku/commodityName）；`marketplace_id_list=[站点]`；`shop_id_list`（每批 ≤20 行）；分页参数类型按当次契约 | `rows` | 20 | Listing 属性 + 成本/费用/价格 + FBA 库存 → 商品基线 `products` |
| 2 | `out/raw/product_analyze.json` | `get_product_analyze_list` | `search_type`；`search_mode=exact`；`search_contents`；`dim_type="1"`（asin 维度）；`marketplace_id_list` / `shop_id_list` 同上 | `rows` | 20 | 类目 / BSR / 上架时间 → 基线 `analysis`；失败不阻断主流程（标需人工核查） |

**落盘纪律**（详见 SELLFOX_CLIENT.md §7）：

- 每步 action 的**完整信封**（status/data/coverage/pagination/warnings/trace_id）原样写盘；
- **不在对话中复述行级数据**——数据只落盘，组装交给脚本（上下文是稀缺资源）；
- 单次调用 ≤1 页（多数工具服务端强制 20 条/页，`page_size` 不被尊重）；
- `CONTRACT_VERSION_MISMATCH` → 重 search 一次、按新 schema 重建参数、重试一次；
- `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（失败时未发业务请求，安全）；
- 空 data（status=complete）= 无数据 → 落盘原样保留，组装层标「无数据」；
- 触达 max_rows 必须保留 coverage，让 `fetch_baseline.py` 披露「仅覆盖 Top N」。

### 校验与组装（脚本执行，零网络）

```bash
python3 scripts/fetch_baseline.py --raw-dir out/raw   # 手工排障时单独跑
```

`fetch_baseline.py` 校验每个落盘信封（存在性 / status / 空数据 / 演示指纹 /
coverage 截断），按白名单裁剪字段并组装出与旧版相同形状的 baseline
（`products` / `analysis` / `demo_fingerprint`）——
`build_asset_brief` / `check_compliance` / `estimate_pricing` / `render_package` 零改动。

规则（组装层）：
- **字段裁剪**：只留白名单字段（见 `scripts/fetch_baseline.py`），避免上下文爆炸。
- **截断必披露**：信封 `coverage.truncated=true` → 写入 warnings（渲染层落「数据说明」），
  禁止静默截断；**不信 `pagination.has_more`，信 coverage**。
- **币种**：顶层 `currency`（USD，售价）与 `inventoryManage.currency`（CNY，成本）
  **分别保留**，绝不直接相减。
- **缺落盘**：不崩——实时字段标「⚠️ 需人工核查」，骨架照出（见降级路径）。

---

## 阶段 3：文案生成

用 `config/listing_spec.yaml` 的限制约束输出结构：

- 标题 Title
- 五点 Bullets（数量见 `bullet_count`）
- 搜索词 Search Terms（注意按**字节**计）
- 商品描述 / A+ 文案

生成后**必须**经 `check_compliance.validate_copy_against_spec` 校验字符/字节；
未配置的限制标「需人工核查」。写作要求见 `references/amazon-listing-spec.md`。

---

## 阶段 4：素材需求单

由 `scripts/build_asset_brief.py` 产出 `asset-brief.json`（契约见 `references/asset-brief-spec.md`）：

```
主图 ×1（纯白底，min_px 1600，商品占比 ≥85%）
场景图 ×6（1:1）
A+ 模块图 ×5~7
主图视频 ×1（15~30s，含分镜 storyboard）
```

数量来自 `asset_plan`，规格来自 `listing_spec.assets`。提示词是模板起点，
由 AI 助手 结合卖点补全后写入 `--copy-json` 同目录的 brief 附件。

---

## 阶段 5：成本闸（生成前确认）

生图/生视频**按次收费**（外部 MCP 官方均声明可能产生费用）。因此：

1. `estimate_generation` 统计数量，用 `estimator.cost_per_image` / `cost_per_video` 估算总价；
2. 未配置单价 → 标「需人工核查」，**不按 0 元估算**；
3. 未传 `--confirm-assets` → 成本闸 `HOLD`，只输出清单，**不生成**；
4. 传 `--confirm-assets` → `PROCEED`，才进入阶段 9。

---

## 阶段 6：合规检查（永不写入）

`scripts/check_compliance.py`，定位等同亚马逊官方 `listing-compliance` 的
**pre-write advisory gate, never writes**：只输出风险提示，不修改任何内容。

检查项：禁词、商标/专利人工核查提示、品类审核提示、AI 素材披露提示、文案超限提示。
词表在 `config/compliance.yaml`（使用者维护）。细节见 `references/compliance-rules.md`。

---

## 阶段 7：定价与利润测算

`scripts/estimate_pricing.py`。字段来源（已核对 01/06 文档）：

| 值 | 字段 | 工具 | 币种 |
|---|---|---|---|
| 售价 | `standardPrice`/`listingPrice`/`salePrices` | get_online_product_page_list | 顶层 `currency`（USD） |
| 采购成本 | `purchaseCost` | get_online_product_page_list | 成本口径（CNY） |
| 佣金 | `referralFee` | get_online_product_page_list | 售价口径 |
| FBA | `fbaFees` | get_online_product_page_list | 售价口径 |
| 变动结算费 | `variableClosingFee` | get_online_product_page_list | 售价口径 |
| 头程 | `headTripPrice` | **仅 get_custom_report_page_list** | 成本口径 |

- 跨币种必须经 `config/fx.yaml` 换算，并标注**汇率来源 + 日期**；未配置 → 标「需人工核查」。
- 头程在本技能用的工具里取不到 → 固定标「需人工核查」。
- 费用不完整时**不给**「精确」利润，只标「估算、仅供参考」。

---

## 阶段 8：渲染与交付

`scripts/render_package.py` 严格按 `references/listing-package-template.md` 渲染 7 个区块：

```
1 市场与竞品调研（外部 MCP 未接入 → 需人工核查）
2 Listing 文案
3 素材需求单 asset-brief.json
4 生成的素材文件（未确认/未生成时留空）
5 合规检查清单（附风险项）
6 定价与利润测算
7 ⚠️ 待人工确认项
+ 诚实边界 + 数据说明（必填，不可省略）
```

写飞书（可选）：`FeishuWriter.guard_demo` 默认在演示环境**阻断**；显式 `--allow-demo-write`
才放行且仍加水印。

---

## 阶段 9：外部 MCP 生成（可插拔）

按 `adapters/README.md` 的接口契约，AI 助手 调用所选生图/生视频 MCP：

- 每个素材的 `prompt` / `negative_prompt` 直接作为调用入参；
- 视频按 `storyboard` 逐镜生成或作为整体脚本；
- 生成结果（URL/文件）回填到上架包区块 4；
- **调用必须串行**（若外部 MCP 也限制并发），并记录每次调用的成本。

> 技能与具体模型完全解耦：换模型只改 adapter，SKILL.md 与流程不变。
> 也支持「人工用其它工具出图」——把 `prompt` 贴过去即可。

---

## 降级路径

| 缺失项 | 影响 | 降级做法 | 必须标注 |
|---|---|---|---|
| `out/raw/` 缺某个（或全部）信封 | 对应实时字段不可用 | **不阻断**：骨架照出，相关字段标「⚠️ 需人工核查」，并在数据说明提示 agent 按取数计划落盘后重跑 | 「缺落盘 {文件名}，实时字段需人工核查」 |
| 某信封 `status=failed` | 该源数据不可信 | 同上（骨架照出 + 需人工核查），其余步骤照常 | 数据说明记录 error_code |
| 某信封 `status=partial` | 部分成功 | 可采信，但必须在数据说明披露 | 「partial：可采信，已披露」 |
| `data` 为空（status=complete） | 该品无数据 | 标「**无数据**」，不写 0，不重试；提示确认 ASIN/MSKU/站点是否正确 | 「无数据」 |
| `coverage.truncated=true` | 只覆盖 Top N | 写入 warnings，渲染层披露「仅覆盖 N 行，其余未纳入」 | 「截断披露」 |
| 命中演示指纹 | 数据非真实 | 包顶加「⚠️ 演示数据」水印，默认阻断写飞书 | 水印 |
| `product_analyze` 缺失/失败 | 类目/BSR/上架时间缺 | 上架包其余部分（文案/需求单/合规/测算）照常产出并披露该降级 | 「商品分析缺失（已降级）」 |
| 汇率（`config/fx.yaml`）未配置 | 跨币种无法折算 | 利润只标「估算、仅供参考」，不给精确值 | 「汇率未配置，需人工核查」 |