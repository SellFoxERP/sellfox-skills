---
name: listing-launch
description: 为跨境电商新品生成「上架素材包」（Listing Package）：用赛狐/SellFox ERP 的成本、类目与价格数据，产出可直接拿去亚马逊后台刊登的标题、五点、搜索词、A+ 文案，以及交给生图/生视频 MCP 的「素材需求单 asset-brief.json」，并附合规检查与定价利润测算。当用户提到"商品上架""上架素材""新品刊登""Listing 文案""写标题/五点/搜索词""主图/场景图/A+图""生图/生视频做素材""素材需求单""asset-brief""上架包""listing package""给新品做素材"时，务必使用本技能，即使用户没有明确说"上架"二字。注意：本技能不做一键上架（赛狐 MCP 无刊登接口），最后一公里由人工在后台完成；已有链接的问题排查（不可售/丢购物车/无流量）请用 Listing 体检技能。
---

## 报表自动发现

任务需要自定义报表时读取共享底座 `ONBOARDING.md`：助手先复用已验证选择，否则通过当前 MCP 发现工具，或从自定义指标的 `reportList` 自动提取报表 ID 并核对用途。不要让商家手填 ID、编辑 YAML 或先创建报表。
匹配唯一且字段适用时自动采用；多个候选只让商家选名称；无法取得可用报表则说明缺口并继续简版。内置查询和全局指标盘点不需要报表初始化。用户已选择简版时不重复打扰。

## 运行环境与路径

以本文件所在目录为技能根目录，执行脚本前切换到该目录，使用 Python 3.10+。
本项目采用批量安装，专项技能（01~09）共用一份底座；源码布局的底座位于 `../_shared/`。
读取下文 `_shared/...` 引用时先定位实际共享底座。脚本定位优先级为：显式参数（支持时）
→ `SELLFOX_SHARED_ROOT` → `config/paths.yaml` → 包内 `_shared` → 同级 `_shared`。
批量安装器将共享路径写入配置，专项技能（01~09）复用同一份商家配置。
`mcp__Sellfox-MCP__...` 是服务工具的示意名称；使用客户端实际暴露的 Sellfox-MCP
`help/search/action`，不得因客户端前缀不同而判定工具缺失。当前完整契约来自 help 或 search；
已有完整契约直接 action，不重复 search。首先读取共享 `SELLFOX_CLIENT.md`。
下文“零网络”仅指赛狐取数后的离线计算；飞书和审批通知模块会联网。
默认输出本地报告；只有用户要求时才启用飞书或外部服务。没有原始数据时仅说明缺口或产出
明确标识的模板，不能把空数据报告当作经营正常的结论。

# 商品上架素材包（Listing Package）

为**新品**产出「上架前」的素材与文案流水线：文案 + 素材需求单 + 合规检查 + 定价测算。
交付物是一份**上架包**，最后一公里由人在赛狐/亚马逊后台完成。

## 这个技能做什么 / 不做什么

先说清楚边界，因为它决定使用者信不信这个技能：

**做**：
- agent 按取数计划调赛狐 MCP 取商品基线（Listing 属性、成本/费用/价格、类目）并落盘 `out/raw/`
- 生成结构化上架包：标题/五点/搜索词/A+ 文案
- 产出「素材需求单」`asset-brief.json`（提示词 + 规格），交给生图/生视频 MCP
- 写前合规检查（**永不写入**）+ 定价与利润测算

**不做**：
- ❌ **不能自动刊登商品**。本技能没有实现创建/修改 Listing 的发布流程，需商家在后台确认并刊登。
- ❌ **不硬编码亚马逊字符限制**。本次调研未验证确切数值，一律读配置、缺失标「需人工核查」。
- ❌ **不直接生图/生视频**。技能只出「需求单」，生成由外部 MCP 完成，且**必须先报数量与成本待确认**。
- ❌ **不自动改文案**。合规检查只提示风险，不改任何内容。

## 先读这些（按需加载，不要一次全读）

