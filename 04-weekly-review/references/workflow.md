# 周报 / 月报执行工作流（workflow.md，v3：agent 取数 + 脚本计算）

> 从 SKILL.md 引用。**当开始一次周报/月报生成任务时读本文件**，按顺序执行。
> **v3 分工**：取数面 = agent 调客户端配置的 `mcp__Sellfox-MCP__{help,search,action}`，
> 按下方「取数计划」把每步**完整信封**落盘 `out/raw/`（多窗口，文件名带窗口标签）；
> 计算面 = `fetch_weekly_data.py`（校验+组装）→ `compute_metrics.py` → `render_report.py`，
> 全部零网络。调用纪律（契约版本/参数类型/错误处理/并发）见 `_shared/SELLFOX_CLIENT.md`；
> 字段名见 `references/metrics-and-fields.md`。

## 目录

- [0. 前置校验（不可跳过）](#0-前置校验不可跳过)
- [1. 确认参数](#1-确认参数)
- [2. 取数计划（agent 执行）](#2-取数计划agent-执行)
- [3. 计算面流水线（脚本执行）](#3-计算面流水线脚本执行)
- [4. 渲染](#4-渲染)
- [5. 输出后回访](#5-输出后回访)
- [6. 降级路径](#6-降级路径)
- [7. 截断与披露](#7-截断与披露)

---

## 0. 前置校验（不可跳过）

在取数前先确认三件事，缺一不可，否则报告口径不可信：

| 校验项 | 方法 | 缺失时 |
|---|---|---|
| 客户端 MCP 就绪 | 会话内能调 `mcp__Sellfox-MCP__*`（Key 只在客户端 MCP 配置里，脚本不需要 `SELLFOX_MCP_KEY`）；数据源是沙箱还是生产由客户端配置决定，技能运行时识别演示指纹 | 提示在客户端配置；**不要**伪造数据 |
| `_shared/` 可定位 | `python scripts/skill_paths.py` 能打印 `shared_root` | 修 `config/paths.yaml` 的 `shared_root` |
| 取数落盘就绪 | `out/raw/` 有本周期窗口文件（见取数计划） | 按「取数计划」由 agent 落盘后重跑脚本 |

**「演示环境」是运行期判定，不是文档里的一句提醒。** 指纹：`MOCK-` / `demo.example.com` /
`SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」。组装脚本会在 `meta.mock=True` 时于报告顶部
加水印；演示环境另有**日期过滤失效**的特征，数据说明须声明
「数值仅验证连通性与参数格式」。

**宽表指标（固定口径）**：流量（`sessions`）/ ACOS / 广告点击率 / 转化率 / 可售天数 /
评分 / 差评数等 489 字段宽表指标不在本技能取数范围，由 `custom-report-analysis` 技能承接；
本技能只消费店铺级利润与 MSKU 利润报表及商品分析趋势。

---

## 1. 确认参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 周期 | `week` | `week` 周报 / `month` 月报 |
| 锚点日期 | 今天 | 决定「本期/上期」是哪一段，格式 `YYYY-MM-DD` |
| 利润口径 | `settlement` | 或 `shipment`；**必须标注**，见 `_shared/METRIC_FORMULAS.md` |
| 店铺范围 | 全部 | 可传 `--shop-ids`（取数时作 `shop_ids` 过滤参数） |
| 折算币种 | 原币种 | 跨站点聚合时建议指定（如 `USD`），并作为 `currency` 参数显式传入 |

时间窗口径由 `scripts/period.py` 固定：本期（最近一个**完整**自然周/月）vs 上期 vs
上月同期 vs 去年同期 vs 本月累计。**所有窗口日期以 `period.py` 输出为准，不要手算**，
否则趋势不可比。

---

## 2. 取数计划（agent 执行）

先固定窗口，再取数：

```bash
python scripts/period.py --period week --anchor 2026-10-12
# 输出各窗口 start/end 及两种日期格式：start_profit/end_profit（yyyyMMdd，利润报表用）
#                                start_iso/end_iso（yyyy-MM-dd，商品分析用）
```

已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再 `action`。参数类型以当次契约为准
（⚠️ 标注处为实测差异）。多窗口/多步骤**可并行发起**（v3 网关同 session 并发安全，实测
10 并发 10/10 成功），但每步都要独立落盘并核对 status。

**多窗口文件命名约定**：`out/raw/<源>_<窗口标签>.json`。利润报表按窗口各落一盘：
`current`（本期）/ `prev`（上期）/ `prev_month`（上月同期）/ `prev_year`（去年同期，同比
开启时）/ `mtd`（本月累计）。

| # | 落盘文件 | 工具 | 关键参数（类型注意） | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/profit_shop_current.json` | `get_profit_report_by_shop` | `date_type=2`；`start/end=本期`（`yyyyMMdd`）；`profit_caliber_type` 显式（settlement/shipment）；`page_no/page_size` 为 **integer** | `reportList` | 40 | 本期核心指标、店铺明细；**缺失/failed → 退出码 2（信号非中断，产物照写，报告可继续生成）** |
| 2 | `out/raw/profit_shop_prev.json` | 同上 | `start/end=上期`（`yyyyMMdd`） | `reportList` | 40 | 环比基线 |
| 3 | `out/raw/profit_shop_prev_month.json` | 同上 | `start/end=上月同期`（`yyyyMMdd`） | `reportList` | 40 | 对比上月同期 |
| 4 | `out/raw/profit_shop_prev_year.json` | 同上（同比开启时） | `start/end=去年同期`；⚠️ 超出「按日限最近 6 个月」的窗口可能被拒/夹取 → 按当次契约描述改**按月 `yyyyMM`**（该月整月口径，报告标注） | `reportList` | 40 | 同比基线；`compare.yoy=false` 时跳过 |
| 5 | `out/raw/profit_shop_mtd.json` | 同上 | `start=本月1号`、`end=anchor`（`yyyyMMdd`） | `reportList` | 40 | **达成率 basis（本月累计）**，缺失则达成率不可用 |
| 6 | `out/raw/profit_msku.json` | `get_profit_report_by_msku` | 类型规则同店铺利润报表（分页 **integer**、日期 `yyyyMMdd`、`profit_caliber_type` 显式）；`currency`（跨币种聚合时） | `reportList` | 20 | Top/Bottom 榜单源，**单文件双榜单**（组装层本地按销售额降序/毛利升序排）；行数需 ≥ top.n+bottom.n，不足则披露覆盖不足 |
| 7 | `out/raw/trend.json`（可选，`trend.enabled` 时） | `get_product_analyze_detail`（先 `get_product_analyze_list` 取 `groupByStr`） | `dim_type=1`；本期窗口；`stat_time_type`（周报=1 按日 / 月报=4 按月）；⚠️ `get_product_analyze_list` 契约顶层 `anyOf` 要求 **`fields` / `field_modules` 至少一项非空**（实测缺失即 `INVALID_PARAMS rule:anyOf`），如 `fields=["asin","title","sevenSaleNum","profitPrice","profitRate"]`；⚠️ 其 `start_date`/`end_date` 为 **`yyyy-MM-dd`**（当次契约 `anyOf[const "", format:date]`）——勿沿用利润报表 `yyyyMMdd` 惯例，实测 `yyyyMMdd` 首调即被拒（`field_errors=[{start_date,anyOf},{end_date,anyOf}]`） | — | — | 落盘 `data.charData` 所在信封；缺失时计算层自动降级为利润报表 `bizDay` 日粒度 |
| 8 | `out/raw/shops.json`（可选） | `get_shop_page_list` | `page_no/page_size` 为 **string** | `rows` | 40 | 本技能计算面不依赖；仅告警路由/展示备用 |

> 自定义报表（`get_custom_report_page_list`）已不在本技能取数计划内：宽表指标由
> `custom-report-analysis` 技能承接。历史落盘中的 `custom_current.json` / `custom_prev*.json`
> 会被组装层忽略并披露「未消费」。

**落盘纪律**（详见 `_shared/SELLFOX_CLIENT.md` §6）：

- 每步 action 的**完整信封**（status/data/coverage/pagination/warnings/trace_id）原样写盘，
  文件名按上方命名约定，多窗口不得混写一个文件；
- **不在对话中复述行级数据**——数据只落盘，分析交给脚本（上下文是稀缺资源）；
- `action` 必带当次 `search` 返回的 `contract_version`；失配（`CONTRACT_VERSION_MISMATCH`）
  → 重 search 一次、按新 schema 重建参数、重试一次，不重放原请求；
- `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（失败时**未发业务请求**，安全）；
- 单次调用 ≤1 页（多数工具服务端强制 20 条/页，`page_size` 不被尊重）；
- 空 data（status=complete）= 无数据 → 落盘原样保留，分析层标「无数据」，不写 0、不重试；
- 翻页前先看 `totalSize`/`total_count` 是否值得继续，触达 max_rows 必须保留 coverage
  让脚本披露；**不信 `pagination.has_more`**（实测与 coverage 矛盾）。

---

## 3. 计算面流水线（脚本执行）

```bash
# 取数落盘完成后（agent 责任已尽），跑计算面（全部零网络）：
python scripts/fetch_weekly_data.py --period week --anchor 2026-10-12 --out weekly_data.json
python scripts/compute_metrics.py  --in weekly_data.json --out computed.json
python scripts/render_report.py    --in computed.json --out weekly_report.md
python scripts/test_regression.py   # 改动计算层后先跑回归护栏
```

- `fetch_weekly_data.py`（v3 改造）：校验 `out/raw/`（存在性/status/空数据/演示指纹/
  coverage 截断），组装出与旧版相同形状的 `weekly_data.json` —— compute/render 零改动；
  本期窗口缺失或失败 → **退出码 2 = 信号（有步骤失败需注意），非中断**：中间产物
  `weekly_data.json` 照写，compute/render 可继续产出降级报告（本期列「⚠️ 需人工核查」，
  核心结论标注取数失败、异常判定不可用）；补齐落盘后重跑可获完整报告；
- 也可单独跑共享校验器：
  `python _shared/scripts/validate_raw.py --dir out/raw --step profit_shop_current:reportList --step profit_msku:reportList --window 2026-10-05:2026-10-11`
  （窗口 = 本期 `start_iso:end_iso`，用于「演示环境日期过滤不可信」检测）。

---

## 4. 渲染

```bash
python scripts/render_report.py --in computed.json --out weekly_report.md
```

报告输出 `weekly_report.html`（主报告，HTML 即报告；`--keep-md` 可附同名 .md 供调试与测试）。
严格使用 `references/report-template.md` 的结构。**「数据说明」区块为必填**，缺失即视为报告不合格。
趋势图优先用 `get_product_analyze_detail` 返回的 `charData`；缺失时降级为利润报表 `bizDay`
日粒度序列，并标注「charData 缺失，降级」。

---

## 5. 输出后回访

1. 达成率口径是否符合管理层预期？（周目标是否要单独配置）
2. 阈值是否需按类目/阶段调整？（`thresholds.yaml`）
3. 是否需要设为每周/每月定时任务？
4. 若报告带演示水印（数据源为沙箱），结论不可用于业务决策——是否需要更换数据源配置？

---

## 6. 降级路径

| 缺失项 | 影响 | 降级做法 | 必须标注 |
|---|---|---|---|
| `out/raw/` 为空 | 无法组装 | `fetch_weekly_data.py` 直接退出并提示按取数计划落盘 | — |
| `profit_shop_current.json` 缺失/failed/损坏 | 本期核心指标不可用 | 退出码 2 = **信号而非中断**：`weekly_data.json` 照写，compute/render 继续产出降级报告（本期列「⚠️ 需人工核查」、核心结论标注取数失败）；补齐后重跑获完整报告 | 数据说明记录失败步骤与 error_code |
| 某历史窗口文件缺失（prev/prev_month/prev_year/mtd） | 对应环比/同比列「不适用」、达成率不可用 | 继续组装，`sources` 标 degraded | 数据说明记录缺失步骤 |
| 宽表指标（sessions/ACOS 等） | 不在本技能范围 | 固定由 `custom-report-analysis` 技能承接；数据说明固定标注 | 「宽表指标由 custom-report-analysis 承接」 |
| `profit_msku.json` 缺失/行数不足 | Top/Bottom 榜降级 | 榜单「无数据」或标「榜单覆盖不足」 | 数据说明记录 |
| `trend.json` 缺失 | 趋势图降级 | 利润报表 `bizDay` 日粒度序列 | 「charData 缺失，降级」 |
| 某步骤信封 `status=partial` | 覆盖不完整 | 可采信但随 `sources` 披露 | 数据说明记录 partial |
| 某 data 为空 | 该项无数据 | 标「**无数据**」，不写 0，不重试 | — |

---

## 7. 截断与披露

- 截断真相在信封 `coverage{complete, fetched_count, total_count, truncated}`——
  **不信 `pagination.has_more`**（实测与 coverage 矛盾）；
- `fetch_weekly_data.py` 会把 `coverage.truncated=true` / `status=partial` 的步骤转成披露
  语句写入 `sources`，渲染层落到「数据说明」区块；
- **禁止静默截断** —— 静默截断会被读成「已覆盖全部」，比不跑更危险；
- `profit_msku.json` 单文件双榜单：若文件被截断，Bottom（毛利升序）可能未覆盖真实最差项，
  披露语句会随榜单带出，结论仅供参考。
