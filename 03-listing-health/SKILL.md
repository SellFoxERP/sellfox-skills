---
name: listing-health
description: 对亚马逊 Listing 做只读健康体检，诊断不可售/被抑制（onlineStatus）、丢购物车、无流量、有流量无转化、评分下滑、BSR 恶化、价格劣势、标签缺失，输出按严重度排序的问题清单（只诊断不修改）。当用户提到"Listing 体检""链接健康""链接体检""不可售""被抑制""丢购物车""buybox 掉了""没流量""有流量没转化""转化下滑""评分掉""BSR 掉排名""标签没打""这个 listing 有问题""帮我看下这些链接"，或要求排查某个 ASIN/店铺在售商品的异常、新品上架后跟踪、断货导致的排名下滑时，务必使用本技能，即使用户没说"体检"二字。
---

## 报表自动发现

任务需要自定义报表时读取共享底座 `ONBOARDING.md`：助手先复用已验证选择，否则通过当前 MCP 发现工具，或从自定义指标的 `reportList` 自动提取报表 ID 并核对用途。不要让商家手填 ID、编辑 YAML 或先创建报表。
匹配唯一且字段适用时自动采用；多个候选只让商家选名称；无法取得可用报表则说明缺口并继续简版。内置查询和全局指标盘点不需要报表初始化。用户已选择简版时不重复打扰。

## 运行环境与路径

以本文件所在目录为技能根目录，执行脚本前切换到该目录，使用 Python 3.10+。
本项目采用批量安装，九个技能共用一份底座；源码布局的底座位于 `../_shared/`。
读取下文 `_shared/...` 引用时先定位实际共享底座。脚本定位优先级为：显式参数（支持时）
→ `SELLFOX_SHARED_ROOT` → `config/paths.yaml` → 包内 `_shared` → 同级 `_shared`。
批量安装器将共享路径写入配置，九个技能复用同一份商家配置。
`mcp__Sellfox-MCP__...` 是服务工具的示意名称；使用客户端实际暴露的 Sellfox-MCP
`help/search/action`，不得因客户端前缀不同而判定工具缺失。当前完整契约来自 help 或 search；
已有完整契约直接 action，不重复 search。首先读取共享 `SELLFOX_CLIENT.md`。
下文“零网络”仅指赛狐取数后的离线计算；飞书和审批通知模块会联网。
默认输出本地报告；只有用户要求时才启用飞书或外部服务。没有原始数据时仅说明缺口或产出
明确标识的模板，不能把空数据报告当作经营正常的结论。

# Listing 健康体检

对在售商品做**逐 ASIN/Listing 的完整体检**，把日报里的单点检查升级成按严重度排序的问题清单。

## 为什么这样设计

本技能只读取数据并生成诊断清单，不修改 Listing 的标题、图片或价格。

技能采用亚马逊官方的**「路由器 + 子流程」**模式：`SKILL.md` 只做**分诊**（判断问题类型、排序严重度），命中后**按需加载 `references/*.md`** 里的处理流程。这样单个技能不臃肿，也不会产生多个抢触发的子技能。

第二个原则：**拿不到的数据一律标注「需人工核查」，绝不编造；空数据标「无数据」，不写 0。** 体检报告的可信度全靠这一点——一条编造的「竞品价格」会让整份报告作废。

## 先读这些

按需读取，别一次全读：

