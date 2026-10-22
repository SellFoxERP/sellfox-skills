---
name: sellfox-business
description: 通过 Sellfox-MCP 完成跨境电商的日常业务查询、跨域分析及明确授权的操作。适用于店铺、销售、订单、产品、库存物流、利润结算、广告和统计指标查询需求，尤其是没有固定报告模板的临时问题或跨业务域任务。
---

# 赛狐通用业务助手

将商家问题映射到业务域，发现当前 MCP 能力，再取数、分析或执行已授权操作。可独立使用本技能，不依赖其他九个专项技能；数据计算使用随附 Python 3.10+ 标准库脚本。

## 使用入口

1. 首次执行时阅读 [MCP 调用流程](references/mcp-workflow.md)。按下表只加载当前任务需要的业务域文档，不一次加载全部 references。
2. 从用户上下文确定平台、对象、时间与预期结果；只补问影响执行的缺项。跨域问题按对象 ID 和数据依赖拆解，复用已确认的信息。
3. 按实时 help/search 契约调用 action。引用文档中的工具 ID 仅是导航，不含可直接执行的固定参数或版本。
4. 多页汇总、比率或期间对比时阅读 [Python 数据处理](references/data-processing.md)，由助手生成字段映射计划并运行 `scripts/process_data.py`，检查结果状态后回答。保留覆盖范围、数据缺口和警告；查询及建议不自动授权业务修改。

## 业务域索引

| 用户需求 | 阅读文档 | MCP domain |
|---|---|---|
| 店铺列表、平台/站点识别、业务查询所需店铺 ID | [店铺与基础信息](references/shops.md) | shops |
| 多平台销量、销售额、订单量、GMV、趋势对比 | [销售统计](references/analytics.md) | analytics |
| 订单列表/详情、在线 Listing、可售状态、在线库存 | [订单与在线商品](references/orders.md) | orders |
| 产品分析、标签商品、透明计划；上架素材事实依据、评分快照 | [产品与商品分析](references/products.md) | products |
| FBA 货件、AWD 库存、在途、头程分摊、装箱、物流商 | [库存与物流](references/logistics.md) | logistics |
| 利润、成本费用、赔偿、收款、各平台结算明细 | [财务与结算](references/finance.md) | finance |
| 广告结构、绩效报告、下载任务、竞价预算和否定投放 | [广告](references/advertising.md) | advertising |
| 统计指标目录、自定义公式增改（治理配合 metric-governance）；自定义报表分析转 custom-report-analysis | [统计指标与自定义公式](references/reports.md) | reports |

## 跨域路由

- “销量下降为什么”：先销售统计确定下降对象/窗口，再按证据选择订单、在线商品、库存物流或广告；缺少证据时给待验证原因，不遍历所有工具。
- “补货但利润不能太低”：在线商品与物流确定可用/在途，销售统计估算需求，财务确认同一商品口径；建议不自动创建采购或货件。
- “广告有效但不赚钱”：广告绩效与财务费用联查，保留归因窗口和粒度差异，避免广告费重复扣除。
- “评分下降/生成上架文案”：产品域说明评分历史、原文和产品事实的获取边界；不把聚合评分当评论主题证据。
- 需要固定日报、周月复盘或专门计算流水线时，可在已安装的专项技能中选择相应能力；本技能不要求用户先安装专项技能。

索引未覆盖的新需求，先按完整业务问句调用 help；只在当前连接确实返回支持能力时继续，不从工具命名猜测功能。

## 主题与决策路由

- 退款率、退货费用或售后侵蚀利润：已安装时使用 returns-loss；不把退款量与退货量相加。
- 自定义报表读取、按报表字段分组或报表期间对比：已安装时使用 custom-report-analysis；本技能不在通用域内做报表目录发现与宽表查询，未安装时按内置域降级并披露缺口。
- 新品上架后阶段表现及是否继续投入：使用 new-product-review；上线前内容仍用 listing-launch。
- 销量下降：结合产品分析与 listing-health 的专项排查；补货/货件跟进用 inventory-health；活动保本测算用 profit-diagnosis。
- 情景计算使用 `scripts/scenarios.py`，输入规范见 [数据处理](references/data-processing.md)；脚本不调用 MCP 或修改业务。
