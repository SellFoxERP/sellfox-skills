# 可购买性（buyability）—— 不可售 / 被抑制 / 丢购物车

> 何时读：分诊命中 P0「不可售/被删除」或 P1「丢购物车」时。
> 目标：确认 Listing 是否还能被买家买到。这是**最高优先级**，因为它直接归零流量与转化。

## 目录

- [1. 不可售 / 被抑制](#1-不可售--被抑制)
- [2. 丢购物车](#2-丢购物车)
- [3. 判定与输出](#3-判定与输出)
- [4. 边界](#4-边界)

## 1. 不可售 / 被抑制

**字段**：`onlineStatus`（来源：`get_online_product_page_list`）。

- 实测样例值 `"Active"`；筛选参数可选值 `active` / `inActive` / `delete`。
- 判定：`onlineStatus` 大小写不敏感地**不等于 `active`** → 命中。
  - `inActive` → **P0**：不可售/被抑制。
  - `delete` → **P0**：已删除。
  - 其它非 active 值 → **P0 待确认**，原样输出字段值，标注「未知状态值，需人工核查」。
- 不要凭记忆断言「抑制（suppressed）」这个具体原因——MCP **拿不到**抑制原因/绩效通知，只能说「当前不可售，原因需去卖家后台/账户健康核查」。

**为什么排 P0**：不可售时 Listing 无曝光、无转化，其它指标全部失去意义。先把这类挑出来，再谈优化。

## 2. 丢购物车

**字段（v1.7.0：宽表已移交 custom-report-analysis，本技能固定用 buyBoxWinner 退化信号）**：

| 场景 | 字段 | 来源 | 判定 |
|---|---|---|---|
| `buyBoxWinner` | `get_online_product_page_list` | `false` → P1（退化信号） |
| — | `buyBoxPercent`（宽表，custom-report-analysis 承接） | **只有布尔值，没有占比** → 报告标注「口径降级」 |

**判定口径说明**：
- `buyBoxPercent` 是占比（实测样例 92.5），越高越好。低于阈值说明相当比例的展示没拿到购物车。
- `buyBoxWinner` 是布尔（实测样例 true）：是否**当前**赢得购物车。它是瞬时信号，存在抖动，**单次为 false 不足以定论**，报告中标注「瞬时信号，建议持续观察」。
- 阈值读 `_shared/config/thresholds.yaml → listing.buybox_pct_min`；退化开关读 `config/listing.yaml → buybox.require_winner`。

## 3. 判定与输出

每条问题输出：

```
[P0] ASIN B0XXXX / 店铺 XXX：不可售（onlineStatus=inActive）
     字段来源：get_online_product_page_list.onlineStatus
     建议：立即登录卖家后台核查「账户健康 / 绩效通知」与商品状态（MCP 拿不到原因，需人工核查）
```

```
[P1] ASIN B0XXXX：购物车占比 78%（阈值 90%）
     字段来源：get_online_product_page_list.buyBoxWinner（buyBoxPercent 为宽表字段，custom-report-analysis 承接）
     建议：检查是否被跟卖、价格是否有竞争力、库存是否充足；竞品 BuyBox 价需人工核查
```

## 4. 边界

- 本技能**只诊断不修改**，不会去改 Listing。
- **拿不到**：抑制原因、账户健康状态、绩效通知、竞品卖家身份与竞品价格 → 全部标 **⚠️ 需人工核查**。
- `buyBoxWinner` 是瞬时值：若要判断趋势，需要多期快照（见 `workflow.md` 的历史快照说明）。