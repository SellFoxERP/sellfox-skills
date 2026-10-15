---
name: profit-diagnosis
description: 诊断跨境电商的利润异常并给出可核对的归因（赛狐/SellFox ERP 数据，只读）。同时拉取 MSKU 利润报表（171 字段财务深度）与自定义报表（489 字段含采购成本/头程/赔偿/延期结算），做本期 vs 基期的环比，把毛利下滑收敛成 3~5 条归因，每条逐字段标注来源工具与建议动作，并显式标注利润口径（settlement 结算 / shipment 发货）。当用户提到"利润诊断""为什么亏""为什么没利润""毛利下滑""利润下降""亏在哪里""利润归因""这个 ASIN 为什么不赚钱""哪个产品在亏钱""利润越来越薄""成本涨了""费用异常""延期结算""赔偿漏领""利润异常""赔的钱没到账"时，务必使用本技能，即使用户没有明确说"诊断"二字。只读，不改价、不改广告、不改 Listing；不生成周报/月报与目标达成率（用周报技能）。
---

## 商家初始化与报表配置

首次使用或商家要求“配置赛狐 / 配置报表 / 暂不配置”时，读取共享底座的 `ONBOARDING.md`，运行其中的 `merchant_setup.py status`。
不让商家编辑 YAML；助手做只读验证后保存报表 ID。独立用户配置跨版本保留，九个技能共用。
未配置时先使用简版；已选择 `deferred` 不再询问，只在报告说明缺口。下文旧路径指默认配置，实际以共享加载器合并后的值为准；配置交互统一按 ONBOARDING.md，不重复要求回填缓存文件。

## 运行环境与路径

以本文件所在目录为技能根目录，执行脚本前切换到该目录，使用 Python 3.10+。
本项目采用批量安装，九个技能共用一份底座；源码布局的底座位于 `../_shared/`。
读取下文 `_shared/...` 引用时先定位实际共享底座。脚本定位优先级为：显式参数（支持时）
→ `SELLFOX_SHARED_ROOT` → `config/paths.yaml` → 包内 `_shared` → 同级 `_shared`。
批量安装器将共享路径写入配置，九个技能复用同一份商家配置。
`mcp__Sellfox-MCP__...` 是服务工具的示意名称；使用客户端实际暴露的 Sellfox-MCP
`help/search/action`，不得因客户端前缀不同而判定工具缺失。当前完整契约来自 help 或 search；
已有完整契约直接 action，不重复 search。首先读取共享 `SELLFOX_CLIENT.md`。
下文“零网络”仅指赛狐取数后的离线计算；飞书和审批通知模块会联网。
默认输出本地报告；只有用户要求时才启用飞书或外部服务。没有原始数据时仅说明缺口或产出
明确标识的模板，不能把空数据报告当作经营正常的结论。

# 利润异常诊断

把两个宽表（MSKU 利润报表 171 字段 + 自定义报表 489 字段）**收敛成「这个 MSKU 为什么亏了」的 3~5 条归因**。

## 为什么这样设计

`get_profit_report_by_msku` 有 171 个字段，`get_custom_report_page_list` 有 489 个 —— 数据极其丰富，
但**多到人看不懂**。直接展示字段清单没有价值，本技能的价值是**归因**。

三条核心原则，都来自实测：

1. **不做字段排行榜。** 171 个字段里绝大多数是别的字段的比率/占比（`cpcCostRate` = `cpcCost`/`productSales`），
   直接排序会得到一堆同源结论（销售额跌会连带佣金、广告销售额、促销分摊一起跌）。本技能先把因子
   **收敛成十来条互相独立、金额可加的因子**，再算每条对毛利跌幅的**贡献额** —— 贡献额可加，
   所以能回答「毛利少赚的这 8 万，分别是谁吃掉的」。
2. **字段归属两个报表不通用。** `purchaseCost` / `headTripPrice` / `reimbursementAmount` /
   `warehouseLostAmount` / `missingFromInboundAmount` / `deferredAmount` 经实测**只存在于自定义报表**，
   **不在** MSKU 利润报表。把它们归错表会永远取不到值、还静默落空。所以归因表**逐字段标注来源工具**。
3. **利润口径必须显式声明。** `settlement`（结算口径，默认）vs `shipment`（发货口径）同一时间段结果不同，
   不标注必然与财务对不上账。每次调用显式传参，并写进报告首部。

## 先读这些