| 文件 | 什么时候读 |
|---|---|
| `references/workflow.md` | 要逐步落地/排障/解释流程时（阶段 0~9 的完整展开） |
| `references/asset-brief-spec.md` | 产出或校验素材需求单、接入新生成 MCP 时 |
| `references/compliance-rules.md` | 执行/解释合规检查、维护禁词表时 |
| `references/amazon-listing-spec.md` | 生成/校验文案、维护字符限制配置时 |
| `references/listing-package-template.md` | 渲染上架包时（严格照此结构） |
| `adapters/README.md` | 接入/更换生图生视频 MCP 时 |
| `_shared/SELLFOX_CLIENT.md` | **必读**：MCP 调用约定（contract_version、参数类型按契约、错误语义、并发、落盘纪律） |
| `_shared/config/reports.yaml` | 商品行 `headTripCost` 缺值、需要宽表头程（`headTripPrice`）时——宽表是补充源 |

> `_shared/` 是专项技能（01~09）的共用底座，**不是技能**。脚本通过本技能 `config/paths.yaml`
> 定位它；优先使用包内 `_shared/`，源码布局回退到同级共享目录。

## 执行流程

### 阶段 0：前置校验

- **客户端 MCP 就绪 + 落盘检查**：取数面 = agent 调 `mcp__Sellfox-MCP__{help,search,action}`
  按取数计划把信封落盘 `out/raw/`（Key 只在客户端 MCP 配置里，脚本**不需要也不接受**
  `SELLFOX_MCP_KEY`，计算面零网络）。缺落盘 → 降级为「离线骨架」，实时字段标
  「需人工核查」，不崩。
- **演示环境**：命中指纹（`MOCK-` / `demo.example.com` / `SHOP-DEMO-` / `B0DEMO` /
  店铺名含「演示」）→ 上架包顶部加水印，**默认阻断写飞书**。
- **配置完整性**：`config/listing_spec.yaml` / `config/fx.yaml` / `config/launch.yaml`
  未配置项一律标「⚠️ 需人工核查」，**不编造**。

### 阶段 1：确认输入

| 参数 | 默认 | 说明 |
|---|---|---|
| 产品名 | 必填 | 用于文案与提示词 |
| 站点 | `launch.yaml → default_marketplace` | 影响语言与字符规范 |
| 搜索类型/内容 | `asin` / 必填 | asin/parentAsin/msku/sku/title/fnsku/commodityName |
| 素材计划 | `launch.yaml → asset_plan` | 主图/场景图/A+/视频数量 |

### 阶段 2：取数与基线组装（agent 落盘 + 脚本校验）

**v3：取数面是 agent**——只调两个赛狐只读工具，按 `references/workflow.md` 的
**取数计划**调 `mcp__Sellfox-MCP__{search,action}`，把每步完整信封落盘
`out/raw/`（两步可并行发起，新网关同 session 并发安全）：

| 顺序 | 落盘文件 | 工具 | 列表字段 | 用途 |
|---|---|---|---|---|
| 1 | `out/raw/online_product.json` | `get_online_product_page_list` | `data.rows` | Listing 属性 + 成本/费用/价格 + FBA 库存 |
| 2 | `out/raw/product_analyze.json` | `get_product_analyze_list` | `data.rows` | 类目 / BSR / 上架时间 |

纪律见 `_shared/SELLFOX_CLIENT.md`：契约先取后用（action 必带当次 search 返回的
contract_version）、参数类型按当次 inputSchema、失配重取一次、每步落盘不复述行级数据、
不信 `has_more` 信 `coverage`。

**计算面 = `scripts/fetch_baseline.py`（零网络）**：校验落盘（存在性/status/空数据/
演示指纹/coverage 截断）并按白名单裁剪字段，组装出 baseline——
`build_asset_brief` / `check_compliance` / `estimate_pricing` / `render_package` 零改动。
缺落盘 → 实时字段标「⚠️ 需人工核查」，骨架照出。

规则：
- **截断必披露**：服务端恒 20 条/页，取数计划 `max_rows` 安全阀；`coverage.truncated=true`
  由脚本写入 warnings，**禁止静默截断**。