- `references/workflow.md` — **执行前必读**：取数计划（agent 取数落盘）、分页硬上限、字段裁剪、降级路径、截断披露。
- `references/buyability.md` — 命中「不可售/被抑制/丢购物车」时读（P0/P1）。
- `references/searchability.md` — 命中「无流量/BSR 恶化/销量衰退」时读（P1/P2）。
- `references/conversion.md` — 命中「有流量无转化/价格劣势/评分下滑」时读（P2）。
- `references/compliance.md` — 需要提示合规风险时读；**只提示，永不写入**。
- `references/report-template.md` — **渲染输出前必读**：问题清单模板与字段来源标注规则。
- `references/listing-fields.md` — 需要核对某字段属于哪个工具、或判断能否取值时读。
- `_shared/SELLFOX_CLIENT.md` — MCP 调用约定（契约版本门禁、参数类型、错误语义、落盘纪律），**agent 取数前必读**。
- `_shared/APPROVAL.md` — 本技能只读，本不涉及写操作；若你要把报告推到飞书，读它的「PII 脱敏 + 演示环境阻断」两节。

## 能力边界（诚实写清）

**本技能能做**：读 `get_online_product_page_list` / `get_product_analyze_list` / `get_product_by_label`，可选叠加 `get_custom_report_page_list`（宽表），产出问题清单。

**本技能不做**：
1. **不修改任何 Listing**（无接口能力，也不该做）。
2. **拿不到**：竞品 BuyBox 价、类目平均转化率基准、账户健康/绩效通知/政策合规状态、评论原文。这些一律标 **⚠️ 需人工核查**。
3. **无 `report_id` 时**，`sessions`/`pageViews`/`conversionRate`/`buyBoxPercent`/`lowReviewNum`/`bigCategoryRanking`/`smallCategoryRanking`/`availableDays` **取不到**（这些字段只在 489 字段宽表里）。技能会**显式降级并列出口径降级说明**，不会用别的字段冒充。
4. **不得把截图、竞品页面、人工观察当作本技能结论**——本技能结论只来自上列接口字段。

## 执行流程

### Step 0：参数与前置校验

确认范围：店铺（`shop_id_list`）、站点、时间窗口（默认近 7/30 天）、是否含不可售状态扫描。未提供时用配置默认值。

**先做前置校验并写入报告顶部**：
- **客户端 MCP 就绪**：会话内能调 `mcp__Sellfox-MCP__{help,search,action}`（Key 只在客户端 MCP 配置里，脚本**不需要** `SELLFOX_MCP_KEY`，也**不得自己发 HTTP**）。若数据源为演示/沙箱环境，运行时指纹识别后加水印并默认阻断写入。
- 读 `config/paths.yaml` 解析 `shared_root`（为空走同仓库同级兜底）；读 `_shared/config/thresholds.yaml`（`listing` 段）与 `_shared/config/reports.yaml`。
- **report_id**：`reports.yaml → daily_ops.id` 为空 → 启用**降级路径**（只用 online 字段），并在报告显式写「口径降级：缺少自定义报表，流量/购物车占比/CVR 等不可用」。
- **落盘目录**：`out/raw/` 为空 → 还没取数，先按 `references/workflow.md` 的取数计划落盘，再跑计算面。
- **演示环境**：组装时命中 `MOCK-` / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」指纹 → 报告顶部加**水印**「⚠️ 演示数据，非真实经营数据」，并默认**阻断写飞书**。

### Step 1：取数（agent 按取数计划落盘 → 脚本校验组装）

**v3：取数面是 agent**——按 `references/workflow.md` 的**取数计划**调
`mcp__Sellfox-MCP__{search,action}`，把每步完整信封落盘 `out/raw/<step>.json`：

| 顺序 | 落盘文件 | 工具 | 列表字段 | 用途 |
|---|---|---|---|---|
| 1 | `online_<batch>.json`（按状态/店铺分批） | `get_online_product_page_list` | `data.rows` | 主数据源（状态/价格/评分/BSR/销量/标签/库存） |
| 2 | `custom_report_<label>.json`（仅 report_id 就绪） | `get_custom_report_page_list` | `data.rows` | 补 sessions/CVR/buyBoxPercent/类目排名/评分明细 |
| 3 | `product_analyze.json`（可选） | `get_product_analyze_list` | `data.rows` | 补类目名/BSR/FBM 可用天数 |
| 4 | `labels.json`（仅配置 `labels.required_groups`） | `get_product_by_label` | `data.rows` | 校验标签覆盖 |

