# 自定义报表分析流程

按序执行：发现 → 验证读 → 正式取数 → 计划 → 脚本与报告。每步落盘完整信封到本次任务目录 `out/raw/`（本地忽略目录，不进 GitHub，不含密钥）。

## 1. 发现候选报表

1. 有当前账号已验证的报表选择则复用并核对本次读取范围；没有选择时自动发现，不让商家手填 report_id、不猜 ID、不先新建报表。
2. 获取 `get_custom_report_index_page_list` 当前契约（已知 ID 缺契约先 search，detail=true）并查询。按契约分页读取自定义指标列表（线上形态约 20 指标页量级，按当次契约翻完），从各行 `data.rows[].reportList[]` 提取真实 `reportId` 与 `reportName`，按 ID 去重。
3. `indexId` 是指标标识，`reportId` 才是报表标识，不可互换；关联清单不是完整目录——未关联自定义指标的报表可能不出现，不宣称「全部报表」或「没有报表」。部分页失败保留成功页候选及警告。
4. 按用户指定名称、用途、范围筛选；同名多 ID 或多个合理候选时只让商家选报表名称，不取第一条。仅凭排名第一、仅一条关联或名字带"利润"不足以选定。

## 2. 候选验证读

1. 匹配唯一候选后，先用 `get_custom_report_page_list` 以 page_size=1 小页验证：报表当前可读、行结构（rows_path，如 data.rows）、字段形态（fieldsMap 键名）、日期与币种字段是否满足本次问题。
2. 验证读失败或权限被拒：保留诊断（错误码、消息、retryable、trace_id），换下一候选或回到发现步骤；全部候选不可读时如实披露并按内置域（analytics/finance 等）降级，不编造报表数据。
3. 验证读通过才进入正式取数；字段与用途核对结果随计划保留。

## 3. 正式取数

1. 日期窗口按报表自身配置与用户问题确定（report_id 对应报表的统计维度），不把默认窗口强加给报表；本期/基期用两个等长窗口分别取数。
2. 按当次契约分页取全，单页 complete 不证明全量；max_rows/体积超限按「错误处置」三步走。逐页落盘 `out/raw/`，失败页也纳入输入清单，不静默丢弃。
3. 只选本次问题需要的字段；请求字段越多体积越大，超预算时优先裁剪非必要字段。

## 4. 处理计划（plan.json）

助手生成 plan.json（文件路径相对计划所在目录），要点：

- `tool_id`: `get_custom_report_page_list`（与信封一致才可用）；
- `rows_path`/`row_mode`：按验证读看到的真实行结构写（如 data.rows），不猜；
- `metrics`：字段映射按当次契约与信封 fieldsMap 逐项核对（名称、层级、单位含义）后才写入；请求了但 fieldsMap 未返回的字段不得进 metrics；误配（如把计数字段当比率分子）会产出 status=ok 的错误数值而非报错，核对证据随计划保留；
- `group_by`/`dedupe_by`：显式分组键（店铺/商品/日期/币种等完整业务粒度）与去重键；
- `currency_field`：金额必须提供，币种自动入组、不做汇率转换；
- 窗口：有已验证的日期字段才填 `date_field` + 每 dataset `period`；两期对比加 `compare`；
- 币种策略：多币种分组呈现，不跨币种汇总。

## 5. 执行与报告

```bash
python scripts/run_report.py --plan <本次任务目录>/plan.json --out-dir <本次任务目录>/processed
```

run_report.py 复用完整包中 sellfox-business/scripts/process_data.py（import 调用，零重实现）。输出 result.json、report.html、plan.json 到新目录，拒绝覆盖已有结果。读取每个 dataset 的 status 与 pages；助手在报告之上补充结论、缺口与下一步。退出码语义见 [计算说明](calculation.md)。

## 错误处置

- **RESULT_BUDGET_EXCEEDED（上游业务码 40019，体积超限）三步**：① retryable=true 时按建议缩小 page_size 重试一次（同一页重新请求）；② 仍超则减少请求字段（只选已发布/必要字段）再试一次；③ 再失败即停止，保留诊断（HTTP 状态、业务码、retryable、建议动作、trace_id），披露缺口，不做无界重试。retryable=false 不自动重试。failed 页不当「无数据/未配置」，全部 dataset 不可用输出诊断且退出码 2。
- **CONTRACT_VERSION_MISMATCH**：重新 search 取当前契约（detail=true），按新契约重建参数后重试一次；仍失配则停止并披露，不套用旧契约。
- **failed/unknown 响应**：不进入计算、保留拒绝原因与 trace_id；不据此断言权限、限流或数据有误（上游根因未确认）。
- **字段映射核对不通过**：拒绝该映射并在计划中披露，改由助手重取或人工核对，不猜字段名跑通。

## MCP 与数据约束

使用客户端实际连接的 Sellfox-MCP help/search/action。未知工具先用完整业务问句 help；已知工具缺当前契约再 search（每次最多 5 个，detail=true），已有完整契约直接 action。help 的 limit 为 1–10，版本来自本次连接。店铺 ID 来自实际店铺列表；分页按当前契约继续。保留请求、response、trace_id、coverage 和 output_validation。金额按币种分组，不将省略值补零。本技能只读，不创建/修改报表与指标。

**子代理取数（可选）**：客户端支持子代理时，可把上表连同调用纪律（`_shared/SELLFOX_CLIENT.md` §9）交给子代理执行——原始信封只进子代理上下文，主代理仅验收步骤摘要并抽验落盘；不支持则主代理直取（默认，行为一致）。
