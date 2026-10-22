# 赛狐 MCP 调用与落盘契约

本文供执行技能的 AI 使用。MCP 由客户端管理连接，技能脚本不持有 Key，也不直连赛狐。
文中 `mcp__Sellfox-MCP__{help,search,action}` 为示意名称，客户端可能将名称规范化；按实际可用工具匹配。

## 1. 发现与契约

- **元工具命名以当前连接的 tools/list 为准**：三个元工具（发现候选 / 读契约 / 执行）在不同网关版本
  命名可能不同——实测见过 `help`/`search`/`action` 与 `sf_discover`/`sf_describe`/`sf_execute`
  两套形态，语义一一对应；本仓库文档统一以 `help/search/action` 指代，调用前按实际名称适配，
  不因改名当作连接故障。
- 不知道业务 `tool_id`：向 `help` 传完整业务问题，可用 domain/platform/operation 收窄。
- `selection_status=needs_clarification` 先澄清，`no_match` 不猜工具 ID。
- `help(include_schema=true)` 返回 `schema_included=true` 且完整契约时可直接 `action`。
- 已知 tool_id、缺当前契约时调用 `search(tool_ids=[...], detail=true)`，一次 1–5 个。
- search 部分返回 `CONTRACT_BUDGET_EXCEEDED` 时，仅对失败项缩小批次重取；已成功的契约继续使用。
- 已有当前完整契约不重复 search；`contract_version` 来自本次会话的 help/search，版本失配时重新获取。
- 阅读 `inputSchema`、`pagination`、`output_fields`、字段说明、`business_constraints` 后构造参数。
- 各技能 workflow 中的工具 ID 和参数是取数计划参考，不能覆盖当前契约。工具不可用时重新 help，不编造替代 ID。

## 2. 执行

`action(tool_id, params, contract_version)` 的 params 只含契约声明的字段。
参数类型、枚举、日期格式、币种和时区按当次契约，不从历史样例推断。店铺、商品等 ID 从查询结果取得或由用户提供。
财务口径（结算/发货）、日期范围、币种在取数前确认；默认配置只能作为待确认的起点。

写操作还须遵守 [APPROVAL.md](APPROVAL.md)：先出提案和影响，按用户批准的具体对象执行。
写入前确认契约仍适用于已批准的提案；新契约改变行为时重新审阅提案。

## 3. 返回状态与重试

| 状态 | 处理 |
|---|---|
| `complete` | 本次请求完成；仍需检查覆盖范围与警告，不能推断全量 |
| `partial` | 读取保留成功项并披露缺口；写入逐项核实，禁止重放整批 |
| `failed` | 该步骤不可作为正常业务数据；记录错误并按技能降级 |
| `unknown` 或缺状态 | 不确定，不进入正常计算；写入先核实实际结果，禁止自动重放 |

版本失配或参数错误：仅在明确未发业务请求时，按新契约或 field_errors 修正后重试一次。
其他只读失败按 retryable 判断是否重试一次；写入失败、部分成功或结果未知不自动重放。
空值、字段省略与空结果均不等于 0。没有数据不等于“没有经营异常”。

失败分层（实测补充）：`failed` 带 `error_code=UPSTREAM_BUSINESS_ERROR` 且 `upstream.stage=business`
表示已过网关本地校验、**被上游拒绝**（鉴权、授权范围、数据权限）——调用方改参数无用；
`retryable=false` 不再重试，保留 `trace_id` 交上游排查。能连上网关拿到工具目录不等于有业务权限，
判据只有 action 的返回。另有 `RESULT_BUDGET_EXCEEDED` 属**返回体积**问题而非参数问题
（`retryable=true`，提示缩小 page_size 或用投影字段）：先聚合、再换有投影的工具、再降页大小。

## 4. 分页与覆盖

分页类型、参数名、列表字段从当前契约读取。常见列表字段为 rows/reportList/itemList，但不得据此猜测。
使用 coverage、响应计数和分页标记交叉核对；发生矛盾时优先按可核验的分页计数判定——
实测利润类工具出现过 `coverage.complete=false` 与 `pagination.has_more=false` **同时成立**的矛盾，
此时以 `data.totalPage`/`pageNo` 翻完为准并在口径行披露矛盾信号；确实无法核验时标“覆盖未知”，
不能宣布全量。
历史测试曾出现请求 1 条返回 20 条，这是特定环境观察，不是所有工具的固定上限。
每次处理一页，按实际 next cursor/page 翻页，并遵守本技能 max_rows 安全阀。
达到上限时披露“仅覆盖前 N 条，其余未纳入”，只有实际按指标排序后才称 Top N。
目录 help 的分页不是业务数据分页。

## 5. 输出契约与数据质量

`output_validation` 只反映文档与返回值的差异，不代表业务口径认证。
出现 `documented_schema_mismatch` 时保留原值并记录字段差异；数值解析需明确支持对应类型，不能把 null/解析失败变成零。
报告保留 warnings，标注覆盖范围、单位、币种和口径。无法核实的指标明确标“需人工核查”。
字段字典是历史参考索引，不是全量字段表；字段不存在的结论须以当前契约和实际响应为依据。

## 6. 落盘约定

AI 每步读取后保存 action 的完整业务信封到 `<技能根目录>/out/raw/<step>.json`，文件名按该技能 workflow。
保留 status/data/coverage/pagination/warnings/period/currency/trace_id/output_validation 等全部字段。
MCP 包装层有 structuredContent 时取其业务信封；仅有 text 时解析其中 JSON；不要把整个 JSON-RPC 结果直接当作 data。
每次业务任务使用新的原始数据集合；避免把不同日期、店铺或旧任务文件混在同一目录。覆盖已有文件前先确认其属于本次任务，或先另存归档。
不同分页使用 workflow 支持的命名/合并形式，合并时保留每页信封，不能静默丢掉失败页或覆盖信息。
先运行共享 `scripts/validate_raw.py` 检查状态和列表结构，再运行技能计算脚本。
行级数据保留在本地，不在聊天中重复粘贴整份数据，不随代码上传。

## 7. 演示环境与外发

命中 MOCK-/SHOP-DEMO-/B0DEMO 等指纹时标记演示数据。未命中不代表生产认证；alpha 地址或服务警告也必须披露。
演示数据不用于证明财务口径正确。日期字段不在请求范围内时停止对应日期统计并说明问题。
默认本地输出；飞书、通知或外部素材服务需要用户要求。外发前脱敏，演示数据默认禁止外发和业务写入。
只读 MCP 与离线计算无业务写入；飞书/审批模块是可选网络功能，因此不宣称整个项目“零网络”。

## 8. 能力演示测试

用户明确要求测试技能、允许展示演示样本时，可以继续执行只读组装、计算和报告流程；在报告开头说明请求窗口与返回业务日期的差异，数值按“返回样本”展示。不要修改原始日期或金额来伪装匹配，也不要把相同样本的差值解释为实际经营变化。
不同币种仍须分别展示；缺失数据仍标缺失。没有绩效时展示广告结构，没有历史基线时展示评分快照，没有异常时也展示已读取的利润样本。真实业务写入不属于能力演示测试。