纪律见 `_shared/SELLFOX_CLIENT.md`：契约先取后用（contract_version）、参数类型按当次
inputSchema、失配重取一次、不信 `has_more` 信 `coverage`、每步落盘不复述数据；多批次**可并行发起**。
落盘完成后跑 `fetch_listing_data.py` 校验+组装（**零网络**），输出与旧版同形状的 `data.json`：

```bash
# 1) agent 已按取数计划把信封落盘 out/raw/*.json
python scripts/fetch_listing_data.py --shop-ids 10001 --out data.json   # 校验 + 组装
# 2) 诊断与渲染（零改动）
python scripts/diagnose.py --in data.json --out problems.json
python scripts/render_report.py --in problems.json --out report.md
```

### Step 2：分诊（本技能的核心）

对每条记录按 `config/listing.yaml` 的 `severity` 判定并**按严重度排序**：

| 级别 | 问题 | 判定字段（数据源） |
|---|---|---|
| **P0** | 不可售 / 被删除 | `onlineStatus` 非 active（online） |
| **P1** | 丢购物车 | `buyBoxPercent < 阈值`（custom）或 `buyBoxWinner=false`（online，退化信号） |
| **P1** | 无流量 | `sessions` 连续 N 天为 0（custom） |
| **P1** | 价格劣势 | 自身 `listingPrice`/`standardPrice` 异常（online；**缺竞品基准 → 需人工核查**） |
| **P2** | 评分下滑 | `rating` 环比下降（online，需历史快照；`lowReviewNum` 仅 custom） |
| **P2** | BSR 恶化 | `bsrRank[].rank` 名次变差（online）；类目名次 `big/smallCategoryRanking`（custom） |
| **P2** | 有流量无转化 | `sessions` 高 + `conversionRate` 低（custom；**类目基准拿不到 → 需人工核查**） |
| **P3** | 标签缺失 | `labels` / `productLabelList` 未含必填分组（online） |

命中某类问题后，**加载对应 reference 拿到完整处理流程**：

- P0/P1 不可售·丢购物车 → `references/buyability.md`
- 无流量·BSR 恶化·销量衰退 → `references/searchability.md`
- 有流量无转化·价格劣势·评分下滑 → `references/conversion.md`
- 合规风险提示 → `references/compliance.md`
- 渲染输出 → `references/report-template.md`

### Step 3：渲染与交付

按 `references/report-template.md` 输出，**必须包含**：
1. 报告顶部：覆盖范围、时间窗口、口径降级说明、演示环境水印（如有）。
2. 问题清单：按 P0→P3 排序，每条含 ASIN/店铺、问题、证据字段值、**该字段所属工具**、建议动作、是否需人工核查。
3. **截断披露**：若 `max_rows` 触顶，写明「本次仅覆盖 Top N，其余未纳入」（禁止静默截断）。
4. 诚实边界段：本技能不修改 Listing；列出需人工核查项。

如用户要求推送飞书：走 `_shared/scripts/feishu_writer.py`，写入前自动 `mask_pii` 脱敏（Listing 字段无 PII，但兜底），并先过 `guard_demo`（演示环境默认阻断）。

## 硬约束速查（违反即为缺陷）

- **MCP 访问只走客户端元工具**：`mcp__Sellfox-MCP__{help,search,action}`，脚本零 HTTP、
  不需要 Key 环境变量（契约见 `_shared/SELLFOX_CLIENT.md`；旧直连 `sellfox_client.py` 已废弃，不得 import）。
- **action 必带当次 search 返回的 `contract_version`**；失配（`CONTRACT_VERSION_MISMATCH`）
  → 重 search 一次、按新 schema 重建参数、重试一次，不重放原请求。
