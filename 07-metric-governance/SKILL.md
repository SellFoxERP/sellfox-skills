---
name: metric-governance
description: 治理并固化跨境电商（赛狐/SellFox ERP）的自定义报表指标口径。读取官方 30 节点指标目录与现有自定义指标，对照公司口径白皮书找出缺失或口径不一致的指标，生成「指标定义提案」（新增/更新公式），人工逐项确认后由 agent 按执行清单写回赛狐（add/update_custom_report_index，经 mcp__Sellfox-MCP__action），并写后回读校验、记录变更。当用户提到"固化指标口径""指标定义""自定义指标""报表指标治理""口径校准""把考核口径写进赛狐""统一指标算法""新增或修改自定义指标""指标公式""季度校准口径""为什么技能算的和财务对不上账"时，务必使用本技能，即使用户没有明说"指标"二字。这是低频写操作（季度级），默认 dry-run，必须人工批准后才写入；脚本零网络，写操作只走 agent。
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

# 经营指标与报表固化

把公司的考核口径**固化进赛狐 ERP 本身**：读取现有自定义指标 → 对照口径白皮书 → 生成提案 →
人工确认 → agent 按执行清单写回赛狐（脚本零网络，永不直接调写工具）。

## 为什么值得做

赛狐 MCP 有一组几乎没人注意的写接口，它们是唯一能「一次性解决技能算的和业务方对不上账」的手段：

| 工具 | 作用 | 性质 |
|---|---|---|
| `get_custom_report_stat_index_list` | 官方指标目录（**30 节点指标树**） | 只读 |
| `get_custom_report_index_page_list` | 查询已有自定义指标 | 只读 |
| `add_custom_report_index` | 新增自定义指标（传 `index_formula`） | **写（L2）** |
| `update_custom_report_index` | 更新自定义指标 | **写（L2）** |

> ⚠️ **为什么是 L2 而不是 L1**：改指标定义会影响**全公司报表口径**，**静默写坏一个公式
> 可能几周后才被发现**（该接口族历史上有过 isError 误报的坑）。
> 所以必须 dry-run 对比新旧公式 + 逐项确认 + 写前快照旧公式 + 写后回读，
> 且**写操作只走 agent 按 mcp__Sellfox-MCP__action 逐项执行**（见 `_shared/APPROVAL.md`）。

## 先读这些（按需，不要一次全读）

- `_shared/SELLFOX_CLIENT.md` — **必读**：MCP 调用约定（三段式、contract_version、参数类型按当次契约、结构化错误、coverage）。
- `_shared/APPROVAL.md` — **必读**：写操作三段式流程、强制约束、read-back 校验、累计闸。
- `_shared/METRIC_FORMULAS.md` — 口径白皮书，本技能的比对基准。
- `references/workflow.md` — 完整取数/比对/写入流程与各分支处理。**执行前先读。**
- `references/index-tree-baseline.md` — 30 节点指标树与 `mappedIndex`，解析官方目录时读。
- `references/formula-and-fields.md` — `index_formula` 诚实处理原则 + 字段核对清单。**写公式前必读。**
- `references/change-record-template.md` — 变更记录与数据说明模板，渲染输出时读。
- `config/metric_governance.yaml` — 本技能参数（待治理指标清单、行数安全阀、演示策略）。
- `config/paths.yaml` — `shared_root` 绝对路径（脚本据此定位共享底座，不写死 `../_shared/`）。

## 这个技能做什么、不做什么

**做**：把**已在口径白皮书中确认过**的指标定义写成赛狐自定义指标，让全公司报表用同一套算法。

**不做**：
- ❌ 不发明新的业务口径。口径由人确认，技能只负责比对与搬运。
- ❌ **不臆造 `index_formula` 语法**。公式语言在 137 个工具文档中**没有任何语法说明**；
  未经确认的公式**一律不写入**（见 `references/formula-and-fields.md`）。
- ❌ 不删除指标（MCP 无删除工具）。
- ❌ 不把指标绑定到某张报表 —— `add_custom_report_index` **没有 `report_id` 参数**，
  绑定关系需在赛狐网页端操作（标注为「需人工核查」）。

## 如何执行

