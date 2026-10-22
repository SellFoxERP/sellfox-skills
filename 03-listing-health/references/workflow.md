# 取数流程与降级路径（workflow.md，v3：agent 取数 + 脚本计算）

> 何时读：**执行前必读**。定义取数计划、分页处理、字段裁剪、降级路径与披露义务。
> **v3 分工**：取数面 = agent 调客户端配置的 `mcp__Sellfox-MCP__{help,search,action}`，
> 按下方「取数计划」把每步**完整信封**落盘 `out/raw/`；计算面 = `fetch_listing_data.py`
> （校验+组装）→ `diagnose.py` → `render_report.py`，全部零网络，报告仅本地 HTML（不外发）。
>
> **v1.7.0 宽表边界**：本技能不再调用 `get_custom_report_page_list`（自定义报表宽表），
> 也不依赖 `report_id`——宽表字段分析由 `custom-report-analysis` 技能承接；
> 残留 `custom_report_*.json` 落盘一律忽略。
> 调用纪律（契约版本/参数类型/错误语义/并发）见 `_shared/SELLFOX_CLIENT.md`；
> 字段名见 `config/field-map.yaml`。

## 目录

- [0. 硬约束](#0-硬约束)
- [1. 取数计划（agent 执行）](#1-取数计划agent-执行)
- [2. 计算面流水线（脚本执行）](#2-计算面流水线脚本执行)
- [3. 降级路径](#3-降级路径)
- [4. 字段裁剪](#4-字段裁剪)
- [5. 输出披露清单](#5-输出披露清单)

---

## 0. 硬约束

1. **脚本零网络**：赛狐数据只经 agent 调 `mcp__Sellfox-MCP__{help,search,action}` 落盘取得；
   `fetch_listing_data.py` 只读 `out/raw/`（旧直连 `sellfox_client.py` 已废弃，不得再 import）。
2. **单次调用 ≤1 页 + max_rows 安全阀**：服务端分页硬上限 **20 条/页**，请求更大 `page_size`
   被静默降级为 20。触达 `max_rows` 必须保留信封 `coverage`（`truncated=true`），让脚本转成披露。
   **不信 `pagination.has_more`**（实测与 coverage 矛盾），覆盖判断只信 coverage 与 totalSize。
3. **先聚合后下钻**：全店铺全 ASIN 直取会非常慢。优先用现成窗口字段
   （`day7SaleNum`/`day30SaleNum`/`bsrRank`），只在必要时逐条下钻。

## 1. 取数计划（agent 执行）

已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再 `action`。参数类型/参数名以当次
inputSchema 为准（⚠️ 标注处为实测差异）。多状态/多店铺批次**可并行发起**
（新网关同 session 并发安全），但落盘文件名不冲突、每步单独核对 status。

| # | 落盘文件 | 工具 | 关键参数（类型注意） | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/online_<batch>.json`（batch=状态名或店铺 ID） | `get_online_product_page_list` | `online_status_list=[status]`（可选值 `active`/`inActive`/`delete`）；`shop_id_list`；分页参数类型**以当次契约为准** | `rows` | 200/批 | 主数据源（状态/价格/评分/BSR/销量/标签/库存）；按 listingId 去重合并 |
| 2 | `out/raw/product_analyze.json` | `get_product_analyze_list` | `start/end`（`yyyy-MM-dd`）；`dim_type=1`（asin 维度）；返回字段集取决于 `fields` 入参 | `rows` | 100 | 可选增强：类目名/BSR 明细/FBM 可用天数（fields 实测含 sessions/buyBoxPercent 等，可作宽表替代源） |
| 3 | `out/raw/labels.json` | `get_product_by_label` | `label_ids`；`group_type=asin`；页参数名 **`page_num`**（非 `page_no`） | `rows` | 100 | **仅配置了 `labels.required_groups` 时**取；未配置则不取 |

**子代理取数（可选）**：客户端支持子代理时，可把上表连同调用纪律（`_shared/SELLFOX_CLIENT.md` §9）交给子代理执行——原始信封只进子代理上下文，主代理仅验收步骤摘要并抽验落盘；不支持则主代理直取（默认，行为一致）。

> `custom_report_<label>.json`（`get_custom_report_page_list`，489 字段宽表）已不在取数计划
> ——宽表字段分析由 `custom-report-analysis` 技能承接；残留落盘一律忽略。

**落盘纪律**（详见 SELLFOX_CLIENT.md §6）：

- 每步 action 的**完整信封**（status/data/coverage/pagination/warnings/trace_id）原样写盘；
- **不在对话中复述行级数据**——数据只落盘，分析交给脚本（上下文是稀缺资源）；
- 单次调用 ≤1 页（多数工具服务端强制 20 条/页，`page_size` 不被尊重）；
- `CONTRACT_VERSION_MISMATCH` → 重 search 一次、按新 schema 重建参数、重试一次，不重放原请求；
- `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（失败时**未发业务请求**，安全）；
- 空 data（status=complete）= 无数据 → 落盘原样保留，组装层标「无数据」，不重试、不写 0；
- 翻页前先看 `totalSize` 是否值得继续，触达 max_rows 必须保留 coverage 让脚本披露。

### 分状态扫描（步骤 1 的批次策略）

`online_status_list` 可选值实测为 `active` / `inActive` / `delete`。默认按
`config/listing.yaml → online_status_scan` **分三批扫描**（一批一文件，如
`online_active.json`），组装层按 `listingId`（缺失时用 `shopId+asin`）去重合并。
这样做是为了**不依赖服务端默认过滤**，避免漏掉不可售/被删商品。店铺多时也可按店铺
分批（`online_<shopId>.json`），**并行发起**即可（v3 新网关同 session 并发安全）。

### ⚠️ 实测备注

- `get_product_analyze_list` 返回的字段集合**取决于 `fields`/`field_modules` 入参**，未全量实测；
  缺字段一律标「需人工核查」，不要假设它一定有。
- `get_product_by_label` 的页参数名是 **`page_num`** 不是 `page_no`。

## 2. 计算面流水线（脚本执行）

```bash
# 取数落盘完成后（agent 责任已尽），一键跑计算面（在技能根目录运行）：
python scripts/fetch_listing_data.py --shop-ids 10001 --out data.json   # 校验+组装
python scripts/diagnose.py --in data.json --out problems.json            # 分诊
python scripts/render_report.py --in problems.json --out report.md       # 渲染
# 报告输出 report.html（主报告，HTML 即报告；--keep-md 可附同名 .md）
# 也可单独跑共享校验器（⚠️ 运行前置：_shared 在仓库根/安装根，不在技能目录内，
# 须从仓库根运行并写清相对路径；批量安装布局下从安装根运行）：
python _shared/scripts/validate_raw.py --dir 03-listing-health/out/raw --step online_active:rows
```

`fetch_listing_data.py`（v3 改造）：校验 `out/raw/`（存在性/status/空数据/演示指纹/
coverage 截断）并组装 `data.json`；`coverage.truncated` 自动转披露；宽表步骤已移除，
固定披露「未接入自定义报表（custom-report-analysis 承接），宽表专属字段不可用」清单。

> **BSR/评分环比基线 = 本地快照自持**：每次成功组装后，组装层把当前各记录的
> `bsrRank`/`rating` 按记录 ID 全量重建落盘 `out/baseline-snapshot.json`（含
> `captured_at`；已消失的记录不保留，避免陈旧基线）。下次运行读入快照生成
> `meta.baseline` 供「BSR 恶化/评分下滑」环比。**首次运行无环比**（照旧标
> 「需历史快照」），**第二次起生效**。

## 3. 降级路径（v1.7.0：宽表为固定边界，非动态判定）

| 缺失/异常项 | 影响 | 降级做法 | 必须标注 |
|---|---|---|---|
| `out/raw/` 为空 | 无法组装 | 停止并提示按取数计划落盘 | — |
| `online_*.json` 全缺失 | 主数据源不可用 | 记 error，脚本退出码 2 | 「主数据源缺失，报告不可用」 |
| 某信封 `status=failed` | 该批缺失 | 记录 error_code，其余批次照常 | 数据说明列出失败项 |
| 未接入自定义报表（固定状态） | 宽表专属字段不可用 | 不消费任何 `custom_report_*.json`；显式列出不可用字段清单 | 「口径降级：未接入自定义报表（由 custom-report-analysis 承接）」 |
| `product_analyze.json` 缺失 | 可选增强缺失 | 跳过，不算失败（与旧版未开启一致） | — |
| `labels.json` 缺失（required_groups 已配置） | 标签覆盖校验降级 | 记 error | 「标签覆盖需人工核查」 |
| 某 data 为空（status=complete） | 该项无数据 | 标「**无数据**」，不写 0，不重试 | — |

检查项口径对照（宽表字段固定由 custom-report-analysis 承接）：

| 检查项 | 本技能口径（online 源） |
|---|---|
| 不可售/被抑制 | `onlineStatus`（online），不降级 |
| 丢购物车 | 退化用 `buyBoxWinner`（online 布尔）→ 标注「口径降级：仅有是否赢得购物车，无占比」 |
| 无流量 | **需人工核查**（sessions 为宽表字段；analyze 实测含会话字段时可作替代源） |
| 有流量无转化 | **需人工核查**（sessions/conversionRate 为宽表字段） |
| 评分下滑 | 仅 `rating` 环比（online，需历史快照）；差评数**需人工核查** |
| BSR 恶化 | 仅 `bsrRank` 名次变化（online）；类目名次**需人工核查** |
| 标签缺失 | `labels`/`productLabelList`（online），不降级 |
| 断货拖排名 | 退化用 `quantity`（online）；`availableDays` **需人工核查** |

降级为固定状态，报告顶部**必须**写：「口径降级：未接入自定义报表（由
custom-report-analysis 技能承接），以下检查项不可用 → 需人工核查：…」，并在每条对应
问题后标 `需人工核查`。宽表专属不可用字段清单：
`sessions` / `pageViews` / `conversionRate` / `buyBoxPercent` / `lowReviewNum` / `reviewNum` /
`bigCategoryRanking` / `smallCategoryRanking` / `availableDays`。

## 4. 字段裁剪

79 字段在线产品表会撑爆上下文。**agent 侧**只保留 `config/field-map.yaml` 中
`checks` 列出的字段；不要整行原始 JSON 塞进报告，也不在对话中复述行级数据。

## 5. 输出披露清单

报告顶部必须逐项声明：
- 覆盖范围（店铺数、扫描状态、是否触顶截断 → Top N 声明）
- 时间窗口与**时区口径**（多站点 US/EU/JP 边界不同，明确写「昨日/近 7 天」按哪个口径）
- 口径降级说明（宽表固定由 custom-report-analysis 承接）
- 演示环境水印（命中指纹时）
- 币种说明：`listingPrice` 顶层为 USD；`inventoryManage.currency` 为 CNY，若同表引用必须显式标注，不得直接相减。
