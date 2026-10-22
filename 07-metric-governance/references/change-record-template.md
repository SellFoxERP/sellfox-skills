# 变更记录与输出模板（change-record-template.md）

> 渲染最终输出时读本文件。**数据说明区块不可省略**，缺少它输出就失去可信度。

## 一、指标治理变更记录模板

```markdown
# 指标口径固化记录 | {日期}

{⚠️ 演示数据，非真实经营数据  ← 命中演示指纹时才出现，放最顶部}

## 执行摘要
- 模式：**dry-run（未写入）** / **已执行**
- 风险级别：L2 ｜ 提案条数：{n}（上限 20）
- 新增：{a} ｜ 更新：{u} ｜ 已对齐：{s} ｜ 待确认公式：{p} ｜ 失败：{f}

## 一、新增指标
| # | 指标名称 | 类型 | 公式 | 依据字段 | 结果 |
|---|---|---|---|---|---|
| 1 | ... | 货币 | ... | productSales, orderNum | ✅ 已写入并回读一致 |

## 二、更新指标（含变更前后）
| # | 指标 ID | 指标名称 | 变更前公式 | 变更后公式 | 结果 |
|---|---|---|---|---|---|
| 2 | 12345 | ... | old | new | ✅ / ❌ |

## 三、待确认公式（未写入）
| 指标名称 | 类型 | 依据字段 | 为什么不写入 |
|---|---|---|---|
| 客单价-公司口径 | 货币 | productSales, orderNum | index_formula 语法无官方文档，需人工在赛狐确认 |

## 四、已对齐（无需动作）
- {指标名}：现有公式与白皮书一致

## 五、覆盖范围披露
- 现有自定义指标：本次仅覆盖 {returned} 行（达 max_rows={requested} 上限，其余未纳入比对）← 有截断才出现
- 报表过滤：{report_id_list 或「未指定，口径降级」}

## 六、回读校验
| 指标 | 回读值 | 期望值 | 判定 |
|---|---|---|---|
| ... | ... | ... | 一致 / 不一致 / ⚠️ 回读字段名未知，需人工核查 |

---
### 数据说明
- 数据源：赛狐 ERP（get_custom_report_stat_index_list / get_custom_report_index_page_list）
- 口径白皮书：_shared/METRIC_FORMULAS.md（版本日期 {日期}）
- 利润口径：settlement（结算口径）｜ 币种：USD（货币型指标注释内已标注）
- 变更日志：{change_log_path}（每行一条 JSON，供累计闸与审计）

### ⚠️ 本技能未能覆盖（需人工核查）
- **index_formula 语法** — 官方无文档，公式须人工在赛狐确认；本技能不臆造
- **指标与报表的绑定** — add 接口无 report_id 参数，需在赛狐网页端完成
- **删除指标** — MCP 无删除工具
- **回读字段名** — get_custom_report_index_page_list 响应字段名未经实测
```

## 二、参数来源标注（来自 config_loader.ensure_params）

```
| 参数 | 取值 | 来源 |
|---|---|---|
| 行数安全阀 max_rows | 100 | 配置命中 |
| 单次提案上限 | 20 | 配置命中 |
| 目标报表 | daily_ops, profit | 配置命中 |
```

> **核心原则**：默认值必须显式标注，不能混进真实数据里。

## 三、变更日志格式（ad_changes.jsonl）

由 `approval.record_changes()` 写入，每行：

```json
{"tool": "add_custom_report_index", "target_type": "index", "target_id": "客单价-公司口径",
 "field": "index_formula", "before": null, "after": "...", "reason": "...",
 "action": "add_index", "risk": "L2", "extra": {...}, "index": 1,
 "operator": "张三", "status": "success", "ts": "2026-09-30T10:00:00"}
```

- `status` 只记 `success` / `failed` / `skipped`；**累计闸只统计 `success`**。
- 回滚：按记录的 `before` 值把公式改回即可（`update_custom_report_index`）。