v3 分工：**取数面 = agent**（按 `references/workflow.md` 取数计划调
`mcp__Sellfox-MCP__{search,action}` 把信封落盘 `out/raw/`）；**计算面 = 脚本**（零网络）；
**写操作 = agent 按批准逐项执行**（脚本永不直接调写工具）。

```bash
# 1) agent 先按取数计划落盘 out/raw/（stat_index_op1/2.json、custom_indices_<page>.json）
python scripts/run_governance.py                                   # dry-run（默认）：落盘检查 → 比对 → 提案
python scripts/run_governance.py --execute --approve "批准 1,3"    # 生成「执行清单」（不是写入！）
# 2) agent 按清单逐项：search 重取写契约 → action → 回写结果 JSON
python scripts/run_governance.py --record out/exec_results.json --manifest out/execute_manifest_<ts>.json
# 3) agent 重取回读落盘 custom_indices_verify_<page>.json 后
python scripts/run_governance.py --verify --manifest out/execute_manifest_<ts>.json
python scripts/run_governance.py --shared-root /path/to/_shared    # 覆盖 _shared 路径
```

## 执行流程

### Step 0：前置校验（不可省略）

1. **客户端 MCP 就绪**：会话内能调 `mcp__Sellfox-MCP__{help,search,action}`
   （Key 只在客户端 MCP 配置里，脚本零网络、不需要 `SELLFOX_MCP_KEY`）。
2. **演示环境识别**：任一响应命中 `MOCK-` / `demo.example.com` / 店铺名含「演示」→
   输出顶部加 `⚠️ 演示数据，非真实经营数据` **水印**，并**默认阻断写飞书**。
   > 演示环境下可以验证**接口连通性与参数格式**，但**不能验证任何业务口径**（数值不可信），
   > 因此**演示环境默认只跑 dry-run，不执行任何 add/update**。
3. **报表 ID 就绪**：读 `_shared/config/reports.yaml`。`report_id` 是一等前置配置；
   若为空，**首轮引导**用户去赛狐网页端「自定义报表」创建报表并回填，给出 `fields_hint`。
   缺失时的**降级路径**：仍可治理全局自定义指标，但**无法按报表过滤**（`report_id_list` 传空），
   输出中显式标注「口径降级：未指定报表，治理范围为全部非共享指标」。

### Step 1：读取官方指标目录（基线，落盘）

agent 按取数计划调 `get_custom_report_stat_index_list`（`op_type=1` 新增视角、
`op_type=2` 编辑视角各一次），把完整信封落盘 `out/raw/stat_index_op1.json` /
`stat_index_op2.json`；脚本读落盘做防御性解析，得到 30 节点指标树作为
「官方认可口径」的基线。解析规则见 `references/index-tree-baseline.md`。

> 该工具返回结构未经实测，脚本按 `data` 为 list / dict 两种形态**防御性解析**；
> 解析不到时**标注「需人工核查」**，不编造节点。基线缺失不阻断比对（降级为
> 「只比对自定义指标」）。

### Step 2：读取现有自定义指标（落盘）

agent 按取数计划调 `get_custom_report_index_page_list`，`share_status=0`（**只取「我的」**，
避免越权改企业共享项），逐页落盘 `out/raw/custom_indices_<page>.json`（历史测试常见 20 条/页（当前分页以契约为准））。
脚本读落盘合并 `data.rows`（分页类列表字段）并做**取数可信门**检查：

- **必须声明 `max_rows` 安全阀**（`config/metric_governance.yaml`），合并行数触达即披露；
- 截断发生时**必须在输出中披露**覆盖范围（禁止静默截断）：
  `⚠️ 本次仅覆盖 N 行，达 max_rows 上限，其余未纳入比对。`
- **任一页 failed/损坏或发生截断 → 不生成提案**：否则「指标其实在未取到的页上」
  会被误判为缺失，生成错误的新增提案（在赛狐重复建指标）。补齐落盘后重跑。

### Step 3：对照口径白皮书找差异

`config/metric_governance.yaml` 的 `desired_indices` 是待治理清单（人工维护、与
`METRIC_FORMULAS.md` 对齐）。逐项比对：

