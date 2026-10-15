---
name: ad-optimizer
description: 诊断并优化亚马逊 SP/SB 广告（赛狐/SellFox ERP 数据）。拉取广告活动/关键词/广告产品结构与绩效，按配置阈值识别「高花费零转化」「ACOS 超标」「优质词扩量」「素材衰退」等问题，先出 dry-run「变更前后对比表」并推送飞书审批，人工批准后才执行调预算、暂停活动、调竞价、加否定词等写操作；写前快照旧值、写后 read-back 校验、全程写 ad_changes.jsonl 以支持回滚。当用户提到「广告优化」「广告诊断」「ACOS 太高」「广告花费异常」「调预算」「降竞价」「加否定词」「暂停广告活动」「SP 广告怎么样」「campaign 表现」「广告跑不动/亏钱」「预算受限」时，务必使用本技能，即使用户没有明确说「优化」二字。本技能只处理广告域，不做素材替换、Listing 修改与价格调整。
---

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

# 广告诊断与优化执行

诊断广告 → 出 dry-run 提案 → 人工审批 → 执行 → 记录回滚。**这是 9 个技能里唯一能造成真实损失的**，
所以每一步都围绕「让人能审、能拦、能回滚」设计。

## 为什么这样设计

**广告写操作是真金白银。** 一次误操作可能把跑得好的活动预算清零，或把活动全暂停。因此：

- **默认 dry-run**：任何写操作先出「变更前后对比表」，只输出、不碰系统。
- **脚本永不直接写**：取数与写操作执行都是 agent 的职责（按取数计划落盘 / 按执行清单
  逐项调 `mcp__Sellfox-MCP__action`），脚本只提供提案构造与记录/校验 helper——
  双路径漂移是误操作的温床。
- **单次 ≤20 条、幅度 ≤±30%**：接口允许 100 条，但一次改 100 个人工根本审不过来。
  限额的本质是**让「人工确认」这件事真的有效**，而不是形式。
- **写后必须 read-back**：网关返回成功 ≠ 业务侧已按预期生效（`partial` 写一律按失败），
  不做回读就可能报告「已改 12 个活动」而实际一个都没改。
- **累计闸**：单次合规挡不住连续调用（100 → +30% → 130 → +30% → 169，累计 +69%），
  所以执行前必须查 `ad_changes.jsonl`。
- **拿不到就标注，绝不编造**：空数据标「无数据」不写 0；拿不到的字段标「⚠️ 需人工核查」。
  宣称做不到的能力会摧毁信任。

## 先读这些

本技能自身的参考资料（按需读，不必一次全读）：

- `references/workflow.md` — **跑完整流程前读**：取数计划（agent 落盘）、计算面流水线、
  **写操作 agent 执行协议**（审批 → 重取契约 → 逐项 action → record → verify）、
  累计闸、回滚、失败降级。
- `references/diagnosis-rules.md` — **要调整/核对诊断规则时读**：规则表、字段来源、阈值键、诚实边界。
- `references/field-dictionary.md` — **构造写工具参数或核对字段是否存在时读**：读写接口字段与分页形态。
- `references/output-templates.md` — **渲染报告/提案/日志时读**：模板与渲染注意事项。

共享底座（**必读**，本技能能力的一半在它们身上）：

- `_shared/SELLFOX_CLIENT.md` — **当前 MCP 调用约定**（agent 调 `mcp__Sellfox-MCP__{help,search,action}`
  的纪律：contract_version、参数类型、错误语义、并发、落盘约定）。
- `_shared/APPROVAL.md` — 人机审核统一规范（风险分级、六条强制约束、四个安全机制）。
- `_shared/config/thresholds.yaml` — 阈值与审批闸门（**技能不硬编码任何阈值**）。

**v3 分工**：脚本**零 HTTP、零 MCP**——取数与写操作执行都是 agent 的职责；
脚本只做落盘校验、诊断、提案构造、记录与校验 helper。配置走 `config_loader.py`；
提案/闸门/确认解析走 `approval.py`；信封与演示指纹走 `sellfox_offline.py`；
**禁止 import 旧 `_shared/scripts/sellfox_client.py`（已废弃）**。

## 诚实边界：本技能做不到什么