- **字段裁剪**：白名单见 `scripts/fetch_baseline.py`（落盘保留原始行，裁剪发生在组装层）。
- **币种**：顶层 `currency`（USD，售价）与 `inventoryManage.currency`（CNY，成本）**分别保留**。

```bash
# listing_launch.py 已封装编排（检查落盘 → 校验组装）；手工排障时单独跑：
python3 scripts/fetch_baseline.py --raw-dir out/raw
```

### 阶段 3：文案生成

按 `listing_spec.yaml` 的限制生成标题/五点/搜索词/A+。写作要点见
`references/amazon-listing-spec.md`。生成后必须过字符校验（字节/字符分列）。

### 阶段 4：素材需求单

`scripts/build_asset_brief.py` 产出 `asset-brief.json`（契约见 `references/asset-brief-spec.md`）：
主图 ×1、场景图 ×6、A+ ×5~7、视频 ×1（含 storyboard）。**只描述、不生成。**

### 阶段 5：成本闸（生成前确认）★

生图/生视频**按次收费**。必须先报数量与成本：

- `estimate_generation` 统计数量并按 `estimator` 单价估算；
- 单价未配置 → 标「需人工核查」，**不按 0 元估算**；
- 未传 `--confirm-assets` → 闸门 `HOLD`，**不生成**；确认后才 `PROCEED`。

### 阶段 6：合规检查（永不写入）

`scripts/check_compliance.py`，定位同亚马逊官方 `listing-compliance` 的
**pre-write advisory gate, never writes**：只输出风险提示，不改任何内容。
检查项：禁词 / 商标专利（仅提示人工核查）/ 品类审核 / AI 素材披露 / 文案超限。
词表在 `config/compliance.yaml`（使用者维护），细节见 `references/compliance-rules.md`。

### 阶段 7：定价与利润测算

`scripts/estimate_pricing.py`。字段来源（已核对 01/06 文档，勿凭记忆改）：

| 值 | 字段 | 工具 | 币种 |
|---|---|---|---|
| 售价 | `standardPrice`/`listingPrice`/`salePrices` | get_online_product_page_list | 顶层 `currency`（USD） |
| 采购成本 | `purchaseCost` | get_online_product_page_list | 成本口径（CNY） |
| 佣金 | `referralFee` | get_online_product_page_list | 售价口径 |
| FBA | `fbaFees` | get_online_product_page_list | 售价口径 |
| 变动结算费 | `variableClosingFee` | get_online_product_page_list | 售价口径 |
| 头程 | `headTripCost`（商品行，首选）/ `headTripPrice`（宽表补充） | get_online_product_page_list / get_custom_report_page_list | 成本口径 |

- 跨币种必须经 `config/fx.yaml` 换算，并标注**汇率来源 + 日期**；未配置 → 「需人工核查」。
- 头程优先取商品行 `headTripCost`（来源标注「商品行」）；缺值再走宽表/人工，标「需人工核查」。
- 费用不完整时**不给**「精确」利润，只标「估算、仅供参考」。

### 阶段 8：渲染与交付

`scripts/render_package.py` 严格按 `references/listing-package-template.md` 渲染 1~7 区块 +
诚实边界 + 数据说明。可选写飞书：`FeishuWriter.guard_demo` 在演示环境默认**阻断**。

### 阶段 9：外部 MCP 生成（可插拔）

按 `adapters/README.md` 的契约，AI 助手 串行调用所选生图/生视频 MCP，把结果 URL 回填区块 4。
换模型只改 adapter，本文件与流程不变；也支持人工把 `prompt` 贴到别的工具。

## 一键运行

```bash
cd sellfox-skills/08-listing-launch

# 0) agent 先按 references/workflow.md 取数计划落盘（零 Key 进脚本）：
#    out/raw/online_product.json + out/raw/product_analyze.json
#    （调 mcp__Sellfox-MCP__{search,action}，完整信封原样写盘）

# 1) 出需求单 + 成本（默认 HOLD，不生成；脚本零网络）
python3 scripts/listing_launch.py \
  --search-type asin --search-content B0XXXXXXXX \
  --name "折叠收纳凳" --marketplace US

# 2) 人工确认素材数量与成本后，放行生成
python3 scripts/listing_launch.py ... --confirm-assets
```