| 情况 | 判定 | 动作 |
|---|---|---|
| 名称不存在 | 缺失 | 提案 **新增** |
| 已存在但公式不同 | 口径不一致 | 提案 **更新**（写前快照旧公式） |
| 已存在且公式一致 | 一致 | 跳过，记「已对齐」 |
| 公式为 `needs_human` | 未确认 | **不进入提案**，列「待确认公式」清单 |

### Step 4：生成 dry-run 提案

用 `_shared/scripts/approval.py` 的 `Proposal` / `ChangeItem` 构造「变更前后对比表」：
- `tool` = `add_custom_report_index` 或 `update_custom_report_index`。
- `target_type` = `index`；`field` = `index_formula`；`before` = 旧公式（更新时）/ `None`（新增）。
- `reason` 写清依据（口径白皮书条目 + 字段来源）。
- `risk_level="L2"`，`dry_run=True`；每项 `extra.contract_version = null` **占位**
  （执行前由 agent 重取写契约后填充）。
- 提案落盘 `out/proposal_<ts>.md`（对比表）+ `.json`（机读）。

`Proposal.validate(limits=thresholds["approval"])` 强校验：**单次 ≤20 条**（`max_items_per_batch`）。
超限则**分批**，并提示用户分别审批。

### Step 5：人工确认（不可跳过）

默认在终端展示对比表；若配置了飞书，走 `push_proposal()` 创建审批文档 + 卡片 @ 负责人。
- 确认文本用 `parse_confirmation()` 解析（`批准 1,3,5` / `批准全部` / `驳回`）。
- `unclear` 必须**回到人工再问**，**不得默认执行**。
- **演示环境默认阻断写飞书**（可显式覆盖，但仍加水印）。

### Step 6：执行写入（agent 执行协议）+ 写后回读校验

`--execute` **不写入**，只生成「执行清单」`out/execute_manifest_<ts>.json`
（tool_id × 参数模板 × 旧公式快照；每项 `contract_version: null` 占位——**执行前重取写契约**）。
写操作由 agent 按批准**逐项**执行，闭环如下（详见 `references/workflow.md` 第三节）：

1. **累计闸**：脚本生成执行清单前用 `AccumulatorGate` 检查（读 `ad_changes.jsonl`），
   有违规即拒绝。
2. **只执行被批准的项**（`approved_subset`）；`unclear` 回到人工再问，不得默认执行。
3. **执行前重取写契约**：agent 调 `search(tool_ids=[tool_id], detail=true)` 取当次
   `contract_version`（写工具契约变更频率高，禁止沿用旧版本/占位 null），
   并按当次 `inputSchema` 校正参数模板（类型/必填/枚举；未声明字段不传）。
4. 调 `action(tool_id, params, contract_version)` 执行写工具。
   失败/`partial` **一律按失败处理，不重放**。
5. **record**：agent 把执行结果 JSON 交给
   `run_governance.py --record <results.json> --manifest <清单>`，追加变更日志
   （`ad_changes.jsonl`，含实际使用的 contract_version，供累计闸与审计）。
6. **写后回读 verify**：agent 重取 `get_custom_report_index_page_list`（share_status=0）
   落盘 `custom_indices_verify_<page>.json`，再跑 `run_governance.py --verify --manifest <清单>`
   比对 `index_formula` 是否等于期望值。
   > ⚠️ **该工具的响应字段名未经实测**（文档只给了请求参数）。脚本按候选字段名
   > （`indexFormula` / `formula` / `index_formula`）探测；**探测不到就标注「回读字段名未知，
   > 请人工登录赛狐核对」**，**不得据此判定 success**。
7. 回读一致 → 该项方可记 `success`；不一致/无法判定 → `failed`/需人工核查，
   **不重放**、不自动重试（要重试须重新走「提案 → 批准 → 重取契约」闭环）。

### Step 7：输出变更记录

按 `references/change-record-template.md` 渲染：本次新增/更新/跳过/失败清单 + 覆写前后的公式 +
**数据说明区块**（口径、数据源、演示水印、未覆盖项）。并询问是否把 `desired_indices`
的 `formula_status` 更新为 `verified` 回写配置。

## 强制约束（来自 APPROVAL.md，写进本技能的硬约束）