| 文档 | 何时读 |
|---|---|
| `references/workflow.md` | **执行前必读**：前置校验、report_id 首轮引导与降级、取数计划（agent 执行）、双数据源落盘顺序、环比窗口、字段裁剪、币种 |
| `references/attribution-rules.md` | **计算前必读**：归因公式、符号（profit_sign）为什么必须显式声明、门槛、因子归属表、两条「易漏的钱」专线 |
| `references/output-template.md` | **渲染前必读**：报告骨架、单 MSKU 归因块、延期结算区块、数据说明区块 |
| `references/field-sources.md` | 需要核对某字段属于哪个工具、哪些字段需人工核查时 |
| `_shared/SELLFOX_CLIENT.md` | **必读（v2）**：MCP 调用纪律（三段式 help/search/action、contract_version、参数类型、错误语义、落盘约定） |
| `_shared/config/thresholds.yaml` | 读取 `profit` 预警阈值（毛利率下限、毛利环比跌幅） |
| `_shared/config/reports.yaml` | 读取 `reports.profit.id`（自定义报表 id，本技能的关键前置） |
| `_shared/config/shops.yaml` | 读取店铺范围（`default_scope`）与负责人 |

## 如何执行

```bash
# 前提：agent 已按 references/workflow.md 取数计划把信封落盘到 out/raw/
python scripts/run_profit_diagnosis.py                                  # 默认今天，本地 Markdown
python scripts/run_profit_diagnosis.py --end 2026-09-30 --period-days 30
python scripts/run_profit_diagnosis.py --shop-ids 10001,10002 --out /tmp/profit.md
python scripts/run_profit_diagnosis.py --caliber shipment               # 切换利润口径
python scripts/run_profit_diagnosis.py --feishu                         # 写飞书（演示环境默认阻断）
python scripts/run_profit_diagnosis.py --check-only                     # 只做前置校验（含落盘检查）
python scripts/fetch_profit.py --end 2026-09-30 --out out/profit_bundle.json  # 只做校验+组装
python scripts/selfcheck.py                                             # 离线自检（零网络，无需凭证）
```

## 执行流程

### Step 0：前置校验（技能能不能跑）

1. **利润口径确认。** 默认读 `config/diagnosis.yaml → caliber.default`（`settlement`）。
   可 `--caliber shipment` 覆盖。**报告首部必须标注用了哪个口径**。
2. **report_id 检查。** 读 `_shared/config/reports.yaml` 的 `reports.profit.id`。
   - 为空 → **主动引导**用户去赛狐网页端「自定义报表」创建一张利润/成本报表，勾选
     `purchaseCost / headTripPrice / reimbursementAmount / warehouseLostAmount /
     missingFromInboundAmount / deferredAmount` 及仓储细分字段，把 ID 回填。
   - 用户暂不回填 → 走**降级路径**：只用 `get_profit_report_by_msku`，并在输出顶部标注
     「⚠️ 口径降级」，且**明确声明**采购成本/头程/赔偿/延期结算类归因**不可用**（标「需人工核查」）。
3. **阈值检查。** 读 `_shared/config/thresholds.yaml → profit`。缺失项按
   `config_loader.ensure_params` 一次性批量询问补全并标注来源，不静默用内置值。
4. **客户端 MCP 就绪。** 会话内可调 `mcp__Sellfox-MCP__{help,search,action}`
   （Key 只在客户端 MCP 配置里，脚本不需要 `SELLFOX_MCP_KEY`）。若数据源为演示/沙箱环境，
   命中指纹即加水印并默认阻断写飞书（见 Step 4）。

### Step 1：确认参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 报告期末日 | 今天 | `--end` |
| 本期天数 | `config/diagnosis.yaml → compare.period_days`（30） | `--period-days` |
| 基期 | 紧邻本期的**等长、不重叠**窗口 | 见 `workflow.md §4` |
| 利润口径 | `caliber.default`（settlement） | `--caliber` |
| 店铺范围 | `shops.yaml → default_scope`（空=全部） | `--shop-ids` |
| 输出 | 本地 Markdown | `--out` / `--feishu` |

> 本技能是**只读**技能：不做任何赛狐写操作。唯一可能的写动作是「把报告写进飞书文档」，且需人确认。

### Step 2：取数（agent 按取数计划落盘，双源 × 双窗口）