主要输出：`out/listing-package-<时间戳>.html`（主报告，附同名 `.md` 留档）与同名 `.asset-brief.json`（含成本闸状态）。

## 数据源与字段（真实字段，勿臆造）

只用以下真实字段（详见 `_shared/FIELD_DICTIONARY.md`）：

- Listing：`title` `asin` `parentAsin` `sku` `fnsku` `mainImage` `onlineStatus`
  `switchFulfillmentTo` `standardPrice` `listingPrice` `salePrices` `shippingPrice`
  `buyBoxWinner` `isVariation` `variationChildStr` `itemDimensions` `brand`
  `quantity` `totalInventory` `saleNum` `day7SaleNum` `day14SaleNum` `day30SaleNum`
  `profitPrice` `profitRate` `fbaFees` `referralFee` `variableClosingFee` `purchaseCost`
  `bsrRank` `rating` `ratingCount` `fullCid` `currency` `inventoryManage`
- 商品分析：`categoryNameList` `bsrRank` `openDate` `spu` `currency`

## 诚实边界（不可省略）

写进上架包末尾，缺它则包失去可信度：

- **不能自动刊登商品** —— 赛狐 MCP 无创建/修改 Listing 接口，最后一公里人工完成。
- **字符/字节限制未验证** —— 按目标站点最新官方要求核对，未配置则标「需人工核查」。
- **商标/专利/资质** —— 本技能无相关数据源，仅提示、不下结论。
- **评论原文** —— 赛狐仅有聚合指标（`rating`/`ratingCount`），无评论内容。
- **头程成本** —— 优先取商品行 `headTripCost`；缺值时需接自定义报表宽表（`headTripPrice`）或人工核查。
- **竞品/关键词情报** —— 需外部 MCP（Tavily/Sorftime/Keepa）或人工调研，否则标「需人工核查」。
- 空数据（`data:{}`）标「无数据」，**不写 0**；演示环境数据必须警示。

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 脚本里自己发 HTTP 调 MCP | 双路径漂移、Key 泄漏面扩大 | 取数只走客户端元工具 `mcp__Sellfox-MCP__{help,search,action}`，脚本零 HTTP |
| action 不带/带旧 contract_version | 被网关拒绝 | 每次执行前 search 拿当次契约；失配重取一次、按新 schema 重建参数、只重试一次 |
| 参数类型沿用旧接口全字符串 | INVALID_PARAMS 被拒 | 类型按当次 inputSchema（integer/string 因工具而异），未声明字段不传 |
| 依赖 `page_size` 控量 | 服务端静默降级为 20 | 单次 ≤1 页 + 取数计划 `max_rows` 安全阀，截断必披露（信 coverage） |
| 在对话里复述行级数据 | 上下文爆炸 | 每步取数立即落盘 `out/raw/`，组装交给脚本 |
| 硬编码亚马逊字符限制 | 政策一变就失效 | 读 `config/listing_spec.yaml` |
| USD 售价减 CNY 成本 | 结果荒谬 | 经 `config/fx.yaml` 换算并标注来源 |
| 把空数据写成 0 | 误导决策 | 区分「0」与「无数据」 |
| 未经确认就批量生图 | 产生意外费用 | 阶段 5 成本闸 `HOLD` → 人工确认 |
| 合规环节顺手改文案 | 越权、法律风险 | 只提示，永不写入 |
| 宣称「一键上架」 | 摧毁信任 | 明确产出上架包，末段人工完成 |

## 输出后

询问使用者：
1. 文案是否符合品类调性？（可回填 `--copy-json` 迭代）
2. 素材数量/规格是否合适？（调整 `config/launch.yaml → asset_plan`）
3. 是否要把某次填写的字符限制/单价回写到配置，供下次复用？（渐进式记忆）
4. 是否需要把上架包写入飞书供协作审阅？（演示环境默认阻断）