- **参数类型/参数名按当次 inputSchema**：分页参数 string/integer 因工具而异
  （如 `get_product_by_label` 页参数名是 `page_num` 不是 `page_no`）；未声明字段不传。
- **不信 `pagination.has_more`，信 `coverage` 与 `totalSize`**（实测 has_more 与 coverage 矛盾）。
- **同 session 并发安全**（新网关实测 10 并发成功）：多状态/多店铺批次可并行，
  但落盘文件名不冲突、每步单独核对 status。
- **单次调用 ≤1 页 + `max_rows` 安全阀**：服务端强制 20 条/页，靠 `page_size` 控量无效；
  触达上限必须保留 coverage 让脚本披露（禁止静默截断）。
- **列表字段以契约 `pagination` 元数据为准**（本技能四个工具都是 `rows`）。
- **`data` 空 = 无数据，不是失败**；标「无数据」，不重试，不写 0。
- **status=partial**：读操作可采信但必须在数据说明披露。
- **每步取数立即落盘 `out/raw/`，不在对话中复述行级数据**（上下文是稀缺资源）。
- **字段名/来源必须查 `config/field-map.yaml`**，不用别的字段冒充 `sessions` 等宽表字段。
- **阈值、到货周期、目标值、report_id 一律外置**到配置文件，绝不硬编码。
- **币种**：`listingPrice` 顶层为 USD；`inventoryManage.currency` 是 CNY —— 跨币种不得直接相减。

## 阈值与配置

- 通用阈值在 `_shared/config/thresholds.yaml` 的 `listing` 段（`buybox_pct_min`、`sessions_zero_days`、`rating_drop_abs`）。
- 技能专属参数在 `config/listing.yaml`（窗口、`max_rows`、BSR/销量降幅、降级字段清单、严重度）。
- 字段与数据源归属在 `config/field-map.yaml`。
- **阈值、到货周期、目标值、report_id 一律读配置，绝不硬编码**；报表 ID 缺失按共享 `ONBOARDING.md` 自动发现；其他参数缺失时按配置加载器的批量询问流程补全，并在输出中标注每个参数的【来源】：配置命中 / 本次填写 / 默认值。

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 脚本里自己发 HTTP 调 MCP | 双路径漂移、Key 泄漏面扩大 | 取数只走客户端元工具，脚本零网络 |
| action 不带/带旧 `contract_version` | 被网关拒绝 | 每次执行前 search 拿当次契约 |
| 参数类型/参数名沿用旧习惯（全字符串、`page_no` 写到 `get_product_by_label`） | `INVALID_PARAMS` 拒绝 | 类型与参数名按当次 inputSchema（该工具是 `page_num`） |
| 传未声明字段 | `additionalProperties` 拒绝 | 只传契约 properties 里的字段 |
| 信 `pagination.has_more` 翻页 | 与 coverage 矛盾导致误判 | 只信 coverage 与 totalSize |
| 依赖 `page_size` 控量 | 服务端静默降级为 20 | 单次 ≤1 页 + `max_rows` 安全阀 + 截断披露 |
| 把 `data:{}` 当失败重试 | 把「无数据」当故障 | 标「无数据」，不写 0，不重试 |
| 用别的字段冒充 `sessions` / 字段来源写错 | 结论不可信 | 字段名/来源查 `config/field-map.yaml` |
| 拉宽表 489 字段全量 / 对话中复述行级数据 | 上下文爆炸 | 字段裁剪；数据只落盘，分析交给脚本 |
| 跨币种相减 | 价格/利润计算错误 | `listingPrice`(USD) 与 `inventoryManage.currency`(CNY) 显式标注，不直接相减 |
| 静默截断 | 被读成「已覆盖全部」 | `coverage.truncated` → 输出披露「仅覆盖 Top N」 |
## 销量下降专项排查

遇到该主题时阅读 [决策支持](references/decision-support.md)，使用随批量包提供的情景脚本；先核对输入来源，输出假设与缺口。