1. 默认 dry-run，任何写操作先出「变更前后对比表」。
2. 单次变更 ≤20 条。
3. 提案必须含回滚方案（**写前快照旧公式**）。
4. 每次执行记录变更日志到 `ad_changes.jsonl`（`--record`，含实际使用的 contract_version）。
5. **写后必须回读校验**（`--verify`）；不一致/无法判定不记 success、不重放。
6. 执行前过**累计闸**。
7. 写飞书前过 **PII 脱敏**。
8. 演示环境默认**阻断写入**。
9. 禁止清单：批量全暂停、预算清零、删除投放（本技能无这些动作，但仍守此边界）。

## 工具与参数速查（字段名以 当前 MCP 契约与共享字段字典 为准）

| 工具 | 必填参数 | 可选参数 | 列表字段 |
|---|---|---|---|
| `get_custom_report_stat_index_list` | — | `op_type`（1 新增/2 编辑） | 结构未实测，防御性解析 |
| `get_custom_report_index_page_list` | `page_no`, `page_size` | `share_status`(0/1), `type_list`[], `report_id_list`[], `uid_list`[], `index_name` | `data.rows` |
| `add_custom_report_index` | `index_formula`, `index_name`, `type` | `index_comment` | — |
| `update_custom_report_index` | `index_formula`, `index_name`, `type` | `id`, `index_comment` | — |

`type` 取值：`0` 其他 / `1` 数量 / `2` 货币 / `3` 百分比。

> ⚠️ **货币类口径的币种**：自定义报表顶层 `currency` 是 **USD**（可配置）；
> 若指标涉及库存/成本，注意 `get_online_product_page_list.inventoryManage.currency` 是 **CNY**。
> 货币型指标的币种必须在 `index_comment` 里写清，跨币种不得直接相减。

## 诚实边界（不可省略）

- **`index_formula` 语法无官方文档** → 未经人工确认的公式不写入；提案里标 `formula_status: needs_human`。
- **`get_custom_report_index_page_list` 响应字段名未知** → read-back 可能无法自动判定，标「需人工核查」。
- **无法绑定报表**（`add` 无 `report_id` 参数）→ 绑定在网页端完成，标「需人工核查」。
- **无法删除指标**（无该工具）。
- 演示环境数值不可信，仅验证连通性。
- 空数据（`data:{}`）标「无数据」，**不写 0**。

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 脚本里自己发 HTTP / 直接调写工具 | 双路径漂移、绕过审批与审计 | 脚本零网络；写只走 agent 按执行清单调 `mcp__Sellfox-MCP__action` |
| action 不带/带旧 `contract_version` | 被网关拒绝（`CONTRACT_VERSION_MISMATCH`） | 每次执行写操作前重 `search` 拿当次契约；失配重取一次、只重试一次 |
| 执行清单里的参数模板直接照抄 | 类型/字段与当次契约不符被拒 | 参数类型/必填按当次 `inputSchema` 校正，未声明字段不传 |
| 臆造 `index_formula` 语法 | 写坏全公司口径 | 公式必须人工确认后才写 |
| 取数有失败页/截断就生成提案 | 把「未取到」误判为缺失，重复建指标 | 取数可信门：补齐 `out/raw/` 落盘后重跑 |
| 依赖 `page_size` 控量 | 服务端静默降级为 20 | 单次 ≤1 页 + `max_rows` + 截断披露 |
| 静默截断 | 被读成「已覆盖全部」 | 输出披露覆盖范围 |
| 直接比较 CNY 与 USD 字段 | 数值荒谬 | 显式折算并标注汇率来源与日期 |
| 不跑 read-back 就报成功 | 静默失败未被发现 | 写后重取落盘跑 `--verify`；不一致不重放 |
| 写失败/partial 后自动重试 | 可能重复写入 | 写操作失败一律按失败处理，进人工核查 |
| 把空数据写成 0 | 误导决策 | 区分「0」与「无数据」 |

## 输出后

询问用户：① 差异判定是否符合口径白皮书？② 是否把确认过的公式回写 `desired_indices`？
③ 是否需要定期（季度）重跑本技能做口径漂移检测？