**v3：取数面是 agent**——按 `references/workflow.md` 的**取数计划**调
`mcp__Sellfox-MCP__{search,action}`，把每步完整信封（status/data/coverage/pagination/trace_id）
落盘 `out/raw/`，**双窗口文件名分开**（current / base），然后由 `fetch_profit.py` 做
「落盘校验 + 组装」（存在性/status/空数据/演示指纹/coverage 截断），组装出与旧版相同形状的
中间结果供归因使用。纪律见 `_shared/SELLFOX_CLIENT.md`：契约先取后用（contract_version）、
参数类型按当次 inputSchema、失配重取一次、不信 has_more 信 coverage、每步落盘不复述数据。

| 顺序 | 落盘文件 | 工具 | 列表字段 | 用途 |
|---|---|---|---|---|
| 1~2 | `profit_msku_current.json` / `profit_msku_base.json` | `get_profit_report_by_msku` | **`data.reportList`** | **主源 A**：171 字段财务深度（本期/基期各一份） |
| 3~4 | `custom_current.json` / `custom_base.json` | `get_custom_report_page_list` | `data.rows` | **主源 B**：489 字段，含成本/赔偿/延期；依赖 `report_id`，缺 → 四类归因标「需人工核查」 |
| 5~6 | `profit_shop_current.json` / `profit_shop_base.json` | `get_profit_report_by_shop` | `data.reportList` | 背景源：整体恶化还是个别 MSKU |
| 7 | `reimbursement.json` | `get_amazon_reimbursement_report` | `data.rows` | 交叉核对赔偿是否漏领；**可能返回 `data:{}`** →「无数据」，不是失败，不重试 |

- 日期格式：利润报表（步骤 1/2/5/6）`yyyyMMdd` + `date_type=2` + `profit_caliber_type` **显式**；
  自定义报表 `yyyy-MM-dd`；赔偿报告 `yyyy-MM-dd`。混用会静默返回空。
- **参数类型按当次契约**：利润报表分页（`page_no`/`page_size`）是 **integer**，与店铺/宽表的
  string 分页不同（实测差异）。
- 分页大小以当前契约为准（历史测试常见 20 条/页），不能靠 `page_size` 控量 —— 单次 ≤1 页 + 取数计划 `max_rows`
  安全阀，**截断时必须披露覆盖范围**（脚本把 `coverage.truncated` 转成披露语句）。
- 详细取数计划与降级路径见 `references/workflow.md`；字段归属见 `references/field-sources.md`。

### Step 3：归因（见 `references/attribution-rules.md`）

对每个 MSKU：

```
毛利跌幅   = 基期 grossProfit − 本期 grossProfit
贡献额     = profit_sign × (基期值 − 本期值)      ← 正 = 该因子把毛利往下拉
占跌幅     = 贡献额 / 毛利跌幅
```

- `profit_sign` 是**每个因子必填**的符号（`+1` 越大越好 / `-1` 越大越不利）。
  赛狐报表里**费用符号不统一**：msku 报表费用为**负数**，自定义报表成本为**正数**，靠猜必反向。
- 门槛（`min_share_pct` 20% 或 `min_amount` 50，取或）→ 按贡献额绝对值降序取**前 3~5 条**。
- 只列贡献额为正的因子；「占跌幅」合计 > 100% 是**正常现象**（因子相关），不是计算错误。
- 每条因子**必须带来源工具**与建议动作；无数据的因子进「需人工核查」，**不写 0**。

### Step 4：渲染与（可选）推送

严格按 `references/output-template.md` 输出：

```
📊 利润异常诊断 | {本期窗口}
【利润口径】settlement / shipment + 本期/基期窗口 + 币种    ← 不可省略
【核心结论】诊断数 / 异常数 / 严重数 / 合计毛利下滑    ← 不可省略
【异常清单】每个 MSKU：毛利对比 + 归因表（含来源工具列）+ 建议动作
【延期结算与赔偿】独立区块（这两类最易被漏看）
【数据说明】取数覆盖范围 / ⚠️ 需人工核查 / 本技能不做什么
```

推送飞书写入前：

- 过 `feishu_writer.guard_demo()` —— 演示环境**默认阻断**：命中指纹即拒绝写入，
  只有显式传 `--allow-demo-write` 才放行，且文档顶部必带演示水印。绝不根据
  「是否命中指纹」自动放行（那等于没有闸门）。
- 本技能主源不含订单 PII，但仍对任何 `buyerEmail`/`buyerName` 走 `mask_pii` 兜底。
- 飞书应用须被加为文档协作者，否则 403；创建接口限流 3 次/秒，同文件夹创建须串行。

## 诚实边界（不可省略）

- **只读，不改任何数据。** 只能诊断，不能改价、改广告、改 Listing（赛狐 MCP 也无改价接口）。
- **不做因果推断。** 归因是**算术贡献度**；因子间相关（销量↔佣金↔广告销售额），
  贡献额合计可 >100%，不声称某条是「根本原因」。
