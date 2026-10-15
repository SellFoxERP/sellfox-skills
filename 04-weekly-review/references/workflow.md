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
- [5. 可选：推送飞书](#5-可选推送飞书)
- [6. 输出后回访](#6-输出后回访)
- [7. 降级路径](#7-降级路径)
- [8. 截断与披露](#8-截断与披露)

---

## 0. 前置校验（不可跳过）

在取数前先确认四件事，缺一不可，否则报告口径不可信：

| 校验项 | 方法 | 缺失时 |
|---|---|---|
| 客户端 MCP 就绪 | 会话内能调 `mcp__Sellfox-MCP__*`（Key 只在客户端 MCP 配置里，脚本不需要 `SELLFOX_MCP_KEY`）；数据源是沙箱还是生产由客户端配置决定，技能运行时识别演示指纹 | 提示在客户端配置；**不要**伪造数据 |
| `_shared/` 可定位 | `python scripts/skill_paths.py` 能打印 `shared_root` | 修 `config/paths.yaml` 的 `shared_root` |
| `report_id` 已回填 | 读 `_shared/config/reports.yaml` 的 `daily_ops.id` | 走降级路径，并**显式标注口径降级**（见下） |
| 取数落盘就绪 | `out/raw/` 有本周期窗口文件（见取数计划） | 按「取数计划」由 agent 落盘后重跑脚本 |

**「演示环境」是运行期判定，不是文档里的一句提醒。** 指纹：`MOCK-` / `demo.example.com` /
`SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」。组装脚本会在 `meta.mock=True` 时于报告顶部
加水印，并默认阻断飞书写入；演示环境另有**日期过滤失效**的特征，数据说明须声明
「数值仅验证连通性与参数格式」。

**report_id 缺失的降级路径**（技能不崩，但必须诚实）：

- 流量（`sessions`）/ 广告（`acos`/`adClickRate`/`adConversionRateCpc`）/ 退款（`refundRate`）/
  可售天数（`availableDays`）等 **489 字段宽表指标不可用** → 取数计划第 7/8 步整步跳过；
- 仅保留店铺级利润（`get_profit_report_by_shop`）与 MSKU 利润（`get_profit_report_by_msku`）
  可得的 GMV / 销量 / 毛利 / 毛利率 / 广告花费；
- 报告「数据说明」区块必须写明「⚠️ 口径降级：未配置 custom report_id」；
- **首轮引导**：提示用户去赛狐网页端「自定义报表」创建报表、勾选
  `reports.yaml → daily_ops.fields_hint` 列出的字段，把 ID 回填后重跑。

---

## 1. 确认参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 周期 | `week` | `week` 周报 / `month` 月报 |
| 锚点日期 | 今天 | 决定「本期/上期」是哪一段，格式 `YYYY-MM-DD` |
| 利润口径 | `settlement` | 或 `shipment`；**必须标注**，见 `_shared/METRIC_FORMULAS.md` |
| 店铺范围 | 全部 | 可传 `--shop-ids`（取数时作 `shop_ids` 过滤参数） |
| 折算币种 | 原币种 | 跨站点聚合时建议指定（如 `USD`），并作为 `currency` 参数显式传入 |
| 输出目标 | 本地 Markdown | 是否推送飞书（`feishu.enabled`） |

时间窗口径由 `scripts/period.py` 固定：本期（最近一个**完整**自然周/月）vs 上期 vs
上月同期 vs 去年同期 vs 本月累计。**所有窗口日期以 `period.py` 输出为准，不要手算**，
否则趋势不可比。

---

## 2. 取数计划（agent 执行）

先固定窗口，再取数：

```bash
python scripts/period.py --period week --anchor 2026-10-12
# 输出各窗口 start/end 及两种日期格式：start_profit/end_profit（yyyyMMdd，利润报表用）
#                                start_iso/end_iso（yyyy-MM-dd，宽表用）
```

已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再 `action`。参数类型以当次契约为准
（⚠️ 标注处为实测差异）。多窗口/多步骤**可并行发起**（v3 网关同 session 并发安全，实测
10 并发 10/10 成功），但每步都要独立落盘并核对 status。

**多窗口文件命名约定**：`out/raw/<源>_<窗口标签>.json`。利润报表按窗口各落一盘：
`current`（本期）/ `prev`（上期）/ `prev_month`（上月同期）/ `prev_year`（去年同期，同比
开启时）/ `mtd`（本月累计）；宽表本期为 `custom_current.json`，历史窗口为 `custom_prev*.json`。

| # | 落盘文件 | 工具 | 关键参数（类型注意） | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/profit_shop_current.json` | `get_profit_report_by_shop` | `date_type=2`；`start/end=本期`（`yyyyMMdd`）；`profit_caliber_type` 显式（settlement/shipment）；`page_no/page_size` 为 **integer** | `reportList` | 40 | 本期核心指标、店铺明细；**缺失 → 脚本退出码 2** |
| 2 | `out/raw/profit_shop_prev.json` | 同上 | `start/end=上期`（`yyyyMMdd`） | `reportList` | 40 | 环比基线 |
| 3 | `out/raw/profit_shop_prev_month.json` | 同上 | `start/end=上月同期`（`yyyyMMdd`） | `reportList` | 40 | 对比上月同期 |
| 4 | `out/raw/profit_shop_prev_year.json` | 同上（同比开启时） | `start/end=去年同期`；⚠️ 超出「按日限最近 6 个月」的窗口可能被拒/夹取 → 按当次契约描述改**按月 `yyyyMM`**（该月整月口径，报告标注） | `reportList` | 40 | 同比基线；`compare.yoy=false` 时跳过 |
| 5 | `out/raw/profit_shop_mtd.json` | 同上 | `start=本月1号`、`end=anchor`（`yyyyMMdd`） | `reportList` | 40 | **达成率 basis（本月累计）**，缺失则达成率不可用 |
| 6 | `out/raw/profit_msku.json` | `get_profit_report_by_msku` | 类型规则同店铺利润报表（分页 **integer**、日期 `yyyyMMdd`、`profit_caliber_type` 显式）；`currency`（跨币种聚合时） | `reportList` | 20 | Top/Bottom 榜单源，**单文件双榜单**（组装层本地按销售额降序/毛利升序排）；行数需 ≥ top.n+bottom.n，不足则披露覆盖不足 |
| 7 | `out/raw/custom_current.json` | `get_custom_report_page_list` | `report_id`；`start/end=本期`（`yyyy-MM-dd`）；`product_dim=msku`；分页参数按当次契约 | `rows` | 80 | 489 字段宽表（本期）；**依赖 report_id，缺 → 跳过整步（降级）** |
| 8 | `out/raw/custom_prev*.json`（可选） | 同上 | 同上，历史窗口（`custom_prev.json` / `custom_prev_month.json` 等） | `rows` | 80 | 计算面当前只消费 `custom_current`；历史窗口备用 |
| 9 | `out/raw/trend.json`（可选，`trend.enabled` 时） | `get_product_analyze_detail`（先 `get_product_analyze_list` 取 `groupByStr`） | `dim_type=1`；本期窗口；`stat_time_type`（周报=1 按日 / 月报=4 按月） | — | — | 落盘 `data.charData` 所在信封；缺失时计算层自动降级为利润报表 `bizDay` 日粒度 |
| 10 | `out/raw/shops.json`（可选） | `get_shop_page_list` | `page_no/page_size` 为 **string** | `rows` | 40 | 本技能计算面不依赖；仅告警路由/展示备用 |

**落盘纪律**（详见 `_shared/SELLFOX_CLIENT.md` §7）：

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
  coverage 截断），组装出与旧版相同形状的 `weekly_data.json` —— compute/render/push 零改动；
  本期窗口缺失或失败 → 退出码 2，先补齐落盘再重跑；
- 也可单独跑共享校验器：
  `python _shared/scripts/validate_raw.py --dir out/raw --step profit_shop_current:reportList --step profit_msku:reportList --window 2026-10-05:2026-10-11`
  （窗口 = 本期 `start_iso:end_iso`，用于「演示环境日期过滤不可信」检测）。

---

## 4. 渲染

```bash
python scripts/render_report.py --in computed.json --out weekly_report.md
```

严格使用 `references/report-template.md` 的结构。**「数据说明」区块为必填**，缺失即视为报告不合格。
趋势图优先用 `get_product_analyze_detail` 返回的 `charData`；缺失时降级为利润报表 `bizDay`
日粒度序列，并标注「charData 缺失，降级」。

---

## 5. 可选：推送飞书

```bash
python scripts/push_feishu.py --markdown weekly_report.md --computed computed.json
```

- 默认 `feishu.enabled=false` → 不推送。
- 演示环境默认**阻断**；确需写入要显式 `--override-demo`，且文档顶部保留水印。
- 写入前过 **PII 脱敏**（`buyerEmail`/`buyerName`/买家地址），文末标注「已脱敏」。
- 飞书应用须先被**添加为文档协作者**，否则写入 403；创建文档限流 3 次/秒。

---

## 6. 输出后回访

1. 达成率口径是否符合管理层预期？（周目标是否要单独配置）
2. 阈值是否需按类目/阶段调整？（`thresholds.yaml`）
3. 是否需要设为每周/每月定时任务？
4. 若报告带演示水印（数据源为沙箱），结论不可用于业务决策——是否需要更换数据源配置？

---

## 7. 降级路径

| 缺失项 | 影响 | 降级做法 | 必须标注 |
|---|---|---|---|
| `out/raw/` 为空 | 无法组装 | `fetch_weekly_data.py` 直接退出并提示按取数计划落盘 | — |
| `profit_shop_current.json` 缺失/failed/损坏 | 本期核心指标不可用 | 脚本退出码 2；补齐落盘后重跑 | — |
| 某历史窗口文件缺失（prev/prev_month/prev_year/mtd） | 对应环比/同比列「不适用」、达成率不可用 | 继续组装，`sources` 标 degraded | 数据说明记录缺失步骤 |
| `daily_ops.report_id` 为空 | 宽表指标（流量/广告/退款/可售天数）不可用 | 取数计划第 7/8 步整步跳过 | 「口径降级：未配置 custom report_id」 |
| `custom_current.json` 缺失/失败 | 同上 | 宽表步骤标 degraded，其余照常 | 数据说明记录 |
| `profit_msku.json` 缺失/行数不足 | Top/Bottom 榜降级 | 榜单「无数据」或标「榜单覆盖不足」 | 数据说明记录 |
| `trend.json` 缺失 | 趋势图降级 | 利润报表 `bizDay` 日粒度序列 | 「charData 缺失，降级」 |
| 某步骤信封 `status=partial` | 覆盖不完整 | 可采信但随 `sources` 披露 | 数据说明记录 partial |
| 某 data 为空 | 该项无数据 | 标「**无数据**」，不写 0，不重试 | — |

---

## 8. 截断与披露

- 截断真相在信封 `coverage{complete, fetched_count, total_count, truncated}`——
  **不信 `pagination.has_more`**（实测与 coverage 矛盾）；
- `fetch_weekly_data.py` 会把 `coverage.truncated=true` / `status=partial` 的步骤转成披露
  语句写入 `sources`，渲染层落到「数据说明」区块；
- **禁止静默截断** —— 静默截断会被读成「已覆盖全部」，比不跑更危险；
- `profit_msku.json` 单文件双榜单：若文件被截断，Bottom（毛利升序）可能未覆盖真实最差项，
  披露语句会随榜单带出，结论仅供参考。