- **不做素材替换**（`adClickRate` 衰退时只提示，换素材仍需人工）。
- **不做 Listing 修改、不改价格**（MCP 也无此能力）。
- **无法获取**账户健康 / 绩效通知 / 买家消息 / 政策合规 / 评论原文 → 一律标「⚠️ 需人工核查」。
- **`servingStatus` 的 `BUDGET_CAPPED` 取值未经实测验证** → 只作人工提示，**不用于自动判定预算受限**。
- **关键词级竞价**（`edit_sp_targeting` 的 `targetId` 是否等于 `keywordId`）**未实测确认** → 默认不自动执行。
- Amazon 广告**没有**按 campaign 直接查绩效的只读工具：绩效依赖 `create_ad_download_task` 导出的 xlsx，
  列名以实际导出为准（映射在 `config/optimizer.yaml`）；未装 `openpyxl` 或绩效缺失时，诊断会降级并标注。

## 执行流程

### Step 1：确认参数（缺失则用默认，并标注来源）

| 参数 | 默认 | 说明 |
|---|---|---|
| 店铺 | 全部 / 指定 `shop_id` | 广告写操作必须落到具体店铺 |
| 日期范围 | 近 7 天 | 绩效周期；「昨日」等业务日须标注时区 |
| 数据源 | `config/optimizer.yaml → data_source.perf_primary` | `download_task`（推荐）/ `custom_report` |
| 输出 | 本地 Markdown + proposal.json | 是否推飞书、@ 哪位负责人 |

**前置校验「客户端 MCP 就绪」**：会话内能调 `mcp__Sellfox-MCP__{help,search,action}`
（Key 只在客户端 MCP 配置里，脚本不需要 `SELLFOX_MCP_KEY`）。
演示环境指纹（`MOCK-` / `demo.example.com` / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」）
**默认阻断写操作与写飞书**，并在报告顶部加水印。

### Step 2：取数（agent 按取数计划落盘）

**v3：取数面是 agent**——按 `references/workflow.md` 的**取数计划**调
`mcp__Sellfox-MCP__{search,action}`，把每步**完整信封**落盘 `out/raw/`：

| 落盘文件 | 工具 | 列表字段 |
|---|---|---|
| `campaigns_<shopId>.json`（必需；游标多页续 `.2.json`…） | `get_sp_campaign_list` | `itemList` + `nextToken`（60s 过期） |
| `keywords_<shopId>.json` | `get_sp_keyword_list` | `itemList` |
| `ad_products_<shopId>.json` | `get_sp_ad_product_list` | `itemList` |
| `targetings_<shopId>.json` | `get_sp_product_targeting_list` | `itemList` |
| `download_tasks.json` | `get_ad_download_task_page_list`（**分页类**） | `rows` |
| `perf_campaign_<end>.xlsx` / `perf_target_<end>.xlsx` | agent 下载 `downloadUrl` 后放入（解析归脚本） | — |

绩效链：`create_ad_download_task`（L1，只读性质的写）→ 轮询 `get_ad_download_task_page_list`
（列表字段是 `data.rows`）拿 `downloadUrl` → **agent 下载 xlsx** 放入 `out/raw/`；
兜底 `get_custom_report_page_list`（MSKU 级，**不可归因 campaign**）落 `perf_custom_<shopId>.json`。

纪律见 `_shared/SELLFOX_CLIENT.md`：每步落盘并核对 status、不在对话中复述行级数据、
单次 ≤1 页 + `max_rows` 安全阀、**截断必披露**（`AdData.disclose()` 生成披露语句，
禁止静默截断）、空数据标「无数据」不写 0、partial 须披露。详细计划与降级路径见
`references/workflow.md`。

### Step 3：诊断（只读，脚本零网络）

```
python scripts/optimize.py diagnose --shop-id 10001 --start 2026-10-08 --end 2026-10-14
# 先做落盘就绪校验（可选）：python scripts/ad_fetch.py --shop-id 10001 --start … --end …
# 离线自检（不调用 MCP）：python scripts/optimize.py selftest
```

阈值全部读 `_shared/config/thresholds.yaml` 的 `ad` 段；判断逻辑在 `scripts/diagnose.py`（纯函数）。
规则与字段来源见 `references/diagnosis-rules.md`。**输出 dry-run 提案 + 报告**，
写到 `out/ad_proposal_<start>_<end>.{md,json}`；提案 JSON 每项含 `tool_id` 与
`contract_version: null` 占位（执行前须重 search 取当次版本）。

### Step 4：审批（飞书）

把提案推送给广告负责人（`approval.push_proposal`，由 agent 在会话内编排），人工回复
「批准 1,3,5」/「批准全部」/「驳回」。**`unclear` 必须回到人工再问，不得默认执行。**
演示环境默认阻断写入飞书。

### Step 5：执行（写，agent 按协议逐项调 action）