- **降级时四类归因不可用。** 未配 `report_id` 时，采购成本/头程/赔偿/延期结算一律标「需人工核查」，
  绝不拿 msku 汇总字段（`purchasePcFee`/`headTripPcFee`/`compensationFee`）冒充明细。
- **`deferredReason`（延期原因）不在本技能范围。** 实测它属于订单利润报表
  （`get_profit_report_by_order`），本技能不含订单级明细；要看原因需改用订单报表。
- **拿不到的**：账户健康/绩效通知/政策合规、买家消息/工单、评论原文 → 一律标「⚠️ 需人工核查」。
- **不做税费细节与汇兑损益重构**，仅用报表自带 `rate`/`currencyList` 提示汇率变动。
- **跨币种不合并。** 售价类与成本类字段不同时相减；用到的汇率必须标注来源与日期。
- **截断即披露。** 触达 `max_rows` 上限时写明「本次仅覆盖 Top N，其余未纳入」，禁止静默截断。
- **空数据标「无数据」，不写 0。** 「没拿到」与「真的是 0」是两回事。
- **空维度即全量，绝不静默变成零调用。** 店铺范围为空 = 全部店铺（`shop_ids` 不传即全量），
  不是「不取数」。报告必须区分「根本没取数（落盘信封为 0 份，`mcp_calls=0`，显式警示）」与
  「取了但无数据（标『无数据』）」，否则默认配置会产出一份「成功但空」、让人误以为当期真没异常
  的报告。v3 双保险：`assemble_all` 对空维度追加「按全部店铺查询」覆盖语；一个落盘信封都没消费
  时另行告警，空落盘目录直接阻断。
- **窗口夹取即披露。** 利润报表按日查询限制最近 `max_lookback_days` 天；本期天数超限时，把本期与
  基期**一并截短**（仍等长、仍紧邻），并在报告「数据说明」写入警示。绝不只夹取基期起点 ——
  那会产出起点晚于终点/非等长的非法基期，环比方向都可能反掉。

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 把 `purchaseCost`/`headTripPrice`/`reimbursementAmount`/`deferredAmount` 归给 msku 报表 | 这些字段该工具不返回，归因永远落空且不报错 | 归自定义报表；见 `field-sources.md §4` |
| 用 `rows` 解析利润报表 | 拿到空结果且不报错 | 利润报表用 `reportList`（契约 `pagination.rows` 声明） |
| 费用符号靠猜 | 归因恰好反向，且不报错 | 每个因子显式填 `profit_sign`（见 `attribution-rules.md §3`） |
| 不标利润口径 | 与财务对不上账 | 每次显式传 `profit_caliber_type` 并写入报告首部 |
| 把延期结算当成本砍 | 砍错方向（钱只是推迟，不是丢了） | 延期结算单列区块，与成本区分处置 |
| 脚本里自己发 HTTP 调 MCP（或 import 旧 `sellfox_client`） | 双路径漂移、Key 泄漏面扩大 | 取数只走客户端元工具，脚本零网络、只读落盘 |
| action 不带/带旧 contract_version | 被网关拒绝 | 每次执行前 search 拿当次契约；失配重 search 一次重建参数重试一次 |
| 参数类型沿用旧接口全字符串 | 利润报表被 INVALID_PARAMS 拒绝 | 类型按当次 inputSchema（利润报表分页是 **integer**） |
| 信 `pagination.has_more` 翻页 | 与 coverage 矛盾导致误判 | 只信 coverage 与 totalSize |
| 双窗口落盘同名互相覆盖 | 基期数据被本期覆盖，环比静默失真 | current / base 分文件落盘（`profit_msku_current.json` / `_base.json`） |
| 靠 `page_size` 控量 | 服务端静默降级为 20 | 单次 ≤1 页 + max_rows 安全阀 + 截断披露 |
| 空数据写 0 | 误导（「没拿到」≠0） | 标「无数据」 |
| 用 `rate` 缺失时自造汇率 | 编造数据 | 标「⚠️ 需人工核查汇率」 |
| 空店铺范围当成「不取数」 | 零落盘信封，产出「成功但空」的报告 | 空 = 全部店铺，照常取数落盘；脚本对 mcp_calls=0 显式告警 |
| 夹取超限基期只改 `base_start` | 基期起点晚于终点/与本期不等长，环比方向反掉且不报错 | 本期与基期一并截短为等长窗口，并写入告警披露 |
