# 输出模板（output-templates.md）

> 何时读：渲染诊断报告、dry-run 提案、ad_changes.jsonl 记录时。
> `scripts/optimize.py render_report()` 已按此模板生成，手写时也请保持一致。

## 目录

- [一、诊断报告模板](#一诊断报告模板)
- [二、dry-run 提案表（由 approval.Proposal 渲染）](#二dry-run-提案表由-approvalproposal-渲染)
- [三、ad_changes.jsonl 记录格式](#三ad_changesjsonl-记录格式)
- [四、渲染注意事项](#四渲染注意事项)

---

## 一、诊断报告模板

```
# 广告诊断与优化提案 | {start_date} ~ {end_date}

- 店铺：`{shop_id}` ｜ 绩效数据：缺失（或：下载任务报表（活动级）等来源中文）

## 一、核心结论
- P0（紧急）：N 项 ｜ P1（建议）：N 项 ｜ 可自动执行提案：N 条

## 二、诊断明细
| 级别 | 规则 | 对象 | 指标 | 结论/动作 |

## 三、dry-run 变更提案（未执行）
（见下方提案表；0 条变更时改为直说原因 + 下一步，不渲染空表）

## 四、数据说明（必读）
- 覆盖范围 / 截断披露 / 数据来源
- 参数来源表（每个参数的来源：配置命中 / 本次填写 / 默认值）

## 五、本技能做不到什么
- 明确列出能力边界
```

演示环境命中时，**报告第一行**必须是水印：

```
> ⚠️ **演示数据，非真实经营数据**（指纹：{fingerprint}）
```

**为什么「数据说明」不可省略**：截断不披露会被读成「已覆盖全部」，比不跑更危险；
参数来源不标注会让默认值混进真实数据里，无法判断结论置信度。

---

## 二、dry-run 提案表（由 approval.Proposal 渲染）

由 `_shared/scripts/approval.py` 的 `Proposal.render_markdown()` 生成，固定结构：

```
# {title}
- 技能：`ad-optimizer` ｜ 风险级别：**L2 中风险**
- 生成时间：… ｜ 变更条数：N（上限 20，幅度上限 ±30%）
- 模式：**dry-run（未执行）**

## 变更前后对比
| # | 工具 | 对象 | 字段 | 变更前 → 变更后 | 幅度 | 理由 |

## 回滚方案
所有变更均已在写前快照旧值（见「变更前」列）。如需回滚，按上表逐项改回「变更前」的值。

## 确认方式
回复「批准 1,3,5」执行指定项；回复「批准全部」执行全部；回复「驳回」则不执行。
```

> `#` 序号与执行时的「批准 1,3,5」一一对应；序号在 `build_change_items` 里分配，勿手工打乱。

**变更条数为 0 时**（2026-10-22 商家视角修复）：`optimize.py` 不渲染空的
「变更前后对比」表与回滚模板，改为醒目段直说原因与下一步——绩效缺失时为
「本次不产生变更提案——绩效数据缺失，无法判断投放好坏。下一步：先解决绩效文件获取
（见诊断明细 P2 项），再重跑」；技能/风险/模式与「确认方式」等审批说明保留。
诊断明细表规则名用中文（如 `insufficient_data` → 数据不足）。

---

## 三、ad_changes.jsonl 记录格式

每行一条 JSON（v3 由 agent 执行完一项后调 `python scripts/optimize.py record --change change.json` 写入）：

```json
{"tool":"edit_sp_campaign","target_type":"campaign","target_id":"C1","field":"state",
 "before":"enabled","after":"paused","reason":"近周期花费 120.50 但订单为 0",
 "action":"pause","risk":"L2","extra":{},"index":1,
 "operator":"张三","status":"success","ts":"2026-09-30T09:30:00",
 "contract_version":"<当次执行用的版本>","read_back":{"ok":true,"actual":"paused"}}
```

- `status` ∈ `success` / `failed` / `skipped`；**只有 `success` 参与累计闸**。
- `before` 即回滚值。
- **record 防线**：`status=success` 但 `read_back.ok=false` 会被强制改记 `failed`
  （回读不一致即失败，不更新本地状态）。
- 日志必须真实、及时写入，否则累计闸失效。

---

## 四、渲染注意事项

- 报告用中文祈使句，结论先行；管理层只看「核心结论」即可。
- 数值缺失写「无数据」，**不写 0**。
- 拿不到的字段写「⚠️ 需人工核查」。
- 币种：绩效金额的币种按报表口径；跨币种（如与库存成本 CNY 混算）必须显式换算并标注汇率来源与日期，
  **不得直接相减**。
- 「昨日」等业务日必须标注时区口径（多站点 US/EU/JP 边界不同）。