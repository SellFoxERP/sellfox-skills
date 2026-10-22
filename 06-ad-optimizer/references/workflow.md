# 工作流详解（workflow.md，v3：agent 取数/执行 + 脚本计算）

> 何时读：准备跑一次完整的「诊断 → 提案 → 审批 → 执行 → 记录」时；或需要理解每一步的
> 数据来源、安全机制、失败处理时。SKILL.md 只给骨架，细节都在这里。
>
> **v3 分工**：**取数面与写操作执行面 = agent**（调客户端配置的
> `mcp__Sellfox-MCP__{help,search,action}`，按「取数计划」把每步**完整信封**落盘
> `out/raw/`，写操作按「写操作 agent 执行协议」逐项调 action）；**计算面 = 脚本**
> （`ad_fetch.py` 校验+组装 → `diagnose.py` 诊断 → `optimize.py` 编排），**全部零网络**。
> 调用纪律（契约版本/参数类型/错误语义/并发）见 `_shared/SELLFOX_CLIENT.md`。

## 目录

- [一、三段式与双平面](#一三段式与双平面)
- [二、取数计划（agent 执行）](#二取数计划agent-执行)
- [三、计算面（脚本执行）](#三计算面脚本执行)
- [四、诊断与 dry-run 提案](#四诊断与-dry-run-提案)
- [五、人工确认（会话内）](#五人工确认会话内)
- [六、写操作 agent 执行协议](#六写操作-agent-执行协议)
- [七、read-back 与 verify 校验](#七read-back-与-verify-校验)
- [八、累计闸](#八累计闸)
- [九、记录与回滚](#九记录与回滚)
- [十、失败与降级路径](#十失败与降级路径)

---

## 一、三段式与双平面

广告写操作**唯一能造成真实损失**。所以流程被强制切成不可跳过的三段：

```
① 诊断（只读，agent 取数落盘 + 脚本计算）
② 提案（dry-run，只输出「变更前后对比表」，不碰系统）
③ 人工确认（会话内，回复「批准 1,3,5」等）
④ 执行（写）—— v3：agent 按执行清单逐项调 action，脚本不发请求
⑤ read-back 校验 + 写 logs/ad_changes.jsonl（可回滚）
```

`scripts/optimize.py` 各子命令的职责边界：

| 子命令 | 做什么 | 不做什么 |
|---|---|---|
| `diagnose` | 读 `out/raw/` 落盘 → 诊断 → 提案 `.html/.json` | 不取数、不写任何对象 |
| `execute` | 解析批准 + 累计闸 → 输出**执行清单**（`out/ad_execute_*.html` 主报告与 `.json`） | **不调用任何写工具** |
| `record` | 把 agent 的一条执行结果追加 `logs/ad_changes.jsonl` | 不校验网关返回真伪（防线是 read_back.ok） |
| `verify` | 对比写后回读与期望，输出差异 | 不重试、不修复 |
| `selftest` | 合成数据离线自检（全绿） | 不调 MCP、不写生产日志 |

`unclear`（看不懂的确认文本）**必须回到人工再问，绝不默认执行**。

> 建议先跑 2~4 周纯 dry-run，只出提案不执行；团队认可后再从最安全的动作（改预算、加否定词）
> 逐步放开。配置里的 `safety.*` 开关就是为这个渐进过程准备的。

---

## 二、取数计划（agent 执行）

按顺序取数；已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再 `action`。
参数类型以当次 inputSchema 为准。多步可并行发起（新网关同 session 并发安全，
实测 10 并发 10/10 成功），但**每步都要落盘并核对 status**。

| # | 落盘文件 | 工具 | 关键参数 | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/campaigns_<shopId>.json` | `get_sp_campaign_list` | `shop_id`（string）；翻页请求参数是 **`next_token`**（真实 inputSchema 参数名，传 `nextToken` 会 `INVALID_PARAMS additionalProperties`；`nextToken` 只是**响应**里的游标字段，**60s 过期**），多页续落 `campaigns_<shopId>.2.json`、`.3.json`… | `itemList` | 100 | **必需**（缺失则 diagnose 直接报错）；活动结构 |
| 2 | `out/raw/keywords_<shopId>.json` | `get_sp_keyword_list` | 同上游标；可带 `campaign_id` 过滤 | `itemList` | 200 | 关键词结构与竞价 |
| 3 | `out/raw/ad_products_<shopId>.json` | `get_sp_ad_product_list` | 同上游标 | `itemList` | 200 | 广告产品结构（可选维度） |
| 4 | `out/raw/targetings_<shopId>.json` | `get_sp_product_targeting_list` | 同上游标 | `itemList` | 200 | 商品投放结构（可选维度） |
| 5 | `out/raw/download_tasks.json` | `get_ad_download_task_page_list` | `task_ids`；**分页类** | `rows` | 20 | 绩效下载任务进度（`reportState`/`downloadUrl` 在行里） |
| 6 | `out/raw/perf_campaign_<end>.xlsx` | agent 下载 `downloadUrl` 后放入 | —（本地文件） | — | 500 | 活动级绩效（`adCampaignReport` 导出） |
| 6b | `out/raw/perf_target_<end>.xlsx` | 同上 | — | — | 500 | 关键词/投放级绩效（`adTargetReport` 导出） |

**子代理取数（优先尝试）**：默认先把上表连同调用纪律（`_shared/SELLFOX_CLIENT.md` §9）交给子代理执行——原始信封只进子代理上下文，主代理仅验收步骤摘要并抽验落盘；客户端不支持/派发失败/摘要不合规时回退主代理直取（兜底路径，行为一致）。

**绩效链（步骤 5→6）的正确顺序**：

1. `create_ad_download_task`（**L1 只读性质的写**，可直接执行）：`ad_type_code=sp`、
   `report_type_code=adCampaignReport`（活动）与 `adTargetReport`（投放）各建一个，
   `report_start_date/report_end_date` = 诊断窗口 → 返回 `data.id`；
2. 轮询 `get_ad_download_task_page_list`（列表字段是 `data.rows`，**不是 itemList**）
   到 `downloadUrl` 出现；`reportState` 为失败态立即报错，不空轮询；
3. **agent 下载 xlsx**（downloadUrl 是普通 HTTP 链接）放入 `out/raw/perf_campaign_<end>.xlsx` /
   `perf_target_<end>.xlsx`——**解析交给脚本**（`ad_fetch.parse_xlsx`，本地文件、零网络）。

**落盘纪律**（详见 `_shared/SELLFOX_CLIENT.md` §6）：

- 每步 action 的**完整信封**（status/data/coverage/pagination/warnings/trace_id）原样写盘；
- **不在对话中复述行级数据**——数据只落盘，分析交给脚本；
- 单次调用 ≤1 页；`page_size` **以当轮契约为准，不照搬历史值**（eval3-2026-10-22 实测：
  SP 列表要求**数字字符串**，且网关按**未写入契约 inputSchema** 的 100~1000 范围校验——
  传 integer 或 `"20"` 都会被 `INVALID_PARAMS` 拒）；被拒时失败未发业务请求，按
  message/field_errors 修正后重试一次；翻页前看 `totalSize` 是否值得继续，
  触达 `max_rows` 必须保留 coverage 让脚本披露；
- 游标类工具（步骤 1~4）：翻页**请求参数名是 `next_token`**（不是响应字段 `nextToken`，
  传错名会 `INVALID_PARAMS additionalProperties`）；游标值取上一页**响应**的 `nextToken`
  （60 秒过期）——当页取完立即决定是否翻页，
  过期就重取第一页，不要拿旧游标硬翻；未尽页会由组装器披露「仅覆盖已落盘页」；
- `CONTRACT_VERSION_MISMATCH` → 重 search 一次、按新 schema 重建参数、重试一次；
- `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（失败时**未发业务请求**，安全）；
- 空 data（status=complete）= 无数据 → 落盘原样保留，分析层标「无数据」，不写 0 不重试。

> xlsx 的**列名以实际导出为准**，映射在 `config/optimizer.yaml` 的 `perf_columns`。
> 匹配不到的指标一律标「无数据」，**绝不写 0**。

---

## 三、计算面（脚本执行）

取数落盘完成后（agent 责任已尽），一键跑计算面（全部零网络）：

```bash
# （可选）先做落盘就绪校验，打印覆盖/降级摘要：
python scripts/ad_fetch.py --shop-id 10001 --start 2026-10-08 --end 2026-10-14

# 诊断 + dry-run 提案：
python scripts/optimize.py diagnose --shop-id 10001 --start 2026-10-08 --end 2026-10-14
```

- `ad_fetch.load_from_raw()` 校验 `out/raw/`（存在性/status/空数据/演示指纹/coverage 截断/
  nextToken 未尽），组装出与旧版**同形状**的 `AdData`——`diagnose.py` 零改动；
- `diagnose.py` 是纯函数：结构 + 绩效 + 阈值 → `Finding` 列表。三条硬边界：
  ① 只用**实测确认**的字段与取值（`state` 仅 enabled/paused/archived；`servingStatus`
  的 `BUDGET_CAPPED` 未实测，仅作人工提示）；② 数值缺失标「无数据」不按 0 处理；
  ③ 阈值全部读 `_shared/config/thresholds.yaml`，不硬编码。
- 也可单独跑共享校验器。⚠️ **须从仓库根运行**（`_shared/` 在仓库根，技能目录内没有；
  在技能目录下跑会报 `No such file or directory`），`--dir` 给技能 `out/raw` 的路径：

  ```bash
  python _shared/scripts/validate_raw.py --dir 06-ad-optimizer/out/raw \
      --step campaigns_10001:itemList --step download_tasks:rows
  ```

---

## 四、诊断与 dry-run 提案

`diagnose.build_change_items()` 把 `auto_executable=True` 的 Finding 转成
`approval.ChangeItem`（含 `before` 快照，即回滚方案）。随后
`Proposal.validate(limits=thresholds["approval"])` 强校验：

- 单次 ≤ 20 条（`max_items_per_batch`）；
- 数值型字段幅度 ≤ ±30%（`max_budget_change_pct`）；
- 禁止清单：批量全暂停 / 预算清零 / 删除投放。

若提案**全部**是「暂停活动」且 ≥2 条，`build_change_items` 会主动把它们降级为人工，
不让整份提案被禁止清单直接打死。校验不过时提案清空，只保留人工核查清单。

写出两个文件：`out/ad_proposal_<start>_<end>.html`（主报告）与 `.json`（机读提案）。
提案 JSON 每项含 **`tool_id`**（`edit_sp_campaign` / `edit_sp_targeting` 等，即寻址
action 的工具名）与 **`contract_version: null` 占位**——占位值**不得**直接用于执行，
每项执行前必须重新 `search` 取当次版本（写契约变更频率高于读契约）。

---

## 五、人工确认（会话内）

```
生成提案（本地 HTML/JSON）→ 会话内呈给广告负责人 → 人工回复「批准 1,3,5」
        → optimize.py execute 生成执行清单 → agent 逐项执行（见下一节）
```

v1.7.0 起飞书审批链已移除：不再推送审批文档/卡片，审批即**会话内人工确认**——
助手把 `Proposal.render_markdown()` 的 dry-run 对比表呈给负责人，人工回复
「批准 1,3,5 / 批准全部 / 驳回」。确认文本解析（`approval.parse_confirmation`）与
`unclear` 必回人工的规则见 `_shared/APPROVAL.md`。

> 对外输出（如导出文档给第三方）前须过 PII 脱敏（`sellfox_offline.mask_pii*`）。
> 演示环境下默认**阻断写操作与对外写出**。

---

## 六、写操作 agent 执行协议

**脚本永不直接调写工具**（v3 起删除了直连代码）。写操作由 agent 按以下协议执行，
每一步都不可跳过：

```
① 审批：python scripts/optimize.py execute --proposal out/ad_proposal_*.json \
            --approve "批准 1,3,5" --operator 张三（操作人标识）
   → 脚本完成：批准解析（unclear/reject 即止）→ 演示阻断 → 累计闸 →
     生成执行清单 out/ad_execute_<start>_<end>.{md,json}
     （被批准项 × tool_id × 参数模板 × before 快照 × read-back 计划 × record 模板）

② 重取契约：逐项执行前重新 mcp__Sellfox-MCP__search([tool_id], detail=true)
   取**当次** contract_version（不得沿用占位 null、不得沿用其他项的版本）；
   遇 CONTRACT_VERSION_MISMATCH → 重 search 一次 → 按新 schema 重建参数 → 重试一次，
   仍失败则该项记 failed，**不重放**。

③ 逐项 action：mcp__Sellfox-MCP__action(tool_id, params, contract_version)
   · params 用执行清单里的参数模板（`edit_sp_campaign`：
     `{"shop_id","items":[{"campaignId","state"|"budget"}]}`；
     `edit_sp_targeting`：`{"shop_id","items":[{"targetId","targetingType","bid"|"state"}]}`；
     `create_sp_negative_keyword_targeting`：`{"shop_id","items":[{campaignId,adGroupId,keywordText,matchType}]}`）；
   · 参数类型按当次 inputSchema；未声明字段不传；
   · `state` 取值仅用实测确认的 enabled / paused / archived；
   · **status=partial 的写一律按失败处理**：不重放、进人工核查（铁律，见 SELLFOX_CLIENT.md §3）。

④ record 落日志：每执行完一项，把清单里的 record_template 填上实际结果
   （status success/failed、read_back.ok、回读实际值、当次 contract_version）存为
   change.json，运行：
   python scripts/optimize.py record --change change.json
   → 追加一行到 logs/ad_changes.jsonl。
   防线：status=success 但 read_back.ok=false 会被 record 强制改记 failed。

⑤ verify 校验：全部执行完，把回读数据（完整信封或 {items:[{index,target_id,field,actual}]}
   判定表）存盘后运行：
   python scripts/optimize.py verify --proposal out/ad_proposal_*.json --actual <回读落盘.json>
   → 输出逐项差异；有差异的项按 failed 处理并提示人工到赛狐网页端核实。
```

**partial / failed 不重放**：写操作的失败可能已部分生效，自动重放等于二次变更；
正确动作是 record 记 failed → 输出差异 → 人工核实后**重新走一轮提案**（新 before 快照）。

**change.json schema**（record 的输入；模板已在执行清单里）：

```json
{
  "tool": "edit_sp_campaign", "target_type": "campaign", "target_id": "C001",
  "field": "budget", "before": 100, "after": 120, "reason": "…",
  "action": "set_budget", "risk": "L2", "extra": {}, "index": 1,
  "operator": "张三", "status": "success",
  "contract_version": "当次执行用的版本",
  "read_back": {"ok": true, "actual": 120}
}
```

必填：`tool/target_id/field/before/after/status`（status ∈ success/failed/skipped）；
其余透传进日志。`trace_id`（action 返回里的）建议一并记录便于追查。

---

## 七、read-back 与 verify 校验

写操作可能「静默失败」（网关错误语义可靠，但业务侧未必按预期生效），所以**写后必须回读**：

```
IF 回读值 == 期望值  → record success
ELSE                → record FAILED（告警 + 不更新本地状态）
```

- 回读工具与匹配方式已在执行清单的 `read_back_plan` 里：
  活动级 `get_sp_campaign_list`（带 `campaign_id`，`itemList` 按 `campaignId` 匹配）；
  关键词级 `get_sp_keyword_list`（`keywordId` 匹配）；否定词按 `keywordText` 集合包含校验；
- 比较用宽松比较（容忍 `"100"` vs `100`）；
- 回读结果交给 `record`（单条）与 `verify`（整批差异表）——`verify` 支持两种 actual 形态：
  ① agent 落盘的**完整回读信封**（自动按 `campaignId`/`keywordId`/`targetId` 匹配）；
  ② **判定表** `{"items":[{index,target_id,field,actual}]}`；
- `verify` 只对**实际执行过**的项（`logs/ad_changes.jsonl` 里有 success/failed 记录）做
  回读比对；未批准/未执行的项标注「未执行（不在本次批准范围）」，**不报不一致**；
- 没有 read-back，就会报告「已修改 N 个活动」而实际一个都没改。

---

## 八、累计闸

「单次 ≤20 条」挡不住连续调用 10 次。`AccumulatorGate` 读 `logs/ad_changes.jsonl` 拦住：

| 闸门 | 默认 | 配置键（thresholds → approval） |
|---|---|---|
| 单 campaign 24h 调整次数 | ≤ 2 | `max_changes_per_campaign_24h` |
| 单 campaign 净变化（回看窗见下） | ≤ ±30% | `max_net_change_pct_24h` |
| 全局单日预算增量总额 | 5000 | `max_daily_budget_increment` |
| 同一对象同一天去重 | — | 读日志自然去重 |

净变化累计的回看窗口默认 **7 天（`net_change_lookback_hours: 168`，_shared 默认值，
可在 thresholds → approval 覆盖）**——跨天/跨周拆批的复利累计同样受限幅约束。

例：预算 100，一次 +30% 到 130，再一次 +30% 到 169 —— 两次单次都合规，累计却 +69%
（即使第二次在一周后，也落在默认回看窗内被拦）。
只有 `status="success"` 的记录参与累计，所以 `record` 必须真实、及时。命中累计闸时
`execute` 拒绝生成执行清单并列出违规（退出码 4）。

---

## 九、记录与回滚

每次执行由 agent 逐项 `record` 写 `logs/ad_changes.jsonl`（一行一条 JSON）：时间 / 对象 /
字段 / 变更前 / 变更后 / 操作人 / status / contract_version / read_back。**变更前 = 回滚值**：
把字段改回 `before` 即回滚（同样要走提案 → 审批 → agent action 的完整协议，不得直改）。

> ⚠️ 演示环境默认阻断写操作（`execute` 需显式 `--allow-demo` 才生成清单），
> 因为演示数据上的写入不具业务意义。

---

## 十、失败与降级路径

| 情况 | 处理 |
|---|---|
| 客户端 MCP 不可用 | 取数/写操作都无法进行——检查客户端 Sellfox MCP 配置（Key 只在客户端，脚本不需要环境变量） |
| `out/raw/campaigns_<shopId>.json` 缺失 | `diagnose` 直接报错停止，提示按取数计划落盘（活动结构是诊断前提） |
| 某步骤信封 `status=failed` | 该页数据跳过并记 warning，其余照常；数据说明记录 error_code |
| 广告列表 `data:{}` / `null` | 标「无数据」，**不写 0**，**不重试** |
| 绩效 xlsx 落盘缺失 / 任务一直未完成 | 降级为「仅结构与状态诊断」并标注；任务失败态在 `download_tasks.json` 摘要里披露 |
| 未装 openpyxl | 明确提示 `pip install openpyxl`；装不上则降级「仅结构与状态诊断」并披露 |
| 仅拿到 MSKU 级绩效 | 活动级/关键词级诊断降级为「人工核查」并标注 |
| 提案违反强制约束 | 清空自动提案，仅保留人工核查清单（不静默） |
| 提案混入不支持的写工具（如 Listing 类） | `execute` 拒绝执行整批（fail-closed），指明提案项 #N 与工具名（退出码 5），不生成执行清单 |
| 写 action 失败 / partial | 该项记 `failed`，**不重放**；人工核实后重新走提案 |
| 回读不一致 | `record` 防线改记 `failed`；`verify` 输出差异表；不更新本地状态 |
| 命中累计闸 | `execute` 拒绝生成执行清单，列出违规（记 skipped 的责任在 agent，见 APPROVAL.md） |
| 演示环境 | 报告顶部加水印；默认阻断写操作与对外写出 |
