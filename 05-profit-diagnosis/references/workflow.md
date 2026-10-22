# 取数与编排工作流（workflow.md，v3：agent 取数 + 脚本计算）

本文件是执行取数时的详细编排。SKILL.md 的 Step 0~2 是摘要，这里给出每一步的坑与正确写法。

> **v3 分工**：取数面 = agent 调客户端配置的 `mcp__Sellfox-MCP__{help,search,action}`，
> 按下方「取数计划」把每步**完整信封**落盘 `out/raw/`；计算面 = `fetch_profit.py`（校验+组装）
> → `diagnose.py` → `render.py`，全部零网络。
> 调用纪律（契约版本/参数类型/错误处理/并发）见 `_shared/SELLFOX_CLIENT.md`；
> 字段名见 `references/field-sources.md`。

## 目录

- [1. 前置校验：技能能不能跑](#1-前置校验技能能不能跑)
- [2. 宽表因子的固定分工（自定义报表不在取数范围）](#2-宽表因子的固定分工自定义报表不在取数范围)
- [3. 取数计划（agent 执行）](#3-取数计划agent-执行)
- [4. 环比窗口的选取](#4-环比窗口的选取)
- [5. 店铺分批与 20 条/页硬上限](#5-店铺分批与-20-条页硬上限)
- [6. 字段裁剪](#6-字段裁剪)
- [7. 维度字段的列表格式](#7-维度字段的列表格式)
- [8. 空数据与截断的披露](#8-空数据与截断的披露)
- [9. 币种](#9-币种)

---

## 1. 前置校验：技能能不能跑

动手取数前先确认三件事，否则跑出来的结论不可信：

1. **客户端 MCP 是否就绪。** 会话内能调 `mcp__Sellfox-MCP__{help,search,action}`
   （Key 只在客户端 MCP 配置里，脚本不需要 `SELLFOX_MCP_KEY`）。若数据源为演示/沙箱环境，
   数值可能内部不自洽（沙箱曾实测 `quantity` 与 `totalInventory` 矛盾），
   仅可用于验证字段名与分页，**不可用于验证任何利润口径**。命中演示指纹
   （`MOCK-` / `demo.example.com` / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」）就在输出顶部
   加水印。
   快速自检：`python scripts/run_profit_diagnosis.py --check-only`。
2. **利润口径是否确认。** `get_profit_report_by_msku` / `_shop` 都有 `profit_caliber_type`：
   `settlement`（结算口径，赛狐默认）vs `shipment`（发货口径）。**同一时间段两者结果不同。**
   agent 每次调用都显式传该参数（枚举只传契约声明的值），并把口径写进报告首部
   （不标注必然与财务对不上账）。默认读 `config/diagnosis.yaml → caliber.default`，
   可用 `--caliber` 覆盖。
3. **阈值配置是否存在。** 读 `_shared/config/thresholds.yaml → profit`（毛利率下限、毛利环比跌幅）。
   缺失项不得静默用内置值，按 `config_loader.ensure_params` 的批量询问流程补全，并在输出标注来源。

## 2. 宽表因子的固定分工（自定义报表不在取数范围）

自定义报表（`get_custom_report_page_list`，489 字段）已不在本技能取数与归因范围。
`purchaseCost` / `headTripPrice` / `reimbursementAmount` / `warehouseLostAmount` /
`missingFromInboundAmount` / `deferredAmount` 这几类**只存在于自定义报表**的因子（实测确认，
msku 报表拿不到）由 `custom-report-analysis` 技能承接：

| 因子类别 | 字段 | 承接方 |
|---|---|---|
| 采购成本上涨 | `purchaseCost` | custom-report-analysis |
| 头程运费上涨 | `headTripPrice` | custom-report-analysis |
| 赔偿/仓损**金额** | `reimbursementAmount` / `warehouseLostAmount` / `missingFromInboundAmount` | custom-report-analysis |
| 延期结算**金额** | `deferredAmount` | custom-report-analysis |

> ⚠️ 绝不可把上面这些字段名套到 `get_profit_report_by_msku` 上 —— 该工具不返回它们，
> 套了只会全部落空、把有数据的行误判成「无数据」。
>
> msku 报表侧**有**对应的汇总费用字段可参考（`purchasePcFee` / `headTripPcFee` / `compensationFee`），
> 但它们是**汇总口径**，与宽表明细口径不同，不可互相替代。
>
> 赔偿**条数级**核对（是否漏领）仍属本技能：`get_amazon_reimbursement_report`（非自定义报表）。

## 3. 取数计划（agent 执行）

按顺序取数；已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再 `action`。
参数类型以当次契约为准（⚠️ 标注处为实测差异）。各步骤**可并行发起**
（新网关同 session 并发安全），但每步都要落盘并核对 status。
为做环比，**MSKU 主源取两次**（本期 + 基期）——文件名按窗口区分，绝不能互相覆盖。

| # | 落盘文件 | 工具 | 关键参数（类型注意） | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/profit_msku_current.json` | `get_profit_report_by_msku` | `date_type=2`；`start_date`/`end_date`=**本期窗口**（`yyyyMMdd`）；`profit_caliber_type` **显式**；`page_no`/`page_size` 为 **integer** | **`reportList`** | 200 | 主源（本期）：171 字段财务深度 |
| 2 | `out/raw/profit_msku_base.json` | 同上 | 同上，但日期=**基期窗口** | `reportList` | 200 | 主源（基期） |
| 3 | `out/raw/profit_shop_current.json` | `get_profit_report_by_shop` | 同步骤 1（`yyyyMMdd` + `date_type=2` + `profit_caliber_type`；分页 **integer**） | `reportList` | 60 | 背景源：判断整体恶化 vs 个别 MSKU（本期） |
| 4 | `out/raw/profit_shop_base.json` | 同上 | 同上，但日期=基期 | `reportList` | 60 | 背景源（基期） |
| 5 | `out/raw/reimbursement.json` | `get_amazon_reimbursement_report` | `start_time`=基期起点、`end_time`=本期终点（**`yyyy-MM-dd HH:mm:ss`**，按契约；范围 ≤1 年） | `rows` | 100 | 交叉核对赔偿漏领。**可能返回 `data:{}`** → 落盘原样保留，标「无数据」，不重试 |

**子代理取数（优先尝试）**：默认先把上表连同调用纪律（`_shared/SELLFOX_CLIENT.md` §9）交给子代理执行——原始信封只进子代理上下文，主代理仅验收步骤摘要并抽验落盘；客户端不支持/派发失败/摘要不合规时回退主代理直取（兜底路径，行为一致）。

> 历史落盘中的 `custom_current.json` / `custom_base.json` 已不在取数计划内；
> `fetch_profit` 会忽略它们并在覆盖语中披露「宽表因子由 custom-report-analysis 承接」。

**落盘纪律**（详见 SELLFOX_CLIENT.md §6）：

- 每步 action 的**完整信封**（status/data/coverage/pagination/warnings/trace_id）原样写盘；
- **不在对话中复述行级数据**——数据只落盘，分析交给脚本；
- 单次调用 ≤1 页（多数工具服务端强制 20 条/页，`page_size` 不被尊重）；
- `CONTRACT_VERSION_MISMATCH` → 重 search 一次、按新 schema 重建参数（注意 `reportList`
  列表字段与 integer 分页）、重试一次，不重放原请求；
- `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（失败时**未发业务请求**，安全）；
- 空 data（status=complete）= 无数据 → 落盘原样保留，分析层标「无数据」；
- 翻页前先看 `totalSize` 是否值得继续，触达 max_rows 必须保留 coverage 让脚本披露；
- 按店铺分批时同一窗口可落盘 `profit_msku_current.1.json` / `.2.json` 等，
  `fetch_profit` 会自动合并（或直接合并进一个文件）。

**窗口日期由脚本口径决定**：`fetch_profit.resolve_windows()` 保证本期/基期等长、紧邻、不重叠，
并在超回看上限时把两窗口一并截短（见 §4）。取数前先跑
`python scripts/run_profit_diagnosis.py --check-only`（或 `resolve_windows`）拿到本期/基期起止日，
再按上表传参。

## 4. 环比窗口的选取

诊断的核心是「本期 vs 基期」的差异，窗口选取直接决定结论：

- **基期必须是紧邻本期的等长窗口且不重叠** —— 否则环比会被同期数据自身污染。
  `fetch_profit.resolve_windows()` 默认取 `prev_period`（上一等长窗口），可用 `compare.baseline_mode` 调整。
  返回值为 `(cur_start, cur_end, base_start, base_end, warnings)`；`warnings` 必须并入报告的
  `fetch_meta.warnings`（`assemble_all(..., window_warnings=...)`），不得丢弃。
- **利润报表按日查询限制最近 6 个月**（见 01 接口清单）。`resolve_windows` 保证「本期 + 基期」
  都落在 `max_lookback_days` 之内：本期窗口最多取 `max_lookback_days // 2` 天。
  `--period-days` 超限时，**把本期与基期一并截短为同一天数**（仍是紧邻的等长窗口），并返回一条
  告警写入报告「数据说明」。
  > ⚠️ 绝不可只夹取基期起点 —— 那会产出「起点晚于终点」或「两窗口不等长」的非法基期，
  > 环比方向都可能反掉。夹取必须保持等长，且**必须披露**（`resolve_windows` 内建 `base_start<=base_end`
  > 与等长断言，违反即抛错，不静默）。
- 本期窗口：`--end` 减去 `period_days`；基期：紧邻本期之前一个等长窗口。

## 5. 店铺分批与 20 条/页硬上限

服务端分页**硬上限恒为 20 条/页**，请求更大值被**静默降级**为 20，不报错；请求更小值还可能被
**放大**到 20（`get_order_page_list` 实测请求 3 条返回 20 条）。所以：

- ❌ 不能靠 `page_size` 控量。
- ✅ 遵守取数计划的 `max_rows` 安全阀：单次 ≤1 页，翻页前看 `totalSize` 值不值得继续；
  触达上限保留 coverage，由 `fetch_profit` 转成披露语句。
- ✅ agent 按 `shop_ids` 分批（`config/diagnosis.yaml → shop_batch_size`，默认每批 3 个店铺），
  每批独立落盘（`<step>.1.json` / `<step>.2.json`），`fetch_profit` 合并消费。
  > ⚠️ **空 `shop_ids` = 全部店铺，不是「不取数」。** 不传该参数即全量查询（服务端默认）。
  > 若因此跳过取数，会得到一份「成功但空」的报告，用户误以为当期真没异常。
  > 计算面对此有双保险：`assemble_all` 对空维度追加「按全部店铺查询」覆盖语；
  > 一个落盘信封都没消费（`mcp_calls=0`）时另行告警并在渲染层显式警示。
- ✅ 截断发生必须披露：`fetch_profit._cov_line()` 把信封 `coverage.truncated` 转成
  「本次仅覆盖 Top N，其余未纳入」语句，汇总进报告的「取数覆盖范围」。

## 6. 字段裁剪

主源是 **171 字段**的宽表，全量塞进上下文会撑爆。
agent 落盘的是完整信封（行字段原样），`fetch_profit.py` 的 `MSKU_FIELDS`
把需要字段归一化成扁平记录，其余丢弃。**新增字段时改这张映射表**，不要在下游到处 `.get()`。

> 💡 相比「全字段搬运」，**归一化同时完成了归类** —— 下游归因只认规范名，
> 「字段→工具」的归属固化在 `fetch_profit.FIELD_SOURCES`（全部指向 msku 报表）。

## 7. 维度字段的列表格式

⚠️ 最容易踩的隐性坑：宽表的**维度字段是列表**，不是字符串。

| 字段 | 实测样例 |
|---|---|
| `msku` / `sku` / `spu` | `["MSKU-DEMO-001"]` |
| `asin` / `asinList`（线上契约实测） | `["B0DEMO1001"]` —— 线上为**纯字符串数组**；亦兼容 `[{"key": "B0DEMO0001"}]` 形态 |
| `parentAsin` / `fnsku` / `shopId` / `marketplaceId` / `title` | `[{"key": "B0DEMO0001"}]` |

直接把 `msku` 当字符串用会得到 `"['MSKU-DEMO-001']"` 或报错。
统一走 `fetch_profit.first_scalar()` / `pick()` 归一化（列表 → 取第一个非空；dict → 取 `key`/`value`/`name`，
纯字符串数组 → 原样取标量）——两种形态都被归一化吸收，无功能差异。

## 8. 空数据与截断的披露

- `data: {}` / `data: null` / 空列表 → **「无数据」**，不是失败；`sellfox_offline.is_no_data()` 可判定；
  **不得触发重试**（无数据重试只是浪费一次调用）。
- `status=failed` → 该源标「⚠️ 需人工核查」，信封原样落盘，`fetch_profit` 记 warning、不消费其行。
- `status=partial` → 可采信但必须在报告「数据说明」披露。
- 截断 → 必须在「数据说明」里列出每条覆盖语句与 `max_rows` 上限。**禁止静默截断**——
  静默截断会被读成「已覆盖全部」，比不跑更危险。
- **不信 `pagination.has_more`**（实测与 coverage 矛盾）；覆盖判断只信
  `coverage{complete, fetched_count, total_count, truncated}` 与 `totalSize`。

## 9. 币种

- 利润报表的 `currency` 是**报表币种**（可配置，默认 USD）；`get_profit_report_by_*`
  传 `currency` 参数可按指定币种折算。
- `get_online_product_page_list.inventoryManage.currency` 是 **CNY**（成本口径），顶层 `currency` 是 **USD**。
- 利润报表自带 `rate` / `currencyList` 字段（汇率）。**本技能不做跨币种合并** ——
  售价类与成本类字段不同时相减。若两期 `rate` 发生变化，在报告「数据说明 / 交叉核对」中提示，
  标注「跨币种结论受汇率变动影响，需人工核查」。
- 确需折算时，按 `config/diagnosis.yaml → currency.fx_rate` 折算并标注**汇率来源与日期**；
  未配置则标「⚠️ 需人工核查汇率」，绝不编造汇率。

---

## 附：降级路径速查

| 缺失项 | 影响 | 降级做法 | 必须标注 |
|---|---|---|---|
| 宽表因子（采购成本/头程/赔偿金额/延期结算） | 不在本技能范围 | 固定由 `custom-report-analysis` 技能承接 | 报告「利润口径」区固定披露口径分工 |
| `out/raw/` 为空 | 无法组装 | 停止并提示按 §3 取数计划落盘 | — |
| 主源某窗口落盘缺失 | 该窗口无 MSKU 记录 | 照常组装，相关归因标「⚠️ 需人工核查」 | 数据说明记录缺失文件名 |
| 某步骤信封 `status=failed` | 该源缺失 | 记 warning、不消费其行，其余照常 | 数据说明记录 error_code |
| 背景/交叉源缺失（步骤 3~5） | 背景判断 / 赔偿核对降级 | 跳过，记录 warnings | 「店铺级利润缺失」「赔偿漏领核对不可用」 |
| `reimbursement` 返回 `data:{}` | 赔偿无数据 | 标「无数据」，不写 0，不重试 | — |
| 本期天数超 `max_lookback_days` 上限 | 窗口被截短 | 本期与基期**一并等长截短**（`resolve_windows`） | 「窗口夹取」告警写入数据说明 |
