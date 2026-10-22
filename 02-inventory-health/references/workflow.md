# 库存取数与计算工作流（workflow.md，v3：agent 取数 + 脚本计算）

> SKILL.md 执行本技能时读本文件。
> **v3 分工**：取数面 = agent 调客户端配置的 `mcp__Sellfox-MCP__{help,search,action}`，
> 按下方「取数计划」把每步**完整信封**落盘 `out/raw/`；计算面 = `fetch_inventory.py`
> （校验+组装）→ `replenishment.py` → `render.py`，全部零网络，报告仅本地 HTML（不外发）。
> 调用纪律（契约版本 / 参数类型 / 错误语义 / 并发 / 落盘约定）见 `_shared/SELLFOX_CLIENT.md`；
> 字段名见 `references/field-sources.md`。

## 目录

- [一、前置校验](#一前置校验)
- [二、取数计划（agent 执行）](#二取数计划agent-执行)
- [三、计算面流水线（脚本执行）](#三计算面流水线脚本执行)
- [四、降级路径（落盘三态）](#四降级路径落盘三态)
- [五、字段裁剪与维度字段格式](#五字段裁剪与维度字段格式)
- [六、空数据、截断与币种](#六空数据截断与币种)

---

## 一、前置校验

跑数前确认（`scripts/run_inventory_health.py --check-only` 已实现）：

1. **客户端 MCP 就绪**：会话内能调 `mcp__Sellfox-MCP__*`（Key 只在客户端 MCP 配置里，
   脚本不需要也不读 `SELLFOX_MCP_KEY`）。数据源是沙箱还是生产由客户端配置决定；
   技能对落盘数据做演示指纹运行时识别，不预设环境。
2. **`out/raw/` 已有取数落盘**：v3 取数面由 agent 完成，计算面脚本只读落盘文件（零网络）。
3. **阈值 / 到货周期配置**：`thresholds.yaml → inventory`、`lead_time.yaml` 缺失项不得
   静默用内置值，按 `config_loader.ensure_params` 的批量询问流程补全，并在输出标注来源。
4. **演示环境指纹**（`MOCK-` / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」等，组装层扫落盘
   识别）。命中 → 输出顶部加水印（演示数据仅加水印、不外发）。

> **宽表边界（v1.7.0）**：本技能**不取** `get_custom_report_page_list`（自定义报表宽表），
> 也不依赖 `report_id`——宽表字段分析（赛狐官方 availableDays / suggestNumSum /
> turnoverDaysMonth / 库龄官方分桶等）由 `custom-report-analysis` 技能承接。
> 残留 `msku_*.json` 落盘一律忽略（不消费、不采信）。

---

## 二、取数计划（agent 执行）

按顺序取数；已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再 `action`。
参数类型以当次契约为准（⚠️ 标注处为实测差异）。店铺批次**可并行发起**（v3 新网关同
session 并发安全，实测 10 并发 10/10 成功），但每步都要落盘并核对 status。

| # | 落盘文件 | 工具 | 参数类型注意 | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/shops.json` | `get_shop_page_list` | `page_no/page_size` 为 **string**（pattern `^[1-9][0-9]*$`） | `rows` | 40 | 店铺清单：为步骤 2 提供 shop_id 分批依据；可缓存 7 天 |
| 2 | `out/raw/online_products_<batch>.json` | `get_online_product_page_list` | `shop_id_list` 传数组（按当次契约）；`inventoryManage` 子对象随行返回 | `rows` | 200 | **主源**：在线产品真实字段（day7/day30SaleNum、inventoryManage）；单次 ≤1 页（服务端 20 条/页硬上限） |
| 3 | `out/raw/fba_shipments.json` | `get_fba_shipment_page_list` | `update_time_start/end` 至少一组完整区间（`yyyy-MM-dd HH:mm:ss`，≤360 天）；`shop_ids` 契约为 **integer 数组**——与店铺清单字符串 ID（`"AD-10001"`）不兼容，从在线产品行取数字 `shopId` 传入，取不到则省略（default `[]`） | `rows` | 100 | 在途货件明细；可能 `data:{}` → 无数据 |
| 4 | `out/raw/awd_inventory.json` | `get_awd_inventory_page_list` | `page_no/page_size` 必填 | `rows` | 100 | AWD 库存；可能为空 → 无数据 |

**子代理取数（优先尝试）**：默认先把上表连同调用纪律（`_shared/SELLFOX_CLIENT.md` §9）交给子代理执行——原始信封只进子代理上下文，主代理仅验收步骤摘要并抽验落盘；客户端不支持/派发失败/摘要不合规时回退主代理直取（兜底路径，行为一致）。

**落盘纪律**（详见 SELLFOX_CLIENT.md §6）：

- 每步 action 的**完整信封**（status/data/coverage/pagination/warnings/trace_id）原样写盘；
- **不在对话中复述行级数据** —— 数据只落盘，字段裁剪与归一化交给 `fetch_inventory.py`；
- 单次调用 ≤1 页（多数工具服务端强制 20 条/页，`page_size` 不被尊重）；翻页前先看
  `totalSize` 是否值得继续，触达 max_rows 必须保留 coverage 让脚本披露；
- `CONTRACT_VERSION_MISMATCH` → 重 search 一次、按新 schema 重建参数、重试一次；
- `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（失败时**未发业务请求**，安全）；
- 空 data（status=complete）= 无数据 → 落盘原样保留，组装层标「无数据」。

### 在线产品自算口径（固定口径，v1.7.0 起不再有宽表主源）

在线产品**没有** `availableDays` / `fbmAvailableDays` / `suggestNumSum`（仅属自定义报表与
产品分析，由 custom-report-analysis 承接），必须用在线产品真实字段自算：

| 用途 | 在线产品真实字段 | 自算口径 |
|---|---|---|
| 日均销量 v | `day30SaleNum` / `day7SaleNum` | `/30`、`/7` |
| 可用库存 | `inventoryManage.available`、顶层 `quantity` / `totalInventory` | 库存 ÷ v |
| 在途 | `inventoryManage.inboundWorking` / `inboundShipped` / `inboundReceiving` | 求和 |
| 库龄分桶 | `inventoryManage.invAge0to90Days` … `invAge365PlusDays` | 滞销侧可用；分母用 `totalInventory` 口径 |

⚠️ 绝不可把宽表字段（`salesVelocityMonth`/`fbaInventory`/`localAvailableQty`…）套到在线产品源上 ——
这些字段该工具不返回，套了只会全部落空、把可用行误判成「无数据」。缺 `day30SaleNum`
与库存字段的行，一律标「⚠️ 需人工核查」，不猜。

> 自算是**显式的口径降级** —— 报告顶部固定标注
> 「未接入自定义报表（由 custom-report-analysis 承接），按在线产品口径自算」，
> 让使用者知道结论建立在更弱的数据上。

---

## 三、计算面流水线（脚本执行）

```bash
# 取数落盘完成后（agent 责任已尽），一键跑计算面：
python scripts/run_inventory_health.py --date 2026-09-29 --out out/inventory.md
python scripts/run_inventory_health.py --check-only    # 只做前置校验（含落盘检查）
```

报告输出 `out/inventory.html`（主报告，HTML 即报告；`--keep-md` 可附同名 .md 供调试与测试）。

- `fetch_inventory.py`（v3 改造）：校验 `out/raw/`（存在性 / status / 空数据 / 演示指纹 /
  coverage 截断），组装出与旧版相同形状的记录 —— `replenishment.py` / `render.py` 零改动；
- 也可单独跑共享校验器或组装器：
  `python _shared/scripts/validate_raw.py --dir out/raw --step shops:rows --step fba_shipments:rows`；
  `python scripts/fetch_inventory.py --out out/assemble_summary.json`（组装摘要）。
- 组装侧 max_rows 硬截断：合并行数超上限时裁剪并披露（服务端 20 条/页硬上限在取数侧
  用「单次 ≤1 页 + 批次落盘」规避）。

---

## 四、降级路径（落盘三态）

每个落盘步骤有三态：**缺失**（文件不在）/ **failed**（status=failed）/ **空数据**（`data:{}`）。
处置如下：

| 落盘状态 | 影响 | 降级做法 | 必须标注 |
|---|---|---|---|
| `out/raw/` 为空 | 无法组装 | 停止并提示按取数计划落盘 | — |
| 未接入自定义报表（固定状态） | 无宽表官方口径 | 按在线产品口径自算（主源即在线产品） | 「⚠️ 口径降级：未接入自定义报表（由 custom-report-analysis 承接），可售天数/建议量为自算，非赛狐口径」 |
| 某步骤信封 `status=failed` | 该源缺失 | 该批记录不纳入 + 警告，其余照常 | 数据说明记录 error_code |
| 某 data 为空（`data:{}`） | 该项无数据 | 标「无数据」，不写 0，不重试 | — |
| `online_products_*.json` 全缺失/无数据 | 无有效数据项 | 报告横幅 + rc=2 信号，绝不静默全 0 报告 | 「主源不可用，本报告无有效数据项」 |
| `fba_shipments` / `awd_inventory` 缺失或为空 | 在途 / AWD 未纳入 | 跳过该源（不影响主流程） | 「无数据」或落盘缺失警告 |

---

## 五、字段裁剪与维度字段格式

- 在线产品表字段较多，v3 落盘是**原样信封**（裁剪不在取数侧做），真正的裁剪
  发生在计算面：`fetch_inventory.py` 的 `CUSTOM_FIELDS` 把需要的字段归一化成一个扁平记录，
  其余丢弃。新增字段时改这张映射表，不要在下游到处 `.get()`。
- ⚠️ 最容易踩的隐性坑：在线产品的**维度字段形态不稳定**——**以当轮契约与实测落盘为准**，
  列表 / 纯字符串 / integer 均出现过（跨日数据轮换会变）：

| 字段 | 已实测过的形态 |
|---|---|
| `msku` | 列表 `["MSKU-DEMO-001"]`（10-16 前）｜纯字符串 `"MYJJ-PILLOW-GY45"`（10-22 在线产品） |
| `asin` / `parentAsin` / `fnsku` / `marketplaceId` / `title` | `[{"key": "B0DEMO0001"}]`（dict 列表）｜纯字符串（真实形态数据） |
| `shopId` | `[{"key": "演示店铺001"}]`｜纯字符串 `"826471"`｜integer `826471`（10-22 在线产品） |
| `sku` / `spu` | 列表 `["SKU-DEMO-001"]`｜纯字符串 |

按任一形态写死（当字符串解、或硬取 `[0]`）都会在形态漂移时出错。统一走
`fetch_inventory.first_scalar()` / `pick()` 归一化（列表 → 取第一个非空；dict → 取 `key`/`value`/`name`；
标量原样返回）——两种形态都兼容，不要在取数/组装侧自行解包。

---

## 六、空数据、截断与币种

- `data: {}` / `data: null` / 空 `rows` → **「无数据」**，不是失败；不重试（重试只会浪费
  一次调用与排队）。
- 截断真相在信封 `coverage{complete, fetched_count, total_count, truncated}` ——
  **不信 `pagination.has_more`**（实测与 coverage 矛盾）；`fetch_inventory.py` 会把
  truncated=true 的步骤转成披露语句写进报告「取数覆盖范围」。
- **禁止静默截断** —— 静默截断会被读成「已覆盖全部」，比不跑更危险。
- 币种：在线产品顶层 `currency` = **USD**（售价口径）；其 `inventoryManage.currency` =
  **CNY**（成本口径）。跨币种**绝不直接相减**；确需合并时按 `config/inventory.yaml → currency.fx_rate`
  折算并标注汇率来源与日期；未配置汇率则标注「⚠️ 需人工核查汇率」。