```
# ① 脚本只生成「执行清单」（不调用任何写工具）：
python scripts/optimize.py execute --proposal out/ad_proposal_2026-10-08_2026-10-14.json \
    --approve "批准 1,3,5" --operator ou_xxx
#    → 审批解析 → 累计闸 → out/ad_execute_<start>_<end>.{md,json}
# ② agent 逐项：重取写契约（search 取当次 contract_version）→ action → 按 read-back 计划回读
# ③ 每项执行完落日志：
python scripts/optimize.py record --change change.json     # → logs/ad_changes.jsonl
# ④ 全部执行完校验：
python scripts/optimize.py verify --proposal out/ad_proposal_….json --actual <回读落盘.json>
```

顺序：解析批准 → 过**累计闸**（读 `logs/ad_changes.jsonl`）→ **agent 逐项调写工具**
（partial/failed **不重放**）→ **read-back 校验** → `record` 落 `ad_changes.jsonl`
（`before` 即回滚值；`status=success` 但 `read_back.ok=false` 会被 record 防线改记 `failed`）。
回读不一致不更新本地状态，人工到赛狐网页端核实。详见 `references/workflow.md`
「写操作 agent 执行协议」。

## 写进 SKILL 的硬约束（违反即缺陷）

1. 默认 dry-run，任何写操作先出「变更前后对比表」；
2. 单次变更 ≤ 20 条（`approval.max_items_per_batch`）；
3. 预算/竞价调整幅度 ≤ ±30%（`approval.max_budget_change_pct`）；
4. 提案必须含回滚方案（写前快照旧值 `before`）；
5. 每次执行记录变更日志到 `logs/ad_changes.jsonl`（agent 逐项 `record`）；
6. 禁止清单：**批量全暂停、预算清零、删除投放**；
7. 写后必须 read-back 校验（`record` + `verify`，不能只信网关返回）；
8. 执行前过**累计闸**（读变更日志去重、限次数、限净变化、限单日预算增量）；
9. 写飞书前过 **PII 脱敏**（`buyerEmail`/`buyerName`），文档标注「已脱敏」；
10. 演示环境默认**阻断写入**；
11. **脚本零 HTTP、零 MCP**：取数与写操作只走 agent 调 `mcp__Sellfox-MCP__{help,search,action}`，
    禁止 import 旧 `sellfox_client.py`；
12. **写操作由 agent 按 APPROVAL.md 逐项调 action**：脚本只出执行清单（tool_id × 参数模板 ×
    before 快照），不发请求；partial/failed 不重放；
13. **写契约执行前重取**：提案里的 `contract_version: null` 是占位，每项执行前必须重新
    `search` 取当次版本；失配 → 重取一次重建参数重试一次，仍失败记 failed。

> 排序也是原则：先跑 **2~4 周纯 dry-run**，团队认可后再开启执行，且从最安全的动作
> （改预算、加否定词）开始。配置里的 `safety.*` 开关就是为此设的。

## 配置

- `config/paths.yaml` — **本技能特有**：`shared_root` 绝对路径（不写死 `../_shared/`）、输出/日志路径。
- `config/optimizer.yaml` — **本技能特有**：数据源、绩效列名映射、提案幅度、范围安全阀、写操作开关。
- `_shared/config/thresholds.yaml` — 阈值与审批闸门（ACOS 上限、目标值、ROAS、闸门上限）。
- `_shared/config/shops.yaml` — 店铺与负责人（告警路由）。
- `_shared/config/reports.yaml` — 报表 ID（若用 `custom_report` 兜底则需自建广告报表并回填）。

参数缺失时按统一交互规范**一次性批量询问**，并在输出中标注每个参数的【来源】：
配置命中 / 本次填写 / 默认值（默认值必须显式标注，不能混进真实数据）。

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/_bootstrap.py` | 解析 `paths.yaml`、挂载 `_shared`、统一配置入口（零网络） |
| `scripts/ad_fetch.py` | **落盘校验 + 组装器**：校验 `out/raw/` 信封并组装 `AdData`（零网络） |
| `scripts/diagnose.py` | 纯函数诊断引擎 + 提案构造（不碰系统） |
| `scripts/optimize.py` | 编排：`diagnose`（读落盘）/ `execute`（出执行清单，不写）/ `record` / `verify` / `selftest` |

先跑 `python scripts/optimize.py selftest` 验证环境与逻辑（不调用 MCP）。

## 测试用例

1. 「帮我看看广告，ACOS 太高的降一降竞价」→ 触发；出 dry-run 提案，不直接执行。
2. 「把花了很多钱但没出单的活动暂停掉」→ 触发；P0 规则，单次 ≤20 条，批量全暂停被拦截。
3. 「昨天广告花了多少，哪些亏钱」→ 触发；先取数诊断，绩效缺失时标注「无数据/需人工核查」，不编造。