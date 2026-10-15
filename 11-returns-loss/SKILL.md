---
name: returns-loss
description: 分析 Amazon 商品退款、退货与相关费用的规模、变化和整改优先级。适用于退款率上涨、售后侵蚀利润和每周售后复盘；不替代评论原文分析或自动退款。
---

# 退款退货损失分析

先阅读 [工作流程](references/workflow.md)，按当前 MCP 契约取数并保留完整信封。由助手生成处理计划，使用 [计算说明](references/calculation.md) 和 `scripts/run_review.py` 输出可复核报告；商家无需编辑 JSON。

## 运行环境与路径

需要 Python 3.10+ 和批量安装包中的 sellfox-business；计算只用标准库。脚本兼容源码及批量安装目录，不联网，不读取密钥，不执行业务修改。缺组件时重新批量安装完整包。

执行示例（路径由助手替换）：

```bash
python scripts/run_review.py --plan <本次任务目录>/plan.json --out-dir <本次任务目录>/review
```

每次使用新的输出目录。读取 result.json 的各数据集状态，空数据不表示经营正常，退出码 2 表示无可用数据。report.md 是计算证据报告，助手还须补充有依据的结论与行动清单。

报告应包含范围、窗口、口径、主要发现、证据、缺口与下一步；不因请求“优化”就修改广告、价格、库存或退款。未知费用符号、币种、分组或日期须先核实。
