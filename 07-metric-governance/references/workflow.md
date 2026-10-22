# 指标治理完整流程（workflow.md，v3：agent 取数/写 + 脚本计算）

> 执行本技能前读本文件。它把 SKILL.md 的 7 个 Step 展开到可操作粒度，并给出各分支
> （空数据 / 截断 / 演示环境 / 公式未确认 / 回读失败）的处理方式。
>
> **v3 分工**：取数面 = agent 调客户端配置的 `mcp__Sellfox-MCP__{help,search,action}`，
> 按下方「取数计划」把每步**完整信封**落盘 `out/raw/`；计算面 = `run_governance.py`
> （落盘检查 → 差异比对 → dry-run 提案 / 执行清单 / 记录 / 回读比对），**零网络**。
> **写操作（add/update_custom_report_index）由 agent 按批准逐项执行**，脚本永不直接调写工具。
> 调用纪律（三段式 / contract_version / 参数类型 / 错误语义）见 `_shared/SELLFOX_CLIENT.md`。

## 目录

- [零、前置校验](#零前置校验)
- [一、取数计划（agent 执行）](#一取数计划agent-执行)
- [二、计算面流水线（脚本执行）](#二计算面流水线脚本执行)
- [三、写操作 agent 执行协议](#三写操作-agent-执行协议)
- [四、降级路径](#四降级路径)
- [五、截断与披露](#五截断与披露)

---

## 零、前置校验

1. **客户端 MCP 就绪**：会话内能调 `mcp__Sellfox-MCP__*`（Key 只在客户端 MCP 配置里，
   脚本零网络、不需要 `SELLFOX_MCP_KEY`）。若数据源为演示/沙箱环境，命中指纹即加水印，
   且本技能默认只 dry-run（见写操作协议）。
2. **报表范围**：全局只读治理无需 ID；用户指定报表时按共享 `ONBOARDING.md` 自动发现并把已验证 ID 传入 report_id_list。无法定位不能扩大写入范围。发现阶段可只读共享关联，指标修改仍须核对所有权与具体授权。
3. **演示环境指纹**（`MOCK-` / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」等，
   脚本从落盘信封自动检测）。命中 → 输出顶部加水印，**默认阻断生成执行清单**
   （`demo_policy.block_write_execute`）。演示数据只可验证连通性与参数格式，
   **不可验证任何业务口径**。

---

## 一、取数计划（agent 执行）

按顺序取数；已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再 `action`。
每步把 action 的**完整信封**（status/data/coverage/pagination/warnings/trace_id）
原样写盘，**不在对话中复述行级数据**。

| # | 落盘文件 | 工具 | 关键参数 | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/stat_index_op1.json` | `get_custom_report_stat_index_list` | `op_type=1`（新增视角） | 结构未实测，防御性解析 | — | 官方指标目录基线（30 节点指标树） |
| 2 | `out/raw/stat_index_op2.json` | `get_custom_report_stat_index_list` | `op_type=2`（编辑视角） | 同上 | — | 与步骤 1 取并集做参照 |
| 3 | `out/raw/custom_indices_<page>.json`（逐页一盘，如 `custom_indices_1.json`） | `get_custom_report_index_page_list` | `page_no`/`page_size`；`share_status=0`（**只取「我的」，避免越权改共享项**）；可加 `report_id_list`（自动发现并验证后） | `rows` | 100 | 现有自定义指标；翻页各落一盘，翻页前看 `totalSize` 是否值得继续 |
| 4 | `out/raw/custom_indices_verify_<page>.json`（仅写后） | 同步骤 3 | 同步骤 3 | `rows` | 100 | **写后回读**：agent 执行完写操作后重取，供 `--verify` 比对 |

取数纪律（详见 SELLFOX_CLIENT.md）：

- 单次调用 ≤1 页（历史测试常见 20 条/页（当前分页以契约为准），`page_size` 不被尊重）；
  触达 `max_rows` 必须保留 coverage 让脚本披露；
- `CONTRACT_VERSION_MISMATCH` → 重 search 一次、按新 schema 重建参数、重试一次；
- `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（失败时**未发业务请求**，安全）；
- 空 data（status=complete）= 无数据 → 落盘原样保留，分析层标「无数据」；
- 演示环境日期过滤不可信：落盘数据含日期字段且不在请求窗口内须标注。

### 官方目录的防御性解析（保留）

`get_custom_report_stat_index_list` 的返回结构**未经实测**（文档只给了请求参数）。
脚本读取落盘后按以下顺序**防御性解析** `data`：

- **list**：直接就是节点数组；
- **dict**：`data.rows` / `data.list` / `data.records` / `data.children` / `data.dataList`
  → 节点数组；否则递归找第一个「元素为 dict 且含 `mappedIndex` 或 `indexName`/`name`」的 list。

**全部失败则标注「⚠️ 官方目录结构未能解析，需人工核查」，不臆造节点**；官方目录只作
命名与口径的官方参照（见 `references/index-tree-baseline.md`），缺失/解析失败**不阻断**
差异比对（降级为「只比对自定义指标」）。

---

## 二、计算面流水线（脚本执行）

```bash
# agent 落盘完成后（取数面责任已尽），脚本零网络：

python scripts/run_governance.py                                  # 落盘检查 → 比对 → dry-run 提案（默认）
python scripts/run_governance.py --execute --approve "批准 1,3"    # 生成「执行清单」（不是写入！）
python scripts/run_governance.py --record out/exec_results.json --manifest out/execute_manifest_<ts>.json
python scripts/run_governance.py --verify --manifest out/execute_manifest_<ts>.json
```

- **落盘检查**：`out/raw/` 存在性、信封 status、空数据、演示指纹、coverage 截断，
  产出「数据就绪/降级」结论（等价于
  `python _shared/scripts/validate_raw.py --dir out/raw --step custom_indices_1:rows`）；
- **取数可信门**：步骤 3 任一页 failed/损坏，或发生截断 → **不生成提案**
  （「指标缺失」可能只是「未取到」，据此新增会在赛狐重复建指标）；
- **差异比对**：`desired_indices`（`name` 主键）join 现有指标——
  名称不存在→新增；公式不同（去空格/大小写后比较，**不做语义等价推断**）→更新
  （快照旧公式）；公式相同→已对齐；`formula_status=needs_human`→待确认（不进提案）；
- **提案**：`approval.Proposal`（L2、dry-run、单次 ≤20 条、超限分批），
  每项 `extra.contract_version = null` 占位（执行前重取写契约），
  落盘 `out/proposal_<ts>.html`（主报告，附同名 `.md` 留档）+ `.json`；
- **执行清单**：`--execute --approve "..."` 只生成 `out/execute_manifest_<ts>.json`
  （tool_id × 参数模板 × 旧公式快照 × `contract_version: null`），生成前过累计闸；
  演示环境默认阻断（`--allow-demo-write` 显式覆盖，仍加水印）。

---

## 三、写操作 agent 执行协议

**脚本永不直接调写工具**（v3 硬规则）。`--execute` 只产出执行清单；每一条的执行主体是
agent，按下述闭环逐项走（清单 `protocol` 字段内嵌了同样的步骤）：

```
批准（人工，parse_confirmation） → 重取写契约 → action → record → 回读 verify
```

1. **批准**：只执行被人工批准的条目（清单 `items[].index` 命中批准序号）；
   `unclear` 必须回到人工再问，**不得默认执行**。
2. **重取写契约**：`search(tool_ids=[tool_id], detail=true)` 取**当次** `contract_version`
   （写工具契约变更频率高于读工具，**每次执行前必重取**，禁止沿用旧版本或 null 占位）；
   并按当次 `inputSchema` 校正参数模板的类型/必填/枚举（未声明字段不传）。
3. **action**：`action(tool_id, params, contract_version)`。
   - `CONTRACT_VERSION_MISMATCH` → 重 search 一次、按新 schema 重建参数、重试一次
     （版本失配未发业务请求，安全）；只重试一次。
   - `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（未发业务请求，安全）。
   - **上游业务失败 / status=partial**：写操作一律按失败处理，**不重放**，进人工核查。
4. **record**：把每条执行结果写成 JSON 数组交给脚本追加变更日志
   （`ad_changes.jsonl`，累计闸与审计的依据）：

   ```json
   [{"index": 1, "status": "success", "contract_version": "2026-10-15T11:00:00Z"},
    {"index": 2, "status": "failed", "contract_version": "2026-10-15T11:00:00Z",
     "note": "上游返回失败，未重放"}]
   ```

   `python scripts/run_governance.py --record out/exec_results.json --manifest <清单>`
   `status` 只记 `success` / `failed` / `skipped`；实际使用的 `contract_version` 会写进
   日志（写契约可审计）。**success 只应在回读一致后记录**。
5. **回读 verify**：agent 重取步骤 4 落盘（`custom_indices_verify_<page>.json`，share_status=0），
   然后 `python scripts/run_governance.py --verify --manifest <清单>` 做公式回读比对：

   | 判定 | 处理 |
   |---|---|
   | 一致（match） | 该项方可记 success |
   | 不一致（mismatch） | 告警，**不重放**，列「需人工核查」，不得记 success |
   | 回读未找到（not_found，可能分页未覆盖） | 无法判定，需人工核查，不得记 success |
   | 回读字段名未知（unknown_field，响应字段名未经实测，脚本按 `indexFormula`/`formula`/`index_formula` 探测） | 需人工登录赛狐核对，**不得据此判定 success** |

6. **失败/回读一致前不重放**：任何写失败都不自动重试；需重试时重新走
   「提案 → 批准 → 重取契约」闭环，由人决定。

> 回滚：变更日志里每条都记录了 `before`（旧公式快照，即执行清单的
> `old_formula_snapshot`）；把公式改回旧值即回滚（`update_custom_report_index`）。

---

## 四、降级路径

| 缺失项 | 影响 | 降级做法 | 必须标注 |
|---|---|---|---|
| `out/raw/` 无步骤 3 落盘 | 无法比对 | 停止并提示按取数计划落盘（退出码 2） | — |
| 步骤 1/2（官方目录）缺失/失败/解析不到 | 基线参照缺失 | 仍比对自定义指标，只缺官方口径参照 | 「官方目录不可用/结构未能解析，需人工核查」 |
| 某页信封 `status=failed` | 取数不可信 | **不生成提案**（取数可信门），补齐后重跑 | 记录 error_code |
| 截断（coverage.truncated 或达 max_rows） | 覆盖不全 | **不生成提案** + 披露「仅覆盖 N 行」 | 「本次仅覆盖 N 行，其余未纳入比对」 |
| `report_id` 为空 | 无法按报表过滤 | 取数不传 `report_id_list`，治理全部「我的」指标 | 「口径降级：未指定报表」 |
| 某 data 为空 | 该项无数据 | 标「**无数据**」，不写 0，不重试 | — |
| 公式为 `needs_human` | 口径未确认 | 不进提案，列「待确认公式」清单 | — |
| 演示环境 | 数值不可信 | 默认只 dry-run，不出执行清单；可验证连通性与参数格式 | 顶部水印 |

---

## 五、截断与披露

- 截断真相在信封 `coverage{complete, fetched_count, total_count, truncated}`——
  **不信 `pagination.has_more`**（实测与 coverage 矛盾）；
- 脚本会把 `coverage.truncated=true` 的页与「合并行数达 max_rows」都转成披露语句；
- **禁止静默截断**——静默截断会被读成「已覆盖全部」，比不跑更危险；
  本技能里它还会进一步触发取数可信门（不生成提案）；
- 要覆盖更多：按 `report_id_list` / `type_list` 分批多次调用（每批 ≤20 行/页），
  逐页落盘，**不要**试图用 `page_size` 一次拿更多。
