---
name: sales-profit-analysis
description: 销售利润分析（赛狐/SellFox ERP，只读）。即席回答利润类问题：按 MSKU/SKU/店铺/订单取数，算清毛利额、毛利率、ROI 的结构与商品/店铺 Top 榜，并把毛利变化归因到销售/成本/费用/退款/汇率五条子链，每条给出证据字段与建议动作。当用户提到"销售利润""毛利""毛利率""利润结构""利润率""哪些品赚钱/在亏钱""利润排行""钱被什么吃了""成本占比""费用拆解""店铺利润对比""ROI""利润变化归因""延期结算""结算未到账""广告费变化"时使用本技能，即使用户没明说"利润分析"。只算账与归因：不做财务对账、不报净利、不改价、不调广告、不下采购单；周月报与目标达成用 weekly-review，配置化异常诊断流水线用 profit-diagnosis。
---

# sales-profit-analysis · 销售利润分析

> v3 双平面：取数 = agent 调客户端 `mcp__Sellfox-MCP__{help,search,action}` 按
> [workflow.md](references/workflow.md) 的取数计划把完整信封落盘 `out/raw/`；
> 计算 = `scripts/run_analysis.py` → `scripts/render_report.py`，零网络。
> 铁律：**口径没锁不出数 · 比率不重算不出榜 · 十几行以上不口算 · 无源就不判。**

## 0. 执行卡（按需选路，不必全走）

约束分两类：**A 类不可协商**（接口事实与数学正确性——参数类型、口径词、比率不可加、
符号方向、字段名不许猜，违反就是错数）；**B 类是默认做法**（步骤、检查、输出形状——
**按问题裁剪**，有更合适的路径就走）。本技能的价值是判断准，不是格式齐。

```
时间维度默认=按天（date_type=2、yyyyMMdd、近 14 天 vs 前 14 天，相邻不重叠）；
用户点名"本月/上月"等月份时才切月窗口 yyyyMM。按需选路，不必全走：
  只问一个数 / 只要明细      → ①②③ 就够
  问"赚不赚钱、谁在亏、谁最大" → ①②③⑤（排名走产品分析）
  问"变化/为什么/被什么吃了"  → 全走 ①~⑦（含日序列，见下）
① 锁口径   profit_caliber_type（order 侧叫 caliber_type，发货词是 delivery）+ currency + 窗口
            窗口默认按天（近 14 天 vs 前 14 天）；点名月份才用 yyyyMM
            未确认 → 问一句，别拿默认空串硬跑
② 定店铺   get_shop_page_list（page_size=100，string，按 totalPage 翻完）→ shop_ids（integer 数组）
            status 枚举以当轮契约与实测为准（线上见过 active/inactive 与数字码 1·2 两种形态；
            授权失效的店铺别当"没生意"
③ 取窗口金额  profit_by_msku|_by_shop 聚合（date_type=2 + show_on_day=false）→ 1 行/窗口
④ 取日序列  show_on_day=true 分段取，每次 ≤7 天；变化归因类默认要做
⑤ 取排名   get_product_analyze_list + 顶层 fields/field_modules 投影（anyOf 至少一项非空，
            参数以当轮契约为准）；两期各一次，本地按 ASIN 对齐
⑥ 算账     scripts/run_analysis.py：代数和桥接 + 四条自检，残差如实报，不凑系数
⑦ 归因输出  scripts/render_report.py：六段内容，第一行必须是口径行；出口 out/report.html（主报告，--keep-md 才附同名 .md）
```

每步的参数类型、落盘路径、预算处置见 [workflow.md](references/workflow.md)。

## 1. 三门（不过门不出数）

**门 1 · 利润口径** `_by_msku/shop/sku` 用 `profit_caliber_type`（`""|settlement|shipment`），
`_by_order` 用 `caliber_type`（`""|settlement|delivery`）。默认 `""` 是**未选**；help 对本域返回
`needs_clarification` → 先澄清。本期与基期必须同口径，跨口径比较整份作废。

**门 2 · 币种不可加** 契约 `not_for` 原文：「不直接汇总不同币种或口径」。多站点**分开报**，
不折算相加。行内 `currency` 与 `rate`（转人民币）同时给，但金额侧原币/折算契约未说明——
确认前不要一会儿乘 `rate` 一会儿不乘。

**门 3 · 比率不可加** 毛利率、ROI 及所有 `*Rate` 占比字段禁止 SUM/AVG。整体率 = Σ分子÷Σ分母，
标【自算】。零分母行返回**哨兵值**（`grossProfitRate=0`、`roi=-1`、`profitRate="0E-7"`）→
零销售行一律剔除出率值计算与排名，单列「无销售，仅费用」。

三门细节、字段名、符号方向见 [field-map.md](references/field-map.md)。

## 2. 计算与归因

- **不许口算**：落盘 → `run_analysis.py`（内置四自检：同层不双计 / 残差 / 哨兵剔除 /
  比率自算）→ `render_report.py`。`--selfcheck` 可独立自检。
- **区间汇总常单独出不了结论**：带"变化/为什么"的问题默认查日序列四项（单日贷项、
  停投断点、费用集中入账、结算滞后），规则见 [attribution-rules.md](references/attribution-rules.md)。
- 五条子链（销售/成本/费用/退款/汇率）的根因表、证据字段与置信分级同在
  [attribution-rules.md](references/attribution-rules.md)；阈值与降级见
  [thresholds.md](references/thresholds.md)（数值在 `config/analysis.yaml`）。

## 3. 输出（内容齐就行，形状按问题挑；第一行仍是口径行）

六段结构（口径行/结论/结构/榜/归因/局限）与**反模式清单**见
[output-template.md](references/output-template.md)。要点：

1. 口径行不可省：换个口径就是另一个数，读者得能照它复现；
2. 结论先给方向（是升是降，别顺着用户的错误前提答）再给额与率；
3. 演示/样本数据（MOCK-/SHOP-DEMO-/「演示」指纹）→ 报告首部水印「不作为经营结论」；
4. 失败步骤的 error_code、未覆盖、残差与警告进数据说明，failed ≠ 无数据。

## 4. 边界与拒绝区

**MCP 无源，直接说明，不外借**：流量/会话/转化率/BuyBox/评分/评论 ｜ 库龄·可售·在途口径 ｜
退货原因文本 ｜ 供应商报价·竞品价 ｜ 成本配置开关 ｜ 净利（契约无字段）。

**拒绝区**：不做财务对账；不报净利；不跨币种/跨口径汇总；不改价、不调广告、不下采购单、
不建移除销毁单；不写自定义指标（写侧需另行授权）。

**与相邻技能的分工**：周期复盘固定报告 → `weekly-review`；配置窗口的异常诊断流水线
（含赔偿核对）→ `profit-diagnosis`；本技能 = 任意窗口的即席利润分析与排行。临时问一个数
也走本技能，不必起固定流水线。

## 运行环境与路径

需要 Python 3.10+ 与本仓库批量安装包（共享底座 `_shared/`：离线工具/配置加载）。
脚本零网络、不读密钥；`config/paths.yaml` 留空自动定位同级 `../_shared`。
首次运行缺参数时按共享规范批量询问并回写本地配置文件（local.yaml，不入库）。
