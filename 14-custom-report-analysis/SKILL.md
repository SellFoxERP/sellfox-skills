---
name: custom-report-analysis
description: 分析商家在赛狐网页端配置的自定义报表（宽表）：自动发现候选报表、按问题选字段与分组、期间对比、多币种分组、Top/Bottom 排行，输出可复核的 HTML 报告。当用户提到"自定义报表""我的报表""宽表""报表字段""按报表看""报表对比"时使用本技能。只读分析，不创建/修改报表与指标（metric-governance 管）、不做利润归因（sales-profit-analysis 管）、不替代固定日报周报（daily-ops-report / weekly-review 管）。
---

# 自定义报表分析

先阅读 [工作流程](references/workflow.md)，按当前 MCP 契约发现并验证商家已有报表，保留完整信封。由助手生成处理计划，使用 [计算说明](references/calculation.md) 和 `scripts/run_report.py` 输出可复核报告；商家无需编辑 JSON，也无需手填 report_id。

## 流程概览

发现 → 验证读 → 计划 → 脚本 → 报告：

1. **发现**：`get_custom_report_index_page_list` 分页读取自定义指标，从各行 `reportList` 提取真实 reportId/reportName 候选（indexId 是指标标识，不能替代 reportId）。
2. **验证读**：候选先以 `get_custom_report_page_list` 小页（page_size=1）验证可读性与字段形态，再让商家确认名称。
3. **计划**：助手按当次契约与信封 fieldsMap 逐项核对字段映射，生成本次 plan.json。
4. **脚本**：`run_report.py` 复用完整包中 sellfox-business 的处理器执行（分币种、加权率、两期对比、去重）。
5. **报告**：report.html（主报告，唯一报告文件）+ result.json，助手在其证据之上补充结论、缺口与下一步。

## 三门要点（不过门不出数）

- **口径门**：统计口径与日期窗口按报表自身配置；本期与基期必须同报表同口径，跨口径比较整份作废；比率用 Σ分子/Σ分母，不平均行级百分比。
- **币种门**：金额按币种分组分别呈现，不跨币种相加，无汇率来源不折算；缺币种的金额不汇总。
- **字段门**：metrics 的每个字段键（field/numerator/denominator）必须与信封 fieldsMap 及契约 output_fields 核对一致后才写入计划；请求了但未返回的字段不得进 metrics，误配会产出 status=ok 的错误数值而非报错。

## 与相邻技能的分工

- 建/改报表与自定义指标、口径固化治理 → `metric-governance`；本技能只读。
- 利润归因、费用拆解、赚钱/亏损榜 → `sales-profit-analysis`；本技能不做归因链。
- 固定经营日报、周月复盘 → `daily-ops-report` / `weekly-review`；本技能是针对已有报表的即席分析。
- 未安装本技能时，通用助手（sellfox-business）按内置业务域降级并披露缺口，不代为重建宽表查询。

## 运行环境与路径

需要批量安装包中的 sellfox-business；计算只用标准库。计算脚本实测 Python 3.9 即可完整运行（无 3.10 专有语法；批量安装器仍按 3.10+ 要求分发，两者不冲突）。脚本兼容源码及批量安装目录，不联网，不读取密钥，不执行业务修改。缺组件时重新批量安装完整包。

执行示例（路径由助手替换）：

```bash
python scripts/run_report.py --plan <本次任务目录>/plan.json --out-dir <本次任务目录>/processed
```

每次使用新的输出目录。读取 result.json 的各数据集状态，空数据不表示经营正常，退出码 2 表示无可用数据。「无数据」存在两个出口：empty→退出码 0 与 unavailable→退出码 2 可能并存，以 result.json 各数据集状态为准，勿只看退出码。报告应包含范围、窗口、口径、主要发现、证据、缺口与下一步；不因请求"优化"就修改广告、价格、库存、报表或指标。未知字段含义、币种、分组或日期须先核实。
