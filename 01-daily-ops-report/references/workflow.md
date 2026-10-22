# 日报执行流程（workflow.md，v3：agent 取数 + 脚本计算）

> SKILL.md 执行本技能时读本文件。
> **v3 分工**：取数面 = agent 调客户端配置的 `mcp__Sellfox-MCP__{help,search,action}`，
> 按下方「取数计划」把每步**完整信封**落盘 `out/raw/`；计算面 = `fetch_data.py`（校验+组装）
> → `analyze.py` → `render_report.py`，全部零网络。
> 调用纪律（契约版本/参数类型/错误处理/并发）见 `_shared/SELLFOX_CLIENT.md`；
> 字段名见 `references/field-map.md`。

## 目录

- [零、前置校验](#零前置校验)
- [一、取数计划（agent 执行）](#一取数计划agent-执行)
- [二、计算面流水线（脚本执行）](#二计算面流水线脚本执行)
- [三、降级路径](#三降级路径)
- [四、截断与披露](#四截断与披露)

---

## 零、前置校验

跑数前检查（`scripts/run_daily_report.py --check-only` 已实现）：

1. **客户端 MCP 就绪**：会话内能调 `mcp__Sellfox-MCP__*`（Key 在客户端配置里，
   脚本不需要 `SELLFOX_MCP_KEY`）。数据源是沙箱还是生产由客户端配置决定，
   技能只做运行时指纹识别（见第 3 条），不预设环境。
2. **报表自动发现**：按共享 `ONBOARDING.md` 获取并验证 daily_ops 报表；缺少适用报表时继续内置查询并标注宽表字段缺口，不要求商家填写 ID。
3. **演示环境指纹**（`MOCK-` / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」等）。命中 →
   输出顶部加水印，**默认阻断写飞书**。

---

## 一、取数计划（agent 执行）

按顺序取数；已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再 `action`。
参数类型以当次契约为准（⚠️ 标注处为实测差异）。多店铺批次**可并行发起**
（新网关同 session 并发安全），但每步都要落盘并核对 status。

| # | 落盘文件 | 工具 | 关键参数（类型注意） | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/shops.json` | `get_shop_page_list` | `page_no/page_size` 为 **string** | `rows` | 40 | 店铺清单；可缓存 7 天（本地 `out/shops_cache.json`） |
| 2 | `out/raw/profit_shop.json` | `get_profit_report_by_shop` | `date_type=2`；`start/end=昨日`（`yyyyMMdd`）；`profit_caliber_type`；`page_no/page_size` 为 **integer** | `reportList` | 40 | 店铺级利润 → L1；**用户点名口径（结算=settlement / 发货=shipment）时取数传该值，并把同一值透传计算面 `--caliber`**（缺省 settlement 走配置） |
| 2b | `out/raw/profit_shop_range.json`（可选） | 同上 | `show_on_day=true`；近 30 天窗口 | `reportList` | 40×30 | 仅 `baseline=slice_30d` 时取 |
| 3 | `out/raw/msku_<shopId>.json`（每店铺批一个） | `get_custom_report_page_list` | `report_id`；`start=end=昨日`（`yyyy-MM-dd`）；`shop_ids=<单个>` | `rows` | 20/批 | 依赖 report_id；缺 → 跳过整步（降级） |
| 4 | `out/raw/orders.json` | `get_order_page_list` | `date_type=purchase`；`date_start/end` 当日 00:00:00~23:59:59 | `rows` | 40 | 仅异常下钻样本；**含 PII**，渲染前脱敏 |
| 5 | `out/raw/campaigns_<shopId>.json`（每店铺一个） | `get_sp_campaign_list` | `shop_id`（string）；游标 `nextToken`（60s 过期） | `itemList` | 200/店 | STEP 03 广告结构 |
| 6 | `out/raw/multiplatform_<metric>.json`（可选，默认关） | `get_multiplatform_sale_stats` | `type=<metric>`；`group_type=shop`；`stat_time_type=1`；`currency` **必填**（空串=原币种）；分页参数 **string** | `rows` | 200 | 非亚马逊平台；跨币种不得直接合计 |

**落盘纪律**（详见 SELLFOX_CLIENT.md §7）：

- 每步 action 的**完整信封**（status/data/coverage/pagination/warnings/trace_id）原样写盘；
- **不在对话中复述行级数据**——数据只落盘，分析交给脚本；
- 单次调用 ≤1 页（多数工具服务端强制 20 条/页，`page_size` 不被尊重）；
- `CONTRACT_VERSION_MISMATCH` → 重 search 一次、按新 schema 重建参数、重试一次；
- `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（失败时**未发业务请求**，安全）；
- 空 data（status=complete）= 无数据 → 落盘原样保留，分析层标「无数据」；
- 翻页前先看 `totalSize` 是否值得继续，触达 max_rows 必须保留 coverage 让脚本披露。
- **口径/范围覆盖参数**：用户点名利润口径（shipment/settlement）→ 取数参数与计算面
  `run_daily_report.py --caliber <口径>` 用同一值（缺省读 `config/daily_report.yaml`），
  报告口径行按实际口径标注；用户点名站点范围（如「仅美国站」）→ 取数传
  `marketplace_ids`，并传 `--marketplace US` 让计算面核验过滤是否生效
  （上游忽略过滤时报告会披露「站点过滤未生效」）。

### 基线的正确做法（不变）

- **方案 A（默认）**：直接用宽表现成窗口字段 `sevenSaleNum` / `thirtySaleNum`，
  日均 = 窗口值 ÷ 天数，**不为基线多发调用**。
- **方案 B（`baseline=slice_30d`）**：额外取步骤 2b，技能内按日切片。

### 计数 vs 样本（不变）

- 退款/退货/订单异常计数优先用宽表 `refundNum` / `returnSaleNum`；
- `get_order_page_list` 只作下钻样本，输出标注「抽样，非全量」。

---

## 二、计算面流水线（脚本执行）

```bash
# 取数落盘完成后（agent 责任已尽），一键跑计算面：
python scripts/run_daily_report.py --date 2026-09-29     # 校验+组装 → 分析 → 渲染
```

- `fetch_data.py`（v3 改造）：校验 `out/raw/`（存在性/status/空数据/演示指纹/coverage 截断），
  组装出与旧版相同形状的 `bundle.json` —— analyze/render 零改动；
- 也可单独跑共享校验器：
  `python _shared/scripts/validate_raw.py --dir out/raw --step shops:rows --step profit_shop:reportList --window 2026-09-29:2026-09-29`

---

## 三、降级路径

| 缺失项 | 影响 | 降级做法 | 必须标注 |
|---|---|---|---|
| `reports.daily_ops.id` 为空 | L2 宽表不可用 | 只用步骤 1/2/4/5；流量/广告点击/库龄分桶/补货建议不可用 | 「口径降级：缺少自定义报表」 |
| `out/raw/` 为空 | 无法组装 | 停止并提示按取数计划落盘 | — |
| 某步骤信封 `status=failed` | 该源缺失 | 该项标「⚠️ 需人工核查」，其余照常 | 数据说明记录 error_code |
| 步骤 6 失败/缺失 | 跨平台销量缺失 | 跳过，记录 `degradations` | 「跨平台销量缺失（已跳过）」 |
| 某 data 为空 | 该项无数据 | 标「**无数据**」，不写 0，不重试 | — |

---

## 四、截断与披露

- 截断真相在信封 `coverage{complete, fetched_count, total_count, truncated}`——
  **不信 `pagination.has_more`**（实测与 coverage 矛盾）；
- `fetch_data.py` 会把 `coverage.truncated=true` 的步骤转成披露语句写入 bundle，
  渲染层落到「取数覆盖范围」区块；
- **禁止静默截断** —— 静默截断会被读成「已覆盖全部」，比不跑更危险；
- 店铺批次每批 ≤20 行（与 20 条/页硬上限对齐）。
