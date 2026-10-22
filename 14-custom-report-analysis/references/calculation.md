# 计算说明

计算复用完整技能包中 sellfox-business/scripts/process_data.py 的通用处理器（`scripts/run_report.py` 为薄入口，import 调用，零独立重实现），本技能不重写数值逻辑。

## 能力边界（处理器提供）

- **分币种**：金额按 currency_field 自动分组分别汇总；缺币种的金额不汇总，无汇率来源不折算。
- **加权率**：ratio 用合计分子/合计分母（currency_sensitive 涉金额时须 true），不平均行级百分比；零分母为缺失不输出 0%。
- **两期对比**：datasets 含 current/baseline 并加 compare 时计算差值与变化率，按完全一致的分组键比较；百分比差值用百分点，零基期/缺基期组为缺失。
- **去重**：dedupe_by 完整业务键去重；相同键不同值排除整个冲突键并要求核查。
- **数据质量**：failed/unknown 页不进入计算并保留拒绝原因；空值/布尔/NaN 不变零；日期越界行在 production 模式排除并保留排除计数；输出始终 coverage=not_certified。

计划由助手生成，文件路径相对计划所在目录；字段映射按当次契约与 fieldsMap 核对后才写入（见 [工作流程](workflow.md) 第 4 步）。

## 退出码语义

| 退出码 | 含义 |
|---|---|
| 0 | 处理流程完成（可能部分数据可用、partial 或 empty） |
| 1 | 计划/文件/运行错误（如字段映射缺失必填项、覆盖已有输出目录） |
| 2 | 所有 dataset 都不可用（如全部页 failed），已输出诊断报告 |

退出码 0 不等于业务成功；必须读取 result.json 各 dataset 的 status 与 pages。「无数据」两个出口并存：empty→退出码 0 与 unavailable→退出码 2，以 result.json 为准。脚本生成 result.json、report.html（主报告，唯一报告文件）、plan.json；拒绝覆盖已有目录。不将计算报告当作已验证的经营结论。
